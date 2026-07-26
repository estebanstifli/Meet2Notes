from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.transcription.faster_whisper import (
    FasterWhisperEngine,
)
from local_meeting_ai.domain.entities import ModelProfile


def test_model_is_loaded_once_on_the_dedicated_worker(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    constructed_on: list[str] = []

    class FakeWhisperModel:
        def __init__(self, *_: Any, **__: Any) -> None:
            constructed_on.append(threading.current_thread().name)

    engine = FasterWhisperEngine(tmp_path / "models")
    monkeypatch.setattr(engine, "_model_class", lambda: FakeWhisperModel)
    profile = ModelProfile(
        id="default",
        display_name="Default",
        description="Configured default",
        engine="faster-whisper",
        model="small",
        device="cpu",
        compute_type="int8",
        beam_size=5,
        vad_filter=True,
        installed=True,
    )

    try:
        asyncio.run(engine.prepare(profile, allow_model_download=False))
        asyncio.run(engine.prepare(profile, allow_model_download=False))

        assert len(constructed_on) == 1
        assert constructed_on[0].startswith("faster-whisper")
        capability = engine.capability()
        assert capability["loaded_models"] == ["small"]
        assert capability["worker"]["model_resident"] is True
        assert capability["worker"]["state"] == "ready"
    finally:
        engine.shutdown()
