from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLUGIN_API_VERSION = "1"
HookKind = Literal["action", "filter"]
FailurePolicy = Literal["continue", "fail"]
ProviderKind = Literal["transcription", "diarization", "summary", "embedding"]
SettingFieldKind = Literal["string", "integer", "number", "boolean", "select"]
VectorStoreOperationName = Literal[
    "rows_for_transcription",
    "replace_transcription",
    "candidates",
    "counts",
    "clear",
]


class PluginManifest(BaseModel):
    """Stable metadata declared by one extension package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=500)
    author: str = Field(default="Unknown", min_length=1, max_length=120)
    plugin_api: str = Field(default=PLUGIN_API_VERSION, min_length=1, max_length=20)
    requires_meet2notes: str | None = Field(default=None, max_length=80)
    permissions: tuple[str, ...] = ()
    homepage: str | None = Field(default=None, max_length=500)
    default_enabled: bool = False
    isolated_runtime: bool = False


class PluginSettingField(BaseModel):
    """Declarative setting rendered and validated by the host application."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    label: str = Field(min_length=1, max_length=120)
    kind: SettingFieldKind = "string"
    description: str = Field(default="", max_length=500)
    default: str | int | float | bool | None = None
    required: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    placeholder: str = Field(default="", max_length=300)
    advanced: bool = False

    @model_validator(mode="after")
    def validate_definition(self) -> PluginSettingField:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Setting minimum cannot exceed maximum")
        if self.kind == "select":
            if not self.choices:
                raise ValueError("Select settings require at least one choice")
            if len(set(self.choices)) != len(self.choices):
                raise ValueError("Select setting choices must be unique")
        elif self.choices:
            raise ValueError("Only select settings can declare choices")
        if self.kind not in {"integer", "number"} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Only numeric settings can declare bounds")
        if self.default is None:
            return self
        if self.kind in {"string", "select"}:
            if not isinstance(self.default, str):
                raise ValueError(f"Default does not match setting kind '{self.kind}'")
            if self.kind == "select" and self.default not in self.choices:
                raise ValueError("Select setting default must be one of its choices")
        elif self.kind == "boolean":
            if not isinstance(self.default, bool):
                raise ValueError("Default does not match setting kind 'boolean'")
        elif self.kind == "integer":
            if not isinstance(self.default, int) or isinstance(self.default, bool):
                raise ValueError("Default does not match setting kind 'integer'")
        elif not isinstance(self.default, (int, float)) or isinstance(self.default, bool):
            raise ValueError("Default does not match setting kind 'number'")
        if self.kind in {"integer", "number"}:
            numeric_default = float(self.default)
            if self.minimum is not None and numeric_default < self.minimum:
                raise ValueError("Setting default is below its minimum")
            if self.maximum is not None and numeric_default > self.maximum:
                raise ValueError("Setting default is above its maximum")
        return self


class ProviderModel(BaseModel):
    """Portable model/profile metadata contributed by an engine provider."""

    model_config = ConfigDict(extra="allow", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=500)
    device: str = Field(default="auto", max_length=40)
    compute_type: str = Field(default="auto", max_length=80)
    managed: bool = True
    external_file: bool = False
    recommended: bool = False
    supports_live: bool = False
    supports_final: bool = True
    download_size: str | None = Field(default=None, max_length=80)
    compatibility_note: str | None = Field(default=None, max_length=500)
    defaults: dict[str, Any] = Field(default_factory=dict)


class ProviderDescriptor(BaseModel):
    """Stable declaration for a selectable AI provider contributed by a plugin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    kind: ProviderKind
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    models: tuple[ProviderModel, ...] = ()
    settings: tuple[PluginSettingField, ...] = ()
    execution_target: Literal["in-process", "isolated-local", "remote"] = "in-process"
    outputs: tuple[str, ...] = ()
    homepage: str | None = Field(default=None, max_length=500)


@dataclass(frozen=True, slots=True)
class ProviderRuntimeContext:
    """Host-owned directories and dynamic settings passed to a provider factory."""

    plugin_id: str
    data_dir: Path
    models_dir: Path
    _settings_provider: Callable[[str], dict[str, Any]] = field(repr=False)

    def settings(self) -> dict[str, Any]:
        return self._settings_provider(self.plugin_id)


class TranscriptDocumentSegment(BaseModel):
    """Portable transcript segment exposed to filters without database access."""

    model_config = ConfigDict(extra="forbid")

    id: int
    index: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    speaker_id: int | None = None
    speaker_label: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MeetingDocument(BaseModel):
    """Canonical, serializable analysis input presented to community filters.

    This object is a working copy. Returning a modified document never changes
    the persisted transcript; it only affects downstream processing for the
    current job.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: int
    transcription_id: int
    source_language: str | None = None
    analysis_language: str | None = None
    segments: list[TranscriptDocumentSegment]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def prompt_text(self) -> str:
        lines: list[str] = []
        for segment in self.segments:
            label = segment.speaker_label or "Unidentified speaker"
            lines.append(f"[{segment.start_ms / 1000:.1f}s] {label}: {segment.text}")
        return "\n".join(lines)


class AnalysisArtifact(BaseModel):
    """Portable result passed through analysis post-filters."""

    model_config = ConfigDict(extra="forbid")

    meeting_id: int
    transcription_id: int
    summary_id: int
    content_markdown: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class HookContext(BaseModel):
    """Read-only execution context supplied to every action or filter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hook: str
    pipeline_id: str | None = None
    job_uuid: str | None = None
    meeting_id: int | None = None
    transcription_id: int | None = None
    stage: str | None = None
    plugin_settings: dict[str, Any] = Field(default_factory=dict)


class VectorStoreDescriptor(BaseModel):
    """One vector destination exposed by core or a plugin."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    local: bool = False
    supports_vector_acceleration: bool = True
    plugin_id: str | None = Field(default=None, max_length=80)


class VectorStoreCatalog(BaseModel):
    """Filter payload used by plugins to register additional vector stores."""

    model_config = ConfigDict(extra="forbid")

    stores: list[VectorStoreDescriptor] = Field(default_factory=list)


class VectorStoreOperation(BaseModel):
    """Serializable command/result envelope for plugin vector-store adapters."""

    model_config = ConfigDict(extra="forbid")

    store_id: str = Field(min_length=1, max_length=80)
    operation: VectorStoreOperationName
    payload: dict[str, Any] = Field(default_factory=dict)
    handled: bool = False
    result: dict[str, Any] = Field(default_factory=dict)
