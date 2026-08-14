from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from local_meeting_ai.application.live_assistant import (
    LIVE_ASSISTANT_DEFAULTS,
    LiveAssistantService,
    _parse_model_response,
)
from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    LiveCaptureSession,
    SegmentDraft,
    SummaryResult,
)
from local_meeting_ai.domain.enums import SourceType
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.live_assistant import (
    LiveAssistantRepository,
)
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import (
    MeetingRepository,
    SettingsRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.live_assistant_credentials import (
    MemoryLiveAssistantCredentialStore,
)


class FakeAssistantEngine:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.stopped = False

    def capability(self) -> dict[str, Any]:
        return {
            "available": True,
            "models": [],
            "worker": {"state": "idle", "active_requests": 0, "model_resident": False},
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        del config, allow_model_download

    async def uninstall(self, profile_id: str) -> None:
        del profile_id

    async def summarize(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: Any,
        is_cancelled: Any,
    ) -> SummaryResult:
        del progress
        assert not is_cancelled()
        self.requests.append((transcript, config))
        await asyncio.sleep(0)
        return SummaryResult(
            content_markdown=(
                '{"respond":true,"kind":"information","text":"Alexa is Amazon\'s '
                'voice assistant.","confidence":0.9,"related_segment_ids":[], '
                '"memory_summary":"Alexa was discussed."}'
            ),
            prompt_tokens=42,
            completion_tokens=18,
        )

    def unload(self) -> None:
        return None

    def shutdown(self) -> None:
        self.stopped = True


def _capture_session(meeting_id: int, transcription_id: int) -> LiveCaptureSession:
    source = AudioCaptureSource(
        id="test-source",
        name="Test microphone",
        kind="microphone",
        backend="test",
        host_api="test",
        channels=1,
        sample_rate=16000,
    )
    return LiveCaptureSession(
        session_id="capture-session-1",
        meeting_id=meeting_id,
        transcription_id=transcription_id,
        title="Assistant test",
        state="recording",
        source=source,
        elapsed_ms=0,
        level=0,
        started_at="2026-08-14T12:00:00+00:00",
        profile_id="small",
        language="en",
        realtime_status="live",
        realtime_message="Listening",
        segment_count=0,
    )


@pytest.mark.asyncio
async def test_live_assistant_runs_outside_capture_and_persists_compact_insight(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "assistant.db")
    MigrationRunner(database).apply()
    meetings = MeetingRepository(database)
    transcriptions = TranscriptionRepository(database)
    preferences = SettingsRepository(database)
    repository = LiveAssistantRepository(database)
    credentials = MemoryLiveAssistantCredentialStore()
    credentials.set("assistant-only-key")
    engine = FakeAssistantEngine()
    config = {
        **LIVE_ASSISTANT_DEFAULTS,
        "enabled": True,
        "provider": "litellm",
        "profile_id": "litellm-custom",
        "model": "openai/test-model",
        "model_file": "not-managed.gguf",
        "evaluation_interval_seconds": 0.01,
        "cooldown_seconds": 0,
        "trigger_phrases": ["Alexa"],
    }
    preferences.update({"live_assistant": config})
    meeting = meetings.create(
        title="Assistant test",
        description=None,
        source_type=SourceType.MANUAL,
        language="en",
    )
    transcription = transcriptions.create(
        meeting_id=meeting.id,
        title="Live",
        engine="test",
        model="test",
        language="en",
        settings={},
    )
    service = LiveAssistantService(
        engine=engine,
        repository=repository,
        preferences=preferences,
        credentials=credentials,
    )
    await service.start()
    session = _capture_session(meeting.id, transcription.id)
    service.session_started(session)

    started = time.perf_counter()
    service.publish_segments(
        session_id=session.session_id,
        meeting_id=meeting.id,
        transcription_id=transcription.id,
        meeting_title=meeting.title,
        segments=[
            SegmentDraft(
                index=0,
                start_ms=0,
                end_ms=1500,
                text="Can someone explain Alexa?",
                confidence=0.95,
            )
        ],
        sequence=1,
    )
    assert time.perf_counter() - started < 0.05

    deadline = time.monotonic() + 2
    insights = []
    while time.monotonic() < deadline:
        insights = repository.insights(meeting.id)
        if insights:
            break
        await asyncio.sleep(0.02)

    assert insights[0].text == "Alexa is Amazon's voice assistant."
    assert insights[0].provider == "litellm"
    assert repository.session(session.session_id).memory == {
        "summary": "Alexa was discussed.",
        "assistant_responses": ["Alexa is Amazon's voice assistant."],
    }
    assert engine.requests[0][1]["api_key"] == "assistant-only-key"
    assert "Can someone explain Alexa?" in engine.requests[0][0]
    runtime = service.status(meeting.id)
    assert runtime["evaluation_count"] == 1
    assert runtime["insight_count"] == 1

    service.publish_segments(
        session_id=session.session_id,
        meeting_id=meeting.id,
        transcription_id=transcription.id,
        meeting_title=meeting.title,
        segments=[
            SegmentDraft(
                index=1,
                start_ms=1600,
                end_ms=2600,
                text="Now we are discussing Madrid.",
                confidence=0.9,
            )
        ],
        sequence=2,
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        runtime = service.status(meeting.id)
        if runtime["status"] == "waiting_trigger":
            break
        await asyncio.sleep(0.02)
    assert runtime["status"] == "waiting_trigger"
    assert runtime["skipped_by_trigger"] == 1

    service.session_stopped(session)
    await service.shutdown()
    assert engine.stopped is True


def test_live_assistant_json_parser_accepts_fenced_json() -> None:
    parsed = _parse_model_response(
        '```json\n{"respond": false, "text": "", "memory_summary": "Project X"}\n```'
    )

    assert parsed["respond"] is False
    assert parsed["memory_summary"] == "Project X"


def test_live_assistant_json_parser_rejects_truthy_string_response() -> None:
    with pytest.raises(ValueError, match="boolean respond"):
        _parse_model_response(
            '{"respond":"false","text":"Do not show this","memory_summary":""}'
        )


@pytest.mark.asyncio
async def test_dispatcher_exists_only_while_assistant_is_enabled(tmp_path: Path) -> None:
    database = Database(tmp_path / "assistant-disabled.db")
    MigrationRunner(database).apply()
    preferences = SettingsRepository(database)
    engine = FakeAssistantEngine()
    service = LiveAssistantService(
        engine=engine,
        repository=LiveAssistantRepository(database),
        preferences=preferences,
        credentials=MemoryLiveAssistantCredentialStore(),
    )

    await service.start()
    assert service._worker_task is None

    preferences.update(
        {"live_assistant": {**LIVE_ASSISTANT_DEFAULTS, "enabled": True}}
    )
    await service.reconfigure()
    assert service._worker_task is not None
    assert not service._worker_task.done()

    preferences.update(
        {"live_assistant": {**LIVE_ASSISTANT_DEFAULTS, "enabled": False}}
    )
    await service.reconfigure()
    assert service._worker_task is None

    await service.shutdown()
