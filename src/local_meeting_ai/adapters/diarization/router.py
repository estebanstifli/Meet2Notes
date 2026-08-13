"""Routing for selectable local diarization engines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import DiarizationSegment
from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    DiarizationEngine,
    ProgressReporter,
)


class DiarizationEngineRouter:
    """Select an isolated diarizer from the persisted diarization settings."""

    name = "diarization-router"

    def __init__(
        self,
        engines: dict[str, DiarizationEngine],
        *,
        primary: str = "sherpa-onnx",
    ) -> None:
        if not engines:
            raise ValueError("At least one diarization engine is required")
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
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        await self._engine_from_config(config).prepare(
            config,
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
        return await self._engine_from_config(config).diarize(
            audio_path,
            config,
            progress,
            is_cancelled,
        )

    def unload(self) -> None:
        for engine in self.engines.values():
            engine.unload()

    def shutdown(self) -> None:
        for engine in self.engines.values():
            engine.shutdown()

    def _engine_from_config(self, config: dict[str, Any]) -> DiarizationEngine:
        return self._engine(str(config.get("engine") or self.primary))

    def _engine(self, engine_id: str) -> DiarizationEngine:
        engine = self.engines.get(engine_id)
        if engine is None:
            raise CapabilityUnavailableError(
                f"The diarization engine '{engine_id}' is not registered"
            )
        return engine
