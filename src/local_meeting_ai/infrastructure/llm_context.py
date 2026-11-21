"""Request-sized context budgets; provider APIs remain authoritative for remote limits."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from local_meeting_ai.infrastructure.bonsai_assets import PROFILES
from local_meeting_ai.infrastructure.llama_runtime import load_llama_cpp


def discover_context(config: dict[str, Any]) -> dict[str, Any]:
    """Read Ollama or GGUF metadata without inference or sending meeting text."""
    if config.get("provider", "local") == "local" and config.get("profile_id") == "custom-gguf":
        path = Path(str(config.get("model_path") or ""))
        if path.is_file():
            stat = path.stat()
            limit = _gguf_context(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
            if limit:
                return {**config, "model_context_limit": limit}
    if not is_ollama(config) or config.get("model_context_limit"):
        return config
    base = str(config.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    name = str(config["model"]).split("/", 1)[1]
    try:
        response = httpx.post(base + "/api/show", json={"model": name}, timeout=3)
        response.raise_for_status()
        detail = response.json()
        limits = [int(value) for key, value in detail.get("model_info", {}).items()
                  if key.endswith(".context_length") and isinstance(value, (int, float))
                  and value >= 1024]
        if limits:
            return {**config, "model_context_limit": min(limits)}
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    return config


@lru_cache(maxsize=32)
def _gguf_context(path: str, size: int, modified: int) -> int | None:
    del size, modified  # Part of the cache identity, invalidated when the GGUF changes.
    try:
        llama_cpp = load_llama_cpp()
        model = llama_cpp.Llama(
            model_path=path, vocab_only=True, n_ctx=512, n_gpu_layers=0, verbose=False,
        )
        try:
            limits = [int(v) for k, v in model.metadata.items()
                      if k.endswith(".context_length") and str(v).isdigit()]
            return min(limits) if limits else None
        finally:
            model.close()
    except (ImportError, OSError, ValueError, RuntimeError):
        return None


def automatic_context(config: dict[str, Any]) -> bool:
    explicit = config.get("auto_context")
    if explicit is not None:
        return bool(explicit)
    return bool(config.get("bonsai_auto_context", True))


def is_ollama(config: dict[str, Any]) -> bool:
    return str(config.get("model", "")).startswith(("ollama/", "ollama_chat/"))


def remote_context(config: dict[str, Any]) -> bool:
    return config.get("provider") in {"litellm", "openai-compatible"} and not is_ollama(config)


def context_limit(config: dict[str, Any]) -> int:
    profile = str(config.get("profile_id", ""))
    if profile in PROFILES:
        return int(PROFILES[profile].get("max_context_length", 262144))
    if profile in {"lfm2.5-1.2b-q4", "qwen3-0.6b", "qwen3-1.7b"}:
        return 32768
    # Ollama discovery / GGUF metadata can supply the trained model window.
    return int(config.get("model_context_limit") or config.get("context_length", 16384))


def size_context(config: dict[str, Any], required: int) -> int:
    initial = max(1024, int(config.get("context_length", 16384)))
    if remote_context(config):
        # This is an application budget, not an allocation on the provider.
        # Never reject a full attachment based on a guessed remote token count.
        if automatic_context(config):
            return max(initial, (required * 10 + 8) // 9 + 1024)
        return initial
    limit = context_limit(config)
    context = min(initial, limit)
    while automatic_context(config) and context < limit and required > int(context * 0.9):
        context = min(limit, context * 2)
    return context
