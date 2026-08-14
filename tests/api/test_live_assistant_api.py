from __future__ import annotations

from fastapi.testclient import TestClient

from local_meeting_ai.domain.enums import SourceType


def test_live_assistant_settings_and_separate_credential(client: TestClient) -> None:
    catalog = client.get("/api/live-assistant")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["settings"]["enabled"] is False
    assert body["capability"]["dedicated_worker"] is True
    assert body["credential"] == {"available": True, "configured": False}

    credential = client.put(
        "/api/live-assistant/api-key",
        json={"api_key": "live-secret"},
    )
    assert credential.status_code == 200
    assert credential.json() == {"available": True, "configured": True}

    settings = body["settings"]
    settings.update(
        {
            "enabled": True,
            "provider": "litellm",
            "profile_id": "litellm-custom",
            "model": "openai/test-model",
            "model_file": "not-managed.gguf",
            "base_url": "https://api.example.test/v1",
            "trigger_phrases": ["Alexa"],
            "max_output_tokens": 5092,
        }
    )
    saved = client.put("/api/live-assistant/settings", json=settings)
    assert saved.status_code == 200
    assert saved.json()["settings"]["model"] == "openai/test-model"
    assert saved.json()["settings"]["max_output_tokens"] == 5092
    assert "live-secret" not in saved.text
    assert "api_key" not in saved.json()["settings"]

    removed = client.delete("/api/live-assistant/api-key")
    assert removed.json() == {"available": True, "configured": False}


def test_live_assistant_insight_lifecycle(client: TestClient) -> None:
    container = client.app.state.container
    meeting = container.meetings.create(
        title="Native assistant",
        description=None,
        source_type=SourceType.MANUAL,
        language="en",
    )
    transcription = container.transcriptions.create(
        meeting_id=meeting.id,
        title="Live",
        engine="test",
        model="test",
        language="en",
        settings={},
    )
    container.live_assistant_repository.start_session(
        session_id="session-api-test",
        meeting_id=meeting.id,
        transcription_id=transcription.id,
        configuration={"enabled": True},
    )
    insight = container.live_assistant_repository.add_insight(
        session_id="session-api-test",
        meeting_id=meeting.id,
        transcription_id=transcription.id,
        kind="information",
        text="Useful context",
        confidence=0.8,
        related_segment_ids=["live-1-0"],
        start_ms=1000,
        end_ms=2000,
        provider="local",
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=12,
    )

    response = client.get(f"/api/live-assistant/meetings/{meeting.id}")
    assert response.status_code == 200
    assert response.json()["insights"][0]["text"] == "Useful context"

    updated = client.put(
        f"/api/live-assistant/insights/{insight.id}",
        json={"status": "accepted"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "accepted"
