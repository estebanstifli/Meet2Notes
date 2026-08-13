from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from local_meeting_ai.domain.errors import CapabilityUnavailableError, ValidationError

from .contracts import (
    ProviderDescriptor,
    ProviderKind,
    ProviderModel,
    ProviderRuntimeContext,
)

ProviderFactory = Callable[[ProviderRuntimeContext], Any]


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    plugin_id: str | None
    plugin_version: str
    descriptor: ProviderDescriptor
    factory: ProviderFactory


@dataclass(frozen=True, slots=True)
class ModelRegistration:
    plugin_id: str | None
    plugin_version: str
    kind: ProviderKind
    provider_id: str
    model: ProviderModel


class ProviderRegistry:
    """Thread-safe, lazy registry shared by every selectable engine router.

    Core providers and enabled plugin providers use the same lookup path. Plugin
    rescans replace only third-party registrations and shut down orphaned engine
    instances, so enabling or disabling a package does not require editing or
    rebuilding the application core.
    """

    def __init__(
        self,
        runtime_context: ProviderRuntimeContext,
    ) -> None:
        self._runtime_context = runtime_context
        self._core_providers: dict[tuple[ProviderKind, str], ProviderRegistration] = {}
        self._plugin_providers: dict[tuple[ProviderKind, str], ProviderRegistration] = {}
        self._core_models: list[ModelRegistration] = []
        self._plugin_models: list[ModelRegistration] = []
        self._instances: dict[tuple[ProviderKind, str], Any] = {}
        self._lock = threading.RLock()

    def register_core(
        self,
        descriptor: ProviderDescriptor,
        factory: ProviderFactory,
    ) -> None:
        registration = ProviderRegistration(None, "core", descriptor, factory)
        key = (descriptor.kind, descriptor.id)
        with self._lock:
            if key in self._core_providers:
                raise ValueError(f"Duplicate core provider: {descriptor.kind}/{descriptor.id}")
            self._core_providers[key] = registration

    def register_core_model(
        self,
        kind: ProviderKind,
        provider_id: str,
        model: ProviderModel,
    ) -> None:
        with self._lock:
            self._core_models.append(
                ModelRegistration(None, "core", kind, provider_id, model)
            )

    def replace_plugins(
        self,
        providers: Iterable[ProviderRegistration],
        models: Iterable[ModelRegistration],
    ) -> None:
        next_providers: dict[tuple[ProviderKind, str], ProviderRegistration] = {}
        for registration in providers:
            key = (registration.descriptor.kind, registration.descriptor.id)
            if key in self._core_providers or key in next_providers:
                raise ValueError(
                    f"Duplicate provider id: {registration.descriptor.kind}/"
                    f"{registration.descriptor.id}"
                )
            next_providers[key] = registration
        next_models = list(models)
        available = set(self._core_providers) | set(next_providers)
        seen_models: set[tuple[ProviderKind, str]] = set()
        declared_models = [
            ModelRegistration(
                provider.plugin_id,
                provider.plugin_version,
                provider.descriptor.kind,
                provider.descriptor.id,
                model,
            )
            for provider in [*self._core_providers.values(), *next_providers.values()]
            for model in provider.descriptor.models
        ]
        for model_registration in [*self._core_models, *next_models, *declared_models]:
            provider_key = (model_registration.kind, model_registration.provider_id)
            if provider_key not in available:
                raise ValueError(
                    f"Model {model_registration.model.id} targets unknown provider "
                    f"{model_registration.kind}/{model_registration.provider_id}"
                )
            model_key = (model_registration.kind, model_registration.model.id)
            if model_key in seen_models:
                raise ValueError(
                    f"Duplicate model id: {model_registration.kind}/"
                    f"{model_registration.model.id}"
                )
            seen_models.add(model_key)
        with self._lock:
            removed = set(self._plugin_providers) - set(next_providers)
            replaced = {
                key
                for key, value in next_providers.items()
                if self._plugin_providers.get(key) != value
            }
            for key in removed | replaced:
                self._shutdown_instance(key)
            self._plugin_providers = next_providers
            self._plugin_models = next_models

    def registrations(
        self,
        kind: ProviderKind | None = None,
        *,
        plugins_only: bool = False,
    ) -> list[ProviderRegistration]:
        with self._lock:
            source = dict(self._plugin_providers)
            if not plugins_only:
                source = {**self._core_providers, **source}
            values = [
                registration
                for (provider_kind, _provider_id), registration in source.items()
                if kind is None or provider_kind == kind
            ]
        return sorted(
            values,
            key=lambda item: (item.descriptor.kind, item.descriptor.display_name.casefold()),
        )

    def models(
        self,
        kind: ProviderKind,
        *,
        plugins_only: bool = False,
    ) -> list[ModelRegistration]:
        with self._lock:
            registrations = list(self._plugin_models)
            providers = self.registrations(kind, plugins_only=plugins_only)
            for provider in providers:
                registrations.extend(
                    ModelRegistration(
                        provider.plugin_id,
                        provider.plugin_version,
                        kind,
                        provider.descriptor.id,
                        model,
                    )
                    for model in provider.descriptor.models
                )
            if not plugins_only:
                registrations = [*self._core_models, *registrations]
            else:
                registrations = [item for item in registrations if item.plugin_id]
        return [item for item in registrations if item.kind == kind]

    def resolve(self, kind: ProviderKind, provider_id: str) -> Any:
        key = (kind, provider_id)
        with self._lock:
            registration = self._plugin_providers.get(key) or self._core_providers.get(key)
            if registration is None:
                raise CapabilityUnavailableError(
                    f"The {kind} provider '{provider_id}' is not registered"
                )
            if key not in self._instances:
                if registration.plugin_id is None:
                    context = self._runtime_context
                else:
                    plugin_id = registration.plugin_id
                    context = ProviderRuntimeContext(
                        plugin_id=plugin_id,
                        data_dir=self._runtime_context.data_dir / "plugins" / plugin_id,
                        models_dir=self._runtime_context.models_dir / "plugins" / plugin_id,
                        _settings_provider=self.settings_for_plugin,
                    )
                    context.data_dir.mkdir(parents=True, exist_ok=True)
                    context.models_dir.mkdir(parents=True, exist_ok=True)
                self._instances[key] = registration.factory(context)
            return self._instances[key]

    def capability(self, kind: ProviderKind, provider_id: str) -> dict[str, Any]:
        registration = next(
            (
                item
                for item in self.registrations(kind)
                if item.descriptor.id == provider_id
            ),
            None,
        )
        if registration is None:
            raise CapabilityUnavailableError(
                f"The {kind} provider '{provider_id}' is not registered"
            )
        capability = dict(self.resolve(kind, provider_id).capability())
        descriptor = registration.descriptor
        capability.setdefault("engine", descriptor.id)
        capability.setdefault("display_name", descriptor.display_name)
        capability.setdefault("description", descriptor.description)
        capability.setdefault("execution_target", descriptor.execution_target)
        capability.setdefault("outputs", list(descriptor.outputs))
        capability["plugin_id"] = registration.plugin_id
        return capability

    def provider_for_model(
        self,
        kind: ProviderKind,
        model_id: str,
    ) -> ModelRegistration:
        matches = [item for item in self.models(kind) if item.model.id == model_id]
        if not matches:
            raise ValidationError(f"Unknown {kind} model profile: {model_id}")
        return matches[0]

    def configuration(
        self,
        kind: ProviderKind,
        provider_id: str,
        base: dict[str, Any],
    ) -> dict[str, Any]:
        registration = next(
            (
                item
                for item in self.registrations(kind)
                if item.descriptor.id == provider_id
            ),
            None,
        )
        if registration is None or registration.plugin_id is None:
            return dict(base)
        return {
            **base,
            "plugin_id": registration.plugin_id,
            "plugin_settings": self.settings_for_plugin(registration.plugin_id),
        }

    def settings_for_plugin(self, plugin_id: str) -> dict[str, Any]:
        """Return validated descriptor defaults overlaid with persisted values."""
        defaults: dict[str, Any] = {}
        with self._lock:
            for registration in self._plugin_providers.values():
                if registration.plugin_id != plugin_id:
                    continue
                for setting in registration.descriptor.settings:
                    if setting.default is not None:
                        defaults.setdefault(setting.id, setting.default)
        return {
            **defaults,
            **self._runtime_context._settings_provider(plugin_id),
        }

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                **registration.descriptor.model_dump(mode="json"),
                "plugin_id": registration.plugin_id,
                "plugin_version": registration.plugin_version,
            }
            for registration in self.registrations()
        ]

    def shutdown(self) -> None:
        with self._lock:
            for key in list(self._instances):
                self._shutdown_instance(key)

    def _shutdown_instance(self, key: tuple[ProviderKind, str]) -> None:
        instance = self._instances.pop(key, None)
        shutdown = getattr(instance, "shutdown", None)
        if callable(shutdown):
            shutdown()
