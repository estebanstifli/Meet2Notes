from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from local_meeting_ai.domain.live_assistant import (
    LiveAssistantInsight,
    LiveAssistantSession,
)
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.repositories import utc_now


def _session(row: Any) -> LiveAssistantSession:
    return LiveAssistantSession(
        id=str(row["id"]),
        meeting_id=int(row["meeting_id"]),
        transcription_id=int(row["transcription_id"]),
        status=row["status"],
        configuration=json.loads(row["configuration_json"] or "{}"),
        memory=json.loads(row["memory_json"] or "{}"),
        last_sequence=int(row["last_sequence"] or 0),
        last_error=row["last_error"],
        started_at=str(row["started_at"]),
        stopped_at=row["stopped_at"],
    )


def _insight(row: Any) -> LiveAssistantInsight:
    return LiveAssistantInsight(
        id=str(row["id"]),
        session_id=row["session_id"],
        meeting_id=int(row["meeting_id"]),
        transcription_id=(
            int(row["transcription_id"]) if row["transcription_id"] is not None else None
        ),
        kind=str(row["kind"]),
        text=str(row["text"]),
        confidence=(float(row["confidence"]) if row["confidence"] is not None else None),
        related_segment_ids=tuple(
            str(item) for item in json.loads(row["related_segment_ids_json"] or "[]")
        ),
        start_ms=int(row["start_ms"]) if row["start_ms"] is not None else None,
        end_ms=int(row["end_ms"]) if row["end_ms"] is not None else None,
        provider=str(row["provider"]),
        model=str(row["model"]),
        prompt_tokens=(int(row["prompt_tokens"]) if row["prompt_tokens"] is not None else None),
        completion_tokens=(
            int(row["completion_tokens"]) if row["completion_tokens"] is not None else None
        ),
        latency_ms=int(row["latency_ms"]) if row["latency_ms"] is not None else None,
        status=row["status"],
        created_at=str(row["created_at"]),
    )


class LiveAssistantRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def recover_active(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE live_assistant_sessions
                SET status = 'interrupted', stopped_at = ?,
                    last_error = 'Application stopped while the Live AI Assistant was active'
                WHERE status = 'active'
                """,
                (utc_now(),),
            )
        return cursor.rowcount

    def start_session(
        self,
        *,
        session_id: str,
        meeting_id: int,
        transcription_id: int,
        configuration: dict[str, Any],
    ) -> LiveAssistantSession:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO live_assistant_sessions(
                    id, meeting_id, transcription_id, status, configuration_json,
                    memory_json, last_sequence, started_at
                ) VALUES (?, ?, ?, 'active', ?, '{}', 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    meeting_id = excluded.meeting_id,
                    transcription_id = excluded.transcription_id,
                    status = 'active',
                    configuration_json = excluded.configuration_json,
                    memory_json = '{}',
                    last_sequence = 0,
                    last_error = NULL,
                    started_at = excluded.started_at,
                    stopped_at = NULL
                """,
                (
                    session_id,
                    meeting_id,
                    transcription_id,
                    json.dumps(configuration),
                    now,
                ),
            )
        session = self.session(session_id)
        if session is None:
            raise RuntimeError("Could not create Live AI Assistant session")
        return session

    def session(self, session_id: str) -> LiveAssistantSession | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM live_assistant_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return _session(row) if row else None

    def latest_session(self, meeting_id: int) -> LiveAssistantSession | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM live_assistant_sessions
                WHERE meeting_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (meeting_id,),
            ).fetchone()
        return _session(row) if row else None

    def update_session(
        self,
        session_id: str,
        *,
        memory: dict[str, Any] | None = None,
        last_sequence: int | None = None,
        last_error: str | None = None,
    ) -> LiveAssistantSession | None:
        existing = self.session(session_id)
        if existing is None:
            return None
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE live_assistant_sessions
                SET memory_json = ?, last_sequence = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    json.dumps(memory if memory is not None else existing.memory),
                    last_sequence if last_sequence is not None else existing.last_sequence,
                    last_error,
                    session_id,
                ),
            )
        return self.session(session_id)

    def stop_session(
        self,
        session_id: str,
        *,
        status: str = "stopped",
        error: str | None = None,
    ) -> LiveAssistantSession | None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE live_assistant_sessions
                SET status = ?, last_error = ?, stopped_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (status, error, utc_now(), session_id),
            )
        return self.session(session_id)

    def add_insight(
        self,
        *,
        session_id: str,
        meeting_id: int,
        transcription_id: int,
        kind: str,
        text: str,
        confidence: float | None,
        related_segment_ids: Sequence[str],
        start_ms: int | None,
        end_ms: int | None,
        provider: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: int | None,
    ) -> LiveAssistantInsight:
        insight_id = str(uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO live_assistant_insights(
                    id, session_id, meeting_id, transcription_id, kind, text,
                    confidence, related_segment_ids_json, start_ms, end_ms,
                    provider, model, prompt_tokens, completion_tokens, latency_ms,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
                """,
                (
                    insight_id,
                    session_id,
                    meeting_id,
                    transcription_id,
                    kind[:40] or "insight",
                    text[:4000],
                    confidence,
                    json.dumps([str(item) for item in related_segment_ids[:20]]),
                    start_ms,
                    end_ms,
                    provider[:80],
                    model[:300],
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    utc_now(),
                ),
            )
        insight = self.insight(insight_id)
        if insight is None:
            raise RuntimeError("Could not create Live AI Assistant insight")
        return insight

    def insight(self, insight_id: str) -> LiveAssistantInsight | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM live_assistant_insights WHERE id = ?",
                (insight_id,),
            ).fetchone()
        return _insight(row) if row else None

    def insights(self, meeting_id: int, limit: int = 50) -> list[LiveAssistantInsight]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM live_assistant_insights
                WHERE meeting_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (meeting_id, limit),
            ).fetchall()
        return [_insight(row) for row in rows]

    def update_insight(
        self,
        insight_id: str,
        status: str,
    ) -> LiveAssistantInsight | None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE live_assistant_insights SET status = ? WHERE id = ?
                """,
                (status, insight_id),
            )
        return self.insight(insight_id)
