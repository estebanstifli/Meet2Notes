from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_meeting_ai.domain.enums import JobStatus, JobType, MeetingStatus, SourceType


class MeetingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_type: SourceType = SourceType.MANUAL
    language: str | None = Field(default=None, max_length=20)


class MeetingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    language: str | None = Field(default=None, max_length=20)


class MeetingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    recording_count: int


class RecordingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    role: str
    original_filename: str | None
    media_type: str | None
    size_bytes: int | None
    duration_ms: int | None
    sample_rate: int | None
    channels: int | None
    sha256: str | None
    metadata: dict[str, Any]
    created_at: str


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    meeting_id: int | None
    job_type: JobType
    status: JobStatus
    progress: float
    message: str | None
    result: dict[str, Any] | None
    error_text: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    cancel_requested: bool


class ImportResponse(BaseModel):
    recording: RecordingResponse
    job: JobResponse


class FasterWhisperPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="small", min_length=1, max_length=200)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    device_index: int = Field(default=0, ge=0, le=31)
    compute_type: Literal[
        "auto",
        "default",
        "int8",
        "int8_float32",
        "int8_float16",
        "int8_bfloat16",
        "int16",
        "float16",
        "bfloat16",
        "float32",
    ] = "auto"
    language: str | None = Field(default=None, max_length=20)
    task: Literal["transcribe", "translate"] = "transcribe"
    beam_size: int = Field(default=5, ge=1, le=10)
    vad_filter: bool = True
    vad_min_silence_ms: int = Field(default=500, ge=100, le=5000)
    word_timestamps: bool = False
    condition_on_previous_text: bool = True
    cpu_threads: int = Field(default=0, ge=0, le=128)
    num_workers: int = Field(default=1, ge=1, le=4)
    keep_model_loaded: bool = True
    realtime_chunk_seconds: float = Field(default=3.0, ge=1.0, le=30.0)
    realtime_overlap_seconds: float = Field(default=1.0, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def validate_realtime_window(self) -> Self:
        if self.realtime_overlap_seconds >= self.realtime_chunk_seconds:
            raise ValueError("Real-time overlap must be shorter than the audio chunk")
        return self


class SummaryEnginePreference(BaseModel):
    """Future-proof summary runtime configuration without persisting secrets."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["disabled", "local", "openai-compatible"] = "local"
    local_runtime: Literal["managed-llama-cpp", "external-openai"] = (
        "managed-llama-cpp"
    )
    model: str = Field(
        default="LiquidAI/LFM2.5-1.2B-Instruct-GGUF",
        min_length=1,
        max_length=300,
    )
    model_file: str = Field(
        default="LFM2.5-1.2B-Instruct-Q4_K_M.gguf",
        min_length=1,
        max_length=300,
    )
    model_path: str | None = Field(default=None, max_length=1000)
    base_url: str | None = Field(default=None, max_length=500)
    api_key_env: str = Field(
        default="MEET2NOTES_AI_API_KEY",
        pattern=r"^[A-Z][A-Z0-9_]{0,127}$",
    )
    context_length: int = Field(default=16384, ge=2048, le=131072)
    batch_size: int = Field(default=512, ge=32, le=4096)
    micro_batch_size: int = Field(default=128, ge=16, le=4096)
    threads: int = Field(default=0, ge=0, le=256)
    batch_threads: int = Field(default=0, ge=0, le=256)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    top_k: int = Field(default=40, ge=0, le=500)
    min_p: float = Field(default=0.05, ge=0.0, le=1.0)
    repeat_penalty: float = Field(default=1.1, ge=0.0, le=2.0)
    seed: int = Field(default=-1, ge=-1, le=2147483647)
    gpu_layers: int = Field(default=-1, ge=-1, le=999)
    main_gpu: int = Field(default=0, ge=0, le=31)
    split_mode: Literal["none", "layer", "row"] = "layer"
    use_mmap: bool = True
    use_mlock: bool = False
    offload_kqv: bool = True
    flash_attention: bool = True
    numa: bool = False
    keep_model_loaded: bool = True
    system_prompt: str = Field(
        default=(
            "You are a precise meeting analyst. Summarize only information "
            "present in the transcript. Write in the transcript language."
        ),
        min_length=1,
        max_length=4000,
    )

    @model_validator(mode="after")
    def validate_remote_provider(self) -> Self:
        if self.provider == "openai-compatible" and (
            not self.base_url
            or not self.base_url.startswith(("http://", "https://"))
        ):
            raise ValueError(
                "An HTTP(S) base URL is required for an OpenAI-compatible provider"
            )
        return self


class DiarizationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["sherpa-onnx"] = "sherpa-onnx"
    segmentation_model: Literal["pyannote-3.0"] = "pyannote-3.0"
    embedding_model: Literal["3d-speaker", "nemo-titanet"] = "3d-speaker"
    quantized_segmentation: bool = True
    provider: Literal["cpu", "cuda", "coreml"] = "cpu"
    num_threads: int = Field(default=2, ge=1, le=64)
    num_speakers: int = Field(default=-1, ge=-1, le=50)
    cluster_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)
    min_duration_on: float = Field(default=0.3, ge=0.0, le=10.0)
    min_duration_off: float = Field(default=0.5, ge=0.0, le=10.0)
    minimum_overlap_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    debug: bool = False
    keep_model_loaded: bool = True


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_language: Literal["en", "es"] | None = None
    default_transcription_language: str | None = Field(default=None, max_length=20)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    confirm_permanent_delete: bool | None = None
    transcription_engine: Literal["faster-whisper"] = "faster-whisper"
    faster_whisper: FasterWhisperPreference = Field(
        default_factory=FasterWhisperPreference
    )
    summary_engine: SummaryEnginePreference = Field(
        default_factory=SummaryEnginePreference
    )
    diarization: DiarizationPreference = Field(
        default_factory=DiarizationPreference
    )


class PreferenceResponse(BaseModel):
    ui_language: Literal["en", "es"] = "en"
    default_transcription_language: str | None = None
    retention_days: int | None = None
    confirm_permanent_delete: bool = True
    transcription_engine: Literal["faster-whisper"] = "faster-whisper"
    faster_whisper: FasterWhisperPreference = Field(
        default_factory=FasterWhisperPreference
    )
    summary_engine: SummaryEnginePreference = Field(
        default_factory=SummaryEnginePreference
    )
    diarization: DiarizationPreference = Field(
        default_factory=DiarizationPreference
    )


class ModelProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    description: str
    engine: str
    model: str
    device: str
    compute_type: str
    beam_size: int
    vad_filter: bool
    device_index: int
    cpu_threads: int
    num_workers: int
    vad_min_silence_ms: int
    word_timestamps: bool
    condition_on_previous_text: bool
    keep_model_loaded: bool
    recommended: bool
    installed: bool


class TranscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(default="default", max_length=40)
    language: str | None = Field(default=None, max_length=20)
    task: Literal["transcribe", "translate"] | None = None
    allow_model_download: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=200)


class TranscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    segment_count: int


class TranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class TranscriptionDetailResponse(BaseModel):
    transcription: TranscriptionResponse
    segments: list[TranscriptSegmentResponse]


class TranscriptionStartResponse(BaseModel):
    transcription: TranscriptionResponse
    job: JobResponse


class TranscriptionTitleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class AudioCaptureSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: Literal["microphone", "interface", "system"]
    backend: str
    host_api: str
    channels: int
    sample_rate: int
    is_default: bool
    is_loopback: bool
    available: bool
    unavailable_reason: str | None


class AudioSourcesResponse(BaseModel):
    capability: dict[str, Any]
    sources: list[AudioCaptureSourceResponse]


class LiveCaptureStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    profile_id: str = Field(default="default", max_length=40)
    language: str | None = Field(default=None, max_length=20)
    task: Literal["transcribe", "translate"] | None = None
    allow_model_download: bool = False


class LiveCaptureSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    meeting_id: int
    transcription_id: int
    title: str
    state: Literal["recording", "paused", "stopped"]
    source: AudioCaptureSourceResponse
    elapsed_ms: int
    level: float
    started_at: str
    profile_id: str
    language: str | None
    realtime_status: Literal[
        "warming_up",
        "transcribing",
        "listening",
        "live",
        "error",
        "finalizing",
    ]
    realtime_message: str
    segment_count: int


class LiveCaptureStopResponse(BaseModel):
    session: LiveCaptureSessionResponse
    recording: RecordingResponse
    import_job: JobResponse
    transcription: TranscriptionResponse
    transcription_job: JobResponse


class SegmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10000)


class FindReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    find: str = Field(min_length=1, max_length=500)
    replacement: str = Field(default="", max_length=2000)
    case_sensitive: bool = False


class FindReplaceResponse(BaseModel):
    replacements: int


class SummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    transcription_id: int
    provider: str
    model: str
    status: str
    content_markdown: str | None
    structured: dict[str, Any] | None
    created_at: str
    completed_at: str | None


class SummaryStartResponse(BaseModel):
    summary: SummaryResponse
    job: JobResponse
