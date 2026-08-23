from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure.database.rag_repository import RagRepository
from local_meeting_ai.plugins.contracts import (
    HookContext,
    VectorStoreCatalog,
    VectorStoreDescriptor,
    VectorStoreOperation,
    VectorStoreOperationName,
)
from local_meeting_ai.plugins.manager import PluginManager


class RagVectorStoreGateway:
    """SQLite default plus stable plugin hooks for alternative vector destinations."""

    CATALOG_HOOK = "rag.vector_store.catalog"
    OPERATION_HOOK = "rag.vector_store.operation"

    def __init__(self, repository: RagRepository, plugins: PluginManager) -> None:
        self.repository = repository
        self.plugins = plugins

    async def catalog(self) -> list[dict[str, Any]]:
        initial = VectorStoreCatalog(
            stores=[
                VectorStoreDescriptor(
                    id="sqlite",
                    display_name="SQLite",
                    description=(
                        "Portable local storage with optional sqlite-vec acceleration."
                    ),
                    local=True,
                    supports_vector_acceleration=True,
                )
            ]
        )
        result = await self.plugins.hooks.apply_filters(
            self.CATALOG_HOOK,
            initial,
            HookContext(hook=self.CATALOG_HOOK, stage="catalog"),
        )
        if not isinstance(result, VectorStoreCatalog):
            raise ValidationError("A vector-store plugin returned an invalid catalog")
        ids = [store.id for store in result.stores]
        if len(ids) != len(set(ids)) or "sqlite" not in ids:
            raise ValidationError("Vector-store ids must be unique and include sqlite")
        return [store.model_dump() for store in result.stores]

    async def require(self, store_id: str) -> dict[str, Any]:
        store = next((item for item in await self.catalog() if item["id"] == store_id), None)
        if store is None:
            raise ValidationError(f"Vector store {store_id!r} is not registered")
        return store

    async def rows_for_transcription(
        self,
        store_id: str,
        transcription_id: int,
    ) -> list[dict[str, Any]]:
        if store_id == "sqlite":
            return self.repository.rows_for_transcription(transcription_id)
        result = await self._plugin_operation(
            store_id,
            "rows_for_transcription",
            {"transcription_id": transcription_id},
        )
        return cast(list[dict[str, Any]], result.get("rows", []))

    async def replace_transcription(
        self,
        store_id: str,
        transcription_id: int,
        meeting_id: int,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
        *,
        provider: str,
        model: str,
    ) -> None:
        if store_id == "sqlite":
            self.repository.replace_transcription(
                transcription_id,
                meeting_id,
                chunks,
                vectors,
                provider=provider,
                model=model,
            )
            return
        await self._plugin_operation(
            store_id,
            "replace_transcription",
            {
                "transcription_id": transcription_id,
                "meeting_id": meeting_id,
                "chunks": chunks,
                "vectors": vectors,
                "provider": provider,
                "model": model,
            },
        )

    async def candidates(
        self,
        store_id: str,
        *,
        provider: str,
        model: str,
        meeting_ids: Sequence[int] | None,
        query_vector: Sequence[float],
        sqlite_vec: bool,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if store_id == "sqlite":
            return self.repository.candidates(
                provider=provider,
                model=model,
                meeting_ids=meeting_ids,
                query_vector=query_vector,
                sqlite_vec=sqlite_vec,
                limit=limit,
            )
        result = await self._plugin_operation(
            store_id,
            "candidates",
            {
                "provider": provider,
                "model": model,
                "meeting_id": meeting_ids[0] if meeting_ids and len(meeting_ids) == 1 else None,
                "meeting_ids": list(meeting_ids or []),
                "query_vector": list(query_vector),
                "limit": limit,
            },
        )
        return cast(list[dict[str, Any]], result.get("candidates", []))

    async def lexical_candidates(
        self,
        store_id: str,
        *,
        query: str,
        provider: str,
        model: str,
        meeting_ids: Sequence[int] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if store_id != "sqlite":
            return []
        return self.repository.lexical_candidates(
            query=query,
            provider=provider,
            model=model,
            meeting_ids=meeting_ids,
            limit=limit,
        )

    async def counts(self, store_id: str) -> dict[str, int]:
        if store_id == "sqlite":
            return self.repository.counts()
        result = await self._plugin_operation(store_id, "counts", {})
        return {
            key: int(result.get(key, 0))
            for key in ("chunks", "meetings", "transcriptions")
        }

    async def counts_for_index(
        self, store_id: str, *, provider: str, model: str
    ) -> dict[str, int]:
        if store_id == "sqlite":
            return self.repository.counts_for_index(provider=provider, model=model)
        return await self.counts(store_id)

    async def clear(self, store_id: str, meeting_id: int | None = None) -> int:
        if store_id == "sqlite":
            return self.repository.clear(meeting_id)
        result = await self._plugin_operation(
            store_id,
            "clear",
            {"meeting_id": meeting_id},
        )
        return int(result.get("deleted", 0))

    def sqlite_vec_available(self, store_id: str) -> bool:
        return store_id == "sqlite" and self.repository.extension_available()

    async def _plugin_operation(
        self,
        store_id: str,
        operation: VectorStoreOperationName,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        await self.require(store_id)
        envelope = VectorStoreOperation(
            store_id=store_id,
            operation=operation,
            payload=payload,
        )
        result = await self.plugins.hooks.apply_filters(
            self.OPERATION_HOOK,
            envelope,
            HookContext(hook=self.OPERATION_HOOK, stage=operation),
        )
        if not isinstance(result, VectorStoreOperation) or not result.handled:
            raise ValidationError(
                f"Vector store {store_id!r} did not handle operation {operation!r}"
            )
        return result.result
