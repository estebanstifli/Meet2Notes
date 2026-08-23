from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from local_meeting_ai import updater
from local_meeting_ai.application.ai_services import configured_values
from local_meeting_ai.domain.enums import SourceType
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import (
    MeetingRepository,
    SettingsRepository,
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_release_check_uses_semver_and_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(_request: object, *, timeout: float) -> FakeResponse:
        nonlocal calls
        calls += 1
        assert timeout == 3.0
        return FakeResponse(
            json.dumps(
                {
                    "tag_name": "v0.6.0",
                    "name": "Meet2Notes 0.6.0",
                    "html_url": "https://github.com/estebanstifli/Meet2Notes/releases/v0.6.0",
                    "published_at": "2026-08-23T12:00:00Z",
                    "draft": False,
                    "prerelease": False,
                }
            ).encode()
        )

    monkeypatch.setattr(updater, "installation_directory", lambda: tmp_path)
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)

    release = updater.latest_release(force=True)
    assert release is not None
    assert release.version == "0.6.0"
    assert updater.is_newer_version(release.version, "0.5.0") is True
    assert updater.is_newer_version("0.5.0", "0.5.0") is False
    assert updater.latest_release() == release
    assert calls == 1
    updater.defer_release(release)
    assert updater.latest_release() is None
    assert calls == 1


def test_pre_update_backup_preserves_settings_meetings_and_rag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = tmp_path / "installation"
    data = tmp_path / "private-data"
    installation.mkdir()
    data.mkdir()
    monkeypatch.setattr(updater, "installation_directory", lambda: installation)

    database = Database(data / "app.db")
    MigrationRunner(database).apply()
    preferences = SettingsRepository(database)
    expected_rag: dict[str, Any] = {
        "enabled": True,
        "profile_id": "custom-gguf",
        "model_path": "D:/models/private-embedding.gguf",
        "top_k": 13,
    }
    preferences.update(
        {
            "ui_language": "es",
            "ui_theme": "dark",
            "rag": expected_rag,
            "custom_future_setting": {"keep": True},
        }
    )
    meeting = MeetingRepository(database).create(
        title="Private roadmap",
        description="Must survive updates",
        source_type=SourceType.IMPORTED,
        language="es",
    )
    with database.transaction() as connection:
        transcription_id = connection.execute(
            """
            INSERT INTO transcriptions(
                meeting_id, title, engine, model, language, status, is_active,
                created_at, completed_at, settings_json
            ) VALUES (?, 'Transcript', 'test', 'test', 'es', 'completed', 1, ?, ?, '{}')
            """,
            (meeting.id, "2026-08-23T12:00:00Z", "2026-08-23T12:01:00Z"),
        ).lastrowid
        assert transcription_id is not None
        connection.execute(
            """
            INSERT INTO rag_chunks(
                meeting_id, transcription_id, chunk_index, start_ms, end_ms,
                text, content_hash, embedding_provider, embedding_model,
                embedding_dimensions, embedding, created_at, updated_at
            ) VALUES (?, ?, 0, 0, 1000, ?, 'hash', 'test', 'test', 2, ?, ?, ?)
            """,
            (
                meeting.id,
                transcription_id,
                "Confidential RAG content",
                b"12345678",
                "2026-08-23T12:00:00Z",
                "2026-08-23T12:00:00Z",
            ),
        )

    request = updater.prepare_update(
        updater.ReleaseInfo(
            tag="v0.6.0",
            version="0.6.0",
            name="Meet2Notes 0.6.0",
            url="https://github.com/estebanstifli/Meet2Notes/releases/tag/v0.6.0",
        ),
        ["--data-dir", str(data), "--port", "8899"],
    )
    prepared = json.loads(request.read_text(encoding="utf-8"))
    assert prepared["data_directory"] == str(data.resolve())
    assert prepared["app_args"] == ["--data-dir", str(data), "--port", "8899"]
    backup = updater.backup_database(request)
    assert backup is not None and backup.is_file()
    updater.validate_migrations(request)

    with sqlite3.connect(backup) as connection:
        stored_settings = {
            key: json.loads(value)
            for key, value in connection.execute("SELECT key, value_json FROM settings")
        }
        stored_meeting = connection.execute(
            "SELECT title, description FROM meetings WHERE id = ?", (meeting.id,)
        ).fetchone()
        stored_rag = connection.execute(
            "SELECT text FROM rag_chunks WHERE meeting_id = ?", (meeting.id,)
        ).fetchone()

    assert stored_settings["ui_language"] == "es"
    assert stored_settings["ui_theme"] == "dark"
    assert stored_settings["rag"] == expected_rag
    assert stored_settings["custom_future_setting"] == {"keep": True}
    assert stored_meeting == ("Private roadmap", "Must survive updates")
    assert stored_rag == ("Confidential RAG content",)


def test_new_nested_defaults_do_not_overwrite_existing_preferences(tmp_path: Path) -> None:
    database = Database(tmp_path / "settings.db")
    MigrationRunner(database).apply()
    preferences = SettingsRepository(database)
    stored = {
        "provider": "custom",
        "runtime": {"threads": 7, "device": "cuda"},
        "unknown_from_future": "preserve-me",
    }
    preferences.update({"engine": stored})

    effective = configured_values(
        preferences,
        "engine",
        {
            "provider": "local",
            "new_parameter": True,
            "runtime": {"threads": 2, "device": "cpu", "new_runtime_parameter": 42},
        },
    )

    assert effective == {
        "provider": "custom",
        "new_parameter": True,
        "runtime": {"threads": 7, "device": "cuda", "new_runtime_parameter": 42},
        "unknown_from_future": "preserve-me",
    }
    assert preferences.get_all()["engine"] == stored
