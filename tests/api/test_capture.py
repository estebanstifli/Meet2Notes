from __future__ import annotations

import asyncio
import io
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
    DiarizationSegment,
    MediaProbe,
    SegmentDraft,
    SummaryResult,
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


class FakeDiarizationEngine:
    name = "fake-diarization"

    def capability(self) -> dict[str, Any]:
        return {"engine": self.name, "available": True, "installed": True}

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        del config, allow_model_download

    async def diarize(
        self,
        audio_path: Path,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> list[DiarizationSegment]:
        del config
        assert audio_path.is_file()
        assert not is_cancelled()
        progress(1, "Speakers identified")
        return [DiarizationSegment(start_ms=0, end_ms=500, speaker=0)]

    def shutdown(self) -> None:
        return None

    def unload(self) -> None:
        return None


class FakeSummaryEngine:
    name = "fake-summary"

    def __init__(self, expected_template_name: str = "General Meeting") -> None:
        self.expected_template_name = expected_template_name

    def capability(self) -> dict[str, Any]:
        return {"engine": self.name, "available": True, "installed": True}

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        del config, allow_model_download

    async def summarize(
        self,
        transcript: str,
        config: dict[str, Any],
        progress: ProgressReporter,
        is_cancelled: CancellationCheck,
    ) -> SummaryResult:
        if config.get("summary_scope") == "speaker":
            assert "Captured locally." in transcript
            assert config["speaker_name"] == "Esteban"
        else:
            assert "Captured locally." in transcript
            assert config["summary_template"]["name"] == self.expected_template_name
            assert config["summary_template"]["sections"]
        assert not is_cancelled()
        progress(1, "Notes generated")
        return SummaryResult(content_markdown="# Summary\n\nCaptured locally.")

    def shutdown(self) -> None:
        return None

    def unload(self) -> None:
        return None


class FakeAudioRangeExporter:
    async def export_audio_ranges(
        self,
        source: Path,
        destination: Path,
        ranges: list[tuple[int, int]],
        *,
        output_format: str,
    ) -> None:
        assert source.is_file()
        assert ranges == [(0, 500)]
        assert output_format in {"wav", "mp3", "flac"}
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"speaker-audio")


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


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
            diarization_engine=FakeDiarizationEngine(),
            summary_engine=FakeSummaryEngine(),
            audio_range_exporter=FakeAudioRangeExporter(),
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
        live_meeting = client.get(f"/api/meetings/{session['meeting_id']}").json()
        assert live_meeting["started_at"] is not None

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
        stopped_meeting = client.get(f"/api/meetings/{session['meeting_id']}").json()
        assert stopped_meeting["ended_at"] is not None
        assert stopped_meeting["duration_ms"] == 500
        assert len(
            client.get(
                f"/api/meetings/{session['meeting_id']}/transcriptions"
            ).json()
        ) == 1

        terminal = _wait_for_job(client, payload["transcription_job"]["uuid"])
        assert terminal["status"] == "completed"
        assert terminal["payload"]["postprocess"] is True
        for _ in range(100):
            workflow_jobs = client.get(
                f"/api/jobs?meeting_id={session['meeting_id']}"
            ).json()
            summary_job = next(
                (job for job in workflow_jobs if job["job_type"] == "summarize"),
                None,
            )
            if summary_job and summary_job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Post-processing did not reach the summary stage")
        assert summary_job["status"] == "completed"
        plugin_runs = client.get("/api/plugins/executions").json()
        assert any(
            run["plugin_id"] == "meet2notes.analysis-cleanup"
            and run["hook"] == "analysis.before"
            and run["status"] == "completed"
            for run in plugin_runs
        )
        assert any(job["job_type"] == "diarize" for job in workflow_jobs)
        detail = client.get(
            f"/api/transcriptions/{payload['transcription']['id']}"
        ).json()
        assert detail["segments"][0]["text"] == "Captured locally."
        assert detail["segments"][0]["speaker_id"] is not None
        summaries = client.get(
            f"/api/meetings/{session['meeting_id']}/summaries"
        ).json()
        assert summaries[0]["content_markdown"].startswith("# Summary")

        # A stopped capture must release the backend so the source picker can
        # be opened immediately for the next transcription.
        refreshed_sources = client.get("/api/audio/sources")
        assert refreshed_sources.status_code == 200
        assert refreshed_sources.json()["sources"][0]["id"] == "fake:microphone:0"


def test_live_capture_can_be_discarded_before_postprocessing(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "discard-live-capture-data",
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
            diarization_engine=FakeDiarizationEngine(),
            summary_engine=FakeSummaryEngine(),
            audio_range_exporter=FakeAudioRangeExporter(),
        )
    ) as client:
        started = client.post(
            "/api/capture/sessions",
            json={"source_id": "fake:microphone:0", "title": "Discard this meeting"},
        )
        assert started.status_code == 201
        session = started.json()

        discarded = client.post(f"/api/capture/sessions/{session['session_id']}/discard")

        assert discarded.status_code == 204
        assert client.get("/api/capture/session").json() is None
        assert client.get(f"/api/meetings/{session['meeting_id']}").status_code == 404

        restarted = client.post(
            "/api/capture/sessions",
            json={"source_id": "fake:microphone:0", "title": "Fresh meeting"},
        )
        assert restarted.status_code == 201
        assert restarted.json()["meeting_id"] != session["meeting_id"]


def test_live_capture_can_keep_the_live_text_without_postprocessing(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "capture-without-final-pass-data",
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
            diarization_engine=FakeDiarizationEngine(),
            summary_engine=FakeSummaryEngine(),
            audio_range_exporter=FakeAudioRangeExporter(),
        )
    ) as client:
        started = client.post(
            "/api/capture/sessions",
            json={"source_id": "fake:microphone:0", "title": "Quick note"},
        )
        assert started.status_code == 201
        session = started.json()
        for _ in range(100):
            live = client.get("/api/capture/session").json()
            if live["segment_count"]:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Live segment did not appear during capture")

        stopped = client.post(
            f"/api/capture/sessions/{session['session_id']}/stop",
            json={
                "final_transcription": False,
                "postprocess_options": {
                    "diarization": False,
                    "speaker_count": None,
                    "summary": False,
                },
            },
        )
        assert stopped.status_code == 200
        job = _wait_for_job(client, stopped.json()["transcription_job"]["uuid"])
        assert job["status"] == "completed"
        assert job["payload"]["skip_final_pass"] is True
        assert job["payload"]["postprocess_options"] == {
            "diarization": False,
            "speaker_count": None,
            "summary": False,
            "summary_template_id": None,
        }
        jobs = client.get(f"/api/jobs?meeting_id={session['meeting_id']}").json()
        assert {item["job_type"] for item in jobs} == {"import_media", "transcribe"}
        detail = client.get(f"/api/transcriptions/{session['transcription_id']}").json()
        assert detail["transcription"]["status"] == "completed"
        assert detail["segments"][0]["is_final"] is True


def test_imported_media_runs_complete_meeting_pipeline(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "import-pipeline-data",
        testing=True,
        open_browser=False,
        log_level="WARNING",
    )
    summary_engine = FakeSummaryEngine(expected_template_name="Daily Stand-up")
    with TestClient(
        create_app(
            settings,
            transcription_engine=FakeTranscriptionEngine(),
            audio_normalizer=FakeNormalizer(),
            diarization_engine=FakeDiarizationEngine(),
            summary_engine=summary_engine,
            audio_range_exporter=FakeAudioRangeExporter(),
        )
    ) as client:
        daily_format = next(
            item
            for item in client.get("/api/summary-templates").json()
            if item["name"] == "Daily Stand-up"
        )
        meeting = client.post(
            "/api/meetings",
            json={"title": "Imported interview", "source_type": "imported"},
        ).json()
        imported = client.post(
            f"/api/meetings/{meeting['id']}/import",
            files={"file": ("interview.wav", _wav_bytes(), "audio/wav")},
        )
        assert imported.status_code == 202

        started = client.post(
            f"/api/meetings/{meeting['id']}/transcriptions",
            json={
                "profile_id": "balanced",
                "language": "en",
                "postprocess": True,
                "postprocess_options": {
                    "diarization": True,
                    "speaker_count": None,
                    "summary": True,
                    "summary_template_id": daily_format["id"],
                },
            },
        )
        assert started.status_code == 202
        transcription_job = started.json()["job"]
        assert transcription_job["payload"]["postprocess"] is True

        for _ in range(150):
            workflow_jobs = client.get(
                f"/api/jobs?meeting_id={meeting['id']}"
            ).json()
            summary_job = next(
                (job for job in workflow_jobs if job["job_type"] == "summarize"),
                None,
            )
            if summary_job and summary_job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Imported media pipeline did not finish")

        assert summary_job["status"] == "completed"
        assert summary_job["payload"]["summary_template"]["name"] == "Daily Stand-up"
        assert {job["job_type"] for job in workflow_jobs} >= {
            "import_media",
            "transcribe",
            "diarize",
            "summarize",
        }
        assert all(
            job["payload"].get("postprocess")
            for job in workflow_jobs
            if job["job_type"] in {"transcribe", "diarize", "summarize"}
        )
        detail = client.get(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
        ).json()
        assert detail["segments"][0]["speaker_id"] is not None
        assert detail["speakers"][0]["display_name"] == "Speaker 1"
        assert detail["speakers"][0]["talk_time_ms"] == 500
        assert detail["speaker_turns"][0]["start_ms"] == 0
        speaker_id = detail["speakers"][0]["id"]
        renamed = client.patch(
            f"/api/speakers/{speaker_id}",
            json={"display_name": "Esteban"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "Esteban"
        refreshed = client.get(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
        ).json()
        assert refreshed["speakers"][0]["display_name"] == "Esteban"
        speaker_audio = client.get(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
            f"/speakers/{speaker_id}/audio?format=mp3"
        )
        assert speaker_audio.status_code == 200
        assert speaker_audio.content == b"speaker-audio"
        assert speaker_audio.headers["content-type"].startswith("audio/mpeg")
        speaker_text = client.get(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
            f"/speakers/{speaker_id}/text"
        )
        assert speaker_text.status_code == 200
        assert speaker_text.text == "Esteban\n\nCaptured locally.\n"
        speaker_summary = client.post(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
            f"/speakers/{speaker_id}/summary"
        )
        assert speaker_summary.status_code == 202
        speaker_summary_job = _wait_for_job(
            client,
            speaker_summary.json()["job"]["uuid"],
        )
        assert speaker_summary_job["status"] == "completed"
        summarized = client.get(
            f"/api/transcriptions/{started.json()['transcription']['id']}"
        ).json()["speakers"][0]
        assert summarized["summary_status"] == "completed"
        assert summarized["summary_markdown"].startswith("# Summary")
        summaries = client.get(f"/api/meetings/{meeting['id']}/summaries").json()
        assert summaries[0]["status"] == "completed"
        edited_notes = "# Edited notes\n\n- Added by the meeting owner."
        edited = client.patch(
            f"/api/summaries/{summaries[0]['id']}",
            json={"content_markdown": edited_notes},
        )
        assert edited.status_code == 200
        assert edited.json()["content_markdown"] == edited_notes
        assert edited.json()["structured"]["manual_edit"][
            "original_content_markdown"
        ].startswith("# Summary")
        assert edited.json()["structured"]["manual_edit"]["edited_at"]

        rebuilt = client.post(
            f"/api/transcriptions/{started.json()['transcription']['id']}/summaries",
            json={"template_id": daily_format["id"]},
        )
        assert rebuilt.status_code == 202
        assert rebuilt.json()["summary"]["template_id"] == daily_format["id"]
        rebuilt_job = _wait_for_job(client, rebuilt.json()["job"]["uuid"])
        assert rebuilt_job["status"] == "completed"
        rebuilt_summaries = client.get(
            f"/api/meetings/{meeting['id']}/summaries"
        ).json()
        assert rebuilt_summaries[0]["id"] == rebuilt.json()["summary"]["id"]
        assert rebuilt_summaries[0]["content_markdown"].startswith("# Summary")
        assert any(item["content_markdown"] == edited_notes for item in rebuilt_summaries)

        saved_meeting = client.get(f"/api/meetings/{meeting['id']}").json()
        assert saved_meeting["started_at"] is not None
        library = client.get("/meetings")
        assert f'data-local-time="{saved_meeting["started_at"]}"' in library.text
