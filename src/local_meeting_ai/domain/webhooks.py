from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

WebhookMode = Literal["notification", "live_agent"]
WebhookContentLevel = Literal["metadata", "segments", "full"]
WebhookDeliveryStatus = Literal[
    "queued",
    "delivering",
    "retry",
    "delivered",
    "failed",
    "expired",
]

WEBHOOK_EVENT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "live.session.started",
        "group": "Live meeting",
        "description": "A local recording and Live transcription session started.",
        "content_level": "metadata",
        "live": True,
    },
    {
        "id": "live.session.paused",
        "group": "Live meeting",
        "description": "The local capture session was paused.",
        "content_level": "metadata",
        "live": True,
    },
    {
        "id": "live.session.resumed",
        "group": "Live meeting",
        "description": "The local capture session resumed.",
        "content_level": "metadata",
        "live": True,
    },
    {
        "id": "live.segment.batch",
        "group": "Live meeting",
        "description": "A deduplicated batch of provisional transcript segments is ready.",
        "content_level": "segments",
        "live": True,
    },
    {
        "id": "live.session.stopped",
        "group": "Live meeting",
        "description": "Recording stopped; final processing may still be running.",
        "content_level": "metadata",
        "live": True,
    },
    {
        "id": "recording.ready",
        "group": "Processing",
        "description": "The captured or imported recording is registered and normalized.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "recording.failed",
        "group": "Processing",
        "description": "Recording import or normalization failed.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "transcription.final.completed",
        "group": "Processing",
        "description": "The canonical final transcript is complete.",
        "content_level": "segments",
        "live": False,
    },
    {
        "id": "transcription.final.failed",
        "group": "Processing",
        "description": "The final transcription failed.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "transcription.final.cancelled",
        "group": "Processing",
        "description": "The final transcription was cancelled.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "diarization.completed",
        "group": "Processing",
        "description": "Speaker turns were assigned to the transcript.",
        "content_level": "segments",
        "live": False,
    },
    {
        "id": "diarization.failed",
        "group": "Processing",
        "description": "Speaker diarization failed.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "diarization.cancelled",
        "group": "Processing",
        "description": "Speaker diarization was cancelled.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "summary.completed",
        "group": "Processing",
        "description": "AI meeting notes are complete.",
        "content_level": "full",
        "live": False,
    },
    {
        "id": "summary.failed",
        "group": "Processing",
        "description": "AI meeting-note generation failed.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "summary.cancelled",
        "group": "Processing",
        "description": "AI meeting-note generation was cancelled.",
        "content_level": "metadata",
        "live": False,
    },
    {
        "id": "meeting.processing.completed",
        "group": "Meeting",
        "description": "All requested final processing stages have finished.",
        "content_level": "metadata",
        "live": False,
    },
)

WEBHOOK_EVENT_IDS = frozenset(item["id"] for item in WEBHOOK_EVENT_CATALOG)


@dataclass(frozen=True, slots=True)
class WebhookEndpoint:
    id: str
    name: str
    url: str
    enabled: bool
    mode: WebhookMode
    events: tuple[str, ...]
    content_level: WebhookContentLevel
    timeout_seconds: float
    max_attempts: int
    allow_private_network: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    id: str
    event_type: str
    subject: str
    meeting_id: int | None
    transcription_id: int | None
    data: dict[str, Any]
    occurred_at: str
    expires_at: str | None


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    id: str
    event_id: str
    endpoint_id: str
    status: WebhookDeliveryStatus
    attempt_count: int
    next_attempt_at: str
    last_status_code: int | None
    last_error: str | None
    duration_ms: int | None
    response_excerpt: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class WebhookInsight:
    id: str
    endpoint_id: str
    endpoint_name: str
    event_id: str
    meeting_id: int
    kind: str
    text: str
    confidence: float | None
    related_segment_ids: tuple[str, ...]
    status: Literal["new", "accepted", "dismissed"]
    created_at: str
