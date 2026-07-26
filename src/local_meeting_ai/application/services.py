from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import CapturedAudio, Job, Meeting, Recording
from local_meeting_ai.domain.enums import JobType, MeetingStatus, SourceType
from local_meeting_ai.domain.errors import JobCancelledError, NotFoundError, ValidationError
from local_meeting_ai.domain.protocols import AsyncUpload, MediaProbeClient
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    MeetingRepository,
    RecordingRepository,
)
from local_meeting_ai.infrastructure.jobs import JobContext, LocalJobQueue
from local_meeting_ai.infrastructure.storage import MeetingStorage


class MeetingService:
    def __init__(
        self,
        meetings: MeetingRepository,
        jobs: JobRepository,
        storage: MeetingStorage,
    ) -> None:
        self.meetings = meetings
        self.jobs = jobs
        self.storage = storage

    def create(
        self,
        *,
        title: str,
        description: str | None = None,
        source_type: SourceType = SourceType.MANUAL,
        language: str | None = None,
    ) -> Meeting:
        clean_title = title.strip()
        if not clean_title:
            raise ValidationError("A meeting title is required")
        meeting = self.meetings.create(
            title=clean_title,
            description=_clean_optional(description),
            source_type=source_type,
            language=_clean_optional(language),
        )
        self.storage.ensure_meeting(meeting.uuid)
        return meeting

    def list(self, *, search: str | None = None, limit: int = 100) -> list[Meeting]:
        return self.meetings.list(search=_clean_optional(search), limit=limit)

    def get(self, meeting_id: int) -> Meeting:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")
        return meeting

    def update(self, meeting_id: int, values: dict[str, Any]) -> Meeting:
        if "title" in values:
            title = str(values["title"]).strip()
            if not title:
                raise ValidationError("A meeting title is required")
            values["title"] = title
        for optional in ("description", "language"):
            if optional in values:
                values[optional] = _clean_optional(values[optional])
        meeting = self.meetings.update(meeting_id, values)
        if not meeting:
            raise NotFoundError("Meeting not found")
        return meeting

    def delete(self, meeting_id: int) -> None:
        meeting = self.get(meeting_id)
        self.jobs.request_cancel_for_meeting(meeting_id)
        self.storage.delete_meeting(meeting.uuid)
        if not self.meetings.delete(meeting_id):
            raise NotFoundError("Meeting not found")


class ImportService:
    def __init__(
        self,
        meetings: MeetingRepository,
        recordings: RecordingRepository,
        jobs: JobRepository,
        storage: MeetingStorage,
        media_probe: MediaProbeClient,
        queue: LocalJobQueue,
    ) -> None:
        self.meetings = meetings
        self.recordings = recordings
        self.jobs = jobs
        self.storage = storage
        self.media_probe = media_probe
        self.queue = queue

    async def import_media(
        self, meeting_id: int, upload: AsyncUpload
    ) -> tuple[Recording, Job]:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")

        stored = await self.storage.save_import(meeting.uuid, upload)
        try:
            recording = self.recordings.create(
                meeting_id=meeting.id,
                role="original",
                local_path=stored.path,
                original_filename=stored.original_filename,
                media_type=stored.media_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
            )
            self.meetings.set_status(meeting.id, MeetingStatus.IMPORTING)
            job = self.jobs.create(
                meeting_id=meeting.id,
                job_type=JobType.IMPORT_MEDIA,
                payload={"recording_id": recording.id},
                message="Media stored safely",
            )
            await self.queue.submit(job.uuid)
            return recording, job
        except Exception:
            Path(stored.path).unlink(missing_ok=True)
            raise

    async def process_import(self, job: Job, context: JobContext) -> dict[str, Any]:
        recording_id = job.payload.get("recording_id")
        if not isinstance(recording_id, int):
            raise ValidationError("Import job is missing its recording identifier")
        recording = self.recordings.get(recording_id)
        if not recording:
            raise NotFoundError("The imported recording no longer exists")

        await context.update(0.2, "Checking the media container")
        await context.raise_if_cancelled()
        try:
            probe = await self.media_probe.probe_media(Path(recording.local_path))
            await context.raise_if_cancelled()
            await context.update(0.75, "Saving media details")
            updated = self.recordings.update_probe(
                recording.id,
                duration_ms=probe.duration_ms,
                sample_rate=probe.sample_rate,
                channels=probe.channels,
                size_bytes=probe.size_bytes,
                metadata={
                    **probe.metadata,
                    "has_audio": probe.has_audio,
                    "has_video": probe.has_video,
                },
            )
            if not updated:
                raise NotFoundError("The imported recording no longer exists")
            self.meetings.update(
                recording.meeting_id,
                {
                    "status": MeetingStatus.READY.value,
                    "duration_ms": probe.duration_ms,
                },
            )
            await context.update(0.95, "Import ready")
            return {
                "recording_id": recording.id,
                "duration_ms": probe.duration_ms,
                "has_audio": probe.has_audio,
                "has_video": probe.has_video,
                "format": probe.format_name,
            }
        except JobCancelledError:
            self.meetings.set_status(recording.meeting_id, MeetingStatus.DRAFT)
            raise
        except Exception:
            self.meetings.set_status(recording.meeting_id, MeetingStatus.FAILED)
            raise

    async def register_capture(
        self,
        meeting_id: int,
        captured: CapturedAudio,
    ) -> tuple[Recording, Job]:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")
        path = captured.path.resolve()
        expected_root = self.storage.meeting_root(meeting.uuid).resolve()
        if not path.is_relative_to(expected_root) or not path.is_file():
            raise ValidationError("Captured audio is outside private meeting storage")
        checksum = await asyncio.to_thread(_sha256_path, path)
        recording = self.recordings.create(
            meeting_id=meeting.id,
            role="original",
            local_path=str(path),
            original_filename="live-capture.wav",
            media_type="audio/wav",
            size_bytes=path.stat().st_size,
            sha256=checksum,
            metadata={
                "capture_source_id": captured.source.id,
                "capture_source_name": captured.source.name,
                "capture_backend": captured.source.backend,
                "capture_host_api": captured.source.host_api,
                "is_loopback": captured.source.is_loopback,
            },
        )
        self.recordings.update_probe(
            recording.id,
            duration_ms=captured.duration_ms,
            sample_rate=captured.sample_rate,
            channels=captured.channels,
            size_bytes=path.stat().st_size,
            metadata=recording.metadata,
        )
        self.meetings.set_status(meeting.id, MeetingStatus.IMPORTING)
        job = self.jobs.create(
            meeting_id=meeting.id,
            job_type=JobType.IMPORT_MEDIA,
            payload={"recording_id": recording.id},
            message="Finalizing live capture",
        )
        await self.queue.submit(job.uuid)
        return recording, job


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
