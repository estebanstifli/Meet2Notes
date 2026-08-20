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

    def speaker_export_path(
        self,
        meeting_uuid: str,
        speaker_id: int,
        output_format: str,
    ) -> Path:
        export_dir = self.ensure_meeting(meeting_uuid) / "exports"
        return export_dir / f"speaker-{speaker_id}.{output_format}"

    def meeting_export_path(self, meeting_uuid: str, output_format: str) -> Path:
        export_dir = self.ensure_meeting(meeting_uuid) / "exports"
        return export_dir / f"meeting.{output_format}"

    def speaker_profile_path(self, profile_id: int) -> Path:
        directory = self.paths.root / "speaker_profiles"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"voice-{profile_id}.wav"

    def speaker_profile_upload_path(self, profile_id: int, extension: str) -> Path:
        directory = self.paths.root / "speaker_profiles" / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"voice-{profile_id}.source{extension}"

    async def save_speaker_profile_sample(
        self,
        profile_id: int,
        upload: AsyncUpload,
    ) -> StoredMedia:
        original_filename = Path(upload.filename or "voice-sample").name
        if Path(original_filename).suffix.lower() not in {".wav", ".mp3"}:
            raise ValidationError("Voice samples must be a WAV or MP3 file")
        destination = self.speaker_profile_upload_path(
            profile_id,
            Path(original_filename).suffix.lower(),
        )
        hasher = hashlib.sha256()
        size = 0
        try:
            async with aiofiles.open(destination, "wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise UploadTooLargeError(
                            "Voice sample exceeds the configured upload limit"
                        )
                    hasher.update(chunk)
                    await output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        if not size:
            destination.unlink(missing_ok=True)
            raise ValidationError("The voice sample is empty")
        return StoredMedia(
            str(destination),
            original_filename,
            upload.content_type,
            size,
            hasher.hexdigest(),
        )

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

    def delete_meeting_audio(
        self, meeting_uuid: str, recording_paths: list[str]
    ) -> int:
        meeting_root = self.meeting_root(meeting_uuid).resolve()
        storage_root = self.paths.meetings.resolve()
        if meeting_root.parent != storage_root:
            raise ValidationError("Refusing to remove audio outside meeting storage")

        deleted_bytes = 0
        candidates = {Path(value).resolve() for value in recording_paths}
        for directory_name in ("original", "audio", "temp"):
            directory = meeting_root / directory_name
            if directory.is_dir():
                candidates.update(path.resolve() for path in directory.rglob("*") if path.is_file())
        export_dir = meeting_root / "exports"
        if export_dir.is_dir():
            candidates.update(
                path.resolve()
                for path in export_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in ALLOWED_MEDIA_EXTENSIONS
            )

        for path in candidates:
            if not path.is_relative_to(meeting_root):
                raise ValidationError("Refusing to remove audio outside this meeting")
            if path.is_file():
                deleted_bytes += path.stat().st_size
                path.unlink()
        return deleted_bytes
