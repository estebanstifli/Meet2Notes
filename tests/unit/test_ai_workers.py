from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from local_meeting_ai.adapters.diarization.profile_matching import (
    SherpaOnnxSpeakerProfileMatcher,
)
from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.adapters.transcription.faster_whisper import FasterWhisperEngine
from local_meeting_ai.application.summary_templates import render_summary_template
from local_meeting_ai.domain.entities import ModelProfile


def test_diarization_uses_an_independent_worker(tmp_path: Path) -> None:
    engine = SherpaOnnxDiarizationEngine(tmp_path / "models")
    try:
        worker_name = asyncio.run(
            engine._submit(lambda: threading.current_thread().name)
        )
        assert worker_name.startswith("sherpa-diarization")
        capability = engine.capability()
        assert capability["worker"]["dedicated"] is True
        assert capability["worker"]["dispatcher_threads"] == 1
    finally:
        engine.shutdown()


def test_saved_speaker_profiles_use_a_separate_worker(tmp_path: Path) -> None:
    diarizer = SherpaOnnxDiarizationEngine(tmp_path / "models")
    matcher = SherpaOnnxSpeakerProfileMatcher(tmp_path / "models")
    try:
        worker_name = asyncio.run(
            matcher._submit(lambda: threading.current_thread().name)
        )
        assert worker_name.startswith("speaker-profile-matcher")
        assert not hasattr(diarizer, "match_profiles")
        assert matcher.capability()["worker"]["dedicated"] is True
    finally:
        matcher.shutdown()
        diarizer.shutdown()


def test_summary_uses_an_independent_worker(tmp_path: Path) -> None:
    engine = LlamaCppSummaryEngine(tmp_path / "models")
    try:
        worker_name = asyncio.run(
            engine._submit(lambda: threading.current_thread().name)
        )
        assert worker_name.startswith("llama-summary")
        capability = engine.capability()
        assert capability["worker"]["dedicated"] is True
        assert capability["worker"]["dispatcher_threads"] == 1
    finally:
        engine.shutdown()


def test_summary_accepts_an_external_gguf_without_copying_it(tmp_path: Path) -> None:
    external = tmp_path / "my-model.gguf"
    external.write_bytes(b"GGUF placeholder")
    engine = LlamaCppSummaryEngine(tmp_path / "managed-models")
    try:
        resolved = engine._resolve_model_path(
            {"profile_id": "custom-gguf", "model_path": str(external)},
            False,
        )
        assert resolved == external.resolve()
        assert external.is_file()
    finally:
        engine.shutdown()


def test_note_format_renders_an_ordered_grounded_prompt() -> None:
    prompt = render_summary_template(
        {
            "user_prompt_template": "Create technical notes.",
            "sections": [
                {
                    "title": "Decisions",
                    "instruction": "List explicit decisions.",
                    "format": "list",
                    "item_format": "| Decision | Owner |",
                }
            ],
        }
    )
    assert "## Decisions" in prompt
    assert "Item format: | Decision | Owner |" in prompt
    assert "Never infer owners" in prompt


def test_faster_whisper_uninstall_removes_only_its_local_cache(tmp_path: Path) -> None:
    models = tmp_path / "models"
    engine = FasterWhisperEngine(models)
    tiny_cache = models / "models--Systran--faster-whisper-tiny"
    other_cache = models / "models--Systran--faster-whisper-small"
    tiny_cache.mkdir(parents=True)
    other_cache.mkdir(parents=True)
    (tiny_cache / "model.bin").write_bytes(b"tiny")
    (other_cache / "model.bin").write_bytes(b"small")
    profile = ModelProfile(
        id="fast",
        display_name="Faster Whisper · Fast",
        description="Tiny model",
        engine="faster-whisper",
        model="tiny",
        device="cpu",
        compute_type="int8",
        beam_size=3,
        vad_filter=True,
    )
    try:
        asyncio.run(engine.uninstall(profile))
        assert not tiny_cache.exists()
        assert other_cache.is_dir()
    finally:
        engine.shutdown()
