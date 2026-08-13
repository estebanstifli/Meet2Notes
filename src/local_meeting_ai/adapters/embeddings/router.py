from __future__ import annotations

from pathlib import Path
from typing import Any

from local_meeting_ai.adapters.summary.credentials import secure_storage_status
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.domain.protocols import EmbeddingProvider

from .fastembed_bge import FastEmbedBgeM3Provider
from .litellm import LiteLLMEmbeddingProvider
from .llama_cpp import LlamaCppEmbeddingProvider

BGE_M3_PROFILE: dict[str, Any] = {
    "id": "bge-m3",
    "display_name": "BGE-M3",
    "description": "Recommended multilingual embeddings with an 8K context window.",
    "provider": "fastembed",
    "repository": "BAAI/bge-m3",
    "download_size": "2.3 GB · ONNX FP32",
    "dimensions": 1024,
    "context_length": 8192,
    "managed": True,
    "external_file": False,
    "recommended": True,
}
CUSTOM_GGUF_PROFILE: dict[str, Any] = {
    "id": "custom-gguf",
    "display_name": "Custom GGUF",
    "description": "Load an existing embedding GGUF directly with llama.cpp.",
    "provider": "local",
    "repository": None,
    "download_size": "User-provided file",
    "dimensions": None,
    "context_length": None,
    "managed": False,
    "external_file": True,
    "recommended": False,
}
LITELLM_PROFILE: dict[str, Any] = {
    "id": "litellm-custom",
    "display_name": "Custom local / remote via LiteLLM",
    "description": "Connect an embedding API, Ollama, LM Studio or another LiteLLM provider.",
    "provider": "litellm",
    "repository": None,
    "download_size": "No local installation",
    "dimensions": None,
    "context_length": None,
    "managed": False,
    "external_file": False,
    "recommended": False,
}


class EmbeddingEngineRouter:
    """Selects one of the three supported embedding profiles."""

    name = "embedding-router"

    def __init__(self, models_dir: Path) -> None:
        self._providers: dict[str, EmbeddingProvider] = {
            "fastembed": FastEmbedBgeM3Provider(models_dir),
            "local": LlamaCppEmbeddingProvider(),
            "litellm": LiteLLMEmbeddingProvider(),
        }
        self._last_config: dict[str, Any] = {}

    def capability(self, config: dict[str, Any]) -> dict[str, Any]:
        self._last_config = dict(config)
        bge = self._providers["fastembed"].capability(
            self._profile_config("bge-m3", config)
        )
        custom = self._providers["local"].capability(
            self._profile_config("custom-gguf", config)
        )
        remote = self._providers["litellm"].capability(
            self._profile_config("litellm-custom", config)
        )
        models = [
            {
                **BGE_M3_PROFILE,
                "installed": bool(bge.get("installed")),
                "runtime_available": bool(bge.get("available")),
                "resident": bool(bge.get("model_resident")),
                "base_url": bge.get("base_url"),
                "last_error": bge.get("last_error"),
            },
            {
                **CUSTOM_GGUF_PROFILE,
                "installed": bool(custom.get("installed")),
                "runtime_available": bool(custom.get("available")),
                "resident": bool(custom.get("model_resident")),
                "configured_path": custom.get("configured_path"),
            },
            {
                **LITELLM_PROFILE,
                "installed": True,
                "runtime_available": bool(remote.get("available")),
                "resident": False,
            },
        ]
        selected = str(config.get("profile_id") or "bge-m3")
        selected_capability = {
            "bge-m3": bge,
            "custom-gguf": custom,
            "litellm-custom": remote,
        }.get(selected, {})
        return {
            "engine": self.name,
            "display_name": "Embedding models",
            "available": bool(selected_capability.get("available")),
            "installed": bool(selected_capability.get("installed")),
            "selected_profile": selected,
            "models": models,
            "secure_credentials": secure_storage_status(),
            "worker": selected_capability.get("worker", {
                "dedicated": selected == "custom-gguf",
                "state": "ready" if selected_capability.get("model_resident") else "idle",
                "model_resident": bool(selected_capability.get("model_resident")),
                "last_error": selected_capability.get("last_error"),
            }),
        }

    async def prepare(
        self,
        config: dict[str, Any],
        *,
        allow_model_download: bool,
    ) -> None:
        self._last_config = dict(config)
        profile_id = str(config.get("profile_id") or "bge-m3")
        provider = self._provider(profile_id)
        await provider.prepare(
            self._profile_config(profile_id, config),
            allow_model_download=allow_model_download,
        )

    async def uninstall(self, profile_id: str, config: dict[str, Any]) -> None:
        await self._provider(profile_id).uninstall(
            profile_id,
            self._profile_config(profile_id, config),
        )

    async def unload(self, profile_id: str | None = None) -> None:
        if profile_id is not None:
            await self._provider(profile_id).unload(profile_id)
            return
        for provider in self._providers.values():
            await provider.unload()

    def shutdown(self) -> None:
        for provider in self._providers.values():
            provider.shutdown()

    async def embed(
        self,
        texts: list[str],
        config: dict[str, Any],
    ) -> list[list[float]]:
        self._last_config = dict(config)
        profile_id = str(config.get("profile_id") or "bge-m3")
        return await self._provider(profile_id).embed(
            texts,
            self._profile_config(profile_id, config),
        )

    def _provider(self, profile_id: str) -> EmbeddingProvider:
        provider_id = {
            "bge-m3": "fastembed",
            "custom-gguf": "local",
            "litellm-custom": "litellm",
        }.get(profile_id)
        if provider_id is None:
            raise ValidationError("The selected embedding model is unavailable")
        return self._providers[provider_id]

    @staticmethod
    def _profile_config(profile_id: str, config: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(config)
        selected = str(config.get("profile_id") or "bge-m3")
        if profile_id == "bge-m3":
            resolved.update({
                "profile_id": profile_id,
                "embedding_provider": "fastembed",
                "embedding_model": "BAAI/bge-m3",
            })
        elif profile_id == "custom-gguf":
            resolved.update({
                "profile_id": profile_id,
                "embedding_provider": "local",
                "embedding_model": "custom-gguf",
            })
        elif profile_id == "litellm-custom":
            resolved["embedding_provider"] = "litellm"
            if selected != profile_id:
                resolved.update({
                    "embedding_model": "openai/text-embedding-3-small",
                    "base_url": "",
                })
        return resolved
