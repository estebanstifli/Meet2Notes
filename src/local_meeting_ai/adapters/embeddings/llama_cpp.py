from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.errors import CapabilityUnavailableError, ValidationError


class LlamaCppEmbeddingProvider:
    """Resident llama.cpp worker for user-provided embedding GGUF files."""

    name = "local"

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llama-embedding")
        self._lock = threading.Lock()
        self._model: Any | None = None
        self._model_key: tuple[Any, ...] | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self, config: dict[str, Any]) -> dict[str, Any]:
        dependency = importlib.util.find_spec("llama_cpp") is not None
        path = self._model_path(config, required=False)
        with self._lock:
            resident = self._model is not None
            state = self._state
            error = self._last_error
        return {
            "provider": self.name,
            "available": dependency,
            "installed": bool(path and path.is_file()),
            "configured_path": str(path) if path else None,
            "model_resident": resident,
            "batch_embeddings": True,
            "worker": {
                "dedicated": True,
                "thread_prefix": "llama-embedding",
                "state": state,
                "model_resident": resident,
                "last_error": error,
            },
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        del allow_model_download
        loop = asyncio.get_running_loop()
        await asyncio.wrap_future(self._executor.submit(self._ensure_model, config), loop=loop)

    async def uninstall(self, profile_id: str, config: dict[str, Any]) -> None:
        del profile_id, config
        raise ValidationError("Custom GGUF files are not managed or deleted by Meet2Notes")

    async def unload(self, profile_id: str | None = None) -> None:
        del profile_id
        loop = asyncio.get_running_loop()
        await asyncio.wrap_future(self._executor.submit(self._unload_sync), loop=loop)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        future = self._executor.submit(self._unload_sync)
        future.result(timeout=30)
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def embed(
        self,
        texts: list[str],
        config: dict[str, Any],
    ) -> list[list[float]]:
        if not texts:
            return []
        loop = asyncio.get_running_loop()
        return await asyncio.wrap_future(
            self._executor.submit(self._embed_sync, texts, config),
            loop=loop,
        )

    def _embed_sync(
        self,
        texts: list[str],
        config: dict[str, Any],
    ) -> list[list[float]]:
        model = self._ensure_model(config)
        try:
            response = model.create_embedding(texts)
            data = response.get("data") if isinstance(response, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise CapabilityUnavailableError(
                    "llama.cpp returned an invalid embedding response"
                )
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [
                [float(value) for value in item.get("embedding", [])]
                for item in ordered
                if isinstance(item, dict)
            ]
            if len(vectors) != len(texts) or any(not vector for vector in vectors):
                raise CapabilityUnavailableError("llama.cpp returned an empty embedding")
            return vectors
        except CapabilityUnavailableError:
            raise
        except Exception as error:
            self._set_error(error)
            raise CapabilityUnavailableError(
                f"llama.cpp could not create embeddings: {error}"
            ) from error

    def _ensure_model(self, config: dict[str, Any]) -> Any:
        if self._shutdown:
            raise CapabilityUnavailableError("The embedding worker is shutting down")
        if importlib.util.find_spec("llama_cpp") is None:
            raise CapabilityUnavailableError(
                'llama-cpp-python is not installed. Run: python -m pip install -e ".[rag]"'
            )
        path = self._model_path(config, required=True)
        key = (
            str(path),
            int(config.get("context_length", 8192)),
            int(config.get("runtime_batch_size", 512)),
            int(config.get("threads", 0)),
            int(config.get("gpu_layers", 0)),
            int(config.get("main_gpu", 0)),
            bool(config.get("use_mmap", True)),
            bool(config.get("use_mlock", False)),
        )
        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model
            self._model = None
            self._model_key = None
            self._state = "loading"
            self._last_error = None
        try:
            module = importlib.import_module("llama_cpp")
            arguments: dict[str, Any] = {
                "model_path": str(path),
                "embedding": True,
                "n_ctx": key[1],
                "n_batch": max(1, key[2]),
                "n_gpu_layers": key[4],
                "main_gpu": key[5],
                "use_mmap": key[6],
                "use_mlock": key[7],
                "verbose": False,
            }
            if key[3] > 0:
                arguments["n_threads"] = key[3]
            model = module.Llama(**arguments)
        except Exception as error:
            self._set_error(error)
            raise CapabilityUnavailableError(
                f"Could not load the embedding GGUF with llama.cpp: {error}"
            ) from error
        with self._lock:
            self._model = model
            self._model_key = key
            self._state = "ready"
        return model

    def _unload_sync(self) -> None:
        with self._lock:
            self._model = None
            self._model_key = None
            self._state = "idle"
            self._last_error = None
        gc.collect()

    def _set_error(self, error: Exception) -> None:
        with self._lock:
            self._state = "error"
            self._last_error = str(error) or type(error).__name__

    @staticmethod
    def _model_path(config: dict[str, Any], *, required: bool) -> Path | None:
        raw = str(config.get("model_path") or "").strip()
        if not raw:
            if required:
                raise ValidationError("Choose an embedding GGUF file first")
            return None
        path = Path(raw).expanduser().resolve()
        if path.suffix.lower() != ".gguf" or not path.is_file():
            if required:
                raise ValidationError("The embedding model path must be an existing GGUF file")
            return path
        return path
