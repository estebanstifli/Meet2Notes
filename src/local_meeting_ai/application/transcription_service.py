from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from local_meeting_ai.application.transcription_config import faster_whisper_config
from local_meeting_ai.application.transcription_profiles import (
    TranscriptionProfileCatalog,
)
from local_meeting_ai.domain.entities import (
    Job,
    ModelProfile,
    Recording,
    Transcription,
    TranscriptionEngineRequest,
    TranscriptSegment,
)
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    ConfirmationRequiredError,
    JobCancelledError,
    NotFoundError,
    ValidationError,
)
from local_meeting_ai.domain.protocols import AudioNormalizer, TranscriptionEngine
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    MeetingRepository,
    RecordingRepository,
    SettingsRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.jobs import JobContext, LocalJobQueue
from local_meeting_ai.infrastructure.storage import MeetingStorage

logger = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(
        self,
        *,
        meetings: MeetingRepository,
        recordings: RecordingRepository,
        transcriptions: TranscriptionRepository,
        jobs: JobRepository,
        storage: MeetingStorage,
        normalizer: AudioNormalizer,
        engine: TranscriptionEngine,
        profiles: TranscriptionProfileCatalog,
        preferences: SettingsRepository,
        queue: LocalJobQueue,
    ) -> None:
        self.meetings = meetings
        self.recordings = recordings
        self.transcriptions = transcriptions
        self.jobs = jobs
        self.storage = storage
        self.normalizer = normalizer
        self.engine = engine
        self.profiles = profiles
        self.preferences = preferences
        self.queue = queue

    def capability(self) -> dict[str, Any]:
        return self.engine.capability()

    def model_profiles(self) -> list[ModelProfile]:
        return self.profiles.list()

    async def preload_default(self) -> None:
        config = faster_whisper_config(self.preferences.get_all())
        if not config["preload_on_start"]:
            return
        seen_profiles: set[tuple[str, str]] = set()
        for purpose in ("live", "final"):
            profile = self.profiles.resolve("default", purpose=purpose)
            profile_key = (profile.engine, profile.model)
            if profile_key in seen_profiles:
                logger.info(
                    "The selected %s transcription model is already preloaded: %s",
                    purpose,
                    profile.display_name,
                )
                continue
            seen_profiles.add(profile_key)
            if not profile.installed:
                logger.info(
                    "Selected %s transcription model is not installed: %s",
                    purpose,
                    profile.display_name,
                )
                continue
            logger.info(
                "Preloading selected %s transcription model: %s",
                purpose,
                profile.display_name,
            )
            await self.engine.prepare(profile, allow_model_download=False)

    def validate_configuration(
        self,
        profile_id: str,
        *,
        purpose: str,
        allow_model_download: bool,
    ) -> ModelProfile:
        profile = self.profiles.resolve(profile_id, purpose=purpose)
        capability = self.engine.capability()
        engines = capability.get("engines")
        selected_capability = (
            engines.get(profile.engine, {}) if isinstance(engines, dict) else capability
        )
        if not profile.installed and not allow_model_download:
            raise ConfirmationRequiredError(
                f"The {profile.model} model is not installed. "
                "Confirm the model download to continue."
            )
        if not allow_model_download and not selected_capability.get("available", True):
            raise CapabilityUnavailableError(
                f"The {profile.display_name} runtime is not available on this computer"
            )
        return profile

    async def start(
        self,
        meeting_id: int,
        *,
        profile_id: str,
        language: str | None,
        task: str | None,
        allow_model_download: bool,
        title: str | None = None,
        postprocess: bool = False,
        postprocess_options: dict[str, Any] | None = None,
    ) -> tuple[Transcription, Job]:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")
        original = self.recordings.latest_for_role(meeting_id, "original")
        if not original:
            raise ValidationError("Import a recording before starting transcription")

        profile = self.validate_configuration(
            profile_id,
            purpose="final",
            allow_model_download=allow_model_download,
        )
        clean_language = self._resolve_language(language)
        resolved_task = self._resolve_task(task)
        clean_title = title.strip() if title and title.strip() else None
        workflow_options = _postprocess_options(postprocess_options)
        transcription = self.transcriptions.create(
            meeting_id=meeting_id,
            title=clean_title or self.transcriptions.next_default_title(),
            engine=profile.engine,
            model=profile.model,
            language=clean_language,
            settings={
                "profile_id": profile.id,
                "device": profile.device,
                "compute_type": profile.compute_type,
                "beam_size": profile.beam_size,
                "vad_filter": profile.vad_filter,
                "device_index": profile.device_index,
                "cpu_threads": profile.cpu_threads,
                "num_workers": profile.num_workers,
                "vad_min_silence_ms": profile.vad_min_silence_ms,
                "word_timestamps": profile.word_timestamps,
                "condition_on_previous_text": profile.condition_on_previous_text,
                "keep_model_loaded": profile.keep_model_loaded,
                "task": resolved_task,
                "model_download_confirmed": allow_model_download,
            },
        )
        job = self.jobs.create(
            meeting_id=meeting_id,
            job_type=JobType.TRANSCRIBE,
            payload={
                "transcription_id": transcription.id,
                "recording_id": original.id,
                "profile_id": profile.id,
                "language": clean_language,
                "task": resolved_task,
                "allow_model_download": allow_model_download,
                "postprocess": postprocess,
                "postprocess_options": workflow_options,
            },
            message="Waiting to normalize audio",
        )
        await self.queue.submit(job.uuid)
        return transcription, job

    def begin_realtime(
        self,
        meeting_id: int,
        *,
        profile_id: str,
        language: str | None,
        task: str | None,
        allow_model_download: bool,
        title: str | None = None,
    ) -> tuple[Transcription, ModelProfile]:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")
        profile = self.validate_configuration(
            profile_id,
            purpose="live",
            allow_model_download=allow_model_download,
        )
        clean_language = self._resolve_language(language)
        resolved_task = self._resolve_task(task)
        clean_title = title.strip() if title and title.strip() else None
        transcription = self.transcriptions.create(
            meeting_id=meeting_id,
            title=clean_title or self.transcriptions.next_default_title(),
            engine=profile.engine,
            model=profile.model,
            language=clean_language,
            settings={
                "profile_id": profile.id,
                "device": profile.device,
                "compute_type": profile.compute_type,
                "beam_size": profile.beam_size,
                "vad_filter": profile.vad_filter,
                "device_index": profile.device_index,
                "cpu_threads": profile.cpu_threads,
                "num_workers": profile.num_workers,
                "vad_min_silence_ms": profile.vad_min_silence_ms,
                "word_timestamps": profile.word_timestamps,
                "condition_on_previous_text": profile.condition_on_previous_text,
                "keep_model_loaded": profile.keep_model_loaded,
                "task": resolved_task,
                "model_download_confirmed": allow_model_download,
                "mode": "realtime",
            },
        )
        self.transcriptions.mark_running(transcription.id)
        running = self.transcriptions.get(transcription.id)
        assert running is not None
        return running, profile

    async def finalize_realtime(
        self,
        transcription_id: int,
        recording_id: int,
        *,
        profile_id: str,
        language: str | None,
        task: str,
        allow_model_download: bool,
        run_final_pass: bool = True,
        postprocess_options: dict[str, Any] | None = None,
    ) -> tuple[Transcription, Job]:
        transcription = self.transcriptions.get(transcription_id)
        recording = self.recordings.get(recording_id)
        if not transcription or not recording:
            raise NotFoundError("The live transcription source no longer exists")
        if transcription.meeting_id != recording.meeting_id:
            raise ValidationError("The live recording does not match its transcription")
        workflow_options = _postprocess_options(postprocess_options)
        profile_id = "default"
        if run_final_pass:
            profile = self.validate_configuration(
                profile_id,
                purpose="final",
                allow_model_download=allow_model_download,
            )
            profile_id = profile.id
        self.transcriptions.set_status(transcription_id, "queued")
        job = self.jobs.create(
            meeting_id=transcription.meeting_id,
            job_type=JobType.TRANSCRIBE,
            payload={
                "transcription_id": transcription_id,
                "recording_id": recording_id,
                "profile_id": profile_id,
                "language": language,
                "task": task,
                "allow_model_download": allow_model_download,
                "preserve_existing_segments": True,
                "postprocess": True,
                "postprocess_options": workflow_options,
                "skip_final_pass": not run_final_pass,
            },
            message=(
                "Refining the live transcript"
                if run_final_pass
                else "Keeping the live transcript without a final pass"
            ),
        )
        await self.queue.submit(job.uuid)
        queued = self.transcriptions.get(transcription_id)
        assert queued is not None
        return queued, job

    def update_title(self, transcription_id: int, title: str) -> Transcription:
        clean_title = title.strip()
        if not clean_title:
            raise ValidationError("A transcription name is required")
        transcription = self.transcriptions.update_title(transcription_id, clean_title)
        if not transcription:
            raise NotFoundError("Transcription not found")
        return transcription

    async def process(self, job: Job, context: JobContext) -> dict[str, Any]:
        transcription_id = job.payload.get("transcription_id")
        recording_id = job.payload.get("recording_id")
        profile_id = job.payload.get("profile_id")
        if not isinstance(transcription_id, int) or not isinstance(recording_id, int):
            raise ValidationError("Transcription job payload is incomplete")
        if not isinstance(profile_id, str):
            raise ValidationError("Transcription profile is missing")

        transcription = self.transcriptions.get(transcription_id)
        recording = self.recordings.get(recording_id)
        meeting = self.meetings.get(job.meeting_id or -1)
        if not transcription or not recording or not meeting:
            raise NotFoundError("The transcription source no longer exists")
        if bool(job.payload.get("skip_final_pass", False)):
            await context.update(0.5, "Keeping the live transcript")
            completed = self.transcriptions.complete_realtime(transcription_id)
            if not completed:
                raise NotFoundError("The live transcription no longer exists")
            await context.update(0.96, "Saving the live transcript")
            return {
                "transcription_id": transcription_id,
                "segment_count": len(self.transcriptions.segments(transcription_id)),
                "skipped_final_pass": True,
            }
        profile = self.profiles.get(profile_id)
        self.transcriptions.mark_running(transcription_id)
        if not bool(job.payload.get("preserve_existing_segments", False)):
            self.transcriptions.clear_segments(transcription_id)

        def is_cancelled() -> bool:
            current = self.jobs.get(job.uuid)
            return current is None or current.cancel_requested

        normalized: Recording | None = None
        try:
            await context.update(0.04, "Preparing audio for transcription")
            normalized = self._reusable_normalized(recording)
            if not normalized:
                destination = self.storage.new_normalized_audio_path(meeting.uuid)
                probe = await self.normalizer.normalize_for_transcription(
                    Path(recording.local_path),
                    destination,
                    sample_rate=16000,
                    channels=1,
                    is_cancelled=is_cancelled,
                )
                checksum = await asyncio.to_thread(_sha256_file, destination)
                normalized = self.recordings.create(
                    meeting_id=meeting.id,
                    role="normalized",
                    local_path=str(destination),
                    original_filename="transcription-source.wav",
                    media_type="audio/wav",
                    size_bytes=destination.stat().st_size,
                    sha256=checksum,
                    metadata={
                        "source_recording_id": recording.id,
                        "has_audio": True,
                        "has_video": False,
                    },
                )
                self.recordings.update_probe(
                    normalized.id,
                    duration_ms=probe.duration_ms,
                    sample_rate=probe.sample_rate,
                    channels=probe.channels,
                    size_bytes=probe.size_bytes,
                    metadata=normalized.metadata,
                )

            await context.raise_if_cancelled()
            await context.update(0.24, "Starting local transcription")

            def report_progress(progress: float, message: str) -> None:
                self.jobs.update_progress(
                    job.uuid,
                    0.24 + max(0.0, min(progress, 1.0)) * 0.70,
                    message,
                )

            def report_segment(segment: Any) -> None:
                self.transcriptions.append_segment(
                    transcription_id,
                    segment,
                    is_final=False,
                )

            result = await self.engine.transcribe(
                TranscriptionEngineRequest(
                    audio_path=Path(normalized.local_path),
                    model=profile.model,
                    device=profile.device,
                    compute_type=profile.compute_type,
                    language=job.payload.get("language"),
                    task=str(job.payload.get("task", "transcribe")),
                    beam_size=profile.beam_size,
                    vad_filter=profile.vad_filter,
                    allow_model_download=bool(job.payload.get("allow_model_download", False)),
                    engine=profile.engine,
                    device_index=profile.device_index,
                    cpu_threads=profile.cpu_threads,
                    num_workers=profile.num_workers,
                    vad_min_silence_ms=profile.vad_min_silence_ms,
                    word_timestamps=profile.word_timestamps,
                    condition_on_previous_text=(profile.condition_on_previous_text),
                    keep_model_loaded=profile.keep_model_loaded,
                    provider_options=profile.provider_options,
                ),
                report_progress,
                is_cancelled,
                report_segment,
            )
            await context.raise_if_cancelled()
            await context.update(0.96, "Saving the final transcript")
            completed = self.transcriptions.complete(
                transcription_id,
                language=result.language,
                segments=result.segments,
            )
            if not completed:
                raise NotFoundError("The transcription no longer exists")
            assigned_segments = 0
            if result.speaker_turns:
                diarization = self.preferences.get_all().get("diarization")
                minimum_overlap_ratio = (
                    float(diarization.get("minimum_overlap_ratio", 0.15))
                    if isinstance(diarization, dict)
                    else 0.15
                )
                assigned_segments = self.transcriptions.assign_diarization(
                    meeting_id=completed.meeting_id,
                    transcription_id=transcription_id,
                    diarization=result.speaker_turns,
                    minimum_overlap_ratio=minimum_overlap_ratio,
                )
            return {
                "transcription_id": transcription_id,
                "segment_count": len(result.segments),
                "language": result.language,
                "language_probability": result.language_probability,
                "duration_ms": result.duration_ms,
                "normalized_recording_id": normalized.id,
                "speaker_count": len(
                    {turn.speaker for turn in result.speaker_turns}
                ),
                "assigned_segments": assigned_segments,
            }
        except JobCancelledError:
            self.transcriptions.set_status(transcription_id, "cancelled")
            raise
        except Exception:
            self.transcriptions.set_status(transcription_id, "failed")
            raise

    def list(self, meeting_id: int) -> list[Transcription]:
        if not self.meetings.get(meeting_id):
            raise NotFoundError("Meeting not found")
        return self.transcriptions.list_for_meeting(meeting_id)

    def get(self, transcription_id: int) -> tuple[Transcription, Sequence[TranscriptSegment]]:
        transcription = self.transcriptions.get(transcription_id)
        if not transcription:
            raise NotFoundError("Transcription not found")
        return transcription, self.transcriptions.segments(transcription_id)

    def activate(self, transcription_id: int) -> Transcription:
        transcription = self.transcriptions.activate(transcription_id)
        if not transcription:
            raise ValidationError("Only a completed transcription can be activated")
        return transcription

    def update_segment(self, segment_id: int, text: str) -> TranscriptSegment:
        clean_text = text.strip()
        if not clean_text:
            raise ValidationError("A transcript segment cannot be empty")
        segment = self.transcriptions.update_segment(segment_id, clean_text)
        if not segment:
            raise NotFoundError("Transcript segment not found")
        return segment

    def find_replace(
        self,
        transcription_id: int,
        *,
        find: str,
        replacement: str,
        case_sensitive: bool,
    ) -> int:
        if not find:
            raise ValidationError("Search text cannot be empty")
        if not self.transcriptions.get(transcription_id):
            raise NotFoundError("Transcription not found")
        return self.transcriptions.find_replace(
            transcription_id,
            find=find,
            replacement=replacement,
            case_sensitive=case_sensitive,
        )

    def _reusable_normalized(self, original: Recording) -> Recording | None:
        candidate = self.recordings.latest_for_role(original.meeting_id, "normalized")
        if (
            candidate
            and candidate.metadata.get("source_recording_id") == original.id
            and Path(candidate.local_path).is_file()
        ):
            return candidate
        return None

    def _resolve_language(self, language: str | None) -> str | None:
        if language and language.strip():
            return language.strip()
        preferences = self.preferences.get_all()
        config = faster_whisper_config(preferences)
        configured = config.get("language")
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        legacy = preferences.get("default_transcription_language")
        return legacy.strip() if isinstance(legacy, str) and legacy.strip() else None

    def _resolve_task(self, task: str | None) -> str:
        if task in {"transcribe", "translate"}:
            return task
        config = faster_whisper_config(self.preferences.get_all())
        configured = str(config.get("task", "transcribe"))
        return configured if configured in {"transcribe", "translate"} else "transcribe"


def _postprocess_options(options: dict[str, Any] | None) -> dict[str, Any]:
    source = options if isinstance(options, dict) else {}
    speaker_count = source.get("speaker_count")
    return {
        "diarization": bool(source.get("diarization", True)),
        "speaker_count": speaker_count if isinstance(speaker_count, int) else None,
        "summary": bool(source.get("summary", True)),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
