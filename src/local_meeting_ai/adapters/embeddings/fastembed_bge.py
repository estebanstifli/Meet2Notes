from __future__ import annotations

import asyncio
import gc
import importlib
import importlib.util
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from local_meeting_ai.domain.errors import CapabilityUnavailableError, ValidationError

BGE_M3_MODEL = "BAAI/bge-m3"


class FastEmbedBgeM3Provider:
    """Managed BGE-M3 ONNX worker using FastEmbed without a PyTorch dependency."""

    name = "fastembed"

    def __init__(self, models_dir: Path) -> None:
        self.models_dir = models_dir / "embeddings" / "fastembed"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="fastembed-bge-m3",
        )
        self._lock = threading.Lock()
        self._model: Any | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._shutdown = False

    def capability(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        dependency = importlib.util.find_spec("fastembed") is not None
        with self._lock:
            resident = self._model is not None
            state = self._state
            error = self._last_error
        return {
            "provider": self.name,
            "model": BGE_M3_MODEL,
            "available": dependency,
            "installed": self._installed(),
            "model_resident": resident,
            "batch_embeddings": True,
            "models_directory": str(self.models_dir),
            "worker": {
                "dedicated": True,
                "thread_prefix": "fastembed-bge-m3",
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
        loop = asyncio.get_running_loop()
        await asyncio.wrap_future(
            self._executor.submit(self._ensure_model, config, allow_model_download),
            loop=loop,
        )

    async def uninstall(self, profile_id: str, config: dict[str, Any]) -> None:
        del config
        if profile_id != "bge-m3":
            raise ValidationError("The selected FastEmbed model is not managed")
        loop = asyncio.get_running_loop()
        await asyncio.wrap_future(self._executor.submit(self._uninstall_sync), loop=loop)

    async def unload(self, profile_id: str | None = None) -> None:
        if profile_id not in {None, "bge-m3"}:
            return
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
        model = self._ensure_model(config, False)
        try:
            vectors = list(
                model.embed(
                    texts,
                    batch_size=int(config.get("embedding_batch_size", 16)),
                )
            )
            result = [
                [float(value) for value in cast(Any, vector).tolist()]
                for vector in vectors
            ]
            if len(result) != len(texts) or any(not vector for vector in result):
                raise CapabilityUnavailableError("FastEmbed returned an invalid embedding")
            return result
        except CapabilityUnavailableError:
            raise
        except Exception as error:
            self._set_error(error)
            raise CapabilityUnavailableError(
                f"FastEmbed could not create BGE-M3 embeddings: {error}"
            ) from error

    def _ensure_model(
        self,
        config: dict[str, Any],
        allow_model_download: bool,
    ) -> Any:
        if self._shutdown:
            raise CapabilityUnavailableError("The embedding worker is shutting down")
        if importlib.util.find_spec("fastembed") is None:
            raise CapabilityUnavailableError(
                'FastEmbed is not installed. Run: python -m pip install -e ".[rag]"'
            )
        with self._lock:
            if self._model is not None:
                return self._model
            self._state = "loading"
            self._last_error = None
        if not allow_model_download and not self._installed():
            with self._lock:
                self._state = "idle"
            raise CapabilityUnavailableError(
                "BGE-M3 is not installed. Use Install from Settings > RAG."
            )
        try:
            text_embedding = self._text_embedding_class()
            threads = int(config.get("threads", 0)) or None
            model = text_embedding(
                model_name=BGE_M3_MODEL,
                cache_dir=str(self.models_dir),
                threads=threads,
                cuda=False,
                local_files_only=not allow_model_download,
            )
            # Force lazy runtimes to validate the graph and external weights now.
            list(model.embed(["Meet2Notes BGE-M3 warmup"], batch_size=1))
        except Exception as error:
            self._set_error(error)
            raise CapabilityUnavailableError(
                f"Could not {'install' if allow_model_download else 'load'} BGE-M3: {error}"
            ) from error
        with self._lock:
            self._model = model
            self._state = "ready"
        return model

    @staticmethod
    def _text_embedding_class() -> Any:
        fastembed = importlib.import_module("fastembed")
        text_embedding = fastembed.TextEmbedding
        supported = {
            str(item.get("model", "")).casefold()
            for item in text_embedding.list_supported_models()
            if isinstance(item, dict)
        }
        if BGE_M3_MODEL.casefold() not in supported:
            descriptions = importlib.import_module("fastembed.common.model_description")
            text_embedding.add_custom_model(
                model=BGE_M3_MODEL,
                pooling=descriptions.PoolingType.CLS,
                normalization=True,
                sources=descriptions.ModelSource(hf=BGE_M3_MODEL),
                dim=1024,
                model_file="onnx/model.onnx",
                description=(
                    "Multilingual BGE-M3 dense embeddings with an 8192-token context."
                ),
                license="mit",
                size_in_gb=2.27,
                additional_files=[
                    "onnx/model.onnx_data",
                    "onnx/Constant_7_attr__value",
                ],
            )
        return text_embedding

    def _installed(self) -> bool:
        cache = self.models_dir / "models--BAAI--bge-m3"
        graphs = list(cache.glob("snapshots/*/onnx/model.onnx"))
        return any(
            graph.is_file()
            and graph.with_name("model.onnx_data").is_file()
            and graph.with_name("Constant_7_attr__value").is_file()
            for graph in graphs
        )

    def _unload_sync(self) -> None:
        with self._lock:
            self._model = None
            self._state = "idle"
            self._last_error = None
        gc.collect()

    def _uninstall_sync(self) -> None:
        self._unload_sync()
        target = (self.models_dir / "models--BAAI--bge-m3").resolve()
        root = self.models_dir.resolve()
        if target.parent != root:
            raise ValidationError("Refusing to remove a model outside the managed folder")
        if target.is_dir():
            shutil.rmtree(target)

    def _set_error(self, error: Exception) -> None:
        with self._lock:
            self._state = "error"
            self._last_error = str(error) or type(error).__name__
