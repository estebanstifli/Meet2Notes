from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    AudioFrameBatch,
    CapturedAudio,
    CaptureStatus,
    DiarizationSegment,
    MediaProbe,
    ModelProfile,
    SegmentDraft,
    SummaryResult,
    TranscriptionEngineRequest,
    TranscriptionResult,
)


class AsyncUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...

    async def close(self) -> None: ...


class MediaProbeClient(Protocol):
    async def probe_media(self, path: Path) -> MediaProbe: ...


class AudioNormalizer(Protocol):
    async def normalize_for_transcription(
        self,
        source: Path,
        destination: Path,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        is_cancelled: CancellationCheck | None = None,
    ) -> MediaProbe: ...


class ProgressReporter(Protocol):
    def __call__(self, progress: float, message: str) -> None: ...


class CancellationCheck(Protocol):
    def __call__(self) -> bool: ...


class SegmentReporter(Protocol):
    def __call__(self, segment: SegmentDraft) -> None: ...


class TranscriptionEngine(Protocol):
    name: str

    def capability(self) -> dict[str, Any]: ...

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None: ...

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult: ...

    def unload(self) -> None: ...

    def shutdown(self) -> None: ...


class AudioCaptureBackend(Protocol):
    name: str

    def capability(self) -> dict[str, Any]: ...

    def list_sources(self) -> list[AudioCaptureSource]: ...

    def probe_level(self, source_id: str) -> float: ...

    def start(
        self,
        *,
        session_id: str,
        source_id: str,
        destination: Path,
    ) -> CaptureStatus: ...

    def status(self) -> CaptureStatus | None: ...

    def drain_frames(self) -> AudioFrameBatch | None: ...

    def pause(self) -> CaptureStatus: ...

    def resume(self) -> CaptureStatus: ...

    def stop(self) -> CapturedAudio: ...

    def shutdown(self) -> None: ...


class DiarizationEngine(Protocol):
    name: str

    def capability(self) -> dict[str, Any]: ...

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None: ...

    async def diarize(
        self,
        audio_path: Path,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]: ...

    def unload(self) -> None: ...

    def shutdown(self) -> None: ...


class SummaryEngine(Protocol):
    name: str

    def capability(self) -> dict[str, Any]: ...

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None: ...

    async def summarize(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> SummaryResult: ...

    def unload(self) -> None: ...

    def shutdown(self) -> None: ...
