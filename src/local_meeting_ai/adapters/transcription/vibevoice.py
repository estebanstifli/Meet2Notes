from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.domain.entities import (
    DiarizationSegment,
    ModelProfile,
    SegmentDraft,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    JobCancelledError,
)
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SegmentReporter,
)

logger = logging.getLogger(__name__)

VIBEVOICE_ASR_REPOSITORY = "microsoft/VibeVoice-ASR-HF"
VIBEVOICE_BITNET_REPOSITORY = "microsoft/VibeVoice-ASR-BitNet"
BITNET_MODEL_FILES = (
    "vibeasr-vae-encoder-i8_s.gguf",
    "vibeasr-lm-i2_s-embed-q6_k.gguf",
)


class VibeVoiceAsrEngine:
    """Microsoft VibeVoice-ASR final-pass worker.

    The 7B model is intentionally excluded from live capture: it is a
    long-context refinement engine and can use CPU offload when VRAM is limited.
    """

    name = "vibevoice-asr"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self.model_dir = models_dir / "vibevoice-asr-hf"
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vibevoice-asr",
        )
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        runtime = _vibevoice_runtime_available()
        with self._state_lock:
            state = self._state
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": "Microsoft VibeVoice ASR 7B",
            "kind": "local",
            "available": runtime,
            "runtime_available": runtime,
            "installed": self._installed(),
            "installed_models": ([VIBEVOICE_ASR_REPOSITORY] if self._installed() else []),
            "loaded_models": ([VIBEVOICE_ASR_REPOSITORY] if self._model is not None else []),
            "supports_live": False,
            "supports_final": True,
            "supports_speakers": True,
            "supports_timestamps": True,
            "languages": "50+ including Spanish",
            "download_size": "~17 GB",
            "memory_note": (
                "Does not fit fully in 8 GB VRAM; Transformers device_map=auto "
                "can offload layers to system RAM."
            ),
            "install_command": ('python -m pip install -e ".[vibevoice-asr]"'),
            "worker": {
                "dedicated": True,
                "thread_prefix": "vibevoice-asr",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": 1 if state in {"loading", "inferencing"} else 0,
                "model_resident": self._model is not None,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(self._prepare_sync, profile, allow_model_download)

    async def uninstall(self, profile: ModelProfile) -> None:
        del profile
        await self._submit(self._uninstall_model_sync)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        return cast(
            TranscriptionResult,
            await self._submit(
                self._transcribe_sync,
                request,
                progress,
                is_cancelled,
                segment_ready,
            ),
        )

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._processor = None
        _release_torch_memory()
        with self._state_lock:
            if not self._shutdown:
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
                raise CapabilityUnavailableError("The VibeVoice ASR worker is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(
        self,
        profile: ModelProfile,
        allow_model_download: bool,
    ) -> None:
        self._set_state("loading")
        try:
            if allow_model_download:
                self._download()
                # Installing a 17 GB model must not immediately reserve RAM/VRAM.
                self._set_state("idle")
                return
            self._load(profile.device)
            self._set_state("ready")
        except Exception as error:
            self._set_error(error)
            raise

    def _transcribe_sync(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        self._set_state("inferencing")
        try:
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            progress(0.03, "Loading Microsoft VibeVoice ASR 7B")
            _configure_torch_cpu_threads(request.cpu_threads)
            model, processor = self._load(request.device)
            progress(0.12, "Preparing long-form audio")
            inputs = processor.apply_transcription_request(
                audio=str(request.audio_path),
            )
            model_device = getattr(model, "device", None)
            model_dtype = getattr(model, "dtype", None)
            if model_device is not None:
                inputs = (
                    inputs.to(model_device, model_dtype)
                    if model_dtype is not None
                    else inputs.to(model_device)
                )
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            progress(0.20, "Running long-context speech recognition")
            output_ids = model.generate(**inputs)
            generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
            parsed = processor.decode(
                generated_ids,
                return_format="parsed",
            )[0]
            drafts = _vibevoice_drafts(parsed, segment_ready)
            progress(0.98, f"Transcribed {len(drafts)} speaker turns")
            if not request.keep_model_loaded:
                self.unload()
            else:
                self._set_state("ready")
            duration_ms = max((item.end_ms for item in drafts), default=None)
            return TranscriptionResult(
                language=request.language,
                language_probability=None,
                duration_ms=duration_ms,
                segments=drafts,
                speaker_turns=_speaker_turns(drafts),
            )
        except Exception as error:
            self._set_error(error)
            raise

    def _download(self) -> None:
        _ensure_vibevoice_python_runtime()
        try:
            hub = importlib.import_module("huggingface_hub")
        except ImportError as error:
            raise CapabilityUnavailableError(
                "huggingface-hub is required to install VibeVoice ASR"
            ) from error
        self.model_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Downloading Microsoft VibeVoice ASR 7B (~17 GB) into %s",
            self.model_dir,
        )
        hub.snapshot_download(
            repo_id=VIBEVOICE_ASR_REPOSITORY,
            local_dir=str(self.model_dir),
        )
        (self.model_dir / ".meet2notes-installed").write_text(
            VIBEVOICE_ASR_REPOSITORY,
            encoding="utf-8",
        )
        logger.info("Microsoft VibeVoice ASR 7B model download completed")

    def _load(self, device: str) -> tuple[Any, Any]:
        if not self._installed():
            raise CapabilityUnavailableError("Microsoft VibeVoice ASR 7B is not installed")
        if not (_module_available("torch") and _module_available("transformers")):
            raise CapabilityUnavailableError(
                'VibeVoice runtime is missing. Run: python -m pip install -e ".[vibevoice-asr]"'
            )
        with self._lock:
            if self._model is not None and self._processor is not None:
                return self._model, self._processor
            transformers = importlib.import_module("transformers")
            processor_class = transformers.AutoProcessor
            model_class = getattr(
                transformers,
                "VibeVoiceAsrForConditionalGeneration",
                None,
            )
            if model_class is None:
                raise CapabilityUnavailableError("VibeVoice ASR requires Transformers 5.3 or newer")
            device_map: Any = (
                "cpu" if device == "cpu" else ({"": "cuda:0"} if device == "cuda" else "auto")
            )
            logger.info(
                "Loading Microsoft VibeVoice ASR 7B with device map %s",
                device_map,
            )
            self._processor = processor_class.from_pretrained(
                str(self.model_dir),
                local_files_only=True,
            )
            self._model = model_class.from_pretrained(
                str(self.model_dir),
                device_map=device_map,
                torch_dtype="auto",
                local_files_only=True,
            )
            return self._model, self._processor

    def _installed(self) -> bool:
        return (self.model_dir / ".meet2notes-installed").is_file() or (
            (self.model_dir / "config.json").is_file()
            and (self.model_dir / "model.safetensors.index.json").is_file()
        )

    def _uninstall_model_sync(self) -> None:
        with self._state_lock:
            if self._state in {"loading", "inferencing"}:
                raise CapabilityUnavailableError(
                    "Wait for the active VibeVoice ASR task to finish before uninstalling it"
                )
        self.unload()
        removed = remove_managed_model_tree(
            root=self.models_dir,
            target=self.model_dir,
            label="Microsoft VibeVoice ASR 7B",
        )
        if not removed:
            raise CapabilityUnavailableError("Microsoft VibeVoice ASR 7B is not installed locally")
        logger.info("Removed Microsoft VibeVoice ASR 7B files from %s", self.model_dir)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
            self._last_error = None

    def _set_error(self, error: Exception) -> None:
        with self._state_lock:
            self._state = "error"
            self._last_error = str(error)


class VibeVoiceBitNetEngine:
    """Official VibeASR.cpp model installer and optional CLI worker."""

    name = "vibevoice-asr-bitnet"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        self.model_dir = models_dir / "vibevoice-asr-bitnet"
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="vibevoice-bitnet",
        )
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        binary = self._runtime_binary()
        with self._state_lock:
            state = self._state
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": "Microsoft VibeVoice ASR BitNet",
            "kind": "local",
            "available": binary is not None,
            "runtime_available": binary is not None,
            "installed": self._installed(),
            "installed_models": ([VIBEVOICE_BITNET_REPOSITORY] if self._installed() else []),
            "loaded_models": [],
            "supports_live": False,
            "supports_final": True,
            "supports_speakers": True,
            "supports_timestamps": True,
            "languages": "Validated: EN, ZH, FR, IT, KO, PT, VI",
            "download_size": "1.58 GB",
            "memory_note": (
                "CPU-only official VibeASR.cpp runtime; Spanish is not yet "
                "listed among Microsoft's validated languages."
            ),
            "runtime_path": str(binary) if binary else None,
            "worker": {
                "dedicated": True,
                "thread_prefix": "vibevoice-bitnet",
                "dispatcher_threads": 1,
                "state": state if binary or state == "error" else "unavailable",
                "active_requests": 1 if state == "inferencing" else 0,
                "model_resident": False,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None:
        del profile
        await asyncio.wrap_future(self._executor.submit(self._prepare_sync, allow_model_download))

    async def uninstall(self, profile: ModelProfile) -> None:
        del profile
        await asyncio.wrap_future(self._executor.submit(self._uninstall_model_sync))

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        return cast(
            TranscriptionResult,
            await asyncio.wrap_future(
                self._executor.submit(
                    self._transcribe_sync,
                    request,
                    progress,
                    is_cancelled,
                    segment_ready,
                )
            ),
        )

    def unload(self) -> None:
        return

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._state_lock:
            self._state = "stopped"

    def _prepare_sync(self, allow_model_download: bool) -> None:
        if self._shutdown:
            raise CapabilityUnavailableError("The VibeVoice BitNet worker is shutting down")
        self._set_state("loading")
        try:
            if not self._installed() and not allow_model_download:
                raise CapabilityUnavailableError("Microsoft VibeVoice ASR BitNet is not installed")
            if allow_model_download:
                self._download()
            self._set_state("idle")
        except Exception as error:
            self._set_error(error)
            raise

    def _download(self) -> None:
        try:
            hub = importlib.import_module("huggingface_hub")
        except ImportError as error:
            raise CapabilityUnavailableError(
                "huggingface-hub is required to install VibeVoice BitNet"
            ) from error
        self.model_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Downloading Microsoft VibeVoice ASR BitNet (1.58 GB) into %s",
            self.model_dir,
        )
        for filename in BITNET_MODEL_FILES:
            logger.info("Downloading %s", filename)
            hub.hf_hub_download(
                repo_id=VIBEVOICE_BITNET_REPOSITORY,
                filename=filename,
                local_dir=str(self.model_dir),
            )
        try:
            self._attempt_runtime_build()
        except Exception:
            logger.warning(
                "BitNet weights are installed, but VibeASR.cpp could not be built automatically",
                exc_info=True,
            )
        logger.info("Microsoft VibeVoice ASR BitNet model download completed")

    def _attempt_runtime_build(self) -> None:
        if self._runtime_binary() is not None:
            return
        git = shutil.which("git")
        cmake = shutil.which("cmake")
        if not git or not cmake:
            logger.warning(
                "VibeASR.cpp weights are installed, but its runtime could not "
                "be built automatically because Git or CMake is missing"
            )
            return
        if os.name == "nt" and not (shutil.which("gcc") and shutil.which("g++")):
            logger.warning(
                "VibeASR.cpp weights are installed. Windows inference needs "
                "MinGW GCC/Clang; MSVC is not supported by Microsoft"
            )
            return
        source = self.model_dir / "runtime-source"
        if not source.is_dir():
            _run_logged(
                [
                    git,
                    "clone",
                    "--depth",
                    "1",
                    "--recursive",
                    "https://github.com/microsoft/VibeASR.cpp.git",
                    str(source),
                ],
                "VibeASR.cpp source",
            )
        build = source / "build"
        configure = [
            cmake,
            "-S",
            str(source),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
        ]
        if os.name == "nt":
            configure.extend(
                [
                    "-G",
                    "MinGW Makefiles",
                    "-DCMAKE_C_COMPILER=gcc",
                    "-DCMAKE_CXX_COMPILER=g++",
                    "-DCMAKE_MAKE_PROGRAM=mingw32-make",
                ]
            )
        _run_logged(configure, "VibeASR.cpp configure")
        _run_logged(
            [cmake, "--build", str(build), "--target", "asr_infer", "-j"],
            "VibeASR.cpp build",
        )

    def _transcribe_sync(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        binary = self._runtime_binary()
        if binary is None:
            raise CapabilityUnavailableError(
                "The official VibeASR.cpp runtime is not installed. On Windows "
                "it currently requires a MinGW GCC/Clang build; the model "
                "weights are installed and will become selectable when the "
                "runtime is available."
            )
        if not self._installed():
            raise CapabilityUnavailableError(
                "Microsoft VibeVoice ASR BitNet model files are not installed"
            )
        self._set_state("inferencing")
        try:
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            progress(0.10, "Starting Microsoft VibeASR.cpp")
            threads = request.cpu_threads or max(3, min(8, (os.cpu_count() or 4) // 2))
            command = [
                str(binary),
                "--vae-model",
                str(self.model_dir / BITNET_MODEL_FILES[0]),
                "--lm-model",
                str(self.model_dir / BITNET_MODEL_FILES[1]),
                "--audio",
                str(request.audio_path),
                "-t",
                str(threads),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if completed.returncode:
                raise CapabilityUnavailableError(
                    completed.stderr.strip()
                    or f"VibeASR.cpp exited with code {completed.returncode}"
                )
            drafts = _parse_bitnet_output(
                completed.stdout,
                request.audio_path,
                segment_ready,
            )
            progress(0.98, f"Transcribed {len(drafts)} speaker turns")
            self._set_state("idle")
            return TranscriptionResult(
                language=request.language,
                language_probability=None,
                duration_ms=max((item.end_ms for item in drafts), default=None),
                segments=drafts,
                speaker_turns=_speaker_turns(drafts),
            )
        except Exception as error:
            self._set_error(error)
            raise

    def _runtime_binary(self) -> Path | None:
        filename = "asr_infer.exe" if os.name == "nt" else "asr_infer"
        candidates = (
            self.model_dir / "runtime" / filename,
            self.model_dir / "runtime-source" / "build" / "bin" / filename,
            self.models_dir / "vibeasr-runtime" / filename,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        discovered = shutil.which("asr_infer")
        return Path(discovered).resolve() if discovered else None

    def _installed(self) -> bool:
        return all((self.model_dir / name).is_file() for name in BITNET_MODEL_FILES)

    def _uninstall_model_sync(self) -> None:
        with self._state_lock:
            if self._state in {"loading", "inferencing"}:
                raise CapabilityUnavailableError(
                    "Wait for the active VibeVoice BitNet task to finish before uninstalling it"
                )
        removed = remove_managed_model_tree(
            root=self.models_dir,
            target=self.model_dir,
            label="Microsoft VibeVoice ASR BitNet",
        )
        if not removed:
            raise CapabilityUnavailableError(
                "Microsoft VibeVoice ASR BitNet is not installed locally"
            )
        self._set_state("idle")
        logger.info("Removed Microsoft VibeVoice ASR BitNet files from %s", self.model_dir)

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state
            self._last_error = None

    def _set_error(self, error: Exception) -> None:
        with self._state_lock:
            self._state = "error"
            self._last_error = str(error)


def _vibevoice_drafts(
    parsed: Any,
    segment_ready: SegmentReporter,
) -> list[SegmentDraft]:
    items = parsed if isinstance(parsed, list) else []
    drafts: list[SegmentDraft] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = str(item.get("Content") or item.get("content") or "").strip()
        if not text:
            continue
        start = _seconds(item.get("Start", item.get("start", 0)))
        end = max(start, _seconds(item.get("End", item.get("end", start))))
        speaker = item.get("Speaker", item.get("speaker"))
        draft = SegmentDraft(
            index=index,
            start_ms=round(start * 1000),
            end_ms=round(end * 1000),
            text=text,
            metadata={
                "speaker": speaker,
                "provider": "microsoft-vibevoice-asr",
            },
        )
        drafts.append(draft)
        segment_ready(draft)
    return drafts


def _parse_bitnet_output(
    output: str,
    audio_path: Path,
    segment_ready: SegmentReporter,
) -> list[SegmentDraft]:
    match = re.search(r"(\[\s*\{.*\}\s*\])", output, flags=re.DOTALL)
    if match:
        try:
            return _vibevoice_drafts(json.loads(match.group(1)), segment_ready)
        except json.JSONDecodeError:
            pass
    text = output.strip()
    if not text:
        raise CapabilityUnavailableError("VibeASR.cpp returned an empty transcript")
    duration_ms = _wave_duration_ms(audio_path)
    draft = SegmentDraft(
        index=0,
        start_ms=0,
        end_ms=duration_ms,
        text=text,
        metadata={"provider": "microsoft-vibevoice-asr-bitnet"},
    )
    segment_ready(draft)
    return [draft]


def _speaker_turns(drafts: list[SegmentDraft]) -> list[DiarizationSegment]:
    labels: dict[str, int] = {}
    turns: list[DiarizationSegment] = []
    for draft in drafts:
        raw = (draft.metadata or {}).get("speaker")
        if raw is None or draft.end_ms <= draft.start_ms:
            continue
        label = str(raw).strip()
        if not label:
            continue
        speaker = labels.setdefault(label, len(labels))
        turns.append(
            DiarizationSegment(
                start_ms=draft.start_ms,
                end_ms=draft.end_ms,
                speaker=speaker,
            )
        )
    return turns


def _wave_duration_ms(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as audio:
            return round(audio.getnframes() / audio.getframerate() * 1000)
    except (OSError, wave.Error, ZeroDivisionError):
        return 0


def _seconds(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _configure_torch_cpu_threads(requested_threads: int) -> None:
    """Apply the VibeVoice profile's CPU budget to PyTorch offload work."""

    if requested_threads < 1 or not _module_available("torch"):
        return
    torch = importlib.import_module("torch")
    current_threads = int(torch.get_num_threads())
    if current_threads == requested_threads:
        return
    torch.set_num_threads(requested_threads)
    logger.info(
        "Configured PyTorch with %s CPU threads for VibeVoice ASR offload",
        requested_threads,
    )


def _release_torch_memory() -> None:
    gc.collect()
    if not _module_available("torch"):
        return
    try:
        torch = importlib.import_module("torch")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Could not release the optional PyTorch CUDA cache", exc_info=True)


def _ensure_vibevoice_python_runtime() -> None:
    if _vibevoice_runtime_available():
        return
    requirements = (
        "torch>=2.6,<3",
        "transformers>=5.3,<6",
        "accelerate>=1.2,<2",
        "librosa>=0.10,<1",
    )
    logger.info("Installing the optional Microsoft VibeVoice ASR Python runtime")
    process = subprocess.Popen(
        [sys.executable, "-m", "pip", "install", *requirements],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.strip()
        if message:
            logger.info("VibeVoice runtime · %s", message)
    return_code = process.wait()
    if return_code:
        raise CapabilityUnavailableError(
            f"The VibeVoice Python runtime installation failed with code {return_code}"
        )
    importlib.invalidate_caches()


def _run_logged(command: list[str], label: str) -> None:
    logger.info("%s · %s", label, " ".join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.strip()
        if message:
            logger.info("%s · %s", label, message)
    return_code = process.wait()
    if return_code:
        raise CapabilityUnavailableError(f"{label} failed with exit code {return_code}")


def _vibevoice_runtime_available() -> bool:
    if not _module_available("torch") or not _module_available("transformers"):
        return False
    try:
        raw_version = importlib.metadata.version("transformers")
        major, minor, *_rest = (int(part) for part in re.findall(r"\d+", raw_version))
        return (major, minor) >= (5, 3)
    except (ValueError, importlib.metadata.PackageNotFoundError):
        return False
