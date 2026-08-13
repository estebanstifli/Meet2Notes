"""Reusable matching of saved Speaker WAV profiles to diarization turns.

This component deliberately has no dependency on the selected diarization
engine.  Sherpa-ONNX supplies the compact local embedding model, while Sherpa,
Pyannote and diarize may each produce the turns to be matched.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import threading
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError

from .sherpa_onnx import EMBEDDING_FILES, EMBEDDING_URLS, _configure_cuda_dlls, _download

logger = logging.getLogger(__name__)


class SherpaOnnxSpeakerProfileMatcher:
    """Dedicated voiceprint matcher shared by every local diarization engine."""

    name = "sherpa-onnx-speaker-profiles"

    def __init__(self, models_dir: Path) -> None:
        # Keep using the original Sherpa model location so existing installs do
        # not redownload the 3D-Speaker embedding checkpoint.
        self.models_dir = models_dir / "diarization" / "sherpa-onnx"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="speaker-profile-matcher",
        )
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._active_requests = 0
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self) -> dict[str, Any]:
        dependency = importlib.util.find_spec("sherpa_onnx") is not None
        with self._state_lock:
            state = self._state
            active = self._active_requests
            error = self._last_error
        return {
            "engine": self.name,
            "display_name": "Saved speaker profile matcher",
            "available": dependency,
            "installed": self._model_path({}).is_file(),
            "models_directory": str(self.models_dir),
            "worker": {
                "dedicated": True,
                "thread_prefix": "speaker-profile-matcher",
                "dispatcher_threads": 1,
                "state": state,
                "active_requests": active,
                "model_resident": False,
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

    async def match(
        self,
        audio_path: Path,
        turns: list[DiarizationSegment],
        profiles: list[Any],
        config: dict[str, Any],
    ) -> dict[int, Any]:
        return cast(
            dict[int, Any],
            await self._submit(self._match_sync, audio_path, turns, profiles, config),
        )

    def unload(self) -> None:
        # Extractors are created for one matching pass and released immediately.
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
        with self._state_lock:
            self._state = "stopped"

    async def _submit(self, function: Any, *args: Any) -> Any:
        with self._state_lock:
            if self._shutdown:
                raise CapabilityUnavailableError("The saved speaker matcher is shutting down")
        return await asyncio.wrap_future(self._executor.submit(function, *args))

    def _prepare_sync(self, config: dict[str, Any], allow_model_download: bool) -> None:
        self._request_started("loading")
        failure: Exception | None = None
        try:
            if importlib.util.find_spec("sherpa_onnx") is None:
                raise CapabilityUnavailableError(
                    'sherpa-onnx is required for saved speaker matching. Run: '
                    'python -m pip install -e ".[diarization]"'
                )
            model = self._model_path(config)
            if allow_model_download and not model.is_file():
                logger.info("Downloading saved-speaker embedding model %s", model.name)
                _download(
                    EMBEDDING_URLS[self._embedding_name(config)],
                    model,
                )
            if not model.is_file():
                raise CapabilityUnavailableError(
                    "The saved-speaker embedding model is not installed. "
                    "Install the selected diarization engine in Settings."
                )
        except Exception as error:
            failure = error
            raise
        finally:
            self._request_finished(failure)

    def _match_sync(
        self,
        audio_path: Path,
        turns: list[DiarizationSegment],
        profiles: list[Any],
        config: dict[str, Any],
    ) -> dict[int, Any]:
        if not profiles or not turns:
            return {}
        self._request_started("matching voices")
        failure: Exception | None = None
        try:
            model = self._model_path(config)
            if not model.is_file():
                raise CapabilityUnavailableError(
                    "The saved-speaker embedding model is not installed"
                )
            sherpa = importlib.import_module("sherpa_onnx")
            provider = str(config.get("provider", "cpu"))
            if provider == "cuda":
                _configure_cuda_dlls()
            extractor = sherpa.SpeakerEmbeddingExtractor(
                sherpa.SpeakerEmbeddingExtractorConfig(
                    model=str(model),
                    num_threads=int(config.get("num_threads", 2)),
                    debug=bool(config.get("debug", False)),
                    provider=provider,
                )
            )
            manager = sherpa.SpeakerEmbeddingManager(extractor.dim)
            for profile in profiles:
                try:
                    sample, sample_rate = _read_pcm_wave(Path(profile.sample_path))
                    embedding = self._embedding(extractor, sample, sample_rate)
                    if embedding is not None:
                        manager.add(str(profile.id), embedding)
                except Exception as error:
                    logger.warning("Skipping saved voice %s: %s", profile.id, error)
            samples, sample_rate = _read_pcm_wave(audio_path)
            grouped: dict[int, array[int]] = {}
            for turn in turns:
                start = max(0, round(turn.start_ms * sample_rate / 1000))
                end = min(len(samples), round(turn.end_ms * sample_rate / 1000))
                if end > start:
                    grouped.setdefault(turn.speaker, array("h")).extend(samples[start:end])
            threshold = float(config.get("profile_match_threshold", 0.72))
            by_id = {str(profile.id): profile for profile in profiles}
            matches: dict[int, Any] = {}
            for speaker, voice in grouped.items():
                embedding = self._embedding(extractor, voice, sample_rate)
                if embedding is None:
                    continue
                match = manager.search(embedding, threshold)
                if match and match in by_id:
                    matches[speaker] = by_id[match]
            return matches
        except Exception as error:
            failure = error
            logger.warning("Saved voice matching skipped: %s", error)
            return {}
        finally:
            self._request_finished(failure)

    def _model_path(self, config: dict[str, Any]) -> Path:
        return self.models_dir / EMBEDDING_FILES[self._embedding_name(config)]

    @staticmethod
    def _embedding_name(config: dict[str, Any]) -> str:
        requested = str(config.get("embedding_model", "3d-speaker"))
        return requested if requested in EMBEDDING_FILES else "3d-speaker"

    @staticmethod
    def _embedding(
        extractor: Any, samples: array[int], sample_rate: int
    ) -> Any | None:
        stream = extractor.create_stream()
        numpy = importlib.import_module("numpy")
        stream.accept_waveform(
            sample_rate,
            numpy.asarray(samples, dtype=numpy.float32) / 32768.0,
        )
        stream.input_finished()
        if not extractor.is_ready(stream):
            return None
        return extractor.compute(stream)

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
                self._state = "error" if failure is not None else "idle"


def _read_pcm_wave(path: Path) -> tuple[array[int], int]:
    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise CapabilityUnavailableError(
                "Saved-speaker matching requires 16-bit mono PCM WAV audio"
            )
        samples = array("h")
        samples.frombytes(source.readframes(source.getnframes()))
        return samples, source.getframerate()
