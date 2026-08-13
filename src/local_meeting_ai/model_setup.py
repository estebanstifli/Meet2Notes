from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.diarize_cpu import DiarizeCpuEngine
from local_meeting_ai.adapters.diarization.pyannote_community import (
    PyannoteCommunityDiarizationEngine,
)
from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.embeddings import FastEmbedBgeM3Provider
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.adapters.transcription.nvidia_asr import (
    build_nemotron_engine,
    build_parakeet_engine,
)
from local_meeting_ai.adapters.transcription.vibevoice import VibeVoiceBitNetEngine
from local_meeting_ai.application.ai_services import (
    DIARIZATION_DEFAULTS,
    SUMMARY_DEFAULTS,
)
from local_meeting_ai.application.rag import RAG_DEFAULTS
from local_meeting_ai.application.transcription_config import FASTER_WHISPER_MODELS
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import ModelProfile
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.paths import AppPaths

MODEL_CHOICES = (
    "all",
    "whisper",
    "diarization",
    "diarize",
    "pyannote-community-1",
    "summary",
    "embeddings",
    "vibevoice-bitnet",
    "nvidia-parakeet",
    "nvidia-nemotron",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet2notes-models",
        description=(
            "Download and verify Meet2Notes local AI models. Downloads are "
            "stored in the installation models directory by default."
        ),
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=MODEL_CHOICES,
        default=["all"],
        help="Model groups to install (default: all)",
    )
    parser.add_argument(
        "--whisper-model",
        choices=FASTER_WHISPER_MODELS,
        default="small",
        help="Faster Whisper model to install (default: small)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Private application data directory",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Model directory (default: <Meet2Notes installation>/models)",
    )
    return parser


async def install_models(
    selections: set[str],
    *,
    whisper_model: str,
    data_dir: Path | None,
    models_dir: Path | None,
) -> None:
    install_defaults = "all" in selections
    requested = (
        {"whisper", "diarization", "summary", "embeddings"}
        if install_defaults
        else selections
    )
    settings = AppSettings(data_dir=data_dir, models_dir=models_dir)
    paths = AppPaths.from_settings(settings)
    paths.ensure()
    if models_dir is not None:
        database = Database(paths.database)
        MigrationRunner(database).apply()
        SettingsRepository(database).update({"models_directory": str(paths.models)})
        print("Saved this model directory as the Meet2Notes runtime default.")
    print(f"Meet2Notes model directory: {paths.models}")

    if "whisper" in requested:
        print(f"[1/4] Downloading and verifying Faster Whisper '{whisper_model}'...")
        await _install_whisper(paths, whisper_model)
        print("      Faster Whisper is ready.")

    if "diarization" in requested:
        print("[2/4] Downloading and verifying sherpa-onnx diarization models...")
        await _install_diarization(paths)
        print("      Speaker diarization is ready.")

    if "diarize" in requested:
        print("Creating the isolated CPU runtime for diarize...")
        await _install_diarize(paths)
        print("      diarize is ready.")

    if "pyannote-community-1" in requested:
        print("Downloading and verifying Pyannote Community-1...")
        await _install_pyannote_community(paths, settings.pyannote_token)
        print("      Pyannote Community-1 is ready.")

    if "summary" in requested:
        print("[3/4] Downloading and verifying LFM2.5 1.2B Q4_K_M...")
        await _install_summary(paths)
        print("      Local meeting summaries are ready.")

    if "embeddings" in requested:
        print("[4/4] Downloading and verifying BGE-M3 through FastEmbed...")
        await _install_embeddings(paths)
        print("      BGE-M3 embeddings are ready for CPU inference.")

    if "vibevoice-bitnet" in requested:
        print("Downloading Microsoft VibeVoice ASR BitNet (1.58 GB)...")
        await _install_vibevoice_bitnet(paths)
        print("      BitNet weights are ready; VibeASR.cpp is required for inference.")

    if "nvidia-parakeet" in requested:
        print("Downloading NVIDIA Parakeet TDT 0.6B v3 (~2.6 GB)...")
        await _install_nvidia_engine(paths, "parakeet")
        print("      NVIDIA Parakeet is ready for final transcription.")

    if "nvidia-nemotron" in requested:
        print("Downloading NVIDIA Nemotron 3.5 ASR Streaming 0.6B (~2.6 GB)...")
        await _install_nvidia_engine(paths, "nemotron")
        print("      NVIDIA Nemotron is ready for live and final transcription.")

    print("Meet2Notes model setup completed successfully.")


async def _install_whisper(paths: AppPaths, model: str) -> None:
    engine = FasterWhisperEngine(paths.models)
    profile = ModelProfile(
        id="setup",
        display_name="Installer",
        description="Installer verification profile",
        engine=engine.name,
        model=model,
        # Setup validates the portable path. Runtime Settings can still select
        # CUDA after installation without making model download GPU-dependent.
        device="cpu",
        compute_type="int8",
        beam_size=5,
        vad_filter=True,
        keep_model_loaded=False,
    )
    try:
        await engine.prepare(profile, allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_diarization(paths: AppPaths) -> None:
    engine = SherpaOnnxDiarizationEngine(paths.models)
    config: dict[str, Any] = {
        **DIARIZATION_DEFAULTS,
        "provider": "cpu",
        "keep_model_loaded": False,
    }
    try:
        await engine.prepare(config, allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_summary(paths: AppPaths) -> None:
    engine = LlamaCppSummaryEngine(paths.models)
    # Model setup uses a deliberately small CPU context so validation does not
    # reserve unnecessary RAM/VRAM. Runtime settings remain untouched.
    config: dict[str, Any] = {
        **SUMMARY_DEFAULTS,
        "context_length": 2048,
        "batch_size": 256,
        "micro_batch_size": 64,
        "gpu_layers": 0,
        "flash_attention": False,
        "keep_model_loaded": False,
    }
    try:
        await engine.prepare(config, allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_vibevoice_bitnet(paths: AppPaths) -> None:
    engine = VibeVoiceBitNetEngine(paths.models)
    profile = _vibevoice_profile(
        engine.name,
        "microsoft/VibeVoice-ASR-BitNet",
    )
    try:
        await engine.prepare(profile, allow_model_download=True)
    finally:
        engine.shutdown()


async def _install_embeddings(paths: AppPaths) -> None:
    provider = FastEmbedBgeM3Provider(paths.models)
    config: dict[str, Any] = {
        **RAG_DEFAULTS,
        "keep_model_loaded": False,
    }
    try:
        await provider.prepare(config, allow_model_download=True)
        await provider.unload("bge-m3")
    finally:
        provider.shutdown()


async def _install_diarize(paths: AppPaths) -> None:
    engine = DiarizeCpuEngine(paths.models)
    try:
        await engine.prepare(dict(DIARIZATION_DEFAULTS), allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_pyannote_community(
    paths: AppPaths,
    access_token: str | None,
) -> None:
    engine = PyannoteCommunityDiarizationEngine(paths.models, access_token=access_token)
    config: dict[str, Any] = {
        **DIARIZATION_DEFAULTS,
        "engine": engine.name,
        "provider": "cpu",
        "keep_model_loaded": False,
    }
    try:
        await engine.prepare(config, allow_model_download=True)
        engine.unload()
    finally:
        engine.shutdown()


async def _install_nvidia_engine(paths: AppPaths, variant: str) -> None:
    engine = (
        build_parakeet_engine(paths.models)
        if variant == "parakeet"
        else build_nemotron_engine(paths.models)
    )
    profile = ModelProfile(
        id="setup",
        display_name="Installer",
        description="Installer profile",
        engine=engine.name,
        model=engine.repository,
        device="auto",
        compute_type="auto",
        beam_size=1,
        vad_filter=False,
        keep_model_loaded=False,
    )
    try:
        await engine.prepare(profile, allow_model_download=True)
    finally:
        engine.shutdown()


def _vibevoice_profile(engine: str, model: str) -> ModelProfile:
    return ModelProfile(
        id="setup",
        display_name="Installer",
        description="Installer profile",
        engine=engine,
        model=model,
        device="cpu",
        compute_type="auto",
        beam_size=1,
        vad_filter=False,
        keep_model_loaded=False,
    )


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    try:
        asyncio.run(
            install_models(
                set(arguments.models),
                whisper_model=arguments.whisper_model,
                data_dir=arguments.data_dir,
                models_dir=arguments.models_dir,
            )
        )
    except KeyboardInterrupt as error:
        raise SystemExit("Model setup cancelled.") from error
    except Exception as error:
        raise SystemExit(f"Model setup failed: {error}") from error


if __name__ == "__main__":
    main()
