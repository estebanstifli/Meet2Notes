from __future__ import annotations

import io
import time
import wave

from fastapi.testclient import TestClient


def _tiny_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def _create_meeting(client: TestClient) -> dict[str, object]:
    response = client.post("/api/meetings", json={"title": "Imported interview"})
    assert response.status_code == 201
    return response.json()


def test_import_streams_to_private_storage_and_creates_job(client: TestClient) -> None:
    meeting = _create_meeting(client)
    response = client.post(
        f"/api/meetings/{meeting['id']}/import",
        files={"file": ("interview.wav", _tiny_wav(), "audio/wav")},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["recording"]["original_filename"] == "interview.wav"
    assert payload["recording"]["sha256"]
    assert "local_path" not in payload["recording"]
    assert payload["job"]["job_type"] == "import_media"

    terminal = None
    for _ in range(100):
        current = client.get(f"/api/jobs/{payload['job']['uuid']}").json()
        if current["status"] in {"completed", "failed", "cancelled"}:
            terminal = current
            break
        time.sleep(0.02)
    assert terminal is not None
    assert terminal["status"] in {"completed", "failed"}

    recordings = client.get(f"/api/meetings/{meeting['id']}/recordings")
    assert recordings.status_code == 200
    assert len(recordings.json()) == 1
    if terminal["status"] == "completed":
        assert recordings.json()[0]["duration_ms"] == 100
        assert recordings.json()[0]["channels"] == 1


def test_import_rejects_unsupported_and_empty_files(client: TestClient) -> None:
    meeting = _create_meeting(client)
    unsupported = client.post(
        f"/api/meetings/{meeting['id']}/import",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert unsupported.status_code == 422
    assert "Unsupported media type" in unsupported.json()["detail"]

    empty = client.post(
        f"/api/meetings/{meeting['id']}/import",
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert empty.status_code == 422
    assert empty.json()["detail"] == "The uploaded file is empty"
