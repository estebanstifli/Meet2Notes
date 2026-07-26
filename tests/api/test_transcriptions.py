from __future__ import annotations

import asyncio
import io
import shutil
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import (
    MediaProbe,
    SegmentDraft,
    TranscriptionEngineRequest,
    TranscriptionResult,
)
from local_meeting_ai.domain.protocols import (
    CancellationCheck,
    ProgressReporter,
    SegmentReporter,
)


class FakeNormalizer:
    async def normalize_for_transcription(
        self,
        source: Path,
        destination: Path,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        is_cancelled: CancellationCheck | None = None,
    ) -> MediaProbe:
        if is_cancelled and is_cancelled():
            raise RuntimeError("cancelled")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        await asyncio.sleep(0)
        return MediaProbe(
            format_name="wav",
            duration_ms=100,
            size_bytes=destination.stat().st_size,
            sample_rate=sample_rate,
            channels=channels,
            has_audio=True,
            has_video=False,
            metadata={"format": "wav"},
        )


class FakeTranscriptionEngine:
    name = "fake-whisper"

    def capability(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "available": True,
            "cuda_available": False,
            "installed_models": ["small"],
        }

    async def prepare(
        self,
        profile: Any,
        *,
        allow_model_download: bool,
    ) -> None:
        del profile, allow_model_download
        await asyncio.sleep(0)

    async def transcribe(
        self,
        request: TranscriptionEngineRequest,
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
        segment_ready: SegmentReporter,
    ) -> TranscriptionResult:
        assert request.audio_path.suffix == ".wav"
        assert request.compute_type == "int8"
        drafts = [
            SegmentDraft(
                index=0,
                start_ms=0,
                end_ms=1200,
                text="Welcome to the product review.",
                confidence=0.94,
            ),
            SegmentDraft(
                index=1,
                start_ms=1200,
                end_ms=2800,
                text="We approved the product launch.",
                confidence=0.91,
            ),
        ]
        for index, segment in enumerate(drafts):
            assert not is_cancelled()
            segment_ready(segment)
            progress((index + 1) / len(drafts), f"Segment {index + 1}")
            await asyncio.sleep(0)
        return TranscriptionResult(
            language="en",
            language_probability=0.99,
            duration_ms=2800,
            segments=drafts,
        )

    def shutdown(self) -> None:
        return None

    def unload(self) -> None:
        return None


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


@contextmanager
def _client(tmp_path: Path) -> Iterator[TestClient]:
    settings = AppSettings(
        data_dir=tmp_path / "transcription-data",
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    with TestClient(
        create_app(
            settings,
            transcription_engine=FakeTranscriptionEngine(),
            audio_normalizer=FakeNormalizer(),
        )
    ) as client:
        yield client


def _wait_for_job(client: TestClient, job_uuid: str) -> dict[str, Any]:
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_uuid}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Job did not reach a terminal state")


def test_transcription_pipeline_editor_and_versions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        meeting = client.post("/api/meetings", json={"title": "Product review"}).json()
        imported = client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("review.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202
        recording = imported.json()["recording"]

        profiles = client.get("/api/models/transcription")
        assert profiles.status_code == 200
        balanced = next(item for item in profiles.json() if item["id"] == "balanced")
        assert balanced["installed"] is True
        assert balanced["compute_type"] == "int8"

        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "balanced", "language": "en"},
        )
        assert started.status_code == 202
        started_payload = started.json()
        terminal = _wait_for_job(client, started_payload["job"]["uuid"])
        assert terminal["status"] == "completed"
        assert terminal["result"]["segment_count"] == 2

        transcription_id = started_payload["transcription"]["id"]
        detail = client.get(f"/api/transcriptions/{transcription_id}")
        assert detail.status_code == 200
        assert detail.json()["transcription"]["is_active"] is True
        assert detail.json()["transcription"]["title"] == "New Transcription"
        assert [segment["text"] for segment in detail.json()["segments"]] == [
            "Welcome to the product review.",
            "We approved the product launch.",
        ]
        assert all(segment["is_final"] for segment in detail.json()["segments"])
        assert 'data-default-title="New Transcription 2"' in client.get("/").text

        renamed = client.patch(
            f"/api/transcriptions/{transcription_id}",
            json={"title": "Product launch decision"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Product launch decision"
        assert 'data-default-title="New Transcription"' in client.get("/").text

        first_segment = detail.json()["segments"][0]
        edited = client.patch(
            f"/api/transcript-segments/{first_segment['id']}",
            json={"text": "Welcome to the release review."},
        )
        assert edited.status_code == 200
        assert edited.json()["text"] == "Welcome to the release review."

        replaced = client.post(
            f"/api/transcriptions/{transcription_id}/find-replace",
            json={"find": "product", "replacement": "release", "case_sensitive": False},
        )
        assert replaced.status_code == 200
        assert replaced.json()["replacements"] == 1

        media = client.get(f"/api/recordings/{recording['id']}/media")
        assert media.status_code == 200
        assert media.content.startswith(b"RIFF")

        versions = client.get(f"/api/meetings/{meeting['id']}/transcriptions")
        assert versions.status_code == 200
        assert versions.json()[0]["segment_count"] == 2

        workspace = client.get(f"/?meeting={meeting['id']}")
        assert workspace.status_code == 200
        assert 'id="workspace-meeting-select"' not in workspace.text
        assert 'id="start-transcription"' in workspace.text
        assert 'id="transcription-dialog"' in workspace.text
        assert "Choose your audio source" in workspace.text
        assert "Import media" not in workspace.text


def test_model_download_requires_explicit_confirmation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        meeting = client.post("/api/meetings", json={"title": "Download consent"}).json()
        client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("review.wav", _wav_bytes(), "audio/wav")},
        )

        blocked = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "fast", "allow_model_download": False},
        )
        assert blocked.status_code == 409
        assert "Confirm the model download" in blocked.json()["detail"]

        confirmed = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={"profile_id": "fast", "allow_model_download": True},
        )
        assert confirmed.status_code == 202
