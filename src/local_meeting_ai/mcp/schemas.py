from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StatusResult(PublicModel):
    connected: bool
    enabled: bool | None = None
    app_version: str | None = None
    database: str | None = None
    queue: str | None = None
    backend_url: str | None = None
    rag: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None


class MeetingItem(PublicModel):
    id: int
    title: str
    description: str | None = None
    status: str
    language: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    created_at: str
    recording_count: int = 0


class MeetingListResult(PublicModel):
    query: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    meetings: list[MeetingItem]


class TranscriptionBrief(PublicModel):
    id: int
    title: str
    language: str | None = None
    status: str
    is_active: bool
    segment_count: int
    completed_at: str | None = None


class SummaryBrief(PublicModel):
    id: int
    transcription_id: int
    status: str
    provider: str
    model: str
    created_at: str
    completed_at: str | None = None


class MeetingDetailResult(PublicModel):
    meeting: MeetingItem
    active_transcription: TranscriptionBrief | None = None
    transcriptions: list[TranscriptionBrief]
    summaries: list[SummaryBrief]


class TranscriptSegmentItem(PublicModel):
    segment_index: int
    start_ms: int
    end_ms: int
    speaker: str
    text: str
    text_truncated: bool = False


class TranscriptPageResult(PublicModel):
    meeting_id: int
    transcription: TranscriptionBrief
    segments: list[TranscriptSegmentItem]
    truncated: bool
    next_cursor: int | None = None


class SummaryResult(PublicModel):
    id: int
    meeting_id: int
    transcription_id: int
    provider: str
    model: str
    status: str
    content_markdown: str | None = None
    structured: dict[str, Any] | None = None
    created_at: str
    completed_at: str | None = None
    truncated: bool = False
    next_cursor: int | None = None


class TranscriptSearchItem(PublicModel):
    meeting_id: int
    meeting_title: str
    meeting_date: str
    transcription_id: int
    segment_index: int
    start_ms: int
    end_ms: int
    speaker: str
    text: str
    keyword_score: float


class TranscriptSearchResult(PublicModel):
    query: str
    meeting_id: int | None = None
    results: list[TranscriptSearchItem]


class RagEvidence(PublicModel):
    rank: int
    meeting_id: int
    transcription_id: int
    meeting_title: str
    meeting_date: str | None = None
    start_ms: int
    end_ms: int
    text: str
    score: float
    semantic_score: float
    keyword_score: float
    retrieval_methods: list[str]


class RagSearchResult(PublicModel):
    query: str
    meeting_id: int | None = None
    results: list[RagEvidence]
    ranking: dict[str, Any]
