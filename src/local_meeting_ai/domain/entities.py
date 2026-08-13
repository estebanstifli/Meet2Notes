from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.enums import JobStatus, JobType, MeetingStatus, SourceType


@dataclass(frozen=True, slots=True)
class Meeting:
    id: int
    uuid: str
    title: str
    description: str | None
    status: MeetingStatus
    source_type: SourceType
    language: str | None
    started_at: str | None
    ended_at: str | None
    duration_ms: int | None
    created_at: str
    updated_at: str
    recording_count: int = 0


@dataclass(frozen=True, slots=True)
class Recording:
    id: int
    meeting_id: int
    role: str
    local_path: str
    original_filename: str | None
    media_type: str | None
    size_bytes: int | None
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    sha256: str | None
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    uuid: str
    meeting_id: int | None
    job_type: JobType
    status: JobStatus
    progress: float
    message: str | None
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error_text: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class MediaProbe:
    format_name: str
    duration_ms: int | None
    size_bytes: int | None
    sample_rate: int | None
    channels: int | None
    has_audio: bool
    has_video: bool
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredMedia:
    path: str
    original_filename: str
    media_type: str | None
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Transcription:
    id: int
    meeting_id: int
    title: str
    engine: str
    model: str
    language: str | None
    status: str
    is_active: bool
    created_at: str
    completed_at: str | None
    settings: dict[str, Any]
    segment_count: int = 0


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    id: int
    transcription_id: int
    segment_index: int
    start_ms: int
    end_ms: int
    text: str
    speaker_id: int | None
    confidence: float | None
    is_final: bool
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Speaker:
    id: int
    meeting_id: int
    stable_key: str | None
    display_name: str
    confidence: float | None
    created_at: str
    segment_count: int = 0
    talk_time_ms: int = 0
    summary_status: str | None = None
    summary_markdown: str | None = None
    summary_provider: str | None = None
    summary_model: str | None = None
    summary_updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class SpeakerProfile:
    id: int
    name: str
    sample_path: str | None
    created_at: str
    updated_at: str
    meeting_count: int = 0


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    id: int
    meeting_id: int
    transcription_id: int
    speaker_id: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class SegmentDraft:
    index: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    language: str | None
    language_probability: float | None
    duration_ms: int | None
    segments: list[SegmentDraft]
    speaker_turns: list[DiarizationSegment] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TranscriptionEngineRequest:
    audio_path: Path
    model: str
    device: str
    compute_type: str
    language: str | None
    task: str
    beam_size: int
    vad_filter: bool
    allow_model_download: bool
    engine: str = "faster-whisper"
    device_index: int = 0
    cpu_threads: int = 0
    num_workers: int = 1
    vad_min_silence_ms: int = 500
    word_timestamps: bool = True
    condition_on_previous_text: bool = True
    keep_model_loaded: bool = True
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    display_name: str
    description: str
    engine: str
    model: str
    device: str
    compute_type: str
    beam_size: int
    vad_filter: bool
    device_index: int = 0
    cpu_threads: int = 0
    num_workers: int = 1
    vad_min_silence_ms: int = 500
    word_timestamps: bool = True
    condition_on_previous_text: bool = True
    keep_model_loaded: bool = True
    recommended: bool = False
    installed: bool = False
    supports_live: bool = True
    supports_final: bool = True
    runtime_available: bool = True
    download_size: str | None = None
    compatibility_note: str | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioCaptureSource:
    id: str
    name: str
    kind: str
    backend: str
    host_api: str
    channels: int
    sample_rate: int
    is_default: bool = False
    is_loopback: bool = False
    available: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CaptureStatus:
    session_id: str
    state: str
    source: AudioCaptureSource
    destination: Path
    elapsed_ms: int
    level: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    path: Path
    source: AudioCaptureSource
    duration_ms: int
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class AudioFrameBatch:
    pcm_s16le: bytes
    sample_rate: int
    channels: int
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class DiarizationSegment:
    start_ms: int
    end_ms: int
    speaker: int


@dataclass(frozen=True, slots=True)
class SummaryResult:
    content_markdown: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Summary:
    id: int
    meeting_id: int
    transcription_id: int
    template_id: int | None
    provider: str
    model: str
    status: str
    content_markdown: str | None
    structured: dict[str, Any] | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class SummaryTemplate:
    id: int
    name: str
    description: str | None
    system_prompt: str
    user_prompt_template: str
    sections: list[dict[str, Any]]
    is_builtin: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LiveCaptureSession:
    session_id: str
    meeting_id: int
    transcription_id: int
    title: str
    state: str
    source: AudioCaptureSource
    elapsed_ms: int
    level: float
    started_at: str
    profile_id: str
    language: str | None
    realtime_status: str
    realtime_message: str
    segment_count: int
