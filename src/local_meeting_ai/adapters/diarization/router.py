"""Routing for selectable local diarization engines."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    DiarizationEngine,
    ProgressReporter,
)
from local_meeting_ai.plugins.providers import ProviderRegistry


class DiarizationEngineRouter:
    """Select an isolated diarizer from the persisted diarization settings."""

    name = "diarization-router"

    def __init__(
        self,
        engines: dict[str, DiarizationEngine] | ProviderRegistry,
        *,
        primary: str = "sherpa-onnx",
    ) -> None:
        if isinstance(engines, dict) and not engines:
            raise ValueError("At least one diarization engine is required")
        self.registry = engines if isinstance(engines, ProviderRegistry) else None
        self.engines = (
            {}
            if self.registry is not None
            else dict(cast(dict[str, DiarizationEngine], engines))
        )
        available = self._engine_ids()
        if not available:
            raise ValueError("At least one diarization engine is required")
        self.primary = primary if primary in available else available[0]

    def capability(self) -> dict[str, Any]:
        capabilities = {
            engine_id: (
                self.registry.capability("diarization", engine_id)
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
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        engine_id = str(config.get("engine") or self.primary)
        await self._engine(engine_id).prepare(
            self._provider_config(engine_id, config),
            allow_model_download=allow_model_download,
        )

    async def uninstall(self, engine_id: str) -> None:
        await self._engine(engine_id).uninstall(engine_id)

    async def diarize(
        self,
        audio_path: Path,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]:
        engine_id = str(config.get("engine") or self.primary)
        return await self._engine(engine_id).diarize(
            audio_path,
            self._provider_config(engine_id, config),
            progress,
            is_cancelled,
        )

    def unload(self) -> None:
        for engine in self._engines():
            engine.unload()

    def shutdown(self) -> None:
        for engine in self._engines():
            engine.shutdown()

    def _engine_from_config(self, config: dict[str, Any]) -> DiarizationEngine:
        return self._engine(str(config.get("engine") or self.primary))

    def _engine(self, engine_id: str) -> DiarizationEngine:
        engine = (
            self.registry.resolve("diarization", engine_id)
            if self.registry is not None
            else self.engines.get(engine_id)
        )
        if engine is None:
            raise CapabilityUnavailableError(
                f"The diarization engine '{engine_id}' is not registered"
            )
        return engine

    def _engine_ids(self) -> list[str]:
        if self.registry is not None:
            return [
                item.descriptor.id
                for item in self.registry.registrations("diarization")
            ]
        return list(self.engines)

    def _engines(self) -> list[DiarizationEngine]:
        return [self._engine(engine_id) for engine_id in self._engine_ids()]

    def _provider_config(
        self,
        engine_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if self.registry is None:
            return config
        return self.registry.configuration("diarization", engine_id, config)
