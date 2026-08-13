from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from local_meeting_ai.adapters.audio_capture import create_audio_capture_backend
from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.adapters.diarization.profile_matching import (
    SherpaOnnxSpeakerProfileMatcher,
)
from local_meeting_ai.adapters.diarization.pyannote_community import (
    PyannoteCommunityDiarizationEngine,
)
from local_meeting_ai.adapters.diarization.router import DiarizationEngineRouter
from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.adapters.transcription.nvidia_asr import (
    build_nemotron_engine,
    build_parakeet_engine,
)
from local_meeting_ai.adapters.transcription.router import TranscriptionEngineRouter
from local_meeting_ai.adapters.transcription.vibevoice import VibeVoiceBitNetEngine
from local_meeting_ai.application.ai_services import (
    DiarizationService,
    SummaryService,
)
from local_meeting_ai.application.capture_service import LiveCaptureService
from local_meeting_ai.application.services import ImportService, MeetingService
from local_meeting_ai.application.speaker_service import SpeakerService
from local_meeting_ai.application.summary_templates import BUILTIN_SUMMARY_TEMPLATES
from local_meeting_ai.application.transcription_profiles import (
    TranscriptionProfileCatalog,
)
from local_meeting_ai.application.transcription_service import TranscriptionService
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import Job
from local_meeting_ai.domain.enums import JobStatus, JobType
from local_meeting_ai.domain.protocols import (
    AudioCaptureBackend,
    AudioNormalizer,
    AudioRangeExporter,
    DiarizationEngine,
    SummaryEngine,
    TranscriptionEngine,
)
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    MeetingRepository,
    RecordingRepository,
    SettingsRepository,
    SpeakerProfileRepository,
    SummaryRepository,
    SummaryTemplateRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.ffmpeg import FFmpegClient
from local_meeting_ai.infrastructure.jobs import LocalJobQueue
from local_meeting_ai.infrastructure.pytorch_cuda import PytorchCudaRuntime
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.logging_config import ActivityLog, configure_logging
from local_meeting_ai.paths import AppPaths

logger = logging.getLogger(__name__)


def _retire_removed_final_transcription_preferences(
    preferences: SettingsRepository,
) -> None:
    """Move removed final-transcription selections to a supported default."""

    configured = preferences.get_all()
    removed_engines = {"vibevoice-asr", "moss-transcribe-diarize"}
    removed_profiles = {"vibevoice-asr-7b", "moss-transcribe-diarize-0.9b"}
    if (
        configured.get("final_transcription_engine") not in removed_engines
        and configured.get("final_transcription_profile") not in removed_profiles
    ):
        return
    preferences.update(
        {
            "final_transcription_engine": "faster-whisper",
            "final_transcription_profile": "default",
        }
    )
    logger.info(
        "Removed final-transcription selection reset to Faster Whisper"
    )


@dataclass(slots=True)
class Container:
    settings: AppSettings
    paths: AppPaths
    database: Database
    meetings: MeetingRepository
    recordings: RecordingRepository
    jobs: JobRepository
    preferences: SettingsRepository
    transcriptions: TranscriptionRepository
    storage: MeetingStorage
    ffmpeg: FFmpegClient
    queue: LocalJobQueue
    meeting_service: MeetingService
    import_service: ImportService
    transcription_engine: TranscriptionEngine
    transcription_profiles: TranscriptionProfileCatalog
    transcription_service: TranscriptionService
    audio_capture_backend: AudioCaptureBackend
    capture_service: LiveCaptureService
    diarization_engine: DiarizationEngine
    diarization_service: DiarizationService
    summary_engine: SummaryEngine
    summary_service: SummaryService
    summaries: SummaryRepository
    summary_templates: SummaryTemplateRepository
    speaker_service: SpeakerService
    speaker_profiles: SpeakerProfileRepository
    activity_log: ActivityLog
    pytorch_cuda: PytorchCudaRuntime


async def _start_postprocess_summary(
    summary_service: SummaryService,
    jobs: JobRepository,
    preceding_job: Job,
    transcription_id: int,
    options: dict[str, object],
) -> None:
    try:
        await summary_service.start(transcription_id, postprocess=True)
        logger.info("Meeting AI analysis queued")
    except Exception as error:
        logger.warning("Meeting AI analysis skipped: %s", error)
        failed_job = jobs.create(
            meeting_id=preceding_job.meeting_id,
            job_type=JobType.SUMMARIZE,
            payload={
                "transcription_id": transcription_id,
                "postprocess": True,
                "postprocess_options": options,
            },
            message="Meeting AI analysis could not start",
        )
        jobs.fail(failed_job.uuid, str(error) or type(error).__name__)


def build_container(
    settings: AppSettings,
    *,
    transcription_engine: TranscriptionEngine | None = None,
    audio_normalizer: AudioNormalizer | None = None,
    audio_capture_backend: AudioCaptureBackend | None = None,
    diarization_engine: DiarizationEngine | None = None,
    summary_engine: SummaryEngine | None = None,
    audio_range_exporter: AudioRangeExporter | None = None,
) -> Container:
    paths = AppPaths.from_settings(settings)
    paths.ensure(include_models=False)
    activity_log = configure_logging(paths, settings.log_level)

    database = Database(paths.database)
    MigrationRunner(database).apply()

    meetings = MeetingRepository(database)
    recordings = RecordingRepository(database)
    jobs = JobRepository(database)
    preferences = SettingsRepository(database)
    _retire_removed_final_transcription_preferences(preferences)
    configured_models_directory = preferences.get_all().get("models_directory")
    if settings.models_dir is None and isinstance(configured_models_directory, str):
        clean_models_directory = configured_models_directory.strip()
        if clean_models_directory:
            paths = paths.with_models_directory(Path(clean_models_directory))
    paths.models.mkdir(parents=True, exist_ok=True)
    transcriptions = TranscriptionRepository(database)
    summaries = SummaryRepository(database)
    summary_templates = SummaryTemplateRepository(database)
    summary_templates.seed_builtins(BUILTIN_SUMMARY_TEMPLATES)
    speaker_profiles = SpeakerProfileRepository(database)
    transcriptions.recover_interrupted()
    storage = MeetingStorage(paths, settings.max_upload_bytes)
    ffmpeg = FFmpegClient(settings.ffmpeg_path)
    speaker_service = SpeakerService(
        meetings=meetings,
        recordings=recordings,
        transcriptions=transcriptions,
        storage=storage,
        exporter=audio_range_exporter or ffmpeg,
        normalizer=ffmpeg,
        profiles=speaker_profiles,
    )
    queue = LocalJobQueue(jobs, worker_count=settings.max_heavy_jobs)
    meeting_service = MeetingService(meetings, jobs, storage)
    import_service = ImportService(meetings, recordings, jobs, storage, ffmpeg, queue)
    if transcription_engine is not None:
        resolved_engine = transcription_engine
    else:
        faster_whisper_engine = FasterWhisperEngine(paths.models)
        resolved_engine = TranscriptionEngineRouter(
            {
                faster_whisper_engine.name: faster_whisper_engine,
                "vibevoice-asr-bitnet": VibeVoiceBitNetEngine(paths.models),
                "nvidia-parakeet": build_parakeet_engine(paths.models),
                "nvidia-nemotron": build_nemotron_engine(paths.models),
            }
        )
    profiles = TranscriptionProfileCatalog(resolved_engine, preferences)
    transcription_service = TranscriptionService(
        meetings=meetings,
        recordings=recordings,
        transcriptions=transcriptions,
        jobs=jobs,
        storage=storage,
        normalizer=audio_normalizer or ffmpeg,
        engine=resolved_engine,
        profiles=profiles,
        preferences=preferences,
        queue=queue,
    )
    resolved_capture_backend = audio_capture_backend or create_audio_capture_backend()
    capture_service = LiveCaptureService(
        backend=resolved_capture_backend,
        meetings=meeting_service,
        import_service=import_service,
        transcription_service=transcription_service,
        transcriptions=transcriptions,
        preferences=preferences,
        storage=storage,
        poll_interval=0.02 if settings.testing else 0.75,
        chunk_seconds=0.1 if settings.testing else 3.0,
        overlap_seconds=0.0 if settings.testing else 1.0,
    )
    queue.register(JobType.IMPORT_MEDIA, import_service.process_import)
    queue.register(JobType.TRANSCRIBE, transcription_service.process)
    if diarization_engine is not None:
        resolved_diarization = diarization_engine
        profile_matcher = None
    else:
        sherpa_diarization = SherpaOnnxDiarizationEngine(paths.models)
        resolved_diarization = DiarizationEngineRouter(
            {
                sherpa_diarization.name: sherpa_diarization,
                "diarize": DiarizeCpuEngine(paths.models),
                "pyannote-community-1": PyannoteCommunityDiarizationEngine(
                    paths.models,
                    access_token=settings.pyannote_token,
                ),
            },
            primary=sherpa_diarization.name,
        )
        profile_matcher = SherpaOnnxSpeakerProfileMatcher(paths.models)
    diarization_service = DiarizationService(
        engine=resolved_diarization,
        recordings=recordings,
        transcriptions=transcriptions,
        jobs=jobs,
        preferences=preferences,
        queue=queue,
        speaker_profiles=speaker_profiles,
        profile_matcher=profile_matcher,
    )
    resolved_summary = summary_engine or LlamaCppSummaryEngine(paths.models)
    summary_service = SummaryService(
        engine=resolved_summary,
        summaries=summaries,
        templates=summary_templates,
        transcriptions=transcriptions,
        jobs=jobs,
        preferences=preferences,
        queue=queue,
    )
    queue.register(JobType.DIARIZE, diarization_service.process)
    queue.register(JobType.SUMMARIZE, summary_service.process)

    async def continue_postprocessing(job: Job, status: JobStatus) -> None:
        if not bool(job.payload.get("postprocess")):
            return
        transcription_id = job.payload.get("transcription_id")
        if not isinstance(transcription_id, int):
            return
        raw_options = job.payload.get("postprocess_options")
        options = raw_options if isinstance(raw_options, dict) else {}
        run_diarization = bool(options.get("diarization", True))
        run_summary = bool(options.get("summary", True))
        speaker_count = options.get("speaker_count")
        if not isinstance(speaker_count, int) or speaker_count < 1:
            speaker_count = None
        if job.job_type == JobType.TRANSCRIBE:
            if status != JobStatus.COMPLETED:
                return
            if not run_diarization:
                if not run_summary:
                    logger.info("Post-processing completed without speakers or AI analysis")
                    return
                await _start_postprocess_summary(
                    summary_service,
                    jobs,
                    job,
                    transcription_id,
                    options,
                )
                return
            try:
                await diarization_service.start(
                    transcription_id,
                    speaker_count=speaker_count,
                    postprocess_options=options,
                    postprocess=True,
                )
                logger.info("Speaker identification queued after transcription")
                return
            except Exception as error:
                logger.warning(
                    "Speaker identification skipped; continuing with AI analysis: %s",
                    error,
                )
                failed_job = jobs.create(
                    meeting_id=job.meeting_id,
                    job_type=JobType.DIARIZE,
                    payload={
                        "transcription_id": transcription_id,
                        "postprocess": True,
                        "postprocess_options": options,
                    },
                    message="Speaker identification could not start",
                )
                jobs.fail(failed_job.uuid, str(error) or type(error).__name__)
        elif job.job_type == JobType.DIARIZE:
            if status not in {JobStatus.COMPLETED, JobStatus.FAILED}:
                return
            if status == JobStatus.FAILED:
                logger.warning("AI analysis will continue after diarization failure")
        else:
            return
        if run_summary:
            await _start_postprocess_summary(
                summary_service,
                jobs,
                job,
                transcription_id,
                options,
            )

    queue.register_terminal_handler(continue_postprocessing)

    return Container(
        settings=settings,
        paths=paths,
        database=database,
        meetings=meetings,
        recordings=recordings,
        jobs=jobs,
        preferences=preferences,
        transcriptions=transcriptions,
        storage=storage,
        ffmpeg=ffmpeg,
        queue=queue,
        meeting_service=meeting_service,
        import_service=import_service,
        transcription_engine=resolved_engine,
        transcription_profiles=profiles,
        transcription_service=transcription_service,
        audio_capture_backend=resolved_capture_backend,
        capture_service=capture_service,
        diarization_engine=resolved_diarization,
        diarization_service=diarization_service,
        summary_engine=resolved_summary,
        summary_service=summary_service,
        summaries=summaries,
        summary_templates=summary_templates,
        speaker_service=speaker_service,
        speaker_profiles=speaker_profiles,
        activity_log=activity_log,
        pytorch_cuda=PytorchCudaRuntime(),
    )
