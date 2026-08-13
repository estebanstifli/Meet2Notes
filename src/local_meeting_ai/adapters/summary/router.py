from __future__ import annotations

from typing import Any, cast

from local_meeting_ai.domain.entities import SummaryResult
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SummaryEngine,
)
from local_meeting_ai.plugins.providers import ProviderRegistry


class SummaryEngineRouter:
    """Route summary requests to core or plugin providers selected in Settings."""

    name = "summary-router"

    def __init__(
        self,
        registry: ProviderRegistry,
        initial_config: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self._last_config = dict(initial_config or {})

    def capability(self) -> dict[str, Any]:
        capabilities: dict[str, dict[str, Any]] = {}
        models: list[dict[str, Any]] = []
        for registration in self.registry.registrations("summary"):
            provider_id = registration.descriptor.id
            capability = self.registry.capability("summary", provider_id)
            capabilities[provider_id] = capability
            for item in capability.get("models", []):
                if isinstance(item, dict):
                    models.append({**item, "engine": provider_id})
        known_ids = {str(item.get("id")) for item in models}
        for model_registration in self.registry.models("summary"):
            if model_registration.model.id in known_ids:
                continue
            capability = capabilities.get(model_registration.provider_id, {})
            installed_models = set(capability.get("installed_models", []))
            model = model_registration.model
            models.append(
                {
                    **model.model_dump(mode="json"),
                    "engine": model_registration.provider_id,
                    "provider": str(model.defaults.get("provider", "local")),
                    "repository": model.model,
                    "installed": (
                        model.model in installed_models
                        or bool(capability.get("installed"))
                        or not model.managed
                    ),
                    "runtime_available": bool(
                        capability.get("runtime_available", capability.get("available"))
                    ),
                }
            )
        selected_id = self._selected_engine(self._last_config)
        selected = capabilities.get(selected_id, {})
        return {
            **selected,
            "engine": self.name,
            "selected_engine": selected_id,
            "engines": capabilities,
            "models": models,
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        self._last_config = dict(config)
        engine_id = self._selected_engine(config)
        await self._engine(engine_id).prepare(
            self._provider_config(engine_id, config),
            allow_model_download=allow_model_download,
        )

    async def uninstall(self, profile_id: str) -> None:
        engine_id = self._engine_for_profile(profile_id)
        await self._engine(engine_id).uninstall(profile_id)

    async def summarize(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> SummaryResult:
        self._last_config = dict(config)
        engine_id = self._selected_engine(config)
        return await self._engine(engine_id).summarize(
            transcript,
            self._provider_config(engine_id, config),
            progress,
            is_cancelled,
        )

    def unload(self) -> None:
        for registration in self.registry.registrations("summary"):
            self._engine(registration.descriptor.id).unload()

    def shutdown(self) -> None:
        for registration in self.registry.registrations("summary"):
            self._engine(registration.descriptor.id).shutdown()

    def _selected_engine(self, config: dict[str, Any]) -> str:
        explicit = str(config.get("engine") or "").strip()
        if explicit:
            return explicit
        profile_id = str(config.get("profile_id") or "")
        try:
            return self.registry.provider_for_model("summary", profile_id).provider_id
        except Exception:
            return "llama-cpp"

    def _engine_for_profile(self, profile_id: str) -> str:
        try:
            return self.registry.provider_for_model("summary", profile_id).provider_id
        except Exception:
            return "llama-cpp"

    def _provider_config(
        self,
        engine_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = dict(config)
        profile_id = str(config.get("profile_id") or "")
        try:
            registration = self.registry.provider_for_model("summary", profile_id)
        except Exception:
            registration = None
        if registration is not None and registration.provider_id == engine_id:
            resolved.update(registration.model.defaults)
            resolved.setdefault("model", registration.model.model)
        resolved["engine"] = engine_id
        return self.registry.configuration("summary", engine_id, resolved)

    def _engine(self, engine_id: str) -> SummaryEngine:
        return cast(SummaryEngine, self.registry.resolve("summary", engine_id))
