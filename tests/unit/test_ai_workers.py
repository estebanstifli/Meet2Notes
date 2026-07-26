from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from local_meeting_ai.adapters.diarization.sherpa_onnx import (
    SherpaOnnxDiarizationEngine,
)
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine


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
