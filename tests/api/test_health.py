from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_capabilities_are_explicit(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "version": "0.5.0",
        "database": "ok",
        "queue": "running",
    }

    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    features = capabilities.json()["features"]
    assert features["media_import"] == "available"
    assert features["transcription"] in {"available", "requires_install"}
    assert features["microphone_recording"] in {"available", "requires_install"}
    assert features["system_audio_capture"] in {"available", "unavailable"}
    assert features["summaries"] in {"available", "requires_install"}
    assert features["diarization"] in {"available", "requires_install"}
    payload = capabilities.json()
    assert payload["diarization"]["worker"]["thread_prefix"] == "sherpa-diarization"
    assert sorted(payload["diarization"]["engines"]) == [
        "diarize",
        "pyannote-community-1",
        "sherpa-onnx",
    ]
    assert payload["diarization"]["speaker_profile_matcher"]["engine"] == (
        "sherpa-onnx-speaker-profiles"
    )
    assert payload["summaries"]["worker"]["thread_prefix"] == "llama-summary"

    pytorch_cuda = client.get("/api/runtimes/pytorch-cuda")
    assert pytorch_cuda.status_code == 200
    runtime = pytorch_cuda.json()
    assert runtime["state"] in {"ready", "cpu_only", "not_installed"}
    assert isinstance(runtime["cuda_available"], bool)
    assert isinstance(runtime["can_install"], bool)

    sidebar = client.get("/api/sidebar-system")
    assert sidebar.status_code == 200
    sidebar_payload = sidebar.json()
    assert [engine["role"] for engine in sidebar_payload["engines"]] == [
        "live_transcription",
        "final_transcription",
        "diarization",
        "summary",
    ]
    assert sidebar_payload["process_id"] > 0
    assert "available_bytes" in sidebar_payload["memory"]
    assert isinstance(sidebar_payload["gpus"], list)


def test_web_pages_and_security_headers(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Transcription" in response.text
    assert 'id="start-transcription"' in response.text
    assert 'id="transcription-title-display"' in response.text
    assert 'id="workspace-meeting-select"' not in response.text
    assert "data-ui-language" not in response.text
    assert 'class="classic-menu-bar"' not in response.text
    assert "data-theme-toggle" in response.text
    assert 'href="/settings"' in response.text
    assert 'id="activity-log-card"' in response.text
    assert 'id="activity-log-resizer"' in response.text
    assert 'id="activity-log-output"' in response.text
    assert 'id="global-engine-list"' in response.text
    assert 'id="global-hardware-list"' in response.text
    assert 'id="application-shutdown"' in response.text
    assert 'id="shutdown-dialog"' in response.text
    assert 'id="postprocess-log"' in response.text
    assert "start-transcription-button hidden" in response.text
    assert "?v=0.5.0-" in response.text
    assert "Find and replace" not in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]

    new_meeting = client.get("/?new=1")
    assert "start-transcription-button hidden" not in new_meeting.text
    assert 'data-meeting-id=""' in new_meeting.text
    assert 'class="canvas-audio-row hidden"' in new_meeting.text

    meetings = client.get("/meetings")
    assert meetings.status_code == 200
    assert "Meeting library" in meetings.text
    assert 'id="meeting-search"' in meetings.text

    static_asset = client.get("/static/js/transcript.js")
    assert static_asset.headers["cache-control"] == "no-cache, must-revalidate"

    missing = client.get("/meetings/99999")
    assert missing.status_code == 404
    assert "could not be found" in missing.text

    settings = client.get("/settings")
    assert settings.status_code == 200
    assert 'id="ui-theme"' in settings.text
    assert 'id="models-directory"' in settings.text
    assert 'id="storage-models-directory"' in settings.text
    assert 'id="run-diagnostics"' in settings.text
    assert 'value="system"' in settings.text
    assert 'id="live-transcription-settings"' in settings.text
    assert 'id="final-transcription-settings"' in settings.text
    assert 'id="live-transcription-model-list"' in settings.text
    assert 'id="final-transcription-model-list"' in settings.text
    assert 'id="pytorch-cuda-dialog"' in settings.text
    assert 'id="diarization-engine"' in settings.text


def test_application_shutdown_uses_the_cli_graceful_shutdown_callback(client: TestClient) -> None:
    requested: list[bool] = []
    client.app.state.request_shutdown = lambda: requested.append(True)

    response = client.post("/api/application/shutdown")

    assert response.status_code == 200
    assert response.json()["status"] == "shutting_down"
    assert requested == [True]
    assert client.app.state.shutdown_requested is True


def test_activity_feed_is_incremental(client: TestClient) -> None:
    activity_log = client.app.state.container.activity_log
    activity_log.append("INFO", "test.engine", "Whisper model loaded")
    activity_log.append("ERROR", "test.capture", "Audio source disconnected")

    response = client.get("/api/activity")
    assert response.status_code == 200
    entries = response.json()
    assert entries[-2]["message"] == "Whisper model loaded"
    assert entries[-1]["level"] == "error"

    incremental = client.get(f"/api/activity?after={entries[-2]['id']}")
    assert [entry["message"] for entry in incremental.json()] == ["Audio source disconnected"]


def test_diagnostic_report_continues_and_is_shareable(client: TestClient) -> None:
    response = client.get("/api/diagnostics/report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"].startswith("meet2notes-diagnostics-")
    assert "== Hardware ==" in payload["report"]
    assert "== Non-blocking component checks ==" in payload["report"]
    assert "== Recent application log" in payload["report"]
