from __future__ import annotations

import asyncio
import shutil
import time
import wave
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    AudioFrameBatch,
    CapturedAudio,
    CaptureStatus,
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


class FakeCaptureBackend:
    name = "fake-capture"

    def __init__(self) -> None:
        self.source = AudioCaptureSource(
            id="fake:microphone:0",
            name="Studio microphone",
            kind="microphone",
            backend=self.name,
            host_api="Test Audio",
            channels=1,
            sample_rate=16000,
            is_default=True,
        )
        self._status: CaptureStatus | None = None
        self._frames_drained = False

    def capability(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "platform": "TestOS",
            "supports_microphones": True,
            "supports_system_audio": True,
            "supports_interfaces": True,
            "notes": [],
        }

    def list_sources(self) -> list[AudioCaptureSource]:
        return [self.source]

    def probe_level(self, source_id: str) -> float:
        assert source_id == self.source.id
        return 0.42

    def start(
        self,
        *,
        session_id: str,
        source_id: str,
        destination: Path,
    ) -> CaptureStatus:
        assert source_id == self.source.id
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 1600)
        self._status = CaptureStatus(
            session_id=session_id,
            state="recording",
            source=self.source,
            destination=destination,
            elapsed_ms=120,
            level=0.42,
        )
        self._frames_drained = False
        return self._status

    def status(self) -> CaptureStatus | None:
        return self._status

    def drain_frames(self) -> AudioFrameBatch | None:
        if self._frames_drained:
            return None
        self._frames_drained = True
        return AudioFrameBatch(
            pcm_s16le=b"\x00\x00" * 1600,
            sample_rate=16000,
            channels=1,
            start_frame=0,
            end_frame=1600,
        )

    def pause(self) -> CaptureStatus:
        assert self._status is not None
        self._status = CaptureStatus(
            session_id=self._status.session_id,
            state="paused",
            source=self.source,
            destination=self._status.destination,
            elapsed_ms=180,
            level=0,
        )
        return self._status

    def resume(self) -> CaptureStatus:
        assert self._status is not None
        self._status = CaptureStatus(
            session_id=self._status.session_id,
            state="recording",
            source=self.source,
            destination=self._status.destination,
            elapsed_ms=220,
            level=0.36,
        )
        return self._status

    def stop(self) -> CapturedAudio:
        assert self._status is not None
        captured = CapturedAudio(
            path=self._status.destination,
            source=self.source,
            duration_ms=500,
            sample_rate=16000,
            channels=1,
        )
        self._status = None
        return captured

    def shutdown(self) -> None:
        self._status = None


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
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        await asyncio.sleep(0)
        return MediaProbe(
            format_name="wav",
            duration_ms=500,
            size_bytes=destination.stat().st_size,
            sample_rate=sample_rate,
            channels=channels,
            has_audio=True,
            has_video=False,
            metadata={},
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
        draft = SegmentDraft(
            index=0,
            start_ms=0,
            end_ms=500,
            text="Captured locally.",
            confidence=0.97,
        )
        segment_ready(draft)
        progress(1, "Captured")
        await asyncio.sleep(0)
        return TranscriptionResult(
            language="en",
            language_probability=0.99,
            duration_ms=500,
            segments=[draft],
        )

    def shutdown(self) -> None:
        return None

    def unload(self) -> None:
        return None


def _wait_for_job(client: TestClient, job_uuid: str) -> dict[str, Any]:
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_uuid}").json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Job did not reach a terminal state")


def test_live_capture_pause_stop_and_transcribe(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "capture-data",
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    with TestClient(
        create_app(
            settings,
            transcription_engine=FakeTranscriptionEngine(),
            audio_normalizer=FakeNormalizer(),
            audio_capture_backend=FakeCaptureBackend(),
        )
    ) as client:
        sources = client.get("/api/audio/sources")
        assert sources.status_code == 200
        assert sources.json()["capability"]["platform"] == "TestOS"
        assert sources.json()["sources"][0]["name"] == "Studio microphone"
        level = client.get("/api/audio/sources/fake:microphone:0/level")
        assert level.status_code == 200
        assert level.json() == {"source_id": "fake:microphone:0", "level": 0.42}

        started = client.post(
            "/api/capture/sessions",
            json={
                "source_id": "fake:microphone:0",
                "title": "Customer interview",
                "profile_id": "balanced",
                "language": "en",
            },
        )
        assert started.status_code == 201
        session = started.json()
        assert session["state"] == "recording"
        assert session["title"] == "Customer interview"
        assert session["transcription_id"] > 0

        for _ in range(100):
            current = client.get("/api/capture/session")
            assert current.status_code == 200
            if current.json()["segment_count"] > 0:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Live segment did not appear during capture")
        assert current.json()["level"] == 0.42
        assert current.json()["realtime_status"] == "live"
        live_detail = client.get(
            f"/api/transcriptions/{session['transcription_id']}"
        ).json()
        assert live_detail["transcription"]["status"] == "running"
        assert live_detail["segments"][0]["text"] == "Captured locally."
        assert live_detail["segments"][0]["is_final"] is False

        paused = client.post(
            f"/api/capture/sessions/{session['session_id']}/pause"
        )
        assert paused.status_code == 200
        assert paused.json()["state"] == "paused"

        resumed = client.post(
            f"/api/capture/sessions/{session['session_id']}/resume"
        )
        assert resumed.status_code == 200
        assert resumed.json()["state"] == "recording"

        stopped = client.post(
            f"/api/capture/sessions/{session['session_id']}/stop"
        )
        assert stopped.status_code == 200
        payload = stopped.json()
        assert payload["session"]["state"] == "stopped"
        assert (
            payload["recording"]["metadata"]["capture_source_name"]
            == "Studio microphone"
        )
        assert payload["transcription"]["title"] == "Customer interview"
        assert payload["transcription"]["id"] == session["transcription_id"]
        assert len(
            client.get(
                f"/api/meetings/{session['meeting_id']}/transcriptions"
            ).json()
        ) == 1

        terminal = _wait_for_job(client, payload["transcription_job"]["uuid"])
        assert terminal["status"] == "completed"
        detail = client.get(
            f"/api/transcriptions/{payload['transcription']['id']}"
        ).json()
        assert detail["segments"][0]["text"] == "Captured locally."
