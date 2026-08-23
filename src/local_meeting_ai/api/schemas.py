from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    audio_deleted_at: str | None
    audio_deleted_bytes: int | None


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
    payload: dict[str, Any]
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
    word_timestamps: bool = True
    condition_on_previous_text: bool = True
    cpu_threads: int = Field(default=0, ge=0, le=128)
    num_workers: int = Field(default=1, ge=1, le=4)
    keep_model_loaded: bool = True
    preload_on_start: bool = True
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

    engine: str = Field(default="llama-cpp", min_length=1, max_length=80)
    provider: str = Field(default="local", min_length=1, max_length=80)
    profile_id: str = Field(default="lfm2.5-1.2b-q4", min_length=1, max_length=80)
    local_runtime: Literal["managed-llama-cpp", "external-openai"] = "managed-llama-cpp"
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
    preload_on_start: bool = True
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
        if self.base_url and not self.base_url.startswith(("http://", "https://")):
            raise ValueError("The AI base URL must use HTTP or HTTPS")
        return self


class SummaryApiKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=4096)


class LiveAssistantPreference(SummaryEnginePreference):
    """Independent low-latency model and behavior for the Live AI Assistant."""

    enabled: bool = False
    auto_start: bool = True
    behavior_mode: Literal["questions", "triggers", "continuous"] = "questions"
    provider: Literal["local", "litellm"] = "local"
    context_length: int = Field(default=16384, ge=2048, le=131072)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    preload_on_start: bool = False
    system_prompt: str = Field(
        default=(
            "Answer clearly and concisely using only the supplied meeting context. "
            "If the context does not contain enough information, say so."
        ),
        min_length=1,
        max_length=8000,
    )
    trigger_phrases: list[str] = Field(default_factory=list, max_length=50)
    evaluation_interval_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    recent_context_seconds: int = Field(default=180, ge=15, le=1800)
    cooldown_seconds: float = Field(default=30.0, ge=0.0, le=600.0)
    max_calls_per_minute: int = Field(default=6, ge=1, le=60)
    max_memory_chars: int = Field(default=4000, ge=500, le=20000)
    request_timeout_seconds: float = Field(default=20.0, ge=2.0, le=120.0)

    @field_validator("trigger_phrases")
    @classmethod
    def validate_trigger_phrases(cls, value: list[str]) -> list[str]:
        cleaned = [
            item.strip().strip('"\'').strip()
            for item in value
            if item.strip().strip('"\'').strip()
        ]
        if any(len(item) > 120 for item in cleaned):
            raise ValueError("Live Assistant trigger phrases cannot exceed 120 characters")
        deduplicated: list[str] = []
        seen: set[str] = set()
        for item in cleaned:
            folded = item.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            deduplicated.append(item)
        return deduplicated


class PluginStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PluginSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict)


class DiarizationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(default="sherpa-onnx", min_length=1, max_length=80)
    segmentation_model: Literal["pyannote-3.0"] = "pyannote-3.0"
    embedding_model: Literal["3d-speaker", "nemo-titanet"] = "3d-speaker"
    quantized_segmentation: bool = True
    provider: Literal["cpu", "cuda", "coreml"] = "cpu"
    num_threads: int = Field(default=2, ge=1, le=64)
    num_speakers: int = Field(default=-1, ge=-1, le=50)
    cluster_threshold: float = Field(default=0.7, gt=0.0, lt=1.0)
    min_duration_on: float = Field(default=0.3, ge=0.0, le=10.0)
    min_duration_off: float = Field(default=0.5, ge=0.0, le=10.0)
    minimum_overlap_ratio: float = Field(default=0.15, ge=0.0, le=1.0)
    recognize_saved_speakers: bool = True
    pyannote_exclusive: bool = True
    debug: bool = False
    keep_model_loaded: bool = True
    preload_on_start: bool = True


class RagPreference(BaseModel):
    """Historical retrieval settings; provider/store ids intentionally remain extensible."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    profile_id: str = Field(default="bge-m3", min_length=1, max_length=80)
    embedding_provider: str = Field(default="fastembed", min_length=1, max_length=80)
    embedding_model: str = Field(default="BAAI/bge-m3", min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=500)
    api_key_env: str = Field(default="OPENAI_API_KEY", max_length=120)
    model_path: str | None = Field(default=None, max_length=2048)
    context_length: int = Field(default=8192, ge=256, le=131072)
    threads: int = Field(default=0, ge=0, le=128)
    gpu_layers: int = Field(default=0, ge=-1, le=1000)
    main_gpu: int = Field(default=0, ge=0, le=32)
    use_mmap: bool = True
    use_mlock: bool = False
    keep_model_loaded: bool = True
    preload_on_start: bool = False
    vector_store: str = Field(default="sqlite", min_length=1, max_length=80)
    vector_acceleration: Literal["auto", "sqlite-vec", "python"] = "auto"
    chunk_size_chars: int = Field(default=1800, ge=300, le=12000)
    chunk_overlap_chars: int = Field(default=300, ge=0, le=4000)
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    runtime_batch_size: int = Field(default=512, ge=32, le=4096)
    top_k: int = Field(default=8, ge=1, le=50)
    candidate_k: int = Field(default=40, ge=1, le=500)
    min_score: float = Field(default=0.18, ge=0.0, le=1.0)
    semantic_weight: float = Field(default=0.8, ge=0.0, le=1.0)
    keyword_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    max_context_chars: int = Field(default=14000, ge=1000, le=100000)
    request_timeout: int = Field(default=120, ge=5, le=600)
    keep_alive: str = Field(default="5m", min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_rag(self) -> Self:
        expected_provider = {
            "bge-m3": "fastembed",
            "custom-gguf": "local",
            "litellm-custom": "litellm",
        }.get(self.profile_id)
        if expected_provider is not None and self.embedding_provider != expected_provider:
            raise ValueError(
                f"Embedding profile {self.profile_id} requires provider {expected_provider}"
            )
        if self.profile_id == "bge-m3" and self.embedding_model != "BAAI/bge-m3":
            raise ValueError("The BGE-M3 profile requires the BAAI/bge-m3 model")
        if (
            self.profile_id != "custom-gguf"
            and self.base_url
            and not self.base_url.startswith(("http://", "https://"))
        ):
            raise ValueError("The embedding base URL must use HTTP or HTTPS")
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("RAG chunk overlap must be smaller than chunk size")
        if self.candidate_k < self.top_k:
            raise ValueError("RAG candidate count must be at least the result count")
        if self.semantic_weight + self.keyword_weight <= 0:
            raise ValueError("At least one RAG ranking weight must be greater than zero")
        return self


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    ui_theme: Literal["system", "light", "dark"] | None = None
    models_directory: str | None = Field(default=None, max_length=2048)
    http_port: int | None = Field(default=None, ge=1024, le=65535)
    default_transcription_language: str | None = Field(default=None, max_length=20)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    confirm_permanent_delete: bool | None = None
    default_summary_template_id: int | None = Field(default=None, ge=1)
    transcription_engine: str = Field(default="faster-whisper", max_length=80)
    live_transcription_engine: str = Field(default="faster-whisper", max_length=80)
    live_transcription_profile: str = Field(default="default", max_length=40)
    final_transcription_engine: str = Field(default="faster-whisper", max_length=80)
    final_transcription_profile: str = Field(default="default", max_length=40)
    faster_whisper: FasterWhisperPreference = Field(default_factory=FasterWhisperPreference)
    summary_engine: SummaryEnginePreference = Field(default_factory=SummaryEnginePreference)
    diarization: DiarizationPreference = Field(default_factory=DiarizationPreference)
    rag: RagPreference = Field(default_factory=RagPreference)


class PreferenceResponse(BaseModel):
    ui_language: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    ui_theme: Literal["system", "light", "dark"] = "system"
    models_directory: str
    active_models_directory: str
    models_directory_restart_required: bool = False
    models_directory_runtime_override: bool = False
    http_port: int = Field(default=8765, ge=1024, le=65535)
    default_transcription_language: str | None = None
    retention_days: int | None = None
    confirm_permanent_delete: bool = True
    default_summary_template_id: int | None = None
    transcription_engine: str = "faster-whisper"
    live_transcription_engine: str = "faster-whisper"
    live_transcription_profile: str = "default"
    final_transcription_engine: str = "faster-whisper"
    final_transcription_profile: str = "default"
    faster_whisper: FasterWhisperPreference = Field(default_factory=FasterWhisperPreference)
    summary_engine: SummaryEnginePreference = Field(default_factory=SummaryEnginePreference)
    diarization: DiarizationPreference = Field(default_factory=DiarizationPreference)
    rag: RagPreference = Field(default_factory=RagPreference)


class RagIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: int | None = Field(default=None, ge=1)
    force: bool = False


class RagSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    meeting_id: int | None = Field(default=None, ge=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    ensure_index: bool = True


class PromptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class PromptAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["transcription", "summary"]
    id: int = Field(ge=1)


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    meeting_id: int | None = Field(default=None, ge=1)
    use_rag: bool = True
    history: list[PromptTurn] = Field(default_factory=list, max_length=20)
    attachments: list[PromptAttachment] = Field(default_factory=list, max_length=10)


class ModelDirectoryMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models_directory: str = Field(min_length=1, max_length=2048)
    overwrite_existing: bool = False


class SummaryTemplateSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=1000)
    format: Literal["paragraph", "list", "text"] = "list"
    item_format: str | None = Field(default=None, max_length=500)


class SummaryTemplateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=4000)
    user_prompt_template: str = Field(min_length=1, max_length=4000)
    sections: list[SummaryTemplateSection] = Field(min_length=1, max_length=20)


class SummaryTemplateResponse(SummaryTemplateWrite):
    id: int
    is_builtin: bool
    is_default: bool = False
    created_at: str
    updated_at: str


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
    supports_live: bool
    supports_final: bool
    runtime_available: bool
    download_size: str | None
    compatibility_note: str | None
    provider_options: dict[str, Any] = Field(default_factory=dict)


class PostprocessOptions(BaseModel):
    """Per-meeting choices for the work that follows transcription."""

    model_config = ConfigDict(extra="forbid")

    diarization: bool = True
    speaker_count: int | None = Field(default=None, ge=1, le=20)
    summary: bool = True
    summary_template_id: int | None = Field(default=None, ge=1)


class TranscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(default="default", max_length=40)
    language: str | None = Field(default=None, max_length=20)
    task: Literal["transcribe", "translate"] | None = None
    allow_model_download: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=200)
    postprocess: bool = False
    postprocess_options: PostprocessOptions = Field(default_factory=lambda: PostprocessOptions())


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


class SpeakerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    stable_key: str | None
    display_name: str
    confidence: float | None
    created_at: str
    segment_count: int
    talk_time_ms: int
    summary_status: str | None
    summary_markdown: str | None
    summary_provider: str | None
    summary_model: str | None
    summary_updated_at: str | None


class SpeakerProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sample_path: str | None
    created_at: str
    updated_at: str
    meeting_count: int = 0


class SpeakerProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class SpeakerTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meeting_id: int
    transcription_id: int
    speaker_id: int
    start_ms: int
    end_ms: int


class TranscriptionDetailResponse(BaseModel):
    transcription: TranscriptionResponse
    segments: list[TranscriptSegmentResponse]
    speakers: list[SpeakerResponse] = []
    speaker_turns: list[SpeakerTurnResponse] = []


class TranscriptionStartResponse(BaseModel):
    transcription: TranscriptionResponse
    job: JobResponse


class TranscriptionTitleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class SpeakerNameUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=100)


class SpeakerSummaryStartResponse(BaseModel):
    speaker: SpeakerResponse
    job: JobResponse


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


class LiveCaptureStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_transcription: bool = True
    postprocess_options: PostprocessOptions = Field(default_factory=lambda: PostprocessOptions())


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


class DiarizationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_count: int | None = Field(default=None, ge=1, le=20)


class SummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class SummaryStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: int | None = Field(default=None, ge=1)


class SummaryContentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_markdown: str = Field(min_length=1, max_length=500_000)


class SummaryStartResponse(BaseModel):
    summary: SummaryResponse
    job: JobResponse
