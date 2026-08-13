from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from local_meeting_ai.domain.webhooks import (
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
    WebhookInsight,
)

from .connection import Database
from .repositories import utc_now


def _endpoint(row: Any) -> WebhookEndpoint:
    return WebhookEndpoint(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        enabled=bool(row["enabled"]),
        mode=row["mode"],
        events=tuple(json.loads(row["events_json"] or "[]")),
        content_level=row["content_level"],
        timeout_seconds=float(row["timeout_seconds"]),
        max_attempts=int(row["max_attempts"]),
        allow_private_network=bool(row["allow_private_network"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event(row: Any) -> WebhookEvent:
    return WebhookEvent(
        id=row["id"],
        event_type=row["event_type"],
        subject=row["subject"],
        meeting_id=row["meeting_id"],
        transcription_id=row["transcription_id"],
        data=json.loads(row["data_json"] or "{}"),
        occurred_at=row["occurred_at"],
        expires_at=row["expires_at"],
    )


def _delivery(row: Any) -> WebhookDelivery:
    return WebhookDelivery(
        id=row["id"],
        event_id=row["event_id"],
        endpoint_id=row["endpoint_id"],
        status=row["status"],
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=row["next_attempt_at"],
        last_status_code=row["last_status_code"],
        last_error=row["last_error"],
        duration_ms=row["duration_ms"],
        response_excerpt=row["response_excerpt"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _insight(row: Any) -> WebhookInsight:
    return WebhookInsight(
        id=row["id"],
        endpoint_id=row["endpoint_id"],
        endpoint_name=row["endpoint_name"],
        event_id=row["event_id"],
        meeting_id=int(row["meeting_id"]),
        kind=row["kind"],
        text=row["text"],
        confidence=row["confidence"],
        related_segment_ids=tuple(json.loads(row["related_segment_ids_json"] or "[]")),
        status=row["status"],
        created_at=row["created_at"],
    )


class WebhookRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def endpoints(self) -> list[WebhookEndpoint]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM webhook_endpoints ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [_endpoint(row) for row in rows]

    def endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
        return _endpoint(row) if row else None

    def create_endpoint(self, values: dict[str, Any]) -> WebhookEndpoint:
        endpoint_id = str(uuid4())
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO webhook_endpoints(
                    id, name, url, enabled, mode, events_json, content_level,
                    timeout_seconds, max_attempts, allow_private_network,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint_id,
                    values["name"],
                    values["url"],
                    int(bool(values.get("enabled", False))),
                    values.get("mode", "notification"),
                    json.dumps(values.get("events", [])),
                    values.get("content_level", "metadata"),
                    values.get("timeout_seconds", 10),
                    values.get("max_attempts", 4),
                    int(bool(values.get("allow_private_network", False))),
                    now,
                    now,
                ),
            )
        result = self.endpoint(endpoint_id)
        assert result is not None
        return result

    def update_endpoint(
        self, endpoint_id: str, values: dict[str, Any]
    ) -> WebhookEndpoint | None:
        existing = self.endpoint(endpoint_id)
        if existing is None:
            return None
        merged = {
            "name": existing.name,
            "url": existing.url,
            "enabled": existing.enabled,
            "mode": existing.mode,
            "events": list(existing.events),
            "content_level": existing.content_level,
            "timeout_seconds": existing.timeout_seconds,
            "max_attempts": existing.max_attempts,
            "allow_private_network": existing.allow_private_network,
            **values,
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE webhook_endpoints SET
                    name = ?, url = ?, enabled = ?, mode = ?, events_json = ?,
                    content_level = ?, timeout_seconds = ?, max_attempts = ?,
                    allow_private_network = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    merged["name"],
                    merged["url"],
                    int(bool(merged["enabled"])),
                    merged["mode"],
                    json.dumps(merged["events"]),
                    merged["content_level"],
                    merged["timeout_seconds"],
                    merged["max_attempts"],
                    int(bool(merged["allow_private_network"])),
                    utc_now(),
                    endpoint_id,
                ),
            )
        return self.endpoint(endpoint_id)

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM webhook_endpoints WHERE id = ?", (endpoint_id,)
            )
        return bool(cursor.rowcount)

    def subscribed_endpoints(self, event_type: str) -> list[WebhookEndpoint]:
        return [
            endpoint
            for endpoint in self.endpoints()
            if endpoint.enabled and event_type in endpoint.events
        ]

    def enqueue_event(
        self,
        *,
        event_type: str,
        subject: str,
        meeting_id: int | None,
        transcription_id: int | None,
        data: dict[str, Any],
        endpoint_ids: Sequence[str],
        expires_at: str | None,
    ) -> WebhookEvent | None:
        if not endpoint_ids:
            return None
        event_id = str(uuid4())
        occurred_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO webhook_events(
                    id, event_type, subject, meeting_id, transcription_id,
                    data_json, occurred_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    subject,
                    meeting_id,
                    transcription_id,
                    json.dumps(data),
                    occurred_at,
                    expires_at,
                ),
            )
            connection.executemany(
                """
                INSERT INTO webhook_deliveries(
                    id, event_id, endpoint_id, status, attempt_count,
                    next_attempt_at, created_at
                ) VALUES (?, ?, ?, 'queued', 0, ?, ?)
                """,
                [
                    (str(uuid4()), event_id, endpoint_id, occurred_at, occurred_at)
                    for endpoint_id in endpoint_ids
                ],
            )
        return self.event(event_id)

    def event(self, event_id: str) -> WebhookEvent | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_events WHERE id = ?", (event_id,)
            ).fetchone()
        return _event(row) if row else None

    def delivery(self, delivery_id: str) -> WebhookDelivery | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,)
            ).fetchone()
        return _delivery(row) if row else None

    def recent_deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT d.*, e.event_type, e.meeting_id, p.name AS endpoint_name
                FROM webhook_deliveries d
                JOIN webhook_events e ON e.id = d.event_id
                JOIN webhook_endpoints p ON p.id = d.endpoint_id
                ORDER BY d.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_due(self, limit: int = 20) -> list[WebhookDelivery]:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries SET status = 'expired', completed_at = ?
                WHERE status IN ('queued', 'retry')
                  AND event_id IN (
                    SELECT id FROM webhook_events
                    WHERE expires_at IS NOT NULL AND expires_at <= ?
                  )
                """,
                (now, now),
            )
            rows = connection.execute(
                """
                SELECT * FROM webhook_deliveries
                WHERE status IN ('queued', 'retry') AND next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE webhook_deliveries
                    SET status = 'delivering', attempt_count = attempt_count + 1
                    WHERE id IN ({placeholders})
                    """,
                    ids,
                )
        return [
            delivery
            for delivery_id in ids
            if (delivery := self.delivery(delivery_id)) is not None
        ]

    def finish_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        status_code: int | None,
        error: str | None,
        duration_ms: int,
        response_excerpt: str | None,
        next_attempt_at: str | None = None,
    ) -> None:
        completed_at = utc_now() if status in {"delivered", "failed", "expired"} else None
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries SET
                    status = ?, last_status_code = ?, last_error = ?,
                    duration_ms = ?, response_excerpt = ?,
                    next_attempt_at = COALESCE(?, next_attempt_at), completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    status_code,
                    error[:2000] if error else None,
                    duration_ms,
                    response_excerpt[:4000] if response_excerpt else None,
                    next_attempt_at,
                    completed_at,
                    delivery_id,
                ),
            )

    def retry_delivery(self, delivery_id: str) -> WebhookDelivery | None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE webhook_deliveries
                SET status = 'queued', next_attempt_at = ?, completed_at = NULL,
                    last_error = NULL
                WHERE id = ? AND status IN ('failed', 'expired', 'delivered')
                """,
                (utc_now(), delivery_id),
            )
        return self.delivery(delivery_id)

    def add_insights(
        self,
        *,
        endpoint: WebhookEndpoint,
        event: WebhookEvent,
        suggestions: Sequence[dict[str, Any]],
    ) -> int:
        if event.meeting_id is None:
            return 0
        now = utc_now()
        rows = []
        for suggestion in suggestions[:10]:
            text = str(suggestion.get("text") or "").strip()
            if not text:
                continue
            confidence = suggestion.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                confidence = None
            related = suggestion.get("related_segment_ids")
            related_ids = [str(item) for item in related[:20]] if isinstance(related, list) else []
            rows.append(
                (
                    str(uuid4()),
                    endpoint.id,
                    event.id,
                    event.meeting_id,
                    str(suggestion.get("type") or "insight")[:40],
                    text[:2000],
                    max(0.0, min(float(confidence), 1.0)) if confidence is not None else None,
                    json.dumps(related_ids),
                    now,
                )
            )
        if rows:
            with self.database.transaction() as connection:
                connection.executemany(
                    """
                    INSERT INTO webhook_insights(
                        id, endpoint_id, event_id, meeting_id, kind, text,
                        confidence, related_segment_ids_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
                    """,
                    rows,
                )
        return len(rows)

    def insights(self, meeting_id: int, limit: int = 50) -> list[WebhookInsight]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT i.*, p.name AS endpoint_name
                FROM webhook_insights i
                JOIN webhook_endpoints p ON p.id = i.endpoint_id
                WHERE i.meeting_id = ?
                ORDER BY i.created_at DESC LIMIT ?
                """,
                (meeting_id, limit),
            ).fetchall()
        return [_insight(row) for row in rows]

    def update_insight(self, insight_id: str, status: str) -> WebhookInsight | None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE webhook_insights SET status = ? WHERE id = ?",
                (status, insight_id),
            )
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT i.*, p.name AS endpoint_name
                FROM webhook_insights i JOIN webhook_endpoints p ON p.id = i.endpoint_id
                WHERE i.id = ?
                """,
                (insight_id,),
            ).fetchone()
        return _insight(row) if row else None

    def recover_delivering(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE webhook_deliveries SET status = 'retry', next_attempt_at = ?
                WHERE status = 'delivering'
                """,
                (utc_now(),),
            )
        return cursor.rowcount

    def purge(self, retention_days: int = 30) -> int:
        cutoff = datetime.now(UTC).timestamp() - retention_days * 86400
        cutoff_text = datetime.fromtimestamp(cutoff, UTC).isoformat(timespec="milliseconds")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM webhook_events
                WHERE occurred_at < ? AND NOT EXISTS (
                    SELECT 1 FROM webhook_deliveries d
                    WHERE d.event_id = webhook_events.id
                      AND d.status IN ('queued', 'retry', 'delivering')
                )
                """,
                (cutoff_text,),
            )
        return cursor.rowcount
