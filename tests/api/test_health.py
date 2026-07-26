from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_and_capabilities_are_explicit(client: TestClient) -> None:
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "version": "0.4.0",
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
    assert payload["summaries"]["worker"]["thread_prefix"] == "llama-summary"


def test_web_pages_and_security_headers(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Transcription" in response.text
    assert 'id="start-transcription"' in response.text
    assert 'id="transcription-title-display"' in response.text
    assert 'id="workspace-meeting-select"' not in response.text
    assert 'data-ui-language' not in response.text
    assert "Find and replace" not in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]

    missing = client.get("/meetings/99999")
    assert missing.status_code == 404
    assert "could not be found" in missing.text
