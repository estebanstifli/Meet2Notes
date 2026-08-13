from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import logging
import os
import shutil
import sys
import tarfile
import threading
import urllib.request
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.adapters.model_files import remove_managed_model_tree
from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    JobCancelledError,
)
from local_meeting_ai.domain.protocols import CancellationCheck, ProgressReporter

logger = logging.getLogger(__name__)
_CUDA_DLL_HANDLES: list[Any] = []

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING_URLS = {
    "3d-speaker": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/"
        "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
    ),
    "nemo-titanet": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
        "speaker-recongition-models/nemo_en_titanet_small.onnx"
    ),
}
EMBEDDING_FILES = {
    "3d-speaker": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
    "nemo-titanet": "nemo_en_titanet_small.onnx",
}


class SherpaOnnxDiarizationEngine:
    """Resident sherpa-onnx diarizer isolated from transcription and the API."""

    name = "sherpa-onnx"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir / "diarization" / "sherpa-onnx"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="sherpa-diarization",
        )
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._diarizer: Any | None = None
        self._model_key: tuple[Any, ...] | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._active_requests = 0
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        dependency = importlib.util.find_spec("sherpa_onnx") is not None
        with self._state_lock:
            state = self._state
            error = self._last_error
            active = self._active_requests
        return {
            "engine": self.name,
            "display_name": "sherpa-onnx",
            "available": dependency,
            "installed": self._models_installed(),
            "install_command": 'python -m pip install -e ".[diarization]"',
            "models_directory": str(self.models_dir),
            "supported_providers": ["cpu", "cuda", "coreml"],
            "worker": {
                "dedicated": True,
                "thread_prefix": "sherpa-diarization",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": self._diarizer is not None,
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
        await self._submit(self._uninstall_models_sync)

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
        with self._lock:
            self._diarizer = None
            self._model_key = None
        gc.collect()
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
                raise CapabilityUnavailableError(
                    "The diarization worker is shutting down"
                )
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(
        self,
        config: dict[str, Any],
        allow_model_download: bool,
    ) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if allow_model_download:
                self._download_models(config)
            self._get_diarizer(config)
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
            diarizer = self._get_diarizer(config)
            samples, sample_rate = _read_pcm_wave(audio_path)
            if sample_rate != int(diarizer.sample_rate):
                raise CapabilityUnavailableError(
                    f"Diarization expects {diarizer.sample_rate} Hz mono PCM; "
                    f"received {sample_rate} Hz"
                )
            numpy = importlib.import_module("numpy")
            audio = numpy.asarray(samples, dtype=numpy.float32) / 32768.0

            def callback(processed: int, total: int) -> int:
                if is_cancelled():
                    return 1
                progress(
                    min(0.98, processed / max(1, total)),
                    f"Analyzed {processed} of {total} speaker windows",
                )
                return 0

            raw = diarizer.process(audio, callback=callback).sort_by_start_time()
            if is_cancelled():
                raise JobCancelledError("Diarization was cancelled")
            labels = {
                speaker: index
                for index, speaker in enumerate(
                    sorted({int(item.speaker) for item in raw})
                )
            }
            segments = [
                DiarizationSegment(
                    start_ms=round(float(item.start) * 1000),
                    end_ms=round(float(item.end) * 1000),
                    speaker=labels[int(item.speaker)],
                )
                for item in raw
            ]
            durations = {
                speaker: sum(
                    item.end_ms - item.start_ms
                    for item in segments
                    if item.speaker == speaker
                )
                for speaker in labels.values()
            }
            logger.info(
                "Diarization completed: provider=%s threshold=%.2f requested_speakers=%d "
                "detected_speakers=%d turns=%d talk_time_ms=%s raw_labels=%s",
                config.get("provider", "cpu"),
                float(config.get("cluster_threshold", 0.7)),
                int(config.get("num_speakers", -1)),
                len(labels),
                len(segments),
                durations,
                sorted(labels),
            )
            return segments
        except Exception as error:
            failure = error
            raise
        finally:
            if not bool(config.get("keep_model_loaded", True)):
                self.unload()
            self._request_finished(failure)

    def _get_diarizer(self, config: dict[str, Any]) -> Any:
        if importlib.util.find_spec("sherpa_onnx") is None:
            raise CapabilityUnavailableError(
                'sherpa-onnx is not installed. Run: python -m pip install -e ".[diarization]"'
            )
        segmentation, embedding = self._model_paths(config)
        if not segmentation.is_file() or not embedding.is_file():
            raise CapabilityUnavailableError(
                "The sherpa-onnx diarization models are not installed. "
                "Use the explicit model installation action in Settings."
            )
        key = (
            str(segmentation),
            str(embedding),
            config.get("provider", "cpu"),
            int(config.get("num_threads", 2)),
            int(config.get("num_speakers", -1)),
            float(config.get("cluster_threshold", 0.7)),
            float(config.get("min_duration_on", 0.3)),
            float(config.get("min_duration_off", 0.5)),
            bool(config.get("debug", False)),
        )
        with self._lock:
            if self._diarizer is not None and self._model_key == key:
                return self._diarizer
            sherpa = importlib.import_module("sherpa_onnx")
            provider = str(config.get("provider", "cpu"))
            if provider == "cuda":
                _configure_cuda_dlls()
            threads = int(config.get("num_threads", 2))
            debug = bool(config.get("debug", False))
            resolved = sherpa.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(segmentation)
                    ),
                    num_threads=threads,
                    debug=debug,
                    provider=provider,
                ),
                embedding=sherpa.SpeakerEmbeddingExtractorConfig(
                    model=str(embedding),
                    num_threads=threads,
                    debug=debug,
                    provider=provider,
                ),
                clustering=sherpa.FastClusteringConfig(
                    num_clusters=int(config.get("num_speakers", -1)),
                    threshold=float(config.get("cluster_threshold", 0.7)),
                ),
                min_duration_on=float(config.get("min_duration_on", 0.3)),
                min_duration_off=float(config.get("min_duration_off", 0.5)),
            )
            if not resolved.validate():
                raise CapabilityUnavailableError(
                    "sherpa-onnx rejected the diarization configuration"
                )
            self._diarizer = sherpa.OfflineSpeakerDiarization(resolved)
            self._model_key = key
            return self._diarizer

    def _download_models(self, config: dict[str, Any]) -> None:
        segmentation, embedding = self._model_paths(config)
        if not segmentation.is_file():
            archive = self.models_dir / "segmentation.tar.bz2"
            logger.info("Downloading diarization archive %s", archive.name)
            _download(SEGMENTATION_URL, archive)
            logger.info("Extracting segmentation model files from %s", archive.name)
            _safe_extract(archive, self.models_dir)
            archive.unlink(missing_ok=True)
        if not embedding.is_file():
            logger.info("Downloading speaker embedding file %s", embedding.name)
            _download(
                EMBEDDING_URLS[str(config.get("embedding_model", "3d-speaker"))],
                embedding,
            )

    def _model_paths(self, config: dict[str, Any]) -> tuple[Path, Path]:
        quantized = bool(config.get("quantized_segmentation", True))
        segmentation = (
            self.models_dir
            / "sherpa-onnx-pyannote-segmentation-3-0"
            / ("model.int8.onnx" if quantized else "model.onnx")
        )
        embedding_name = str(config.get("embedding_model", "3d-speaker"))
        return segmentation, self.models_dir / EMBEDDING_FILES[embedding_name]

    def _models_installed(self) -> bool:
        default = {
            "quantized_segmentation": True,
            "embedding_model": "3d-speaker",
        }
        segmentation, embedding = self._model_paths(default)
        return segmentation.is_file() and embedding.is_file()

    def _uninstall_models_sync(self) -> None:
        with self._state_lock:
            if self._active_requests:
                raise CapabilityUnavailableError(
                    "Wait for the active Sherpa-ONNX task to finish before uninstalling it"
                )
        if not self._models_installed():
            raise CapabilityUnavailableError(
                "Sherpa-ONNX diarization models are not installed locally"
            )
        self.unload()
        removed = remove_managed_model_tree(
            root=self.models_dir.parent,
            target=self.models_dir,
            label="Sherpa-ONNX diarization",
        )
        if not removed:
            raise CapabilityUnavailableError(
                "Sherpa-ONNX diarization models are not installed locally"
            )
        logger.info("Removed Sherpa-ONNX diarization models from %s", self.models_dir)

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
                    else ("ready" if self._diarizer is not None else "idle")
                )


def _configure_cuda_dlls() -> None:
    """Expose pip-installed NVIDIA runtime DLLs to ONNX Runtime on Windows."""
    if sys.platform != "win32" or _CUDA_DLL_HANDLES:
        return
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    directories = [
        nvidia_root / package / "bin"
        for package in ("cudnn", "cublas", "cuda_nvrtc")
        if (nvidia_root / package / "bin").is_dir()
    ]
    if not directories:
        return
    os.environ["PATH"] = os.pathsep.join(
        [*(str(directory) for directory in directories), os.environ.get("PATH", "")]
    )
    _CUDA_DLL_HANDLES.extend(
        os.add_dll_directory(str(directory)) for directory in directories
    )


def _read_pcm_wave(path: Path) -> tuple[array[int], int]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise CapabilityUnavailableError(
                "Diarization requires 16-bit mono PCM audio"
            )
        samples = array("h")
        samples.frombytes(source.readframes(source.getnframes()))
        return samples, source.getframerate()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        logger.info("Fetching %s", url)
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as target:
            shutil.copyfileobj(response, target)
        partial.replace(destination)
        logger.info("Saved %s", destination)
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise CapabilityUnavailableError(
            f"Could not download the diarization model: {error}"
        ) from error


def _safe_extract(archive: Path, destination: Path) -> None:
    resolved_destination = destination.resolve()
    with tarfile.open(archive, "r:bz2") as package:
        for member in package.getmembers():
            target = (destination / member.name).resolve()
            if (
                not target.is_relative_to(resolved_destination)
                or member.issym()
                or member.islnk()
            ):
                raise CapabilityUnavailableError(
                    "The diarization model archive contains an unsafe path"
                )
        package.extractall(destination)
