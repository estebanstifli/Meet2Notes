from __future__ import annotations

from pathlib import Path

from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner


def test_initial_migration_is_complete_and_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "migration.db")
    runner = MigrationRunner(database)

    assert runner.apply() == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert runner.apply() == []

    with database.read() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        meeting_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(meetings)")
        }

    assert {
        "meetings",
        "recordings",
        "transcriptions",
        "transcript_segments",
        "summaries",
        "summary_templates",
        "jobs",
        "settings",
        "transcript_search",
        "speaker_turns",
        "plugin_executions",
        "rag_chunks",
        "rag_chunks_fts",
        "webhook_endpoints",
        "webhook_events",
        "webhook_deliveries",
        "webhook_insights",
        "live_assistant_sessions",
        "live_assistant_insights",
    } <= tables
    assert foreign_keys == 1
    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert {"audio_deleted_at", "audio_deleted_bytes"} <= meeting_columns
