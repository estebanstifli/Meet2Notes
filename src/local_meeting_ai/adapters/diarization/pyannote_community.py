"""Local Pyannote Community-1 diarization adapter."""

from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import logging
import os
import threading
import warnings
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError, JobCancelledError
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

logger = logging.getLogger(__name__)
PYANNOTE_COMMUNITY_REPOSITORY = "pyannote/speaker-diarization-community-1"


class PyannoteCommunityDiarizationEngine:
    """Dedicated local Community-1 worker with optional CUDA execution."""

    name = "pyannote-community-1"

    def __init__(self, models_dir: Path, *, access_token: str | None = None) -> None:
        self.model_dir = models_dir / "diarization" / "pyannote-community-1"
        self._access_token = access_token
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="pyannote-community",
        )
        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pipeline: Any | None = None
        self._model_key: tuple[str, bool] | None = None
        self._state = "idle"
        self._active_requests = 0
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        runtime = importlib.util.find_spec("pyannote") is not None
        cuda_available = _torch_cuda_available()
        with self._state_lock:
            state = self._state
            active = self._active_requests
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": "Pyannote Community-1",
            "available": runtime,
            "runtime_available": runtime,
            "installed": self._installed(),
            "repository": PYANNOTE_COMMUNITY_REPOSITORY,
            "models_directory": str(self.model_dir),
            "snapshot_directory": str(self._snapshot_dir()),
            "supported_providers": ["cpu", "cuda"],
            "cuda_available": cuda_available,
            "supports_exclusive_diarization": True,
            "requires_huggingface_token": not self._token(),
            "install_command": 'python -m pip install -e ".[pyannote-diarization]"',
            "worker": {
                "dedicated": True,
                "thread_prefix": "pyannote-community",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": self._pipeline is not None,
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
        await self._submit(self._uninstall_model_sync)

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
        with self._model_lock:
            self._pipeline = None
            self._model_key = None
        _release_torch_memory()
        with self._state_lock:
            if not self._active_requests and not self._shutdown:
                self._state = "idle"

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.unload()
        with self._state_lock:
            self._state = "stopped"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError("The Pyannote Community-1 worker is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(self, config: dict[str, Any], allow_model_download: bool) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if allow_model_download:
                self._download_models()
            self._load(config)
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
            progress(0.04, "Loading Pyannote Community-1")
            pipeline = self._load(config)
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            options: dict[str, Any] = {}
            number = _known_speaker_count(config)
            if number is not None:
                options["num_speakers"] = number
            progress(0.12, "Running Pyannote Community-1 speaker analysis")
            # The application always supplies normalized 16 kHz mono WAV.
            # Passing the waveform avoids Pyannote's optional TorchCodec
            # decoder, which may not match the application's CUDA PyTorch.
            output = pipeline(_normalized_waveform(audio_path), **options)
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            source = None
            if bool(config.get("pyannote_exclusive", True)):
                source = getattr(output, "exclusive_speaker_diarization", None)
            if source is None:
                source = getattr(output, "speaker_diarization", output)
            raw_turns = list(source.itertracks(yield_label=True))
            labels = sorted({str(label) for _, _, label in raw_turns})
            label_ids = {label: index for index, label in enumerate(labels)}
            turns = [
                DiarizationSegment(
                    start_ms=round(float(turn.start) * 1000),
                    end_ms=round(float(turn.end) * 1000),
                    speaker=label_ids[str(label)],
                )
                for turn, _, label in raw_turns
                if float(turn.end) > float(turn.start)
            ]
            progress(0.98, f"Pyannote detected {len(label_ids)} speakers")
            return turns
        except Exception as error:
            failure = error
            raise
        finally:
            if not bool(config.get("keep_model_loaded", True)):
                self.unload()
            self._request_finished(failure)

    def _download_models(self) -> None:
        _require_pyannote_runtime()
        token = self._token()
        if not token:
            raise CapabilityUnavailableError(
                "Pyannote Community-1 requires a Hugging Face access token for "
                "the first download. Accept the model conditions, then set "
                "M2N_PYANNOTE_TOKEN in .env and restart Meet2Notes."
            )
        hub = importlib.import_module("huggingface_hub")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading Pyannote Community-1 into %s", self.model_dir)
        try:
            snapshot = hub.snapshot_download(
                repo_id=PYANNOTE_COMMUNITY_REPOSITORY,
                # ``local_dir`` creates temporary names that recent
                # huggingface_hub versions cannot always represent on Windows.
                # Its native cache layout is Windows-safe and also provides a
                # complete, immutable snapshot for offline loading.
                cache_dir=str(self._cache_dir()),
                token=token,
            )
        except Exception as error:
            detail = str(error)
            logger.warning(
                "Pyannote Community-1 download failed: %s",
                detail,
                exc_info=True,
            )
            if "403" in detail or type(error).__name__ in {
                "GatedRepoError",
                "RepositoryNotFoundError",
            }:
                raise CapabilityUnavailableError(
                    "Hugging Face denied the protected Community-1 files (403). "
                    "Accept the model conditions in the browser and use a Read token, "
                    "or enable 'Read access to contents of public gated repos you can "
                    "access' on the fine-grained token."
                ) from error
            raise CapabilityUnavailableError(
                "Could not download Pyannote Community-1. Confirm that the account "
                "has accepted the model conditions and that M2N_PYANNOTE_TOKEN is valid."
            ) from error
        self._marker_path().write_text(str(snapshot), encoding="utf-8")
        logger.info("Pyannote Community-1 download completed at %s", snapshot)

    def _load(self, config: dict[str, Any]) -> Any:
        _require_pyannote_runtime()
        if not self._installed():
            raise CapabilityUnavailableError(
                "Pyannote Community-1 is not installed. Use Install in Settings first."
            )
        provider = str(config.get("provider", "cpu"))
        if provider not in {"cpu", "cuda"}:
            raise CapabilityUnavailableError(
                "Pyannote Community-1 supports CPU or CUDA, not this provider"
            )
        use_cuda = provider == "cuda"
        if use_cuda and not _torch_cuda_available():
            raise CapabilityUnavailableError(
                "Pyannote Community-1 was configured for CUDA, but CUDA PyTorch is unavailable"
            )
        key = (provider, bool(config.get("pyannote_exclusive", True)))
        with self._model_lock:
            if self._pipeline is not None and self._model_key == key:
                return self._pipeline
            # Privacy is the app default. Local pipeline execution remains fully
            # offline after download; this also disables anonymous metrics.
            os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
            torch = importlib.import_module("torch")
            pyannote = _import_pyannote_audio()
            logger.info("Loading Pyannote Community-1 on %s", "CUDA" if use_cuda else "CPU")
            pipeline = pyannote.Pipeline.from_pretrained(str(self._snapshot_dir()))
            if use_cuda:
                pipeline.to(torch.device("cuda"))
            self._pipeline = pipeline
            self._model_key = key
            return pipeline

    def _token(self) -> str | None:
        return self._access_token or os.getenv("M2N_PYANNOTE_TOKEN") or os.getenv(
            "PYANNOTE_TOKEN"
        )

    def _marker_path(self) -> Path:
        return self.model_dir / ".meet2notes-installed"

    def _cache_dir(self) -> Path:
        return self.model_dir / "hf-cache"

    def _snapshot_dir(self) -> Path:
        snapshots = (
            self._cache_dir()
            / "models--pyannote--speaker-diarization-community-1"
            / "snapshots"
        )
        if not snapshots.is_dir():
            return snapshots / "missing"
        candidates = [path for path in snapshots.iterdir() if path.is_dir()]
        if not candidates:
            return snapshots / "missing"
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _installed(self) -> bool:
        return (self._snapshot_dir() / "config.yaml").is_file()

    def _uninstall_model_sync(self) -> None:
        with self._state_lock:
            if self._active_requests:
                raise CapabilityUnavailableError(
                    "Wait for the active Pyannote Community-1 task to finish before uninstalling it"
                )
        self.unload()
        removed = remove_managed_model_tree(
            root=self.model_dir.parent,
            target=self.model_dir,
            label="Pyannote Community-1",
        )
        if not removed:
            raise CapabilityUnavailableError("Pyannote Community-1 is not installed locally")
        logger.info("Removed Pyannote Community-1 files from %s", self.model_dir)

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
                self._state = (
                    "error"
                    if failure is not None
                    else ("ready" if self._pipeline is not None else "idle")
                )


def _known_speaker_count(config: dict[str, Any]) -> int | None:
    requested = config.get("num_speakers")
    return requested if isinstance(requested, int) and requested > 0 else None


def _torch_cuda_available() -> bool:
    try:
        torch = importlib.import_module("torch")
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _release_torch_memory() -> None:
    gc.collect()
    try:
        torch = importlib.import_module("torch")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Could not release the Pyannote CUDA cache", exc_info=True)


def _require_pyannote_runtime() -> None:
    if importlib.util.find_spec("pyannote") is None:
        raise CapabilityUnavailableError(
            "Pyannote Community-1 requires its optional runtime. Run: "
            'python -m pip install -e ".[pyannote-diarization]"'
        )


def _import_pyannote_audio() -> Any:
    # We pass tensors directly, so TorchCodec is never used. Its import-time
    # diagnostic is otherwise misleading when it targets another PyTorch ABI.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"(?s).*torchcodec.*",
            category=UserWarning,
        )
        return importlib.import_module("pyannote.audio")


def _normalized_waveform(audio_path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(audio_path), "rb") as source:
            if source.getsampwidth() != 2 or source.getnchannels() != 1:
                raise CapabilityUnavailableError(
                    "Pyannote Community-1 requires the normalized 16-bit mono WAV input"
                )
            samples = array("h")
            samples.frombytes(source.readframes(source.getnframes()))
            sample_rate = source.getframerate()
    except wave.Error as error:
        raise CapabilityUnavailableError(
            "Pyannote Community-1 requires normalized WAV audio"
        ) from error
    numpy = importlib.import_module("numpy")
    torch = importlib.import_module("torch")
    waveform = torch.from_numpy(
        numpy.asarray(samples, dtype=numpy.float32) / 32768.0
    ).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": sample_rate}
