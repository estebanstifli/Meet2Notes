from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

import aiofiles

from local_meeting_ai.domain.entities import StoredMedia
from local_meeting_ai.domain.errors import UploadTooLargeError, ValidationError
from local_meeting_ai.domain.protocols import AsyncUpload
from local_meeting_ai.paths import AppPaths

ALLOWED_MEDIA_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
}


class MeetingStorage:
    def __init__(self, paths: AppPaths, max_upload_bytes: int) -> None:
        self.paths = paths
        self.max_upload_bytes = max_upload_bytes

    def meeting_root(self, meeting_uuid: str) -> Path:
        return self.paths.meetings / meeting_uuid

    def ensure_meeting(self, meeting_uuid: str) -> Path:
        root = self.meeting_root(meeting_uuid)
        for name in ("original", "audio", "transcript", "summaries", "exports", "temp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    def new_normalized_audio_path(self, meeting_uuid: str) -> Path:
        audio_dir = self.ensure_meeting(meeting_uuid) / "audio"
        return audio_dir / f"{uuid4().hex}.wav"

    def new_live_capture_path(self, meeting_uuid: str) -> Path:
        original_dir = self.ensure_meeting(meeting_uuid) / "original"
        return original_dir / f"live-{uuid4().hex}.wav"

    def new_realtime_chunk_path(self, meeting_uuid: str) -> Path:
        temp_dir = self.ensure_meeting(meeting_uuid) / "temp"
        return temp_dir / f"live-chunk-{uuid4().hex}.wav"

    async def save_import(self, meeting_uuid: str, upload: AsyncUpload) -> StoredMedia:
        original_filename = Path(upload.filename or "unnamed").name
        extension = Path(original_filename).suffix.lower()
        if extension not in ALLOWED_MEDIA_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_MEDIA_EXTENSIONS))
            raise ValidationError(f"Unsupported media type. Allowed extensions: {allowed}")

        original_dir = self.ensure_meeting(meeting_uuid) / "original"
        destination = original_dir / f"{uuid4().hex}{extension}"
        hasher = hashlib.sha256()
        size = 0

        try:
            async with aiofiles.open(destination, "xb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise UploadTooLargeError(
                            f"File exceeds the {self.max_upload_bytes // 1024 // 1024} MB limit"
                        )
                    hasher.update(chunk)
                    await output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        if size == 0:
            destination.unlink(missing_ok=True)
            raise ValidationError("The uploaded file is empty")

        return StoredMedia(
            path=str(destination),
            original_filename=original_filename,
            media_type=upload.content_type,
            size_bytes=size,
            sha256=hasher.hexdigest(),
        )

    def delete_meeting(self, meeting_uuid: str) -> None:
        root = self.paths.meetings.resolve()
        target = self.meeting_root(meeting_uuid).resolve()
        if target.parent != root:
            raise ValidationError("Refusing to remove a path outside meeting storage")
        if target.exists():
            shutil.rmtree(target)
