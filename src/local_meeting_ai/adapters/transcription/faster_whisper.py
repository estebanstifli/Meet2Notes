from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.application.transcription_config import (
    COMPUTE_TYPES,
    FASTER_WHISPER_MODEL_REPOSITORIES,
    WHISPER_LANGUAGE_CODES,
)
from local_meeting_ai.domain.entities import (
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


class FasterWhisperEngine:
    """Resident Faster Whisper engine isolated in its own executor."""

    name = "faster-whisper"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir
        # Query CTranslate2 before the inference executor starts. Some CUDA
        # runtime builds are not safe when hardware discovery races model
        # construction in another native thread.
        self._runtime_capability = _detect_runtime_capability()
        self._models: dict[tuple[Any, ...], Any] = {}
        self._model_slots: dict[tuple[Any, ...], threading.BoundedSemaphore] = {}
        self._model_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._resident_models: tuple[str, ...] = ()
        self._state = "idle"
        self._last_error: str | None = None
        self._active_requests = 0
        self._shutdown = False
        self._executor = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="faster-whisper",
        )

    def capability(self) -> dict[str, Any]:
        available = bool(self._runtime_capability["available"])
        cuda_devices = int(self._runtime_capability["cuda_devices"])
        ctranslate_version = self._runtime_capability["ctranslate2_version"]
        compute_types = self._runtime_capability["supported_compute_types"]
        with self._state_lock:
            worker_state = self._state
            active_requests = self._active_requests
            last_error = self._last_error
        # This snapshot is updated atomically by the model worker. Capability
        # checks must never wait for a multi-second model load.
        loaded_models = list(self._resident_models)
        return {
            "engine": self.name,
            "display_name": "Faster Whisper",
            "kind": "local",
            "available": available,
            "install_command": 'python -m pip install -e ".[transcription]"',
            "ctranslate2_version": ctranslate_version,
            "cuda_available": cuda_devices > 0,
            "cuda_devices": cuda_devices,
            "supported_devices": [
                {"id": "auto", "available": True},
                {"id": "cpu", "available": True},
                {"id": "cuda", "available": cuda_devices > 0},
            ],
            "unsupported_backends": {
                "vulkan": "CTranslate2 does not provide a Vulkan backend.",
                "metal": "CTranslate2 uses Apple CPUs but has no Metal backend.",
                "directml": "CTranslate2 does not provide a DirectML backend.",
                "rocm": (
                    "HIP/ROCm requires a custom CTranslate2 build and is not "
                    "a separate device in standard Python wheels."
                ),
            },
            "compute_types": list(COMPUTE_TYPES),
            "supported_compute_types": compute_types,
            "recommended_device": "cuda" if cuda_devices else "cpu",
            "recommended_compute_type": "float16" if cuda_devices else "int8",
            "languages": list(WHISPER_LANGUAGE_CODES),
            "installed_models": self._installed_models(),
            "loaded_models": loaded_models,
            "models_directory": str(self.models_dir),
            "worker": {
                "dedicated": True,
                "thread_prefix": "faster-whisper",
                "dispatcher_threads": 4,
                "state": worker_state,
                "active_requests": active_requests,
                "model_resident": bool(loaded_models),
                "last_error": last_error,
            },
        }

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None:
        await self._submit(
            self._prepare_sync,
            profile,
            allow_model_download,
        )

    async def uninstall(self, profile: ModelProfile) -> None:
        await self._submit(self._uninstall_model_sync, profile.model)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        if importlib.util.find_spec("faster_whisper") is None:
            raise CapabilityUnavailableError(
                'Faster Whisper is not installed. Run: python -m pip install -e ".[transcription]"'
            )
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

    def shutdown(self) -> None:
        with self._state_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._state = "stopping"
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._model_lock:
            self._models.clear()
            self._model_slots.clear()
            self._resident_models = ()
        gc.collect()
        with self._state_lock:
            self._state = "stopped"

    def unload(self) -> None:
        with self._model_lock:
            self._models.clear()
            self._model_slots.clear()
            self._resident_models = ()
        gc.collect()
        with self._state_lock:
            if not self._active_requests and not self._shutdown:
                self._state = "idle"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError(
                    "The Faster Whisper worker is shutting down"
                )
        future = self._executor.submit(function, *args)
        return await asyncio.wrap_future(future)

    def _prepare_sync(
        self,
        profile: ModelProfile,
        allow_model_download: bool,
    ) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            model_class = self._model_class()
            self._get_model(
                model_class,
                model=profile.model,
                device=profile.device,
                device_index=profile.device_index,
                compute_type=profile.compute_type,
                cpu_threads=profile.cpu_threads,
                num_workers=profile.num_workers,
                allow_model_download=allow_model_download,
            )
        except Exception as error:
            failure = error
            raise
        finally:
            self._request_finished(failure)

    def _transcribe_sync(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        self._request_started("inferencing")
        model_key: tuple[Any, ...] | None = None
        failure: Exception | None = None
        try:
            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            model_class = self._model_class()
            progress(0.02, f"Loading the {request.model} model")
            try:
                model, slots, model_key = self._get_model(
                    model_class,
                    model=request.model,
                    device=request.device,
                    device_index=request.device_index,
                    compute_type=request.compute_type,
                    cpu_threads=request.cpu_threads,
                    num_workers=request.num_workers,
                    allow_model_download=request.allow_model_download,
                )
            except Exception as error:
                message = str(error)
                if not request.allow_model_download:
                    raise CapabilityUnavailableError(
                        f"The {request.model} model is not installed. "
                        "Start again and explicitly allow the model download."
                    ) from error
                raise CapabilityUnavailableError(
                    f"Could not load the Whisper model: {message}"
                ) from error

            if is_cancelled():
                raise JobCancelledError("Transcription was cancelled")
            with slots:
                raw_segments, info = model.transcribe(
                    str(request.audio_path),
                    language=request.language,
                    task=request.task,
                    beam_size=request.beam_size,
                    vad_filter=request.vad_filter,
                    vad_parameters=(
                        {
                            "min_silence_duration_ms": (
                                request.vad_min_silence_ms
                            )
                        }
                        if request.vad_filter
                        else None
                    ),
                    word_timestamps=request.word_timestamps,
                    condition_on_previous_text=request.condition_on_previous_text,
                )
                duration = float(getattr(info, "duration", 0.0) or 0.0)
                drafts: list[SegmentDraft] = []
                for index, segment in enumerate(raw_segments):
                    if is_cancelled():
                        raise JobCancelledError("Transcription was cancelled")
                    text = str(segment.text).strip()
                    if not text:
                        continue
                    start = max(0.0, float(segment.start))
                    end = max(start, float(segment.end))
                    average_log_probability = _optional_float(
                        getattr(segment, "avg_logprob", None)
                    )
                    confidence = (
                        max(0.0, min(1.0, math.exp(average_log_probability)))
                        if average_log_probability is not None
                        else None
                    )
                    draft = SegmentDraft(
                        index=index,
                        start_ms=round(start * 1000),
                        end_ms=round(end * 1000),
                        text=text,
                        confidence=confidence,
                        metadata={
                            "avg_logprob": average_log_probability,
                            "no_speech_prob": _optional_float(
                                getattr(segment, "no_speech_prob", None)
                            ),
                            "words": _serialize_words(
                                getattr(segment, "words", None)
                            ),
                        },
                    )
                    drafts.append(draft)
                    segment_ready(draft)
                    fraction = (
                        end / duration
                        if duration > 0
                        else min(0.95, (index + 1) / 100)
                    )
                    progress(
                        min(0.98, max(0.04, fraction)),
                        f"Transcribed {len(drafts)} segments",
                    )

            return TranscriptionResult(
                language=getattr(info, "language", request.language),
                language_probability=_optional_float(
                    getattr(info, "language_probability", None)
                ),
                duration_ms=round(duration * 1000) if duration > 0 else None,
                segments=drafts,
            )
        except Exception as error:
            failure = error
            raise
        finally:
            if model_key is not None and not request.keep_model_loaded:
                self._unload_model(model_key)
            self._request_finished(failure)

    def _get_model(
        self,
        model_class: Any,
        *,
        model: str,
        device: str,
        device_index: int,
        compute_type: str,
        cpu_threads: int,
        num_workers: int,
        allow_model_download: bool,
    ) -> tuple[Any, threading.BoundedSemaphore, tuple[Any, ...]]:
        key = (
            model,
            device,
            device_index,
            compute_type,
            cpu_threads,
            num_workers,
        )
        with self._model_lock:
            cached = self._models.get(key)
            if cached is not None:
                logger.debug("Reusing resident Faster Whisper model %s", model)
                return cached, self._model_slots[key], key
            self._models.clear()
            self._model_slots.clear()
            logger.info(
                "%s Faster Whisper model %s on %s with %s compute (model directory: %s)",
                "Downloading and loading" if allow_model_download else "Loading",
                model,
                device,
                compute_type,
                self.models_dir,
            )
            model_instance = model_class(
                model,
                device=device,
                device_index=device_index,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
                download_root=str(self.models_dir),
                local_files_only=not allow_model_download,
            )
            self._models[key] = model_instance
            self._model_slots[key] = threading.BoundedSemaphore(num_workers)
            self._resident_models = (model,)
            logger.info("Faster Whisper model %s is resident in memory", model)
            return model_instance, self._model_slots[key], key

    def _unload_model(self, key: tuple[Any, ...]) -> None:
        with self._model_lock:
            self._models.pop(key, None)
            self._model_slots.pop(key, None)
            self._resident_models = tuple(
                sorted({str(model_key[0]) for model_key in self._models})
            )
        gc.collect()

    def _uninstall_model_sync(self, model: str) -> None:
        with self._state_lock:
            if self._active_requests:
                raise CapabilityUnavailableError(
                    "Wait for the active Faster Whisper task to finish before uninstalling a model"
                )
        repository = FASTER_WHISPER_MODEL_REPOSITORIES.get(model)
        if repository is None:
            raise CapabilityUnavailableError(f"Unknown Faster Whisper model '{model}'")
        self.unload()
        removed = False
        for candidate in (
            self.models_dir / f"models--{repository.replace('/', '--')}",
            self.models_dir / model,
        ):
            removed = remove_managed_model_tree(
                root=self.models_dir,
                target=candidate,
                label=f"Faster Whisper {model}",
            ) or removed
        if not removed:
            raise CapabilityUnavailableError(
                f"The Faster Whisper {model} files are not installed locally"
            )
        logger.info("Removed Faster Whisper model %s from %s", model, self.models_dir)

    def _request_started(self, state: str) -> None:
        with self._state_lock:
            self._active_requests += 1
            self._state = state
            self._last_error = None

    def _request_finished(self, failure: Exception | None = None) -> None:
        with self._state_lock:
            self._active_requests = max(0, self._active_requests - 1)
            if failure is not None:
                self._last_error = str(failure)
            if self._active_requests == 0:
                self._state = (
                    "error"
                    if failure is not None
                    else ("ready" if self._resident_models else "idle")
                )

    @staticmethod
    def _model_class() -> Any:
        if importlib.util.find_spec("faster_whisper") is None:
            raise CapabilityUnavailableError(
                'Faster Whisper is not installed. Run: python -m pip install -e ".[transcription]"'
            )
        return importlib.import_module("faster_whisper").WhisperModel

    def _installed_models(self) -> list[str]:
        if not self.models_dir.exists():
            return []
        installed: set[str] = set()
        for name, repository in FASTER_WHISPER_MODEL_REPOSITORIES.items():
            cache_name = f"models--{repository.replace('/', '--')}"
            if (self.models_dir / cache_name).is_dir():
                installed.add(name)
            candidate = self.models_dir / name
            if candidate.is_dir() and any(candidate.iterdir()):
                installed.add(name)
        return sorted(installed)


def _serialize_words(words: Any) -> list[dict[str, Any]] | None:
    if not words:
        return None
    return [
        {
            "start": _optional_float(getattr(word, "start", None)),
            "end": _optional_float(getattr(word, "end", None)),
            "word": str(getattr(word, "word", "")),
            "probability": _optional_float(getattr(word, "probability", None)),
        }
        for word in words
    ]


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _detect_runtime_capability() -> dict[str, Any]:
    available = importlib.util.find_spec("faster_whisper") is not None
    cuda_devices = 0
    ctranslate_version: str | None = None
    compute_types: dict[str, list[str]] = {"cpu": [], "cuda": []}
    if available and importlib.util.find_spec("ctranslate2") is not None:
        try:
            ctranslate2 = importlib.import_module("ctranslate2")
            ctranslate_version = str(
                getattr(ctranslate2, "__version__", "installed")
            )
            cuda_devices = int(ctranslate2.get_cuda_device_count())
            compute_types["cpu"] = sorted(
                ctranslate2.get_supported_compute_types("cpu")
            )
            if cuda_devices:
                compute_types["cuda"] = sorted(
                    ctranslate2.get_supported_compute_types("cuda")
                )
        except (AttributeError, ImportError, RuntimeError, OSError):
            cuda_devices = 0
    return {
        "available": available,
        "cuda_devices": cuda_devices,
        "ctranslate2_version": ctranslate_version,
        "supported_compute_types": compute_types,
    }
