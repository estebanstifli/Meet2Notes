from __future__ import annotations

import builtins
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from local_meeting_ai.domain.entities import (
    DiarizationSegment,
    Job,
    Meeting,
    Recording,
    SegmentDraft,
    Speaker,
    SpeakerProfile,
    SpeakerTurn,
    Summary,
    SummaryTemplate,
    Transcription,
    TranscriptSegment,
)
from local_meeting_ai.domain.enums import JobStatus, JobType, MeetingStatus, SourceType
from local_meeting_ai.infrastructure.database.connection import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _summary_template_from_row(row: Any) -> SummaryTemplate:
    sections = json.loads(row["output_schema_json"] or "[]")
    return SummaryTemplate(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        system_prompt=row["system_prompt"],
        user_prompt_template=row["user_prompt_template"],
        sections=sections if isinstance(sections, list) else [],
        is_builtin=bool(row["is_builtin"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _meeting_from_row(row: Any) -> Meeting:
    keys = set(row.keys())
    return Meeting(
        id=row["id"],
        uuid=row["uuid"],
        title=row["title"],
        description=row["description"],
        status=MeetingStatus(row["status"]),
        source_type=SourceType(row["source_type"]),
        language=row["language"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_ms=row["duration_ms"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        recording_count=row["recording_count"] if "recording_count" in keys else 0,
        audio_deleted_at=row["audio_deleted_at"] if "audio_deleted_at" in keys else None,
        audio_deleted_bytes=(
            row["audio_deleted_bytes"] if "audio_deleted_bytes" in keys else None
        ),
    )


def _recording_from_row(row: Any) -> Recording:
    return Recording(
        id=row["id"],
        meeting_id=row["meeting_id"],
        role=row["role"],
        local_path=row["local_path"],
        original_filename=row["original_filename"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        duration_ms=row["duration_ms"],
        sample_rate=row["sample_rate"],
        channels=row["channels"],
        sha256=row["sha256"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        created_at=row["created_at"],
    )


def _job_from_row(row: Any) -> Job:
    return Job(
        id=row["id"],
        uuid=row["uuid"],
        meeting_id=row["meeting_id"],
        job_type=JobType(row["job_type"]),
        status=JobStatus(row["status"]),
        progress=float(row["progress"]),
        message=row["message"],
        payload=json.loads(row["payload_json"] or "{}"),
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error_text=row["error_text"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        cancel_requested=bool(row["cancel_requested"]),
    )


def _transcription_from_row(row: Any) -> Transcription:
    keys = set(row.keys())
    return Transcription(
        id=row["id"],
        meeting_id=row["meeting_id"],
        title=row["title"] or f"New Transcription {row['id']}",
        engine=row["engine"],
        model=row["model"],
        language=row["language"],
        status=row["status"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        settings=json.loads(row["settings_json"] or "{}"),
        segment_count=row["segment_count"] if "segment_count" in keys else 0,
    )


def _segment_from_row(row: Any) -> TranscriptSegment:
    return TranscriptSegment(
        id=row["id"],
        transcription_id=row["transcription_id"],
        segment_index=row["segment_index"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        text=row["text"],
        speaker_id=row["speaker_id"],
        confidence=row["confidence"],
        is_final=bool(row["is_final"]),
        metadata=json.loads(row["metadata_json"] or "{}"),
    )


def _speaker_from_row(row: Any) -> Speaker:
    keys = set(row.keys())
    return Speaker(
        id=row["id"],
        meeting_id=row["meeting_id"],
        stable_key=row["stable_key"],
        display_name=row["display_name"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        segment_count=int(row["segment_count"]) if "segment_count" in keys else 0,
        talk_time_ms=int(row["talk_time_ms"] or 0) if "talk_time_ms" in keys else 0,
        summary_status=row["summary_status"] if "summary_status" in keys else None,
        summary_markdown=row["summary_markdown"] if "summary_markdown" in keys else None,
        summary_provider=row["summary_provider"] if "summary_provider" in keys else None,
        summary_model=row["summary_model"] if "summary_model" in keys else None,
        summary_updated_at=(
            row["summary_updated_at"] if "summary_updated_at" in keys else None
        ),
    )


def _speaker_profile_from_row(row: Any) -> SpeakerProfile:
    keys = set(row.keys())
    return SpeakerProfile(
        id=row["id"],
        name=row["name"],
        sample_path=row["sample_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        meeting_count=int(row["meeting_count"]) if "meeting_count" in keys else 0,
    )


def _speaker_turn_from_row(row: Any) -> SpeakerTurn:
    return SpeakerTurn(
        id=row["id"],
        meeting_id=row["meeting_id"],
        transcription_id=row["transcription_id"],
        speaker_id=row["speaker_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
    )


def _summary_from_row(row: Any) -> Summary:
    return Summary(
        id=row["id"],
        meeting_id=row["meeting_id"],
        transcription_id=row["transcription_id"],
        template_id=row["template_id"],
        provider=row["provider"],
        model=row["model"],
        status=row["status"],
        content_markdown=row["content_markdown"],
        structured=(
            json.loads(row["structured_json"]) if row["structured_json"] else None
        ),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


class MeetingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        title: str,
        description: str | None,
        source_type: SourceType,
        language: str | None,
    ) -> Meeting:
        meeting_uuid = str(uuid4())
        now = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO meetings(
                    uuid, title, description, status, source_type, language,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_uuid,
                    title,
                    description,
                    MeetingStatus.DRAFT.value,
                    source_type.value,
                    language,
                    now,
                    now,
                ),
            )
            meeting_id = cursor.lastrowid
        assert meeting_id is not None
        meeting = self.get(meeting_id)
        assert meeting is not None
        return meeting

    def list(
        self,
        *,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[Meeting]:
        query = """
            SELECT m.*, COUNT(r.id) AS recording_count
            FROM meetings m
            LEFT JOIN recordings r ON r.meeting_id = m.id
        """
        parameters: list[Any] = []
        conditions: list[str] = []
        if search:
            conditions.append("(m.title LIKE ? OR COALESCE(m.description, '') LIKE ?)")
            term = f"%{search}%"
            parameters.extend((term, term))
        if date_from:
            conditions.append("date(COALESCE(m.started_at, m.created_at)) >= date(?)")
            parameters.append(date_from)
        if date_to:
            conditions.append("date(COALESCE(m.started_at, m.created_at)) <= date(?)")
            parameters.append(date_to)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY m.id ORDER BY m.created_at DESC LIMIT ?"
        parameters.append(limit)
        with self.database.read() as connection:
            return [
                _meeting_from_row(row)
                for row in connection.execute(query, parameters).fetchall()
            ]

    def get(self, meeting_id: int) -> Meeting | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT m.*, COUNT(r.id) AS recording_count
                FROM meetings m
                LEFT JOIN recordings r ON r.meeting_id = m.id
                WHERE m.id = ?
                GROUP BY m.id
                """,
                (meeting_id,),
            ).fetchone()
        return _meeting_from_row(row) if row else None

    def update(self, meeting_id: int, values: dict[str, Any]) -> Meeting | None:
        allowed = {
            "title",
            "description",
            "language",
            "status",
            "duration_ms",
            "started_at",
            "ended_at",
            "audio_deleted_at",
            "audio_deleted_bytes",
        }
        changes = {key: value for key, value in values.items() if key in allowed}
        if not changes:
            return self.get(meeting_id)
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        parameters = [*changes.values(), meeting_id]
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE meetings SET {assignments} WHERE id = ?",
                parameters,
            )
        return self.get(meeting_id) if cursor.rowcount else None

    def set_status(self, meeting_id: int, status: MeetingStatus) -> Meeting | None:
        return self.update(meeting_id, {"status": status.value})

    def delete(self, meeting_id: int) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        return cursor.rowcount > 0

    def count(self) -> int:
        with self.database.read() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM meetings").fetchone()
        return int(row["total"])


class RecordingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        meeting_id: int,
        role: str,
        local_path: str,
        original_filename: str,
        media_type: str | None,
        size_bytes: int,
        sha256: str,
        metadata: dict[str, Any] | None = None,
    ) -> Recording:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO recordings(
                    meeting_id, role, local_path, original_filename, media_type,
                    size_bytes, sha256, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meeting_id,
                    role,
                    local_path,
                    original_filename,
                    media_type,
                    size_bytes,
                    sha256,
                    json.dumps(metadata or {}),
                    utc_now(),
                ),
            )
            recording_id = cursor.lastrowid
        assert recording_id is not None
        recording = self.get(recording_id)
        assert recording is not None
        return recording

    def get(self, recording_id: int) -> Recording | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return _recording_from_row(row) if row else None

    def list_for_meeting(self, meeting_id: int) -> list[Recording]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM recordings WHERE meeting_id = ? ORDER BY created_at",
                (meeting_id,),
            ).fetchall()
        return [_recording_from_row(row) for row in rows]

    def latest_for_role(self, meeting_id: int, role: str) -> Recording | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM recordings
                WHERE meeting_id = ? AND role = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (meeting_id, role),
            ).fetchone()
        return _recording_from_row(row) if row else None

    def delete_for_meeting(self, meeting_id: int) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM recordings WHERE meeting_id = ?", (meeting_id,)
            )
        return cursor.rowcount

    def update_probe(
        self,
        recording_id: int,
        *,
        duration_ms: int | None,
        sample_rate: int | None,
        channels: int | None,
        size_bytes: int | None,
        metadata: dict[str, Any],
    ) -> Recording | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET duration_ms = ?, sample_rate = ?, channels = ?,
                    size_bytes = COALESCE(?, size_bytes), metadata_json = ?
                WHERE id = ?
                """,
                (
                    duration_ms,
                    sample_rate,
                    channels,
                    size_bytes,
                    json.dumps(metadata),
                    recording_id,
                ),
            )
        return self.get(recording_id) if cursor.rowcount else None


class JobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        meeting_id: int | None,
        job_type: JobType,
        payload: dict[str, Any],
        message: str = "Waiting to start",
    ) -> Job:
        job_uuid = str(uuid4())
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs(
                    uuid, meeting_id, job_type, status, progress, message,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    job_uuid,
                    meeting_id,
                    job_type.value,
                    JobStatus.QUEUED.value,
                    message,
                    json.dumps(payload),
                    utc_now(),
                ),
            )
            job_id = cursor.lastrowid
        assert job_id is not None
        job = self.get_by_id(job_id)
        assert job is not None
        return job

    def get(self, job_uuid: str) -> Job | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE uuid = ?", (job_uuid,)
            ).fetchone()
        return _job_from_row(row) if row else None

    def get_by_id(self, job_id: int) -> Job | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job_from_row(row) if row else None

    def list(
        self,
        *,
        meeting_id: int | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> Sequence[Job]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if meeting_id is not None:
            clauses.append("meeting_id = ?")
            parameters.append(meeting_id)
        if active_only:
            clauses.append("status IN ('queued', 'running', 'paused')")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def queued(self) -> Sequence[Job]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def start(self, job_uuid: str) -> Job | None:
        now = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status = ?, started_at = ?, message = ?
                WHERE uuid = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    now,
                    "Starting",
                    job_uuid,
                    JobStatus.QUEUED.value,
                ),
            )
        return self.get(job_uuid) if cursor.rowcount else None

    def update_progress(self, job_uuid: str, progress: float, message: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs SET progress = ?, message = ?
                WHERE uuid = ? AND status = ?
                """,
                (max(0.0, min(progress, 1.0)), message, job_uuid, JobStatus.RUNNING.value),
            )

    def complete(self, job_uuid: str, result: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, progress = 1, message = ?, result_json = ?,
                    completed_at = ?
                WHERE uuid = ?
                """,
                (
                    JobStatus.COMPLETED.value,
                    "Completed",
                    json.dumps(result),
                    utc_now(),
                    job_uuid,
                ),
            )

    def fail(self, job_uuid: str, error_text: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, error_text = ?, completed_at = ?
                WHERE uuid = ?
                """,
                (
                    JobStatus.FAILED.value,
                    "Could not complete",
                    error_text[:4000],
                    utc_now(),
                    job_uuid,
                ),
            )

    def mark_cancelled(self, job_uuid: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, completed_at = ?
                WHERE uuid = ?
                """,
                (
                    JobStatus.CANCELLED.value,
                    "Cancelled",
                    utc_now(),
                    job_uuid,
                ),
            )

    def request_cancel(self, job_uuid: str) -> Job | None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs SET cancel_requested = 1, message = ?
                WHERE uuid = ? AND status IN ('queued', 'running', 'paused')
                """,
                ("Cancellation requested", job_uuid),
            )
        return self.get(job_uuid)

    def request_cancel_for_meeting(self, meeting_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE jobs SET cancel_requested = 1, message = ?
                WHERE meeting_id = ? AND status IN ('queued', 'running', 'paused')
                """,
                ("Cancellation requested", meeting_id),
            )

    def recover_interrupted(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, message = ?, error_text = ?, completed_at = ?
                WHERE status IN ('running', 'paused')
                """,
                (
                    JobStatus.FAILED.value,
                    "Interrupted by application shutdown",
                    "The job was not replayed automatically because doing so may be unsafe.",
                    utc_now(),
                ),
            )
        return cursor.rowcount


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_all(self) -> dict[str, Any]:
        with self.database.read() as connection:
            rows = connection.execute("SELECT key, value_json FROM settings").fetchall()
        return {row["key"]: json.loads(row["value_json"]) for row in rows}

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                [(key, json.dumps(value), now) for key, value in values.items()],
            )
        return self.get_all()


class TranscriptionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        meeting_id: int,
        title: str,
        engine: str,
        model: str,
        language: str | None,
        settings: dict[str, Any],
    ) -> Transcription:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transcriptions(
                    meeting_id, title, engine, model, language, status, is_active,
                    created_at, settings_json
                ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?)
                """,
                (
                    meeting_id,
                    title,
                    engine,
                    model,
                    language,
                    utc_now(),
                    json.dumps(settings),
                ),
            )
            transcription_id = cursor.lastrowid
        assert transcription_id is not None
        transcription = self.get(transcription_id)
        assert transcription is not None
        return transcription

    def next_default_title(self) -> str:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT title FROM transcriptions
                WHERE title = 'New Transcription'
                   OR title GLOB 'New Transcription [0-9]*'
                """
            ).fetchall()
        existing = {str(row["title"]) for row in rows}
        if "New Transcription" not in existing:
            return "New Transcription"
        suffix = 2
        while f"New Transcription {suffix}" in existing:
            suffix += 1
        return f"New Transcription {suffix}"

    def update_title(self, transcription_id: int, title: str) -> Transcription | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT meeting_id FROM transcriptions WHERE id = ?",
                (transcription_id,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                "UPDATE transcriptions SET title = ? WHERE id = ?",
                (title, transcription_id),
            )
            connection.execute(
                "UPDATE meetings SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), row["meeting_id"]),
            )
        return self.get(transcription_id) if cursor.rowcount else None

    def recover_interrupted(self) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE transcriptions
                SET status = 'failed', completed_at = ?
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
        return cursor.rowcount

    def get(self, transcription_id: int) -> Transcription | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT t.*, COUNT(s.id) AS segment_count
                FROM transcriptions t
                LEFT JOIN transcript_segments s ON s.transcription_id = t.id
                WHERE t.id = ?
                GROUP BY t.id
                """,
                (transcription_id,),
            ).fetchone()
        return _transcription_from_row(row) if row else None

    def list_for_meeting(self, meeting_id: int) -> list[Transcription]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT t.*, COUNT(s.id) AS segment_count
                FROM transcriptions t
                LEFT JOIN transcript_segments s ON s.transcription_id = t.id
                WHERE t.meeting_id = ?
                GROUP BY t.id
                ORDER BY t.created_at DESC
                """,
                (meeting_id,),
            ).fetchall()
        return [_transcription_from_row(row) for row in rows]

    def active_for_meeting(self, meeting_id: int) -> Transcription | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT t.*, COUNT(s.id) AS segment_count
                FROM transcriptions t
                LEFT JOIN transcript_segments s ON s.transcription_id = t.id
                WHERE t.meeting_id = ? AND t.is_active = 1
                GROUP BY t.id
                LIMIT 1
                """,
                (meeting_id,),
            ).fetchone()
        return _transcription_from_row(row) if row else None

    def segments(self, transcription_id: int) -> list[TranscriptSegment]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM transcript_segments
                WHERE transcription_id = ?
                ORDER BY segment_index
                """,
                (transcription_id,),
            ).fetchall()
        return [_segment_from_row(row) for row in rows]

    def segment_page(
        self,
        transcription_id: int,
        *,
        after_segment_index: int = -1,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> tuple[list[TranscriptSegment], bool]:
        conditions = ["transcription_id = ?", "segment_index > ?"]
        parameters: list[Any] = [transcription_id, after_segment_index]
        if start_ms is not None:
            conditions.append("end_ms >= ?")
            parameters.append(start_ms)
        if end_ms is not None:
            conditions.append("start_ms <= ?")
            parameters.append(end_ms)
        bounded_limit = max(1, min(int(limit), 200))
        parameters.append(bounded_limit + 1)
        with self.database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM transcript_segments
                WHERE {" AND ".join(conditions)}
                ORDER BY segment_index
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        has_more = len(rows) > bounded_limit
        return (
            [_segment_from_row(row) for row in rows[:bounded_limit]],
            has_more,
        )

    def search_active_segments(
        self,
        query: str,
        *,
        meeting_id: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        terms = [
            value for value in re.findall(r"[^\W_]+", query, flags=re.UNICODE) if len(value) > 1
        ]
        if not terms:
            return []
        match_query = " OR ".join(
            f'"{value.replace(chr(34), chr(34) * 2)}"' for value in terms[:32]
        )
        conditions = ["t.is_active = 1", "t.status = 'completed'"]
        parameters: list[Any] = [match_query]
        if meeting_id is not None:
            conditions.append("t.meeting_id = ?")
            parameters.append(meeting_id)
        parameters.append(max(1, min(int(limit), 50)))
        with self.database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT m.id AS meeting_id, m.title AS meeting_title,
                       COALESCE(m.started_at, m.created_at) AS meeting_date,
                       t.id AS transcription_id, ts.segment_index,
                       ts.start_ms, ts.end_ms, ts.text,
                       COALESCE(s.display_name, 'Speaker') AS speaker,
                       bm25(transcript_search, 0.0, 0.5, 2.0) AS bm25_score
                FROM transcript_search
                JOIN transcript_segments ts ON ts.id = transcript_search.segment_id
                JOIN transcriptions t ON t.id = ts.transcription_id
                JOIN meetings m ON m.id = t.meeting_id
                LEFT JOIN speakers s ON s.id = ts.speaker_id
                WHERE transcript_search MATCH ? AND {" AND ".join(conditions)}
                ORDER BY bm25_score
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_segment(self, segment_id: int) -> TranscriptSegment | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM transcript_segments WHERE id = ?",
                (segment_id,),
            ).fetchone()
        return _segment_from_row(row) if row else None

    def speakers_for_transcription(self, transcription_id: int) -> list[Speaker]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT s.*,
                    (SELECT COUNT(*) FROM transcript_segments ts
                     WHERE ts.transcription_id = ? AND ts.speaker_id = s.id)
                        AS segment_count,
                    (SELECT COALESCE(SUM(st.end_ms - st.start_ms), 0)
                     FROM speaker_turns st
                     WHERE st.transcription_id = ? AND st.speaker_id = s.id)
                        AS talk_time_ms
                FROM speakers s
                WHERE EXISTS (
                    SELECT 1 FROM transcript_segments ts
                    WHERE ts.transcription_id = ? AND ts.speaker_id = s.id
                ) OR EXISTS (
                    SELECT 1 FROM speaker_turns st
                    WHERE st.transcription_id = ? AND st.speaker_id = s.id
                )
                ORDER BY s.id
                """,
                (transcription_id, transcription_id, transcription_id, transcription_id),
            ).fetchall()
        return [_speaker_from_row(row) for row in rows]

    def get_speaker(self, speaker_id: int) -> Speaker | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT s.*,
                    (SELECT COUNT(*) FROM transcript_segments ts
                     WHERE ts.speaker_id = s.id) AS segment_count,
                    (SELECT COALESCE(SUM(st.end_ms - st.start_ms), 0)
                     FROM speaker_turns st
                     WHERE st.speaker_id = s.id) AS talk_time_ms
                FROM speakers s WHERE s.id = ?
                """,
                (speaker_id,),
            ).fetchone()
        return _speaker_from_row(row) if row else None

    def rename_speaker(self, speaker_id: int, display_name: str) -> Speaker | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE speakers SET display_name = ? WHERE id = ?",
                (display_name, speaker_id),
            )
        return self.get_speaker(speaker_id) if cursor.rowcount else None

    def set_speaker_summary_status(
        self,
        speaker_id: int,
        status: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> Speaker | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE speakers
                SET summary_status = ?,
                    summary_provider = COALESCE(?, summary_provider),
                    summary_model = COALESCE(?, summary_model),
                    summary_updated_at = ?
                WHERE id = ?
                """,
                (status, provider, model, utc_now(), speaker_id),
            )
        return self.get_speaker(speaker_id) if cursor.rowcount else None

    def complete_speaker_summary(
        self,
        speaker_id: int,
        content_markdown: str,
    ) -> Speaker | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE speakers
                SET summary_status = 'completed', summary_markdown = ?,
                    summary_updated_at = ?
                WHERE id = ?
                """,
                (content_markdown, utc_now(), speaker_id),
            )
        return self.get_speaker(speaker_id) if cursor.rowcount else None

    def speaker_turns(
        self,
        transcription_id: int,
        speaker_id: int | None = None,
    ) -> list[SpeakerTurn]:
        query = """
            SELECT * FROM speaker_turns
            WHERE transcription_id = ?
        """
        parameters: list[Any] = [transcription_id]
        if speaker_id is not None:
            query += " AND speaker_id = ?"
            parameters.append(speaker_id)
        query += " ORDER BY start_ms"
        with self.database.read() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_speaker_turn_from_row(row) for row in rows]

    def mark_running(self, transcription_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE transcriptions SET status = 'running' WHERE id = ?",
                (transcription_id,),
            )

    def clear_segments(self, transcription_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM transcript_segments WHERE transcription_id = ?",
                (transcription_id,),
            )

    def append_segment(
        self,
        transcription_id: int,
        segment: SegmentDraft,
        *,
        is_final: bool = False,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO transcript_segments(
                    transcription_id, segment_index, start_ms, end_ms, text,
                    confidence, is_final, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transcription_id, segment_index) DO UPDATE SET
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    text = excluded.text,
                    confidence = excluded.confidence,
                    is_final = excluded.is_final,
                    metadata_json = excluded.metadata_json
                """,
                (
                    transcription_id,
                    segment.index,
                    segment.start_ms,
                    segment.end_ms,
                    segment.text,
                    segment.confidence,
                    int(is_final),
                    json.dumps(segment.metadata or {}),
                ),
            )

    def complete(
        self,
        transcription_id: int,
        *,
        language: str | None,
        segments: Sequence[SegmentDraft],
    ) -> Transcription | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT meeting_id FROM transcriptions WHERE id = ?",
                (transcription_id,),
            ).fetchone()
            if not row:
                return None
            meeting_id = row["meeting_id"]
            connection.execute(
                "UPDATE transcriptions SET is_active = 0 WHERE meeting_id = ?",
                (meeting_id,),
            )
            connection.execute(
                "DELETE FROM transcript_segments WHERE transcription_id = ?",
                (transcription_id,),
            )
            connection.executemany(
                """
                INSERT INTO transcript_segments(
                    transcription_id, segment_index, start_ms, end_ms, text,
                    confidence, is_final, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                [
                    (
                        transcription_id,
                        segment.index,
                        segment.start_ms,
                        segment.end_ms,
                        segment.text,
                        segment.confidence,
                        json.dumps(segment.metadata or {}),
                    )
                    for segment in segments
                ],
            )
            connection.execute(
                """
                UPDATE transcriptions
                SET language = ?, status = 'completed', is_active = 1,
                    completed_at = ?
                WHERE id = ?
                """,
                (language, utc_now(), transcription_id),
            )
        return self.get(transcription_id)

    def complete_realtime(self, transcription_id: int) -> Transcription | None:
        """Promote the captured live segments when a final ASR pass is skipped."""

        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT meeting_id FROM transcriptions WHERE id = ?",
                (transcription_id,),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE transcriptions SET is_active = 0 WHERE meeting_id = ?",
                (row["meeting_id"],),
            )
            connection.execute(
                "UPDATE transcript_segments SET is_final = 1 WHERE transcription_id = ?",
                (transcription_id,),
            )
            connection.execute(
                """
                UPDATE transcriptions
                SET status = 'completed', is_active = 1, completed_at = ?
                WHERE id = ?
                """,
                (utc_now(), transcription_id),
            )
        return self.get(transcription_id)

    def set_status(self, transcription_id: int, status: str) -> None:
        completed_at = utc_now() if status in {"failed", "cancelled"} else None
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE transcriptions
                SET status = ?, completed_at = COALESCE(?, completed_at)
                WHERE id = ?
                """,
                (status, completed_at, transcription_id),
            )

    def activate(self, transcription_id: int) -> Transcription | None:
        transcription = self.get(transcription_id)
        if not transcription or transcription.status != "completed":
            return None
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE transcriptions SET is_active = 0 WHERE meeting_id = ?",
                (transcription.meeting_id,),
            )
            connection.execute(
                "UPDATE transcriptions SET is_active = 1 WHERE id = ?",
                (transcription_id,),
            )
        return self.get(transcription_id)

    def update_segment(self, segment_id: int, text: str) -> TranscriptSegment | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE transcript_segments SET text = ? WHERE id = ?",
                (text, segment_id),
            )
        return self.get_segment(segment_id) if cursor.rowcount else None

    def find_replace(
        self,
        transcription_id: int,
        *,
        find: str,
        replacement: str,
        case_sensitive: bool,
    ) -> int:
        with self.database.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, text FROM transcript_segments
                WHERE transcription_id = ?
                """,
                (transcription_id,),
            ).fetchall()
            updates: list[tuple[str, int]] = []
            replacements = 0
            pattern = re.compile(re.escape(find), 0 if case_sensitive else re.IGNORECASE)
            for row in rows:
                updated_text, count = pattern.subn(replacement, row["text"])
                if count:
                    replacements += count
                    updates.append((updated_text, row["id"]))
            connection.executemany(
                "UPDATE transcript_segments SET text = ? WHERE id = ?",
                updates,
            )
        return replacements

    def assign_diarization(
        self,
        *,
        meeting_id: int,
        transcription_id: int,
        diarization: Sequence[DiarizationSegment],
        minimum_overlap_ratio: float,
        recognized_profiles: dict[int, SpeakerProfile] | None = None,
    ) -> int:
        speaker_numbers = sorted({item.speaker for item in diarization})
        with self.database.transaction() as connection:
            existing_speakers = {
                str(row["stable_key"]): row
                for row in connection.execute(
                    """
                    SELECT stable_key, display_name, summary_status, summary_markdown,
                           summary_provider, summary_model, summary_updated_at
                    FROM speakers
                    WHERE meeting_id = ? AND stable_key LIKE 'diarization:%'
                    """,
                    (meeting_id,),
                ).fetchall()
            }
            connection.execute(
                "DELETE FROM speaker_turns WHERE transcription_id = ?",
                (transcription_id,),
            )
            connection.execute(
                "DELETE FROM speakers WHERE meeting_id = ? AND stable_key LIKE 'diarization:%'",
                (meeting_id,),
            )
            speaker_ids: dict[int, int] = {}
            for number in speaker_numbers:
                stable_key = f"diarization:{number}"
                existing = existing_speakers.get(stable_key)
                profile = (recognized_profiles or {}).get(number)
                cursor = connection.execute(
                    """
                    INSERT INTO speakers(
                        meeting_id, stable_key, display_name, profile_id, created_at,
                        summary_status, summary_markdown, summary_provider,
                        summary_model, summary_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meeting_id,
                        stable_key,
                        profile.name
                        if profile
                        else (
                            str(existing["display_name"])
                            if existing
                            else f"Speaker {number + 1}"
                        ),
                        profile.id if profile else None,
                        utc_now(),
                        existing["summary_status"] if existing else None,
                        existing["summary_markdown"] if existing else None,
                        existing["summary_provider"] if existing else None,
                        existing["summary_model"] if existing else None,
                        existing["summary_updated_at"] if existing else None,
                    ),
                )
                assert cursor.lastrowid is not None
                speaker_ids[number] = cursor.lastrowid

            connection.executemany(
                """
                INSERT INTO speaker_turns(
                    meeting_id, transcription_id, speaker_id,
                    start_ms, end_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        meeting_id,
                        transcription_id,
                        speaker_ids[turn.speaker],
                        turn.start_ms,
                        turn.end_ms,
                        utc_now(),
                    )
                    for turn in diarization
                    if turn.end_ms > turn.start_ms
                ],
            )

            rows = connection.execute(
                """
                SELECT id, start_ms, end_ms FROM transcript_segments
                WHERE transcription_id = ?
                """,
                (transcription_id,),
            ).fetchall()
            assignments: list[tuple[int, int]] = []
            for row in rows:
                duration = max(1, int(row["end_ms"]) - int(row["start_ms"]))
                overlaps: dict[int, int] = {}
                for turn in diarization:
                    overlap = max(
                        0,
                        min(int(row["end_ms"]), turn.end_ms)
                        - max(int(row["start_ms"]), turn.start_ms),
                    )
                    overlaps[turn.speaker] = overlaps.get(turn.speaker, 0) + overlap
                if not overlaps:
                    continue
                speaker, overlap = max(overlaps.items(), key=lambda item: item[1])
                if overlap / duration >= minimum_overlap_ratio:
                    assignments.append((speaker_ids[speaker], int(row["id"])))
            connection.executemany(
                "UPDATE transcript_segments SET speaker_id = ? WHERE id = ?",
                assignments,
            )
        return len(assignments)


class SpeakerProfileRepository:
    """Persistent, meeting-independent voice identities."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, *, search: str | None = None) -> list[SpeakerProfile]:
        query = """
            SELECT sp.*, COUNT(DISTINCT s.meeting_id) AS meeting_count
            FROM speaker_profiles sp
            LEFT JOIN speakers s ON s.profile_id = sp.id
        """
        parameters: list[Any] = []
        if search:
            query += " WHERE sp.name LIKE ?"
            parameters.append(f"%{search.strip()}%")
        query += " GROUP BY sp.id ORDER BY sp.name COLLATE NOCASE"
        with self.database.read() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_speaker_profile_from_row(row) for row in rows]

    def get(self, profile_id: int) -> SpeakerProfile | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM speaker_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return _speaker_profile_from_row(row) if row else None

    def get_by_name(self, name: str) -> SpeakerProfile | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM speaker_profiles WHERE lower(name) = lower(?)", (name,)
            ).fetchone()
        return _speaker_profile_from_row(row) if row else None

    def create(self, *, name: str, sample_path: str | None) -> SpeakerProfile:
        now = utc_now()
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    "INSERT INTO speaker_profiles"
                    "(name, sample_path, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (name, sample_path, now, now),
                )
        except Exception as error:
            if "UNIQUE" in str(error).upper():
                raise ValueError("A saved voice already uses this name") from error
            raise
        assert cursor.lastrowid is not None
        profile = self.get(cursor.lastrowid)
        assert profile is not None
        return profile

    def update(
        self,
        profile_id: int,
        *,
        name: str | None = None,
        sample_path: str | None = None,
    ) -> SpeakerProfile | None:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if sample_path is not None:
            changes["sample_path"] = sample_path
        if not changes:
            return self.get(profile_id)
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    f"UPDATE speaker_profiles SET {assignments} WHERE id = ?",
                    [*changes.values(), profile_id],
                )
        except Exception as error:
            if "UNIQUE" in str(error).upper():
                raise ValueError("A saved voice already uses this name") from error
            raise
        return self.get(profile_id) if cursor.rowcount else None

    def delete(self, profile_id: int) -> bool:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE speakers SET profile_id = NULL WHERE profile_id = ?", (profile_id,)
            )
            cursor = connection.execute("DELETE FROM speaker_profiles WHERE id = ?", (profile_id,))
        return cursor.rowcount > 0

    def link_speaker(self, speaker_id: int, profile_id: int) -> Speaker | None:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE speakers SET profile_id = ?, "
                "display_name = (SELECT name FROM speaker_profiles WHERE id = ?) "
                "WHERE id = ?",
                (profile_id, profile_id, speaker_id),
            )
        if cursor.rowcount:
            return TranscriptionRepository(self.database).get_speaker(speaker_id)
        return None

    def meetings_for_profiles(self, profile_ids: Sequence[int]) -> builtins.list[Meeting]:
        if not profile_ids:
            return []
        marks = ", ".join("?" for _ in profile_ids)
        query = f"""
            SELECT m.*, COUNT(r.id) AS recording_count
            FROM meetings m
            JOIN speakers s ON s.meeting_id = m.id
            LEFT JOIN recordings r ON r.meeting_id = m.id
            WHERE s.profile_id IN ({marks})
            GROUP BY m.id
            HAVING COUNT(DISTINCT s.profile_id) = ?
            ORDER BY m.created_at DESC
        """
        with self.database.read() as connection:
            rows = connection.execute(query, [*profile_ids, len(profile_ids)]).fetchall()
        return [_meeting_from_row(row) for row in rows]


class SummaryTemplateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def seed_builtins(self, templates: Sequence[dict[str, Any]]) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            for template in templates:
                existing = connection.execute(
                    "SELECT id FROM summary_templates WHERE name = ? AND is_builtin = 1",
                    (template["name"],),
                ).fetchone()
                values = (
                    template.get("description"),
                    template["system_prompt"],
                    template["user_prompt_template"],
                    json.dumps(template["sections"]),
                    now,
                )
                if existing:
                    connection.execute(
                        """
                        UPDATE summary_templates
                        SET description = ?, system_prompt = ?, user_prompt_template = ?,
                            output_schema_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (*values, existing["id"]),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO summary_templates(
                            name, description, system_prompt, user_prompt_template,
                            output_schema_json, is_builtin, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            template["name"],
                            template.get("description"),
                            template["system_prompt"],
                            template["user_prompt_template"],
                            json.dumps(template["sections"]),
                            now,
                            now,
                        ),
                    )

    def list(self) -> list[SummaryTemplate]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM summary_templates
                ORDER BY is_builtin DESC, name COLLATE NOCASE
                """
            ).fetchall()
        return [_summary_template_from_row(row) for row in rows]

    def get(self, template_id: int) -> SummaryTemplate | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM summary_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        return _summary_template_from_row(row) if row else None

    def default(self, configured_id: Any = None) -> SummaryTemplate:
        try:
            selected = self.get(int(configured_id)) if configured_id is not None else None
        except (TypeError, ValueError):
            selected = None
        if selected:
            return selected
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM summary_templates
                WHERE name = 'General Meeting' AND is_builtin = 1
                ORDER BY id LIMIT 1
                """
            ).fetchone()
        if not row:
            raise RuntimeError("The built-in General Meeting note format is missing")
        return _summary_template_from_row(row)

    def create(self, values: dict[str, Any]) -> SummaryTemplate:
        now = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO summary_templates(
                    name, description, system_prompt, user_prompt_template,
                    output_schema_json, is_builtin, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    values["name"],
                    values.get("description"),
                    values["system_prompt"],
                    values["user_prompt_template"],
                    json.dumps(values["sections"]),
                    now,
                    now,
                ),
            )
            assert cursor.lastrowid is not None
            template_id = cursor.lastrowid
        template = self.get(template_id)
        assert template is not None
        return template

    def update(self, template_id: int, values: dict[str, Any]) -> SummaryTemplate | None:
        current = self.get(template_id)
        if not current:
            return None
        if current.is_builtin:
            raise ValueError("Built-in note formats cannot be edited")
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE summary_templates
                SET name = ?, description = ?, system_prompt = ?,
                    user_prompt_template = ?, output_schema_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    values["name"],
                    values.get("description"),
                    values["system_prompt"],
                    values["user_prompt_template"],
                    json.dumps(values["sections"]),
                    utc_now(),
                    template_id,
                ),
            )
        return self.get(template_id)

    def delete(self, template_id: int) -> bool:
        current = self.get(template_id)
        if not current:
            return False
        if current.is_builtin:
            raise ValueError("Built-in note formats cannot be deleted")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM summary_templates WHERE id = ?",
                (template_id,),
            )
        return bool(cursor.rowcount)


class SummaryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        *,
        meeting_id: int,
        transcription_id: int,
        provider: str,
        model: str,
        template_id: int | None = None,
    ) -> Summary:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO summaries(
                    meeting_id, transcription_id, template_id, provider, model, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                """,
                (meeting_id, transcription_id, template_id, provider, model, utc_now()),
            )
            assert cursor.lastrowid is not None
            summary_id = cursor.lastrowid
        summary = self.get(summary_id)
        assert summary is not None
        return summary

    def get(self, summary_id: int) -> Summary | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM summaries WHERE id = ?",
                (summary_id,),
            ).fetchone()
        return _summary_from_row(row) if row else None

    def list_for_meeting(self, meeting_id: int) -> list[Summary]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT * FROM summaries
                WHERE meeting_id = ?
                ORDER BY created_at DESC
                """,
                (meeting_id,),
            ).fetchall()
        return [_summary_from_row(row) for row in rows]

    def mark_running(self, summary_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE summaries SET status = 'running' WHERE id = ?",
                (summary_id,),
            )

    def complete(
        self,
        summary_id: int,
        content_markdown: str,
        structured: dict[str, Any] | None = None,
    ) -> Summary | None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE summaries
                SET status = 'completed', content_markdown = ?,
                    structured_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    content_markdown,
                    json.dumps(structured) if structured else None,
                    utc_now(),
                    summary_id,
                ),
            )
        return self.get(summary_id)

    def fail(self, summary_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE summaries
                SET status = 'failed', completed_at = ?
                WHERE id = ?
                """,
                (utc_now(), summary_id),
            )

    def update_content(self, summary_id: int, content_markdown: str) -> Summary | None:
        current = self.get(summary_id)
        if not current:
            return None
        if current.status != "completed":
            raise ValueError("Only completed AI notes can be edited")
        edited_at = utc_now()
        structured = dict(current.structured or {})
        manual_edit = structured.get("manual_edit")
        edit_metadata = dict(manual_edit) if isinstance(manual_edit, dict) else {}
        edit_metadata.setdefault("original_content_markdown", current.content_markdown or "")
        edit_metadata["edited_at"] = edited_at
        structured["manual_edit"] = edit_metadata
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE summaries
                SET content_markdown = ?, structured_json = ?
                WHERE id = ?
                """,
                (content_markdown, json.dumps(structured), summary_id),
            )
        return self.get(summary_id)


class PluginExecutionRepository:
    """Privacy-preserving provenance for extension hook executions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        plugin_id: str,
        plugin_version: str,
        hook: str,
        kind: str,
        context: Any,
        input_digest: str,
    ) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO plugin_executions(
                    plugin_id, plugin_version, hook, kind, pipeline_id, job_uuid,
                    meeting_id, transcription_id, status, input_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    plugin_id,
                    plugin_version,
                    hook,
                    kind,
                    getattr(context, "pipeline_id", None),
                    getattr(context, "job_uuid", None),
                    getattr(context, "meeting_id", None),
                    getattr(context, "transcription_id", None),
                    input_digest,
                    utc_now(),
                ),
            )
            assert cursor.lastrowid is not None
            return int(cursor.lastrowid)

    def finish(
        self,
        execution_id: int,
        *,
        status: str,
        duration_ms: int,
        output_digest: str | None = None,
        message: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE plugin_executions
                SET status = ?, duration_ms = ?, output_digest = ?, message = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    duration_ms,
                    output_digest,
                    message,
                    utc_now(),
                    execution_id,
                ),
            )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        clean_limit = max(1, min(limit, 500))
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT id, plugin_id, plugin_version, hook, kind, pipeline_id,
                       job_uuid, meeting_id, transcription_id, status, duration_ms,
                       input_digest, output_digest, message, created_at, completed_at
                FROM plugin_executions
                ORDER BY id DESC
                LIMIT ?
                """,
                (clean_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_by_plugin(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for execution in self.recent(500):
            plugin_id = str(execution["plugin_id"])
            latest.setdefault(plugin_id, execution)
        return latest
