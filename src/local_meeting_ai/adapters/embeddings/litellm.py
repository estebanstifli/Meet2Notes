from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from typing import Any, cast

from local_meeting_ai.adapters.summary.credentials import get_litellm_api_key
from local_meeting_ai.domain.errors import CapabilityUnavailableError


class LiteLLMEmbeddingProvider:
    """Embedding adapter for any local or remote provider supported by LiteLLM."""

    name = "litellm"

    def capability(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": importlib.util.find_spec("litellm") is not None,
            "installed": True,
            "model_resident": False,
            "model": str(config.get("embedding_model") or "openai/text-embedding-3-small"),
            "base_url": str(config.get("base_url") or ""),
            "batch_embeddings": True,
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        del config, allow_model_download
        if importlib.util.find_spec("litellm") is None:
            raise CapabilityUnavailableError("LiteLLM is not installed")

    async def uninstall(self, profile_id: str, config: dict[str, Any]) -> None:
        del profile_id, config
        raise CapabilityUnavailableError("LiteLLM providers are not managed by Meet2Notes")

    async def unload(self, profile_id: str | None = None) -> None:
        del profile_id

    def shutdown(self) -> None:
        return

    async def embed(
        self,
        texts: list[str],
        config: dict[str, Any],
    ) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, texts, config)

    def _embed_sync(
        self,
        texts: list[str],
        config: dict[str, Any],
    ) -> list[list[float]]:
        if importlib.util.find_spec("litellm") is None:
            raise CapabilityUnavailableError("LiteLLM is not installed")
        litellm = importlib.import_module("litellm")
        key = get_litellm_api_key() or os.getenv(str(config.get("api_key_env") or ""), "")
        arguments: dict[str, Any] = {
            "model": str(config.get("embedding_model") or ""),
            "input": texts,
            "timeout": float(config.get("request_timeout", 120)),
        }
        base_url = str(config.get("base_url") or "").rstrip("/")
        if base_url:
            arguments["api_base"] = base_url
        if key:
            arguments["api_key"] = key
        try:
            response = litellm.embedding(**arguments)
            raw = response.model_dump() if hasattr(response, "model_dump") else cast(Any, response)
            data = raw.get("data") if isinstance(raw, dict) else getattr(raw, "data", None)
            if not isinstance(data, list):
                data = [item.model_dump() for item in data] if data is not None else []
            ordered = sorted(
                (item if isinstance(item, dict) else item.model_dump() for item in data),
                key=lambda item: int(item.get("index", 0)),
            )
            vectors = [[float(value) for value in item.get("embedding", [])] for item in ordered]
            if len(vectors) != len(texts) or any(not vector for vector in vectors):
                raise CapabilityUnavailableError("LiteLLM returned an invalid embedding response")
            return vectors
        except CapabilityUnavailableError:
            raise
        except Exception as error:
            raise CapabilityUnavailableError(
                f"LiteLLM could not create embeddings: {error}"
            ) from error
