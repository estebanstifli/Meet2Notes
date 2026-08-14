from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LiveAssistantSessionStatus = Literal["active", "stopped", "interrupted", "failed"]
LiveAssistantInsightStatus = Literal["new", "accepted", "dismissed"]


@dataclass(frozen=True, slots=True)
class LiveAssistantSession:
    id: str
    meeting_id: int
    transcription_id: int
    status: LiveAssistantSessionStatus
    configuration: dict[str, object]
    memory: dict[str, object]
    last_sequence: int
    last_error: str | None
    started_at: str
    stopped_at: str | None


@dataclass(frozen=True, slots=True)
class LiveAssistantInsight:
    id: str
    session_id: str | None
    meeting_id: int
    transcription_id: int | None
    kind: str
    text: str
    confidence: float | None
    related_segment_ids: tuple[str, ...]
    start_ms: int | None
    end_ms: int | None
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    status: LiveAssistantInsightStatus
    created_at: str
