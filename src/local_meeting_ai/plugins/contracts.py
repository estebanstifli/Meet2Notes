from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PLUGIN_API_VERSION = "1"
HookKind = Literal["action", "filter"]
FailurePolicy = Literal["continue", "fail"]
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
