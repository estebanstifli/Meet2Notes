from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
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
)

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

    def __init__(self, manifest: PluginManifest, registrations: list[HookRegistration]) -> None:
        self.manifest = manifest
        self._registrations = registrations

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
            result = await asyncio.wait_for(
                _call_plugin(registration.callback, payload, plugin_context),
                timeout=registration.timeout_seconds,
            )
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
    ) -> None:
        self.preferences = preferences
        self.executions = executions
        self.hooks = HookBus(executions, self.settings_for)
        self._plugins: dict[str, Plugin] = {}
        self._sources: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.hooks.registrations.clear()
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
                loaded_plugin.register(
                    PluginRegistrar(
                        loaded_plugin.manifest,
                        self.hooks.registrations,
                    )
                )
            except Exception as error:
                self._errors[plugin_id] = str(error) or type(error).__name__
                logger.exception("Could not register plugin %s", plugin_id)

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
        }


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
