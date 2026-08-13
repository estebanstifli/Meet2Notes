"""CPU-only ``diarize`` adapter isolated from Meet2Notes' PyTorch runtime.

The upstream package currently caps Torch below 2.9, while the application
uses newer CUDA-capable Torch for ASR and Pyannote.  A private child virtual
environment avoids downgrading or otherwise altering the application's runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
import venv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

logger = logging.getLogger(__name__)
DIARIZE_VERSION = "0.1.2"


class DiarizeCpuEngine:
    """Persistent subprocess worker for the optional, CPU-only diarize package."""

    name = "diarize"

    def __init__(self, models_dir: Path) -> None:
        self.runtime_dir = models_dir / "runtimes" / "diarize"
        self.cache_dir = models_dir / "diarization" / "diarize"
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="diarize-cpu",
        )
        self._process: subprocess.Popen[str] | None = None
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._active_requests = 0
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        with self._state_lock:
            state = self._state
            active = self._active_requests
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": "diarize · CPU-only",
            "available": self._runtime_python().is_file(),
            "runtime_available": self._runtime_python().is_file(),
            "installed": self._installed(),
            "models_directory": str(self.cache_dir),
            "supported_providers": ["cpu"],
            "requires_isolated_runtime": True,
            "install_command": "Use Install in Settings to create the private diarize runtime",
            "worker": {
                "dedicated": True,
                "thread_prefix": "diarize-cpu",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": self._worker_running(),
                "last_error": error,
            },
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(self._prepare_sync, config, allow_model_download)

    async def uninstall(self, engine_id: str) -> None:
        del engine_id
        await self._submit(self._uninstall_runtime_sync)

    async def diarize(
        self,
        audio_path: Path,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]:
        return cast(
            list[DiarizationSegment],
            await self._submit(
                self._diarize_sync,
                audio_path,
                config,
                progress,
                is_cancelled,
            ),
        )

    def unload(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        with self._state_lock:
            if not self._active_requests and not self._shutdown:
                self._state = "idle"

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self.unload()
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._state_lock:
            self._state = "stopped"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError("The diarize CPU worker is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(self, config: dict[str, Any], allow_model_download: bool) -> None:
        del config
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if allow_model_download and not self._installed():
                self._install_runtime()
            if not self._installed():
                raise CapabilityUnavailableError(
                    "The isolated diarize CPU runtime is not installed. "
                    "Use Install in Settings first."
                )
            self._ensure_worker()
        except Exception as error:
            failure = error
            raise
        finally:
            self._request_finished(failure)

    def _diarize_sync(
        self,
        audio_path: Path,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]:
        self._request_started("inferencing")
        failure: Exception | None = None
        try:
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            self._ensure_worker()
            progress(0.05, "Running diarize CPU speaker analysis")
            request = {
                "action": "diarize",
                "audio_path": str(audio_path),
                "num_speakers": _known_speaker_count(config),
            }
            response = self._request_worker(request)
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            if not bool(response.get("ok")):
                raise CapabilityUnavailableError(str(response.get("error") or "diarize failed"))
            raw_segments = response.get("segments")
            if not isinstance(raw_segments, list):
                raise CapabilityUnavailableError("diarize returned an invalid segment list")
            labels = sorted(
                {
                    str(segment.get("speaker"))
                    for segment in raw_segments
                    if isinstance(segment, dict) and segment.get("speaker") is not None
                }
            )
            indexes = {label: index for index, label in enumerate(labels)}
            turns = [
                DiarizationSegment(
                    start_ms=int(segment.get("start_ms") or 0),
                    end_ms=int(segment.get("end_ms") or 0),
                    speaker=indexes[str(segment["speaker"])],
                )
                for segment in raw_segments
                if isinstance(segment, dict)
                and str(segment.get("speaker")) in indexes
                and int(segment.get("end_ms") or 0) > int(segment.get("start_ms") or 0)
            ]
            progress(0.98, f"diarize detected {len(indexes)} speakers")
            return turns
        except Exception as error:
            failure = error
            raise
        finally:
            if not bool(config.get("keep_model_loaded", True)):
                self.unload()
            self._request_finished(failure)

    def _install_runtime(self) -> None:
        logger.info("Creating isolated CPU runtime for diarize in %s", self.runtime_dir)
        self.runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(self.runtime_dir)
        python = self._runtime_python()
        if not python.is_file():
            raise CapabilityUnavailableError("Could not create the private diarize runtime")
        environment = dict(os.environ)
        environment.update(
            {
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_PROGRESS_BAR": "off",
                "PYTHONUTF8": "1",
            }
        )
        commands = (
            [str(python), "-m", "pip", "install", "--upgrade", "pip"],
            [str(python), "-m", "pip", "install", f"diarize=={DIARIZE_VERSION}"],
        )
        for command in commands:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                check=False,
            )
            for line in (completed.stdout + "\n" + completed.stderr).splitlines():
                if line.strip():
                    logger.info("diarize install | %s", line)
            if completed.returncode:
                raise CapabilityUnavailableError(
                    "Could not install the isolated diarize runtime. See the installation log."
                )
        self._marker_path().write_text(DIARIZE_VERSION, encoding="utf-8")
        logger.info("diarize CPU runtime installation completed")

    def _ensure_worker(self) -> None:
        if self._worker_running():
            return
        if not self._installed():
            raise CapabilityUnavailableError("The isolated diarize CPU runtime is not installed")
        python = self._runtime_python()
        worker = Path(__file__).with_name("diarize_worker.py")
        environment = dict(os.environ)
        environment.update(
            {
                "TORCH_HOME": str(self.cache_dir / "torch"),
                "XDG_CACHE_HOME": str(self.cache_dir / "cache"),
                "M2N_DIARIZE_RUNTIME_HOME": str(self.cache_dir / "runtime-home"),
                "PYTHONUTF8": "1",
            }
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._process = subprocess.Popen(
            [str(python), "-u", str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # The child returns protocol messages on stdout only. Discarding
            # library diagnostics avoids a full stderr pipe blocking a long run.
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._set_state("ready")

    def _request_worker(self, request: dict[str, Any]) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise CapabilityUnavailableError("The diarize CPU worker did not start")
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 60 * 60
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Ignoring unexpected diarize worker output: %s", line.strip())
                    continue
                return response if isinstance(response, dict) else {}
            if process.poll() is not None:
                raise CapabilityUnavailableError(
                    "The diarize CPU worker stopped unexpectedly. See the application log."
                )
        raise CapabilityUnavailableError("The diarize CPU worker timed out")

    def _runtime_python(self) -> Path:
        return self.runtime_dir / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )

    def _marker_path(self) -> Path:
        return self.runtime_dir / ".meet2notes-installed"

    def _installed(self) -> bool:
        return self._runtime_python().is_file() and self._marker_path().is_file()

    def _uninstall_runtime_sync(self) -> None:
        with self._state_lock:
            if self._active_requests:
                raise CapabilityUnavailableError(
                    "Wait for the active diarize task to finish before uninstalling it"
                )
        self.unload()
        removed_runtime = remove_managed_model_tree(
            root=self.runtime_dir.parent,
            target=self.runtime_dir,
            label="diarize CPU runtime",
        )
        remove_managed_model_tree(
            root=self.cache_dir.parent,
            target=self.cache_dir,
            label="diarize CPU cache",
        )
        if not removed_runtime:
            raise CapabilityUnavailableError(
                "The isolated diarize CPU runtime is not installed locally"
            )
        logger.info("Removed isolated diarize CPU runtime from %s", self.runtime_dir)

    def _worker_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
            self._last_error = None

    def _request_started(self, state: str) -> None:
        with self._state_lock:
            self._active_requests += 1
            self._state = state
            self._last_error = None

    def _request_finished(self, failure: Exception | None) -> None:
        with self._state_lock:
            self._active_requests = max(0, self._active_requests - 1)
            if failure is not None:
                self._last_error = str(failure)
            if not self._active_requests:
                self._state = "error" if failure is not None else "ready"


def _known_speaker_count(config: dict[str, Any]) -> int | None:
    requested = config.get("num_speakers")
    return requested if isinstance(requested, int) and requested > 0 else None
