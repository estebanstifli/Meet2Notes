from __future__ import annotations

from dataclasses import dataclass

from local_meeting_ai.adapters.audio_capture import create_audio_capture_backend
from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.application.ai_services import (
    DiarizationService,
    SummaryService,
)
from local_meeting_ai.application.capture_service import LiveCaptureService
from local_meeting_ai.application.services import ImportService, MeetingService
from local_meeting_ai.application.transcription_profiles import (
    TranscriptionProfileCatalog,
)
from local_meeting_ai.application.transcription_service import TranscriptionService
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.protocols import (
    AudioCaptureBackend,
    AudioNormalizer,
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
    SummaryRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.ffmpeg import FFmpegClient
from local_meeting_ai.infrastructure.jobs import LocalJobQueue
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.logging_config import configure_logging
from local_meeting_ai.paths import AppPaths


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


def build_container(
    settings: AppSettings,
    *,
    transcription_engine: TranscriptionEngine | None = None,
    audio_normalizer: AudioNormalizer | None = None,
    audio_capture_backend: AudioCaptureBackend | None = None,
    diarization_engine: DiarizationEngine | None = None,
    summary_engine: SummaryEngine | None = None,
) -> Container:
    paths = AppPaths.from_settings(settings)
    paths.ensure()
    configure_logging(paths, settings.log_level)

    database = Database(paths.database)
    MigrationRunner(database).apply()

    meetings = MeetingRepository(database)
    recordings = RecordingRepository(database)
    jobs = JobRepository(database)
    preferences = SettingsRepository(database)
    transcriptions = TranscriptionRepository(database)
    summaries = SummaryRepository(database)
    transcriptions.recover_interrupted()
    storage = MeetingStorage(paths, settings.max_upload_bytes)
    ffmpeg = FFmpegClient(settings.ffmpeg_path)
    queue = LocalJobQueue(jobs, worker_count=settings.max_heavy_jobs)
    meeting_service = MeetingService(meetings, jobs, storage)
    import_service = ImportService(meetings, recordings, jobs, storage, ffmpeg, queue)
    resolved_engine = transcription_engine or FasterWhisperEngine(paths.models)
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
    resolved_diarization = diarization_engine or SherpaOnnxDiarizationEngine(
        paths.models
    )
    diarization_service = DiarizationService(
        engine=resolved_diarization,
        recordings=recordings,
        transcriptions=transcriptions,
        jobs=jobs,
        preferences=preferences,
        queue=queue,
    )
    resolved_summary = summary_engine or LlamaCppSummaryEngine(paths.models)
    summary_service = SummaryService(
        engine=resolved_summary,
        summaries=summaries,
        transcriptions=transcriptions,
        jobs=jobs,
        preferences=preferences,
        queue=queue,
    )
    queue.register(JobType.DIARIZE, diarization_service.process)
    queue.register(JobType.SUMMARIZE, summary_service.process)

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
    )
