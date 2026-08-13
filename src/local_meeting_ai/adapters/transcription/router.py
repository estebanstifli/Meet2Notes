from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

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
from local_meeting_ai.plugins.providers import ProviderRegistry


class TranscriptionEngineRouter:
    """Route each request to an isolated transcription provider worker."""

    name = "transcription-router"

    def __init__(
        self,
        engines: dict[str, TranscriptionEngine] | ProviderRegistry,
        *,
        primary: str = "faster-whisper",
    ) -> None:
        if isinstance(engines, dict) and not engines:
            raise ValueError("At least one transcription engine is required")
        self.registry = engines if isinstance(engines, ProviderRegistry) else None
        self.engines = (
            {}
            if self.registry is not None
            else dict(cast(dict[str, TranscriptionEngine], engines))
        )
        available = self._engine_ids()
        if not available:
            raise ValueError("At least one transcription engine is required")
        self.primary = primary if primary in available else available[0]

    def capability(self) -> dict[str, Any]:
        capabilities = {
            engine_id: (
                self.registry.capability("transcription", engine_id)
                if self.registry is not None
                else self._engine(engine_id).capability()
            )
            for engine_id in self._engine_ids()
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
        routed_profile = profile
        if self.registry is not None:
            routed_profile = replace(
                profile,
                provider_options=self.registry.configuration(
                    "transcription",
                    profile.engine,
                    profile.provider_options,
                ),
            )
        await self._engine(profile.engine).prepare(
            routed_profile,
            allow_model_download=allow_model_download,
        )

    async def uninstall(self, profile: ModelProfile) -> None:
        routed_profile = profile
        if self.registry is not None:
            routed_profile = replace(
                profile,
                provider_options=self.registry.configuration(
                    "transcription",
                    profile.engine,
                    profile.provider_options,
                ),
            )
        await self._engine(profile.engine).uninstall(routed_profile)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        routed_request = request
        if self.registry is not None:
            config = self.registry.configuration(
                "transcription",
                request.engine,
                request.provider_options,
            )
            routed_request = replace(request, provider_options=config)
        return await self._engine(request.engine).transcribe(
            routed_request,
            progress,
            is_cancelled,
            segment_ready,
        )

    def unload(self) -> None:
        for engine in self._engines():
            engine.unload()

    def shutdown(self) -> None:
        for engine in self._engines():
            engine.shutdown()

    def _engine(self, engine_id: str) -> TranscriptionEngine:
        engine = (
            self.registry.resolve("transcription", engine_id)
            if self.registry is not None
            else self.engines.get(engine_id)
        )
        if engine is None:
            raise CapabilityUnavailableError(
                f"The transcription engine '{engine_id}' is not registered"
            )
        return engine

    def _engine_ids(self) -> list[str]:
        if self.registry is not None:
            return [
                item.descriptor.id
                for item in self.registry.registrations("transcription")
            ]
        return list(self.engines)

    def _engines(self) -> list[TranscriptionEngine]:
        return [self._engine(engine_id) for engine_id in self._engine_ids()]
