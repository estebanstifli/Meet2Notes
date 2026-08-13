from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

import pytest

from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import (
    PluginExecutionRepository,
    SettingsRepository,
)
from local_meeting_ai.plugins import (
    PluginManifest,
    PluginRegistrar,
    PluginSettingField,
    ProviderDescriptor,
    ProviderModel,
    ProviderRuntimeContext,
)
from local_meeting_ai.plugins.manager import PluginManager
from local_meeting_ai.plugins.providers import ProviderRegistry


class FakeTranscriptionEngine:
    name = "test-asr"

    def __init__(self, context: ProviderRuntimeContext) -> None:
        self.context = context
        self.stopped = False

    def capability(self) -> dict[str, Any]:
        return {
            "available": True,
            "runtime_available": True,
            "installed": True,
            "installed_models": ["example/test-asr"],
        }

    def shutdown(self) -> None:
        self.stopped = True


class ProviderPlugin:
    manifest = PluginManifest(
        id="test.provider-plugin",
        name="Provider plugin",
        version="1.0.0",
        description="Registers a selectable test transcription provider.",
        permissions=("read_recording", "write_model_cache"),
        default_enabled=True,
    )

    def register(self, registrar: PluginRegistrar) -> None:
        registrar.add_provider(
            ProviderDescriptor(
                id="test-asr",
                kind="transcription",
                display_name="Test ASR",
                description="A lazy engine used by provider-registry tests.",
                outputs=("transcript", "timestamps", "speaker-turns"),
                settings=(
                    PluginSettingField(
                        id="batch_size",
                        label="Batch size",
                        kind="integer",
                        default=4,
                        minimum=1,
                        maximum=32,
                    ),
                ),
                models=(
                    ProviderModel(
                        id="test-asr-small",
                        display_name="Test ASR Small",
                        description="Small test profile.",
                        model="example/test-asr",
                        supports_live=True,
                        supports_final=True,
                    ),
                ),
            ),
            FakeTranscriptionEngine,
        )


class FakeEntryPoint:
    name = "provider_plugin"

    @staticmethod
    def load() -> type[ProviderPlugin]:
        return ProviderPlugin


class FakeEntryPoints(list[FakeEntryPoint]):
    def select(self, **params: str) -> FakeEntryPoints:
        return self if params.get("group") == "meet2notes.plugins" else FakeEntryPoints()


@pytest.fixture
def provider_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "providers.db")
    MigrationRunner(database).apply()
    settings = SettingsRepository(database)
    settings.update({"plugins": {"enabled": ["test.provider-plugin"]}})
    executions = PluginExecutionRepository(database)

    def settings_for(plugin_id: str) -> dict[str, Any]:
        state = settings.get_all().get("plugins", {})
        configured = state.get("settings", {}) if isinstance(state, dict) else {}
        value = configured.get(plugin_id, {}) if isinstance(configured, dict) else {}
        return dict(value) if isinstance(value, dict) else {}

    registry = ProviderRegistry(
        ProviderRuntimeContext(
            plugin_id="meet2notes.core",
            data_dir=tmp_path / "data",
            models_dir=tmp_path / "models",
            _settings_provider=settings_for,
        )
    )
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda: FakeEntryPoints([FakeEntryPoint()]),
    )
    return settings, executions, registry


def test_plugin_provider_is_lazy_configurable_and_hot_removable(provider_services) -> None:  # type: ignore[no-untyped-def]
    settings, executions, registry = provider_services
    manager = PluginManager(settings, executions, registry)

    catalog = manager.list()
    plugin = next(item for item in catalog if item["id"] == "test.provider-plugin")
    assert plugin["providers"][0]["id"] == "test-asr"
    assert registry.models("transcription")[0].model.id == "test-asr-small"
    assert registry._instances == {}

    engine = registry.resolve("transcription", "test-asr")
    assert isinstance(engine, FakeTranscriptionEngine)
    assert engine.context.models_dir == (
        registry._runtime_context.models_dir / "plugins" / "test.provider-plugin"
    )
    assert engine.context.settings() == {"batch_size": 4}
    assert registry.configuration("transcription", "test-asr", {}) == {
        "plugin_id": "test.provider-plugin",
        "plugin_settings": {"batch_size": 4},
    }

    assert manager.update_settings("test.provider-plugin", {"batch_size": 8}) == {
        "batch_size": 8
    }
    assert engine.context.settings() == {"batch_size": 8}
    assert registry.configuration("transcription", "test-asr", {}) == {
        "plugin_id": "test.provider-plugin",
        "plugin_settings": {"batch_size": 8},
    }

    manager.set_enabled("test.provider-plugin", False)
    assert engine.stopped is True
    assert registry.registrations("transcription", plugins_only=True) == []


def test_model_extension_can_target_an_existing_provider(tmp_path: Path) -> None:
    registry = ProviderRegistry(
        ProviderRuntimeContext(
            plugin_id="meet2notes.core",
            data_dir=tmp_path / "data",
            models_dir=tmp_path / "models",
            _settings_provider=lambda _plugin_id: {},
        )
    )
    registry.register_core(
        ProviderDescriptor(
            id="core-asr",
            kind="transcription",
            display_name="Core ASR",
            description="Core provider extended by a plugin model.",
        ),
        FakeTranscriptionEngine,
    )
    providers = []
    models = []
    registrar = PluginRegistrar(ProviderPlugin.manifest, [], providers, models)
    registrar.add_model(
        "transcription",
        "core-asr",
        ProviderModel(
            id="community-large",
            display_name="Community Large",
            description="A model contributed without replacing its provider.",
            model="community/large",
        ),
    )

    registry.replace_plugins(providers, models)

    registration = registry.provider_for_model("transcription", "community-large")
    assert registration.provider_id == "core-asr"
    assert registration.plugin_id == "test.provider-plugin"


def test_plugin_setting_validation_rejects_out_of_range_values(provider_services) -> None:  # type: ignore[no-untyped-def]
    settings, executions, registry = provider_services
    manager = PluginManager(settings, executions, registry)

    with pytest.raises(ValidationError, match="above its maximum"):
        manager.update_settings("test.provider-plugin", {"batch_size": 100})
