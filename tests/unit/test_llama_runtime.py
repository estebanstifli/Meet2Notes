from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_meeting_ai.adapters.embeddings.llama_cpp import LlamaCppEmbeddingProvider
from local_meeting_ai.adapters.summary.llama_cpp import LlamaCppSummaryEngine
from local_meeting_ai.infrastructure import llama_runtime


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    llama_runtime.reset_llama_cpp()
    find_spec = llama_runtime.importlib.util.find_spec
    monkeypatch.setattr(
        llama_runtime.importlib.util,
        "find_spec",
        lambda name: object() if name == "llama_cpp" else find_spec(name),
    )


def broken_import(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Imitate a llama.dll that cannot load and grows PATH on every attempt."""
    attempts: list[str] = []
    import_module = llama_runtime.importlib.import_module

    def fail(name: str) -> object:
        if name != "llama_cpp":
            return import_module(name)
        attempts.append(name)
        os.environ["PATH"] = "llama-lib" + os.pathsep + os.environ["PATH"]
        raise RuntimeError("Failed to load shared library 'llama.dll'")

    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(llama_runtime.importlib, "import_module", fail)
    return attempts


def test_failed_import_is_attempted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = broken_import(monkeypatch)

    for _ in range(3):
        with pytest.raises(ImportError, match=r"llama\.dll"):
            llama_runtime.load_llama_cpp()

    assert attempts == ["llama_cpp"]
    assert "RuntimeError" in str(llama_runtime.llama_cpp_error())
    assert llama_runtime.llama_cpp_usable() is False


def test_successful_import_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    module = SimpleNamespace()
    calls: list[str] = []
    monkeypatch.setattr(
        llama_runtime.importlib, "import_module", lambda name: calls.append(name) or module
    )

    assert llama_runtime.load_llama_cpp() is module
    assert llama_runtime.load_llama_cpp() is module
    assert calls == ["llama_cpp"]
    assert llama_runtime.llama_cpp_usable() is True


def test_missing_package_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    find_spec = llama_runtime.importlib.util.find_spec
    monkeypatch.setattr(
        llama_runtime.importlib.util,
        "find_spec",
        lambda name: None if name == "llama_cpp" else find_spec(name),
    )

    with pytest.raises(ImportError, match="not installed"):
        llama_runtime.load_llama_cpp()

    assert llama_runtime.llama_cpp_error() is None


def test_status_checks_do_not_grow_path_and_report_the_broken_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = broken_import(monkeypatch)
    engine = LlamaCppSummaryEngine(tmp_path)
    try:
        first = engine.capability()
        path_after_first_check = os.environ["PATH"]
        for _ in range(20):
            capability = engine.capability()
    finally:
        engine.shutdown()

    assert attempts == ["llama_cpp"]
    assert os.environ["PATH"] == path_after_first_check
    assert "llama.dll" in first["runtime_error"]
    custom = next(item for item in capability["models"] if item["id"] == "custom-gguf")
    assert custom["runtime_available"] is False


def test_embedding_status_reports_a_runtime_that_failed_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = broken_import(monkeypatch)
    provider = LlamaCppEmbeddingProvider()
    try:
        assert provider.capability({})["available"] is True
        assert attempts == []
        with pytest.raises(ImportError):
            llama_runtime.load_llama_cpp()
        assert provider.capability({})["available"] is False
    finally:
        provider.shutdown()
