from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.application.ai_services import (
    DIARIZATION_DEFAULTS,
    SUMMARY_DEFAULTS,
)
from local_meeting_ai.application.transcription_config import FASTER_WHISPER_MODELS
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import ModelProfile
from local_meeting_ai.paths import AppPaths

MODEL_CHOICES = ("all", "whisper", "diarization", "summary")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet2notes-models",
        description=(
            "Download and verify Meet2Notes local AI models. Downloads are "
            "stored only in the private application data directory."
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
    return parser


async def install_models(
    selections: set[str],
    *,
    whisper_model: str,
    data_dir: Path | None,
) -> None:
    requested = {"whisper", "diarization", "summary"} if "all" in selections else selections
    paths = AppPaths.from_settings(AppSettings(data_dir=data_dir))
    paths.ensure()
    print(f"Meet2Notes model directory: {paths.models}")

    if "whisper" in requested:
        print(f"[1/3] Downloading and verifying Faster Whisper '{whisper_model}'...")
        await _install_whisper(paths, whisper_model)
        print("      Faster Whisper is ready.")

    if "diarization" in requested:
        print("[2/3] Downloading and verifying sherpa-onnx diarization models...")
        await _install_diarization(paths)
        print("      Speaker diarization is ready.")

    if "summary" in requested:
        print("[3/3] Downloading and verifying LFM2.5 1.2B Q4_K_M...")
        await _install_summary(paths)
        print("      Local meeting summaries are ready.")

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


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    try:
        asyncio.run(
            install_models(
                set(arguments.models),
                whisper_model=arguments.whisper_model,
                data_dir=arguments.data_dir,
            )
        )
    except KeyboardInterrupt as error:
        raise SystemExit("Model setup cancelled.") from error
    except Exception as error:
        raise SystemExit(f"Model setup failed: {error}") from error


if __name__ == "__main__":
    main()
