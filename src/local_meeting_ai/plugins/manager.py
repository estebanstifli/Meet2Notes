from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast

from local_meeting_ai import __version__
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure.database.repositories import (
    PluginExecutionRepository,
    SettingsRepository,
)

from .contracts import (
    PLUGIN_API_VERSION,
    FailurePolicy,
    HookContext,
    HookKind,
    PluginManifest,
    PluginSettingField,
    ProviderDescriptor,
    ProviderKind,
    ProviderModel,
)
from .providers import ModelRegistration, ProviderFactory, ProviderRegistration, ProviderRegistry

logger = logging.getLogger(__name__)
FilterCallback = Callable[[Any, HookContext], Any | Awaitable[Any]]
ActionCallback = Callable[[Any, HookContext], Awaitable[None] | None]


class Plugin(Protocol):
    manifest: PluginManifest

    def register(self, registrar: PluginRegistrar) -> None: ...


@dataclass(frozen=True, slots=True)
class HookRegistration:
    plugin_id: str
    plugin_version: str
    hook: str
    kind: HookKind
    callback: FilterCallback | ActionCallback
    priority: int
    timeout_seconds: float
    failure_policy: FailurePolicy


class PluginRegistrar:
    """Registration surface handed to one plugin during discovery."""

    def __init__(
        self,
        manifest: PluginManifest,
        registrations: list[HookRegistration],
        providers: list[ProviderRegistration] | None = None,
        models: list[ModelRegistration] | None = None,
    ) -> None:
        self.manifest = manifest
        self._registrations = registrations
        self._providers = providers if providers is not None else []
        self._models = models if models is not None else []

    def add_provider(
        self,
        descriptor: ProviderDescriptor,
        factory: ProviderFactory,
    ) -> None:
        """Register a lazy selectable AI provider without importing host internals."""
        validated = ProviderDescriptor.model_validate(descriptor)
        if not callable(factory):
            raise TypeError("Provider factory must be callable")
        required_permissions = {
            "transcription": {"read_recording"},
            "diarization": {"read_recording"},
            "summary": {"read_transcript"},
            "embedding": {"read_transcript"},
        }[validated.kind]
        if any(model.managed for model in validated.models):
            required_permissions.add("write_model_cache")
        if validated.execution_target == "remote":
            required_permissions.add("network")
        missing_permissions = required_permissions - set(self.manifest.permissions)
        if missing_permissions:
            raise ValueError(
                "Provider permissions not declared: "
                + ", ".join(sorted(missing_permissions))
            )
        if any(
            item.descriptor.kind == validated.kind
            and item.descriptor.id == validated.id
            for item in self._providers
        ):
            raise ValueError(f"Duplicate provider id: {validated.kind}/{validated.id}")
        existing_settings = {
            field.id: field
            for item in self._providers
            for field in item.descriptor.settings
        }
        for field in validated.settings:
            existing = existing_settings.get(field.id)
            if existing is not None and existing != field:
                raise ValueError(
                    f"Conflicting plugin setting declaration: {field.id}"
                )
        self._providers.append(
            ProviderRegistration(
                plugin_id=self.manifest.id,
                plugin_version=self.manifest.version,
                descriptor=validated,
                factory=factory,
            )
        )

    def add_model(
        self,
        kind: ProviderKind,
        provider_id: str,
        model: ProviderModel,
    ) -> None:
        """Contribute a model/profile to an existing core or plugin provider."""
        validated = ProviderModel.model_validate(model)
        if validated.managed and "write_model_cache" not in self.manifest.permissions:
            raise ValueError("Managed model extensions require write_model_cache")
        if any(item.kind == kind and item.model.id == validated.id for item in self._models):
            raise ValueError(f"Duplicate model id: {kind}/{validated.id}")
        self._models.append(
            ModelRegistration(
                plugin_id=self.manifest.id,
                plugin_version=self.manifest.version,
                kind=kind,
                provider_id=provider_id,
                model=validated,
            )
        )

    def add_filter(
        self,
        hook: str,
        callback: FilterCallback,
        *,
        priority: int = 50,
        timeout_seconds: float = 30,
        failure_policy: FailurePolicy = "continue",
    ) -> None:
        self._add(
            hook,
            "filter",
            callback,
            priority,
            timeout_seconds,
            failure_policy,
        )

    def add_action(
        self,
        hook: str,
        callback: ActionCallback,
        *,
        priority: int = 50,
        timeout_seconds: float = 30,
        failure_policy: FailurePolicy = "continue",
    ) -> None:
        self._add(
            hook,
            "action",
            callback,
            priority,
            timeout_seconds,
            failure_policy,
        )

    def _add(
        self,
        hook: str,
        kind: HookKind,
        callback: FilterCallback | ActionCallback,
        priority: int,
        timeout_seconds: float,
        failure_policy: FailurePolicy,
    ) -> None:
        clean_hook = hook.strip()
        if not clean_hook or len(clean_hook) > 120:
            raise ValueError("Plugin hook names must contain 1-120 characters")
        if not callable(callback):
            raise TypeError("Plugin hook callback must be callable")
        if not 1 <= priority <= 1000:
            raise ValueError("Plugin hook priority must be between 1 and 1000")
        if not 0.1 <= timeout_seconds <= 3600:
            raise ValueError("Plugin hook timeout must be between 0.1 and 3600 seconds")
        self._registrations.append(
            HookRegistration(
                plugin_id=self.manifest.id,
                plugin_version=self.manifest.version,
                hook=clean_hook,
                kind=kind,
                callback=callback,
                priority=priority,
                timeout_seconds=timeout_seconds,
                failure_policy=failure_policy,
            )
        )


class HookBus:
    def __init__(
        self,
        executions: PluginExecutionRepository,
        settings_provider: Callable[[str], dict[str, Any]],
    ) -> None:
        self.executions = executions
        self.settings_provider = settings_provider
        self.registrations: list[HookRegistration] = []

    async def emit_action(self, hook: str, payload: Any, context: HookContext) -> None:
        for registration in self._for(hook, "action"):
            await self._invoke(registration, payload, context)

    async def apply_filters(self, hook: str, value: Any, context: HookContext) -> Any:
        current = value
        for registration in self._for(hook, "filter"):
            result = await self._invoke(registration, current, context)
            if result is not None:
                current = result
        return current

    def _for(self, hook: str, kind: HookKind) -> list[HookRegistration]:
        return sorted(
            (
                item
                for item in self.registrations
                if item.hook == hook and item.kind == kind
            ),
            key=lambda item: (item.priority, item.plugin_id),
        )

    async def _invoke(
        self,
        registration: HookRegistration,
        payload: Any,
        context: HookContext,
    ) -> Any:
        started = time.perf_counter()
        plugin_context = context.model_copy(
            update={"plugin_settings": self.settings_provider(registration.plugin_id)}
        )
        input_digest = _artifact_digest(payload)
        execution_id = self.executions.start(
            plugin_id=registration.plugin_id,
            plugin_version=registration.plugin_version,
            hook=registration.hook,
            kind=registration.kind,
            context=plugin_context,
            input_digest=input_digest,
        )
        try:
            plugin_task = asyncio.create_task(
                _call_plugin(registration.callback, payload, plugin_context)
            )
            try:
                done, _pending = await asyncio.wait(
                    {plugin_task},
                    timeout=registration.timeout_seconds,
                )
                if plugin_task not in done:
                    raise TimeoutError
                result = plugin_task.result()
            finally:
                if not plugin_task.done():
                    plugin_task.cancel()
                    plugin_task.add_done_callback(_discard_task_result)
            if (
                registration.kind == "filter"
                and result is not None
                and type(result) is not type(payload)
            ):
                raise TypeError(
                    f"Filter {registration.hook} returned {type(result).__name__}; "
                    f"expected {type(payload).__name__}"
                )
            output = payload if registration.kind == "action" or result is None else result
            self.executions.finish(
                execution_id,
                status="completed",
                duration_ms=round((time.perf_counter() - started) * 1000),
                output_digest=_artifact_digest(output),
            )
            return result
        except Exception as error:
            message = str(error) or type(error).__name__
            self.executions.finish(
                execution_id,
                status="failed",
                duration_ms=round((time.perf_counter() - started) * 1000),
                message=message,
            )
            logger.exception(
                "Plugin %s failed in %s: %s",
                registration.plugin_id,
                registration.hook,
                message,
            )
            if registration.failure_policy == "fail":
                raise
            return None


class PluginManager:
    ENTRY_POINT_GROUP = "meet2notes.plugins"

    def __init__(
        self,
        preferences: SettingsRepository,
        executions: PluginExecutionRepository,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.preferences = preferences
        self.executions = executions
        self.hooks = HookBus(executions, self.settings_for)
        self.provider_registry = provider_registry
        self.provider_registrations: list[ProviderRegistration] = []
        self.model_registrations: list[ModelRegistration] = []
        self._plugins: dict[str, Plugin] = {}
        self._sources: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.hooks.registrations.clear()
        self.provider_registrations.clear()
        self.model_registrations.clear()
        self._plugins.clear()
        self._sources.clear()
        self._errors.clear()
        from .builtin import BUILTIN_PLUGINS

        for builtin_plugin in BUILTIN_PLUGINS:
            self._accept(builtin_plugin, "built-in")
        selected: list[importlib.metadata.EntryPoint]
        try:
            entry_points = importlib.metadata.entry_points()
            selected = list(entry_points.select(group=self.ENTRY_POINT_GROUP))
        except Exception as error:
            self._errors["entry-point-discovery"] = str(error) or type(error).__name__
            selected = []
        for entry_point in selected:
            try:
                loaded = entry_point.load()
                candidate = loaded() if inspect.isclass(loaded) else loaded
                if callable(candidate) and not hasattr(candidate, "manifest"):
                    candidate = candidate()
                self._accept(cast(Plugin, candidate), f"package:{entry_point.name}")
            except Exception as error:
                self._errors[f"entry-point:{entry_point.name}"] = (
                    str(error) or type(error).__name__
                )
                logger.exception("Could not load plugin entry point %s", entry_point.name)
        enabled = self._enabled_ids()
        for plugin_id, loaded_plugin in self._plugins.items():
            if plugin_id not in enabled:
                continue
            try:
                hooks: list[HookRegistration] = []
                providers: list[ProviderRegistration] = []
                models: list[ModelRegistration] = []
                loaded_plugin.register(
                    PluginRegistrar(
                        loaded_plugin.manifest,
                        hooks,
                        providers,
                        models,
                    )
                )
                self.hooks.registrations.extend(hooks)
                self.provider_registrations.extend(providers)
                self.model_registrations.extend(models)
            except Exception as error:
                self._errors[plugin_id] = str(error) or type(error).__name__
                logger.exception("Could not register plugin %s", plugin_id)
        if self.provider_registry is not None:
            try:
                self.provider_registry.replace_plugins(
                    self.provider_registrations,
                    self.model_registrations,
                )
            except Exception as error:
                self._errors["provider-registry"] = str(error) or type(error).__name__
                logger.exception("Could not refresh the plugin provider registry")

    def list(self) -> list[dict[str, Any]]:
        enabled = self._enabled_ids()
        recent = self.executions.last_by_plugin()
        result: list[dict[str, Any]] = []
        for plugin_id, plugin in sorted(
            self._plugins.items(), key=lambda item: item[1].manifest.name.casefold()
        ):
            registrations = [
                item for item in self.hooks.registrations if item.plugin_id == plugin_id
            ]
            providers = [
                item
                for item in self.provider_registrations
                if item.plugin_id == plugin_id
            ]
            models = [
                item for item in self.model_registrations if item.plugin_id == plugin_id
            ]
            manifest = plugin.manifest
            result.append(
                {
                    **manifest.model_dump(),
                    "source": self._sources[plugin_id],
                    "enabled": plugin_id in enabled,
                    "compatible": manifest.plugin_api == PLUGIN_API_VERSION,
                    "hooks": [
                        {
                            "name": item.hook,
                            "kind": item.kind,
                            "priority": item.priority,
                            "failure_policy": item.failure_policy,
                        }
                        for item in registrations
                    ],
                    "providers": [
                        item.descriptor.model_dump(mode="json") for item in providers
                    ],
                    "models": [
                        {
                            "kind": item.kind,
                            "provider_id": item.provider_id,
                            **item.model.model_dump(mode="json"),
                        }
                        for item in models
                    ],
                    "settings": self.settings_for(plugin_id),
                    "last_execution": recent.get(plugin_id),
                    "error": self._errors.get(plugin_id),
                }
            )
        for plugin_id, error in sorted(self._errors.items()):
            if plugin_id in self._plugins:
                continue
            result.append(
                {
                    "id": plugin_id,
                    "name": plugin_id,
                    "version": "unknown",
                    "description": "The plugin could not be discovered.",
                    "author": "Unknown",
                    "plugin_api": "unknown",
                    "requires_meet2notes": None,
                    "permissions": [],
                    "homepage": None,
                    "default_enabled": False,
                    "isolated_runtime": False,
                    "source": "entry-point",
                    "enabled": False,
                    "compatible": False,
                    "hooks": [],
                    "providers": [],
                    "models": [],
                    "settings": {},
                    "last_execution": None,
                    "error": error,
                }
            )
        return result

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValidationError("Plugin not found")
        if plugin.manifest.plugin_api != PLUGIN_API_VERSION:
            raise ValidationError(
                f"Plugin API {plugin.manifest.plugin_api} is incompatible with "
                f"Meet2Notes Plugin API {PLUGIN_API_VERSION}"
            )
        if not enabled and self._provider_in_use(plugin_id):
            raise ValidationError(
                "Select a different engine before disabling this provider plugin"
            )
        state = self._state()
        configured = set(self._enabled_ids())
        if enabled:
            configured.add(plugin_id)
        else:
            configured.discard(plugin_id)
        state["enabled"] = sorted(configured)
        self.preferences.update({"plugins": state})
        self.reload()
        return next(item for item in self.list() if item["id"] == plugin_id)

    def settings_for(self, plugin_id: str) -> dict[str, Any]:
        state = self._state()
        settings = state.get("settings")
        if not isinstance(settings, dict):
            return {}
        value = settings.get(plugin_id)
        return dict(value) if isinstance(value, dict) else {}

    def update_settings(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise ValidationError("Plugin not found")
        fields = {
            field.id: field
            for registration in self.provider_registrations
            if registration.plugin_id == plugin_id
            for field in registration.descriptor.settings
        }
        unknown = set(values) - set(fields)
        if unknown:
            raise ValidationError(
                f"Unknown plugin setting(s): {', '.join(sorted(unknown))}"
            )
        incoming = {**self.settings_for(plugin_id), **values}
        validated = {
            field_id: _validate_plugin_setting(field, incoming.get(field_id, field.default))
            for field_id, field in fields.items()
            if field_id in incoming or field.default is not None
        }
        state = self._state()
        settings = state.get("settings")
        configured = dict(settings) if isinstance(settings, dict) else {}
        configured[plugin_id] = validated
        state["settings"] = configured
        self.preferences.update({"plugins": state})
        return self.settings_for(plugin_id)

    @property
    def api_info(self) -> dict[str, str]:
        return {
            "plugin_api": PLUGIN_API_VERSION,
            "meet2notes": __version__,
            "entry_point_group": self.ENTRY_POINT_GROUP,
        }

    def _accept(self, plugin: Plugin, source: str) -> None:
        manifest = PluginManifest.model_validate(plugin.manifest)
        if manifest.id in self._plugins:
            raise ValueError(f"Duplicate plugin id: {manifest.id}")
        self._plugins[manifest.id] = plugin
        self._sources[manifest.id] = source
        if manifest.plugin_api != PLUGIN_API_VERSION:
            self._errors[manifest.id] = (
                f"Requires Plugin API {manifest.plugin_api}; this application provides "
                f"{PLUGIN_API_VERSION}"
            )

    def _state(self) -> dict[str, Any]:
        configured = self.preferences.get_all().get("plugins")
        return dict(configured) if isinstance(configured, dict) else {}

    def _enabled_ids(self) -> set[str]:
        state = self._state()
        explicit = state.get("enabled")
        if isinstance(explicit, list):
            return {str(item) for item in explicit}
        return {
            plugin_id
            for plugin_id, plugin in self._plugins.items()
            if plugin.manifest.default_enabled
            and self._sources.get(plugin_id) == "built-in"
        }

    def _provider_in_use(self, plugin_id: str) -> bool:
        provider_ids = {
            registration.descriptor.id
            for registration in self.provider_registrations
            if registration.plugin_id == plugin_id
        }
        if not provider_ids:
            return False
        preferences = self.preferences.get_all()
        if preferences.get("live_transcription_engine") in provider_ids:
            return True
        if preferences.get("final_transcription_engine") in provider_ids:
            return True
        diarization = preferences.get("diarization")
        if isinstance(diarization, dict) and diarization.get("engine") in provider_ids:
            return True
        summary = preferences.get("summary_engine")
        if isinstance(summary, dict) and summary.get("engine") in provider_ids:
            return True
        rag = preferences.get("rag")
        return isinstance(rag, dict) and rag.get("embedding_provider") in provider_ids


def _artifact_digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        serialized = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = repr(value)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _call_plugin(
    callback: FilterCallback | ActionCallback,
    payload: Any,
    context: HookContext,
) -> Any:
    if inspect.iscoroutinefunction(callback):
        return await cast(Awaitable[Any], callback(payload, context))
    result = await asyncio.to_thread(callback, payload, context)
    if inspect.isawaitable(result):
        return await cast(Awaitable[Any], result)
    return result


def _discard_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.exception()


def _validate_plugin_setting(field: PluginSettingField, value: Any) -> Any:
    if value is None:
        if field.required:
            raise ValidationError(f"Plugin setting '{field.id}' is required")
        return None
    if field.kind in {"string", "select"}:
        if not isinstance(value, str):
            raise ValidationError(f"Plugin setting '{field.id}' must be text")
        clean = value.strip()
        if field.required and not clean:
            raise ValidationError(f"Plugin setting '{field.id}' is required")
        if field.kind == "select" and clean not in field.choices:
            raise ValidationError(f"Plugin setting '{field.id}' has an invalid choice")
        return clean
    if field.kind == "boolean":
        if not isinstance(value, bool):
            raise ValidationError(f"Plugin setting '{field.id}' must be true or false")
        return value
    if field.kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"Plugin setting '{field.id}' must be an integer")
        numeric: int | float = value
    elif field.kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"Plugin setting '{field.id}' must be a number")
        numeric = value
    else:  # pragma: no cover - Pydantic prevents unknown field kinds.
        raise ValidationError(f"Plugin setting '{field.id}' has an unsupported type")
    if field.minimum is not None and numeric < field.minimum:
        raise ValidationError(f"Plugin setting '{field.id}' is below its minimum")
    if field.maximum is not None and numeric > field.maximum:
        raise ValidationError(f"Plugin setting '{field.id}' is above its maximum")
    return value
