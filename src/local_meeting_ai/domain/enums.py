from __future__ import annotations

from enum import StrEnum


class MeetingStatus(StrEnum):
    DRAFT = "draft"
    IMPORTING = "importing"
    READY = "ready"
    FAILED = "failed"


class SourceType(StrEnum):
    MANUAL = "manual"
    IMPORTED = "imported"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    IMPORT_MEDIA = "import_media"
    NORMALIZE_AUDIO = "normalize_audio"
    TRANSCRIBE = "transcribe"
    DIARIZE = "diarize"
    RECOGNIZE_SPEAKERS = "recognize_speakers"
    SUMMARIZE = "summarize"
    INDEX_SEARCH = "index_search"
    EXPORT = "export"
    DOWNLOAD_MODEL = "download_model"
