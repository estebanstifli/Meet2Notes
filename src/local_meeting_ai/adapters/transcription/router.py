from __future__ import annotations

from typing import Any

from local_meeting_ai.domain.entities import (
    ModelProfile,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SegmentReporter,
    TranscriptionEngine,
)


class TranscriptionEngineRouter:
    """Route each request to an isolated transcription provider worker."""

    name = "transcription-router"

    def __init__(
        self,
        engines: dict[str, TranscriptionEngine],
        *,
        primary: str = "faster-whisper",
    ) -> None:
        if not engines:
            raise ValueError("At least one transcription engine is required")
        self.engines = dict(engines)
        self.primary = primary if primary in engines else next(iter(engines))

    def capability(self) -> dict[str, Any]:
        capabilities = {
            engine_id: engine.capability() for engine_id, engine in self.engines.items()
        }
        primary = dict(capabilities[self.primary])
        primary["engine"] = self.name
        primary["primary_engine"] = self.primary
        primary["engines"] = capabilities
        return primary

    def capability_for(self, engine_id: str) -> dict[str, Any]:
        return self._engine(engine_id).capability()

    async def prepare(
        self,
        profile: ModelProfile,
        *,
        allow_model_download: bool,
    ) -> None:
        await self._engine(profile.engine).prepare(
            profile,
            allow_model_download=allow_model_download,
        )

    async def uninstall(self, profile: ModelProfile) -> None:
        await self._engine(profile.engine).uninstall(profile)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        return await self._engine(request.engine).transcribe(
            request,
            progress,
            is_cancelled,
            segment_ready,
        )

    def unload(self) -> None:
        for engine in self.engines.values():
            engine.unload()

    def shutdown(self) -> None:
        for engine in self.engines.values():
            engine.shutdown()

    def _engine(self, engine_id: str) -> TranscriptionEngine:
        engine = self.engines.get(engine_id)
        if engine is None:
            raise CapabilityUnavailableError(
                f"The transcription engine '{engine_id}' is not registered"
            )
        return engine
