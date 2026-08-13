from __future__ import annotations

import hashlib
import hmac
import json

from local_meeting_ai.application.webhooks import _event_envelope
from local_meeting_ai.domain.webhooks import WebhookEvent


def _event() -> WebhookEvent:
    return WebhookEvent(
        id="event-1",
        event_type="summary.completed",
        subject="meeting/8",
        meeting_id=8,
        transcription_id=9,
        data={
            "meeting": {"id": 8, "title": "Roadmap"},
            "transcript": {"id": 9, "segments": [{"text": "Secret text"}]},
            "summary": {"id": 2, "content_markdown": "Private notes"},
        },
        occurred_at="2026-08-13T12:00:00+00:00",
        expires_at=None,
    )


def test_metadata_payload_removes_transcript_and_summary_text() -> None:
    envelope = _event_envelope(_event(), "metadata")

    assert envelope["type"] == "com.meet2notes.summary.completed.v1"
    assert envelope["data"]["transcript"] == {"id": 9, "segment_count": 1}
    assert envelope["data"]["summary"] == {"id": 2}
    assert "Secret text" not in json.dumps(envelope)
    assert "Private notes" not in json.dumps(envelope)


def test_documented_signature_recipe_matches() -> None:
    timestamp = "1786622400"
    body = b'{"id":"event-1"}'
    secret = "test-secret"

    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()

    assert hmac.compare_digest(
        f"sha256={signature}",
        "sha256="
        + hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest(),
    )
