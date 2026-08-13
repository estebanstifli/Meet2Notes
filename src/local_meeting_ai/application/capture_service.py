from __future__ import annotations

import asyncio
import logging
import threading
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from local_meeting_ai.application.services import ImportService, MeetingService
from local_meeting_ai.application.transcription_config import faster_whisper_config
from local_meeting_ai.application.transcription_service import TranscriptionService
from local_meeting_ai.application.webhooks import WebhookService
from local_meeting_ai.domain.entities import (
    AudioCaptureSource,
    AudioFrameBatch,
    Job,
    LiveCaptureSession,
    ModelProfile,
    Recording,
    SegmentDraft,
    Transcription,
    TranscriptionEngineRequest,
)
from local_meeting_ai.domain.enums import SourceType
from local_meeting_ai.domain.errors import NotFoundError, ValidationError
from local_meeting_ai.domain.protocols import AudioCaptureBackend
from local_meeting_ai.infrastructure.database.repositories import (
    SettingsRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.storage import MeetingStorage

logger = logging.getLogger(__name__)


class LiveCaptureService:
    """Coordinates native capture and overlapping low-latency Whisper windows."""

    def __init__(
        self,
        *,
        backend: AudioCaptureBackend,
        meetings: MeetingService,
        import_service: ImportService,
        transcription_service: TranscriptionService,
        transcriptions: TranscriptionRepository,
        preferences: SettingsRepository,
        storage: MeetingStorage,
        webhooks: WebhookService | None = None,
        poll_interval: float = 0.75,
        chunk_seconds: float = 3.0,
        overlap_seconds: float = 1.0,
    ) -> None:
        self.backend = backend
        self.meetings = meetings
        self.import_service = import_service
        self.transcription_service = transcription_service
        self.transcriptions = transcriptions
        self.preferences = preferences
        self.storage = storage
        self.webhooks = webhooks
        self.poll_interval = poll_interval
        self.chunk_seconds = chunk_seconds
        self.overlap_seconds = overlap_seconds
        self._active_chunk_seconds = chunk_seconds
        self._active_overlap_seconds = overlap_seconds
        self._lock = threading.RLock()
        self._session: LiveCaptureSession | None = None
        self._settings: dict[str, Any] | None = None
        self._profile: ModelProfile | None = None
        self._realtime_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._live_buffer = bytearray()
        self._live_buffer_start_frame = 0
        self._live_overlap = b""
        self._live_sample_rate = 0
        self._live_channels = 0
        self._live_segments: list[SegmentDraft] = []
        self._committed_until_ms = 0
        self._chunk_sequence = 0
        self._realtime_status = "warming_up"
        self._realtime_message = "Preparing the local model"
        self._realtime_error: str | None = None

    def capability(self) -> dict[str, Any]:
        preferences = self.preferences.get_all()
        config = faster_whisper_config(preferences)
        configured = isinstance(preferences.get("faster_whisper"), dict)
        return {
            **self.backend.capability(),
            "realtime_transcription": True,
            "realtime_chunk_seconds": (
                float(config["realtime_chunk_seconds"]) if configured else self.chunk_seconds
            ),
            "realtime_overlap_seconds": (
                float(config["realtime_overlap_seconds"]) if configured else self.overlap_seconds
            ),
        }

    def sources(self) -> list[AudioCaptureSource]:
        return self.backend.list_sources()

    async def start(
        self,
        *,
        source_id: str,
        title: str | None,
        profile_id: str,
        language: str | None,
        task: str | None,
        allow_model_download: bool,
    ) -> LiveCaptureSession:
        with self._lock:
            if self._session is not None:
                raise ValidationError("Another live transcription is already active")
            clean_title = title.strip() if title and title.strip() else None
            resolved_title = clean_title or self.transcriptions.next_default_title()
            preference_values = self.preferences.get_all()
            engine_config = faster_whisper_config(preference_values)
            configured_language = engine_config.get("language")
            clean_language = (
                language.strip()
                if language and language.strip()
                else (str(configured_language).strip() if configured_language else None)
            )
            if isinstance(preference_values.get("faster_whisper"), dict):
                self._active_chunk_seconds = float(engine_config["realtime_chunk_seconds"])
                self._active_overlap_seconds = float(engine_config["realtime_overlap_seconds"])
            else:
                self._active_chunk_seconds = self.chunk_seconds
                self._active_overlap_seconds = self.overlap_seconds
            resolved_task = (
                task
                if task in {"transcribe", "translate"}
                else str(engine_config.get("task", "transcribe"))
            )
            meeting = self.meetings.create(
                title=resolved_title,
                source_type=SourceType.MANUAL,
                language=clean_language,
            )
            try:
                transcription, profile = self.transcription_service.begin_realtime(
                    meeting.id,
                    profile_id=profile_id,
                    language=clean_language,
                    task=resolved_task,
                    allow_model_download=allow_model_download,
                    title=resolved_title,
                )
                session_id = str(uuid4())
                destination = self.storage.new_live_capture_path(meeting.uuid)
                status = self.backend.start(
                    session_id=session_id,
                    source_id=source_id,
                    destination=destination,
                )
            except Exception:
                self.meetings.delete(meeting.id)
                raise
            self._reset_realtime_state()
            started_at = datetime.now(UTC).isoformat(timespec="milliseconds")
            self.meetings.update(meeting.id, {"started_at": started_at})
            session = LiveCaptureSession(
                session_id=session_id,
                meeting_id=meeting.id,
                transcription_id=transcription.id,
                title=resolved_title,
                state=status.state,
                source=status.source,
                elapsed_ms=status.elapsed_ms,
                level=status.level,
                started_at=started_at,
                profile_id=profile.id,
                language=clean_language,
                realtime_status=self._realtime_status,
                realtime_message=self._realtime_message,
                segment_count=0,
            )
            self._session = session
            self._profile = profile
            self._settings = {
                "profile_id": profile.id,
                "language": clean_language,
                "task": resolved_task,
                "allow_model_download": allow_model_download,
                "transcription_id": transcription.id,
                "meeting_id": meeting.id,
                "meeting_title": resolved_title,
                "meeting_uuid": meeting.uuid,
            }
            self._realtime_task = asyncio.create_task(
                self._run_realtime(session_id),
                name=f"live-transcription-{session_id}",
            )
            logger.info(
                "Live transcription started from %s with %s-second chunks",
                session.source.name,
                self._active_chunk_seconds,
            )
            if self.webhooks is not None:
                self.webhooks.publish_live_session("live.session.started", session)
            return session

    def status(self) -> LiveCaptureSession | None:
        with self._lock:
            if self._session is None:
                return None
            backend_status = self.backend.status()
            if backend_status is not None:
                self._session = self._with_status(self._session, backend_status)
            return self._session

    def pause(self, session_id: str) -> LiveCaptureSession:
        with self._lock:
            session = self._require_session(session_id)
            self._session = self._with_status(session, self.backend.pause())
            logger.info("Live transcription paused")
            if self.webhooks is not None:
                self.webhooks.publish_live_session("live.session.paused", self._session)
            return self._session

    def resume(self, session_id: str) -> LiveCaptureSession:
        with self._lock:
            session = self._require_session(session_id)
            self._session = self._with_status(session, self.backend.resume())
            logger.info("Live transcription resumed")
            if self.webhooks is not None:
                self.webhooks.publish_live_session("live.session.resumed", self._session)
            return self._session

    async def stop(
        self,
        session_id: str,
        *,
        final_transcription: bool = True,
        postprocess_options: dict[str, Any] | None = None,
    ) -> tuple[LiveCaptureSession, Recording, Job, Transcription, Job]:
        with self._lock:
            session = self._require_session(session_id)
            if self._stopping:
                raise ValidationError("The live transcription is already stopping")
            self._stopping = True
            realtime_task = self._realtime_task
            settings = dict(self._settings or {})
        if realtime_task is not None:
            await asyncio.gather(realtime_task, return_exceptions=True)

        captured = self.backend.stop()
        self.meetings.update(
            session.meeting_id,
            {
                "ended_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "duration_ms": captured.duration_ms,
            },
        )
        try:
            await self._process_available_audio(force=True)
        except Exception as error:
            logger.exception("Could not process the final real-time audio window: %s", error)

        stopped_session = LiveCaptureSession(
            session_id=session.session_id,
            meeting_id=session.meeting_id,
            transcription_id=session.transcription_id,
            title=session.title,
            state="stopped",
            source=session.source,
            elapsed_ms=captured.duration_ms,
            level=0.0,
            started_at=session.started_at,
            profile_id=session.profile_id,
            language=session.language,
            realtime_status="finalizing",
            realtime_message="Refining the complete transcript",
            segment_count=len(self._live_segments),
        )
        if self.webhooks is not None:
            self.webhooks.publish_live_session("live.session.stopped", stopped_session)
        recording, import_job = await self.import_service.register_capture(
            session.meeting_id,
            captured,
        )
        transcription, transcription_job = await self.transcription_service.finalize_realtime(
            session.transcription_id,
            recording.id,
            profile_id=str(settings["profile_id"]),
            language=settings.get("language"),
            task=str(settings.get("task", "transcribe")),
            allow_model_download=bool(settings.get("allow_model_download", False)),
            run_final_pass=final_transcription,
            postprocess_options=postprocess_options,
        )
        with self._lock:
            self._session = None
            self._settings = None
            self._profile = None
            self._realtime_task = None
            self._stopping = False
        logger.info(
            "Live transcription stopped after %.1f seconds; final processing queued",
            captured.duration_ms / 1000,
        )
        return (
            stopped_session,
            recording,
            import_job,
            transcription,
            transcription_job,
        )

    async def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            realtime_task = self._realtime_task
            transcription_id = self._session.transcription_id if self._session is not None else None
        if realtime_task is not None:
            await asyncio.gather(realtime_task, return_exceptions=True)
        self.backend.shutdown()
        if transcription_id is not None:
            self.transcriptions.set_status(transcription_id, "failed")

    async def _run_realtime(self, session_id: str) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            with self._lock:
                if (
                    self._stopping
                    or self._session is None
                    or self._session.session_id != session_id
                ):
                    return
            try:
                await self._process_available_audio(force=False)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Real-time transcription window failed: %s", error)
                self._set_realtime_state(
                    "error",
                    "Live text is temporarily unavailable; the final pass will retry",
                    error=str(error) or type(error).__name__,
                )

    async def _process_available_audio(self, *, force: bool) -> None:
        batch = self.backend.drain_frames()
        if batch is not None:
            self._append_batch(batch)
        if not self._live_sample_rate or not self._live_channels:
            return

        frame_width = self._live_channels * 2
        target_frames = max(
            1,
            round(self._live_sample_rate * self._active_chunk_seconds),
        )
        minimum_frames = max(1, round(self._live_sample_rate * 0.35))
        while len(self._live_buffer) // frame_width >= target_frames:
            await self._process_next_window(target_frames)
        remaining_frames = len(self._live_buffer) // frame_width
        if force and remaining_frames >= minimum_frames:
            await self._process_next_window(remaining_frames)

    def _append_batch(self, batch: AudioFrameBatch) -> None:
        if not batch.pcm_s16le:
            return
        frame_width = batch.channels * 2
        if not self._live_buffer:
            self._live_buffer_start_frame = batch.start_frame
        elif batch.sample_rate != self._live_sample_rate or batch.channels != self._live_channels:
            raise ValidationError("The live audio format changed during capture")
        else:
            expected_start = self._live_buffer_start_frame + len(self._live_buffer) // frame_width
            if batch.start_frame > expected_start:
                self._live_buffer.clear()
                self._live_overlap = b""
                self._live_buffer_start_frame = batch.start_frame
        self._live_sample_rate = batch.sample_rate
        self._live_channels = batch.channels
        self._live_buffer.extend(batch.pcm_s16le)

    async def _process_next_window(self, frame_count: int) -> None:
        frame_width = self._live_channels * 2
        byte_count = frame_count * frame_width
        chunk = bytes(self._live_buffer[:byte_count])
        del self._live_buffer[:byte_count]
        chunk_start_frame = self._live_buffer_start_frame
        self._live_buffer_start_frame += frame_count

        overlap_frames = len(self._live_overlap) // frame_width
        window = self._live_overlap + chunk
        window_start_frame = max(0, chunk_start_frame - overlap_frames)
        overlap_target = max(
            0,
            round(self._live_sample_rate * self._active_overlap_seconds) * frame_width,
        )
        self._live_overlap = chunk[-overlap_target:] if overlap_target else b""
        await self._transcribe_window(window, window_start_frame)

    async def _transcribe_window(self, pcm: bytes, start_frame: int) -> None:
        with self._lock:
            settings = dict(self._settings or {})
            profile = self._profile
        if not settings or profile is None:
            return
        path = self.storage.new_realtime_chunk_path(str(settings["meeting_uuid"]))
        await asyncio.to_thread(
            _write_pcm_wav,
            path,
            pcm,
            self._live_sample_rate,
            self._live_channels,
        )
        self._set_realtime_state(
            "transcribing",
            "Transcribing the latest audio locally",
        )
        try:
            result = await self.transcription_service.engine.transcribe(
                TranscriptionEngineRequest(
                    audio_path=path,
                    model=profile.model,
                    device=profile.device,
                    compute_type=profile.compute_type,
                    language=settings.get("language"),
                    task=str(settings.get("task", "transcribe")),
                    beam_size=min(profile.beam_size, 2),
                    vad_filter=True,
                    allow_model_download=bool(settings.get("allow_model_download", False)),
                    engine=profile.engine,
                    device_index=profile.device_index,
                    cpu_threads=profile.cpu_threads,
                    num_workers=profile.num_workers,
                    vad_min_silence_ms=profile.vad_min_silence_ms,
                    word_timestamps=profile.word_timestamps,
                    condition_on_previous_text=(profile.condition_on_previous_text),
                    keep_model_loaded=profile.keep_model_loaded,
                ),
                lambda _progress, _message: None,
                lambda: False,
                lambda _segment: None,
            )
            self._commit_live_segments(
                int(settings["transcription_id"]),
                result.segments,
                start_frame=start_frame,
            )
        finally:
            path.unlink(missing_ok=True)

    def _commit_live_segments(
        self,
        transcription_id: int,
        segments: list[SegmentDraft],
        *,
        start_frame: int,
    ) -> None:
        window_start_ms = round(start_frame / self._live_sample_rate * 1000)
        committed_text = " ".join(segment.text for segment in self._live_segments[-4:])
        added = 0
        added_segments: list[SegmentDraft] = []
        for segment in segments:
            absolute_start = window_start_ms + segment.start_ms
            absolute_end = window_start_ms + segment.end_ms
            if absolute_end <= self._committed_until_ms - 100:
                continue
            text = _trim_duplicate_prefix(segment.text, committed_text)
            if not text:
                continue
            absolute_start = max(0, absolute_start, self._committed_until_ms - 100)
            absolute_end = max(absolute_start, absolute_end)
            draft = SegmentDraft(
                index=len(self._live_segments),
                start_ms=absolute_start,
                end_ms=absolute_end,
                text=text,
                confidence=segment.confidence,
                metadata={
                    **(segment.metadata or {}),
                    "realtime": True,
                    "window": self._chunk_sequence,
                },
            )
            self.transcriptions.append_segment(
                transcription_id,
                draft,
                is_final=False,
            )
            self._live_segments.append(draft)
            added_segments.append(draft)
            self._committed_until_ms = max(self._committed_until_ms, absolute_end)
            committed_text = f"{committed_text} {text}".strip()
            added += 1
        self._chunk_sequence += 1
        if added:
            count = len(self._live_segments)
            self._set_realtime_state(
                "live",
                f"{count} live segment{'s' if count != 1 else ''}",
            )
            if self.webhooks is not None and self._settings is not None:
                self.webhooks.publish_live_segments(
                    meeting_id=int(self._settings["meeting_id"]),
                    transcription_id=transcription_id,
                    meeting_title=str(self._settings["meeting_title"]),
                    segments=added_segments,
                    sequence=self._chunk_sequence,
                )
        else:
            self._set_realtime_state("listening", "Listening for speech")

    def _set_realtime_state(
        self,
        status: str,
        message: str,
        *,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._realtime_status = status
            self._realtime_message = message
            self._realtime_error = error
            if self._session is not None:
                backend_status = self.backend.status()
                if backend_status is not None:
                    self._session = self._with_status(self._session, backend_status)

    def _require_session(self, session_id: str) -> LiveCaptureSession:
        if self._session is None or self._session.session_id != session_id:
            raise NotFoundError("Live capture session not found")
        return self._session

    def _with_status(self, session: LiveCaptureSession, status: Any) -> LiveCaptureSession:
        return LiveCaptureSession(
            session_id=session.session_id,
            meeting_id=session.meeting_id,
            transcription_id=session.transcription_id,
            title=session.title,
            state=status.state,
            source=status.source,
            elapsed_ms=status.elapsed_ms,
            level=status.level,
            started_at=session.started_at,
            profile_id=session.profile_id,
            language=session.language,
            realtime_status=self._realtime_status,
            realtime_message=self._realtime_message,
            segment_count=len(self._live_segments),
        )

    def _reset_realtime_state(self) -> None:
        self._stopping = False
        self._live_buffer.clear()
        self._live_buffer_start_frame = 0
        self._live_overlap = b""
        self._live_sample_rate = 0
        self._live_channels = 0
        self._live_segments.clear()
        self._committed_until_ms = 0
        self._chunk_sequence = 0
        self._realtime_status = "warming_up"
        self._realtime_message = "Preparing the local model"
        self._realtime_error = None


def _write_pcm_wav(
    path: Path,
    pcm: bytes,
    sample_rate: int,
    channels: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)


def _trim_duplicate_prefix(text: str, committed_text: str) -> str:
    words = text.strip().split()
    previous = committed_text.strip().split()
    if not words or not previous:
        return " ".join(words)

    def normalized(word: str) -> str:
        return word.casefold().strip(".,!?;:()[]{}\"'")

    normalized_words = [normalized(word) for word in words]
    normalized_previous = [normalized(word) for word in previous]
    maximum = min(len(normalized_words), len(normalized_previous), 16)
    for size in range(maximum, 0, -1):
        if normalized_previous[-size:] == normalized_words[:size]:
            return " ".join(words[size:])
    return " ".join(words)
