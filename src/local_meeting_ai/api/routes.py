from __future__ import annotations

import asyncio
import json
import logging
import platform
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from local_meeting_ai import __version__
from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.api.schemas import (
    AudioCaptureSourceResponse,
    AudioSourcesResponse,
    FindReplaceRequest,
    FindReplaceResponse,
    ImportResponse,
    JobResponse,
    LiveCaptureSessionResponse,
    LiveCaptureStart,
    LiveCaptureStopResponse,
    MeetingCreate,
    MeetingResponse,
    MeetingUpdate,
    ModelProfileResponse,
    PreferenceResponse,
    PreferenceUpdate,
    RecordingResponse,
    SegmentUpdate,
    SummaryResponse,
    SummaryStartResponse,
    TranscriptionCreate,
    TranscriptionDetailResponse,
    TranscriptionResponse,
    TranscriptionStartResponse,
    TranscriptionTitleUpdate,
    TranscriptSegmentResponse,
)
from local_meeting_ai.application.ai_services import (
    DIARIZATION_DEFAULTS,
    SUMMARY_DEFAULTS,
    configured_values,
)
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api")
ContainerDependency = Annotated[Container, Depends(get_container)]
logger = logging.getLogger(__name__)


@router.get("/health")
def health(container: ContainerDependency) -> dict[str, Any]:
    database_status = "ok"
    try:
        with container.database.read() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        database_status = "error"
    return {
        "status": "ok" if database_status == "ok" else "degraded",
        "version": __version__,
        "database": database_status,
        "queue": "running",
    }


@router.get("/info")
def info(container: ContainerDependency) -> dict[str, Any]:
    return {
        "name": container.settings.app_name,
        "version": __version__,
        "platform": platform.system(),
        "python": platform.python_version(),
        "data_directory": str(container.paths.root),
        "listen_address": f"http://{container.settings.host}:{container.settings.port}",
        "privacy": {
            "telemetry": False,
            "remote_processing": False,
            "local_only_default": container.settings.host in {"127.0.0.1", "localhost"},
        },
    }


@router.get("/capabilities")
def capabilities(container: ContainerDependency) -> dict[str, Any]:
    transcription = container.transcription_service.capability()
    capture = container.capture_service.capability()
    diarization = container.diarization_service.capability()
    summaries = container.summary_service.capability()
    return {
        "ffmpeg": container.ffmpeg.capabilities(),
        "compute": {
            "cpu": True,
            "cuda": bool(transcription.get("cuda_available")),
            "apple_silicon": "not_implemented",
        },
        "features": {
            "media_import": "available",
            "meeting_management": "available",
            "microphone_recording": (
                "available" if capture.get("supports_microphones") else "requires_install"
            ),
            "system_audio_capture": (
                "available" if capture.get("supports_system_audio") else "unavailable"
            ),
            "transcription": (
                "available" if transcription.get("available") else "requires_install"
            ),
            "diarization": (
                "available"
                if diarization.get("available") and diarization.get("installed")
                else "requires_install"
            ),
            "summaries": (
                "available"
                if summaries.get("available") and summaries.get("installed")
                else "requires_install"
            ),
            "chat": "not_implemented",
            "exports": "not_implemented",
        },
        "supported_import_extensions": [
            "wav",
            "mp3",
            "m4a",
            "flac",
            "ogg",
            "aac",
            "mp4",
            "mkv",
            "webm",
            "mov",
        ],
        "transcription": transcription,
        "transcription_engines": [
            {
                "id": "faster-whisper",
                "name": "Faster Whisper",
                "kind": "local",
                "available": bool(transcription.get("available")),
                "configured": True,
            }
        ],
        "audio_capture": capture,
        "diarization": diarization,
        "summaries": summaries,
    }


@router.get("/audio/sources", response_model=AudioSourcesResponse)
def audio_sources(container: ContainerDependency) -> AudioSourcesResponse:
    return AudioSourcesResponse(
        capability=container.capture_service.capability(),
        sources=[
            AudioCaptureSourceResponse.model_validate(source)
            for source in container.capture_service.sources()
        ],
    )


@router.get("/audio/sources/{source_id}/level")
async def audio_source_level(
    source_id: str,
    container: ContainerDependency,
) -> dict[str, Any]:
    level = await asyncio.to_thread(
        container.audio_capture_backend.probe_level,
        source_id,
    )
    return {
        "source_id": source_id,
        "level": max(0.0, min(1.0, float(level))),
    }


@router.post("/engines/diarization/prepare")
async def prepare_diarization_engine(
    container: ContainerDependency,
    download: bool = Query(default=False),
) -> dict[str, Any]:
    config = configured_values(
        container.preferences,
        "diarization",
        DIARIZATION_DEFAULTS,
    )
    await container.diarization_engine.prepare(
        config,
        allow_model_download=download,
    )
    return container.diarization_engine.capability()


@router.post("/engines/diarization/unload")
def unload_diarization_engine(
    container: ContainerDependency,
) -> dict[str, Any]:
    container.diarization_engine.unload()
    return container.diarization_engine.capability()


@router.post("/engines/summary/prepare")
async def prepare_summary_engine(
    container: ContainerDependency,
    download: bool = Query(default=False),
) -> dict[str, Any]:
    config = configured_values(
        container.preferences,
        "summary_engine",
        SUMMARY_DEFAULTS,
    )
    await container.summary_engine.prepare(
        config,
        allow_model_download=download,
    )
    return container.summary_engine.capability()


@router.post("/engines/summary/unload")
def unload_summary_engine(
    container: ContainerDependency,
) -> dict[str, Any]:
    container.summary_engine.unload()
    return container.summary_engine.capability()


@router.post(
    "/transcriptions/{transcription_id}/diarize",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_diarization(
    transcription_id: int,
    container: ContainerDependency,
) -> JobResponse:
    job = await container.diarization_service.start(transcription_id)
    return JobResponse.model_validate(job)


@router.post(
    "/transcriptions/{transcription_id}/summaries",
    response_model=SummaryStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_summary(
    transcription_id: int,
    container: ContainerDependency,
) -> SummaryStartResponse:
    summary, job = await container.summary_service.start(transcription_id)
    return SummaryStartResponse(
        summary=SummaryResponse.model_validate(summary),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/summaries",
    response_model=list[SummaryResponse],
)
def list_summaries(
    meeting_id: int,
    container: ContainerDependency,
) -> list[SummaryResponse]:
    if not container.meetings.get(meeting_id):
        raise NotFoundError("Meeting not found")
    return [
        SummaryResponse.model_validate(summary)
        for summary in container.summaries.list_for_meeting(meeting_id)
    ]


@router.get(
    "/capture/session",
    response_model=LiveCaptureSessionResponse | None,
)
def current_capture(
    container: ContainerDependency,
) -> LiveCaptureSessionResponse | None:
    session = container.capture_service.status()
    return LiveCaptureSessionResponse.model_validate(session) if session else None


@router.post(
    "/capture/sessions",
    response_model=LiveCaptureSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_capture(
    payload: LiveCaptureStart,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    session = await container.capture_service.start(**payload.model_dump())
    return LiveCaptureSessionResponse.model_validate(session)


@router.post(
    "/capture/sessions/{session_id}/pause",
    response_model=LiveCaptureSessionResponse,
)
def pause_capture(
    session_id: str,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    return LiveCaptureSessionResponse.model_validate(
        container.capture_service.pause(session_id)
    )


@router.post(
    "/capture/sessions/{session_id}/resume",
    response_model=LiveCaptureSessionResponse,
)
def resume_capture(
    session_id: str,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    return LiveCaptureSessionResponse.model_validate(
        container.capture_service.resume(session_id)
    )


@router.post(
    "/capture/sessions/{session_id}/stop",
    response_model=LiveCaptureStopResponse,
)
async def stop_capture(
    session_id: str,
    container: ContainerDependency,
) -> LiveCaptureStopResponse:
    session, recording, import_job, transcription, transcription_job = (
        await container.capture_service.stop(session_id)
    )
    return LiveCaptureStopResponse(
        session=LiveCaptureSessionResponse.model_validate(session),
        recording=RecordingResponse.model_validate(recording),
        import_job=JobResponse.model_validate(import_job),
        transcription=TranscriptionResponse.model_validate(transcription),
        transcription_job=JobResponse.model_validate(transcription_job),
    )


@router.get("/settings", response_model=PreferenceResponse)
def get_settings(container: ContainerDependency) -> PreferenceResponse:
    return PreferenceResponse.model_validate(container.preferences.get_all())


@router.put("/settings", response_model=PreferenceResponse)
async def update_settings(
    payload: PreferenceUpdate,
    request: Request,
    container: ContainerDependency,
) -> PreferenceResponse:
    values = payload.model_dump(exclude_unset=True)
    updated = container.preferences.update(values)
    if "faster_whisper" in values or "transcription_engine" in values:
        profile = container.transcription_profiles.get("default")
        if profile.keep_model_loaded and profile.installed:
            reload_task = asyncio.create_task(
                container.transcription_engine.prepare(
                    profile,
                    allow_model_download=False,
                ),
                name="reload-default-transcription-engine",
            )
            request.app.state.background_tasks.add(reload_task)
            reload_task.add_done_callback(
                lambda finished: _finish_background_task(
                    finished,
                    request.app.state.background_tasks,
                )
            )
        elif not profile.keep_model_loaded:
            container.transcription_engine.unload()
    if "diarization" in values:
        config = configured_values(
            container.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )
        if config["keep_model_loaded"] and container.diarization_engine.capability()[
            "installed"
        ]:
            _track_background_task(
                request,
                container.diarization_engine.prepare(
                    config,
                    allow_model_download=False,
                ),
                "reload-diarization-engine",
            )
        elif not config["keep_model_loaded"]:
            container.diarization_engine.unload()
    if "summary_engine" in values:
        config = configured_values(
            container.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        if (
            config["provider"] == "local"
            and config["keep_model_loaded"]
            and container.summary_engine.capability()["installed"]
        ):
            _track_background_task(
                request,
                container.summary_engine.prepare(
                    config,
                    allow_model_download=False,
                ),
                "reload-summary-engine",
            )
        elif config["provider"] != "local" or not config["keep_model_loaded"]:
            container.summary_engine.unload()
    return PreferenceResponse.model_validate(updated)


def _track_background_task(
    request: Request,
    coroutine: Any,
    name: str,
) -> None:
    task = asyncio.create_task(coroutine, name=name)
    request.app.state.background_tasks.add(task)
    task.add_done_callback(
        lambda finished: _finish_background_task(
            finished,
            request.app.state.background_tasks,
        )
    )


def _finish_background_task(
    task: asyncio.Task[Any],
    tasks: set[asyncio.Task[Any]],
) -> None:
    tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Background transcription-engine reload failed")


@router.get("/meetings", response_model=list[MeetingResponse])
def list_meetings(
    container: ContainerDependency,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MeetingResponse]:
    return [
        MeetingResponse.model_validate(item)
        for item in container.meeting_service.list(search=search, limit=limit)
    ]


@router.post("/meetings", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate, container: ContainerDependency
) -> MeetingResponse:
    meeting = container.meeting_service.create(**payload.model_dump())
    return MeetingResponse.model_validate(meeting)


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
def get_meeting(meeting_id: int, container: ContainerDependency) -> MeetingResponse:
    return MeetingResponse.model_validate(container.meeting_service.get(meeting_id))


@router.patch("/meetings/{meeting_id}", response_model=MeetingResponse)
def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    container: ContainerDependency,
) -> MeetingResponse:
    values = payload.model_dump(exclude_unset=True)
    return MeetingResponse.model_validate(container.meeting_service.update(meeting_id, values))


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: int, container: ContainerDependency) -> Response:
    container.meeting_service.delete(meeting_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/meetings/{meeting_id}/import",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_media(
    meeting_id: int,
    container: ContainerDependency,
    file: Annotated[UploadFile, File(...)],
) -> ImportResponse:
    recording, job = await container.import_service.import_media(meeting_id, file)
    return ImportResponse(
        recording=RecordingResponse.model_validate(recording),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/recordings",
    response_model=list[RecordingResponse],
)
def list_recordings(
    meeting_id: int, container: ContainerDependency
) -> list[RecordingResponse]:
    if not container.meetings.get(meeting_id):
        raise NotFoundError("Meeting not found")
    return [
        RecordingResponse.model_validate(item)
        for item in container.recordings.list_for_meeting(meeting_id)
    ]


@router.get("/recordings/{recording_id}/media")
def recording_media(recording_id: int, container: ContainerDependency) -> FileResponse:
    recording = container.recordings.get(recording_id)
    if not recording:
        raise NotFoundError("Recording not found")
    path = Path(recording.local_path).resolve()
    storage_root = container.paths.meetings.resolve()
    if not path.is_relative_to(storage_root) or not path.is_file():
        raise NotFoundError("Recording file not found")
    return FileResponse(
        path,
        media_type=recording.media_type or "application/octet-stream",
        filename=recording.original_filename or path.name,
        content_disposition_type="inline",
    )


@router.get(
    "/models/transcription",
    response_model=list[ModelProfileResponse],
)
def transcription_models(
    container: ContainerDependency,
) -> list[ModelProfileResponse]:
    return [
        ModelProfileResponse.model_validate(profile)
        for profile in container.transcription_service.model_profiles()
    ]


@router.post(
    "/meetings/{meeting_id}/transcriptions",
    response_model=TranscriptionStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_transcription(
    meeting_id: int,
    payload: TranscriptionCreate,
    container: ContainerDependency,
) -> TranscriptionStartResponse:
    transcription, job = await container.transcription_service.start(
        meeting_id,
        **payload.model_dump(),
    )
    return TranscriptionStartResponse(
        transcription=TranscriptionResponse.model_validate(transcription),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/transcriptions",
    response_model=list[TranscriptionResponse],
)
def list_transcriptions(
    meeting_id: int,
    container: ContainerDependency,
) -> list[TranscriptionResponse]:
    return [
        TranscriptionResponse.model_validate(item)
        for item in container.transcription_service.list(meeting_id)
    ]


@router.get(
    "/transcriptions/{transcription_id}",
    response_model=TranscriptionDetailResponse,
)
def get_transcription(
    transcription_id: int,
    container: ContainerDependency,
) -> TranscriptionDetailResponse:
    transcription, segments = container.transcription_service.get(transcription_id)
    return TranscriptionDetailResponse(
        transcription=TranscriptionResponse.model_validate(transcription),
        segments=[
            TranscriptSegmentResponse.model_validate(segment) for segment in segments
        ],
    )


@router.patch(
    "/transcriptions/{transcription_id}",
    response_model=TranscriptionResponse,
)
def update_transcription(
    transcription_id: int,
    payload: TranscriptionTitleUpdate,
    container: ContainerDependency,
) -> TranscriptionResponse:
    transcription = container.transcription_service.update_title(
        transcription_id,
        payload.title,
    )
    return TranscriptionResponse.model_validate(transcription)


@router.patch(
    "/transcript-segments/{segment_id}",
    response_model=TranscriptSegmentResponse,
)
def update_transcript_segment(
    segment_id: int,
    payload: SegmentUpdate,
    container: ContainerDependency,
) -> TranscriptSegmentResponse:
    segment = container.transcription_service.update_segment(segment_id, payload.text)
    return TranscriptSegmentResponse.model_validate(segment)


@router.post(
    "/transcriptions/{transcription_id}/activate",
    response_model=TranscriptionResponse,
)
def activate_transcription(
    transcription_id: int,
    container: ContainerDependency,
) -> TranscriptionResponse:
    transcription = container.transcription_service.activate(transcription_id)
    return TranscriptionResponse.model_validate(transcription)


@router.post(
    "/transcriptions/{transcription_id}/find-replace",
    response_model=FindReplaceResponse,
)
def find_replace(
    transcription_id: int,
    payload: FindReplaceRequest,
    container: ContainerDependency,
) -> FindReplaceResponse:
    replacements = container.transcription_service.find_replace(
        transcription_id,
        **payload.model_dump(),
    )
    return FindReplaceResponse(replacements=replacements)


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    container: ContainerDependency,
    meeting_id: int | None = None,
    active_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[JobResponse]:
    return [
        JobResponse.model_validate(item)
        for item in container.jobs.list(
            meeting_id=meeting_id,
            active_only=active_only,
            limit=limit,
        )
    ]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, container: ContainerDependency) -> JobResponse:
    job = container.jobs.get(job_id)
    if not job:
        raise NotFoundError("Job not found")
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, container: ContainerDependency) -> JobResponse:
    existing = container.jobs.get(job_id)
    if not existing:
        raise NotFoundError("Job not found")
    if existing.status not in {"queued", "running", "paused"}:
        raise ValidationError(f"A {existing.status.value} job cannot be cancelled")
    job = container.jobs.request_cancel(job_id)
    assert job is not None
    if existing.job_type == JobType.TRANSCRIBE:
        transcription_id = existing.payload.get("transcription_id")
        if isinstance(transcription_id, int):
            container.transcriptions.set_status(transcription_id, "cancelled")
    return JobResponse.model_validate(job)


@router.get("/events")
async def events(request: Request, container: ContainerDependency) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        previous = ""
        while not await request.is_disconnected():
            snapshot = [
                JobResponse.model_validate(job).model_dump(mode="json")
                for job in container.jobs.list(limit=50)
            ]
            encoded = json.dumps(snapshot, separators=(",", ":"))
            if encoded != previous:
                yield f"event: jobs\ndata: {encoded}\n\n"
                previous = encoded
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
