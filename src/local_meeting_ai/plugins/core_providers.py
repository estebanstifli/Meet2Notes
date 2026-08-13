from __future__ import annotations

from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.adapters.diarization.pyannote_community import (
    PyannoteCommunityDiarizationEngine,
)
from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.embeddings.fastembed_bge import FastEmbedBgeM3Provider
from local_meeting_ai.adapters.embeddings.litellm import LiteLLMEmbeddingProvider
from local_meeting_ai.adapters.embeddings.llama_cpp import LlamaCppEmbeddingProvider
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.adapters.transcription.nvidia_asr import (
    build_nemotron_engine,
    build_parakeet_engine,
)
from local_meeting_ai.adapters.transcription.vibevoice import VibeVoiceBitNetEngine
from local_meeting_ai.config import AppSettings

from .contracts import ProviderDescriptor
from .providers import ProviderRegistry


def register_core_providers(
    registry: ProviderRegistry,
    settings: AppSettings,
) -> None:
    """Register built-ins through the same lazy registry exposed to plugins."""
    registry.register_core(
        _descriptor(
            "faster-whisper",
            "transcription",
            "Faster Whisper",
            "Local CTranslate2 transcription for live and final passes.",
            outputs=("transcript", "timestamps"),
        ),
        lambda context: FasterWhisperEngine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "vibevoice-asr-bitnet",
            "transcription",
            "Microsoft VibeVoice ASR BitNet",
            "Compact CPU final-pass transcription through VibeASR.cpp.",
            outputs=("transcript", "timestamps", "speaker-labels"),
        ),
        lambda context: VibeVoiceBitNetEngine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "nvidia-parakeet",
            "transcription",
            "NVIDIA Parakeet",
            "Multilingual final transcription through NVIDIA NeMo.",
            outputs=("transcript", "timestamps"),
        ),
        lambda context: build_parakeet_engine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "nvidia-nemotron",
            "transcription",
            "NVIDIA Nemotron",
            "Streaming and final multilingual transcription through NVIDIA NeMo.",
            outputs=("transcript", "timestamps"),
        ),
        lambda context: build_nemotron_engine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "sherpa-onnx",
            "diarization",
            "Sherpa-ONNX",
            "Local ONNX speaker diarization.",
            outputs=("speaker-turns",),
        ),
        lambda context: SherpaOnnxDiarizationEngine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "diarize",
            "diarization",
            "Diarize",
            "Isolated CPU diarization worker.",
            outputs=("speaker-turns",),
        ),
        lambda context: DiarizeCpuEngine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "pyannote-community-1",
            "diarization",
            "Pyannote Community-1",
            "Pyannote speaker diarization with optional gated model access.",
            outputs=("speaker-turns",),
        ),
        lambda context: PyannoteCommunityDiarizationEngine(
            context.models_dir,
            access_token=settings.pyannote_token,
        ),
    )
    registry.register_core(
        _descriptor(
            "llama-cpp",
            "summary",
            "llama.cpp and LiteLLM",
            "Managed local GGUF summaries and LiteLLM-compatible providers.",
            outputs=("meeting-notes",),
        ),
        lambda context: LlamaCppSummaryEngine(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "fastembed",
            "embedding",
            "FastEmbed",
            "Managed ONNX text embeddings optimized for CPU.",
            outputs=("embeddings",),
        ),
        lambda context: FastEmbedBgeM3Provider(context.models_dir),
    )
    registry.register_core(
        _descriptor(
            "local",
            "embedding",
            "llama.cpp embeddings",
            "User-provided local embedding GGUF files.",
            outputs=("embeddings",),
        ),
        lambda _context: LlamaCppEmbeddingProvider(),
    )
    registry.register_core(
        _descriptor(
            "litellm",
            "embedding",
            "LiteLLM embeddings",
            "Local or remote embedding endpoints supported by LiteLLM.",
            execution_target="remote",
            outputs=("embeddings",),
        ),
        lambda _context: LiteLLMEmbeddingProvider(),
    )


def _descriptor(
    provider_id: str,
    kind: str,
    display_name: str,
    description: str,
    *,
    execution_target: str = "in-process",
    outputs: tuple[str, ...] = (),
) -> ProviderDescriptor:
    return ProviderDescriptor.model_validate(
        {
            "id": provider_id,
            "kind": kind,
            "display_name": display_name,
            "description": description,
            "execution_target": execution_target,
            "outputs": outputs,
        }
    )
