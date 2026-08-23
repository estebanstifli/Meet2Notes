from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from local_meeting_ai.domain.entities import Job
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import NotFoundError, ValidationError
from local_meeting_ai.domain.protocols import EmbeddingProvider, SummaryEngine
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    MeetingRepository,
    SettingsRepository,
    SummaryRepository,
    TranscriptionRepository,
)
from local_meeting_ai.infrastructure.jobs import JobContext, LocalJobQueue

from .ai_services import SUMMARY_DEFAULTS, configured_values
from .rag_vector_store import RagVectorStoreGateway

RAG_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "profile_id": "bge-m3",
    "embedding_provider": "fastembed",
    "embedding_model": "BAAI/bge-m3",
    "base_url": "",
    "api_key_env": "OPENAI_API_KEY",
    "model_path": None,
    "context_length": 8192,
    "threads": 0,
    "gpu_layers": 0,
    "main_gpu": 0,
    "use_mmap": True,
    "use_mlock": False,
    "keep_model_loaded": True,
    "preload_on_start": False,
    "vector_store": "sqlite",
    "vector_acceleration": "auto",
    "chunk_size_chars": 1800,
    "chunk_overlap_chars": 300,
    "embedding_batch_size": 16,
    "runtime_batch_size": 512,
    "top_k": 8,
    "candidate_k": 40,
    "min_score": 0.18,
    "semantic_weight": 0.8,
    "keyword_weight": 0.2,
    "max_context_chars": 14000,
    "request_timeout": 120,
    "keep_alive": "5m",
}

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = {
    "a", "al", "and", "de", "del", "el", "en", "for", "in", "la", "las",
    "los", "of", "on", "que", "se", "the", "to", "un", "una", "y",
}


class RagService:
    """Indexes active meeting transcripts and performs explainable hybrid ranking."""

    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        vector_stores: RagVectorStoreGateway,
        meetings: MeetingRepository,
        transcriptions: TranscriptionRepository,
        preferences: SettingsRepository,
        jobs: JobRepository,
        queue: LocalJobQueue,
    ) -> None:
        self.provider = provider
        self.vector_stores = vector_stores
        self.meetings = meetings
        self.transcriptions = transcriptions
        self.preferences = preferences
        self.jobs = jobs
        self.queue = queue

    def config(self) -> dict[str, Any]:
        return configured_values(self.preferences, "rag", RAG_DEFAULTS)

    def can_index_incrementally(self) -> bool:
        config = self.config()
        if not bool(config["enabled"]):
            return False
        capability = self.provider.capability(config)
        return bool(capability.get("available")) and capability.get("installed") is not False

    async def status(self) -> dict[str, Any]:
        config = self.config()
        store_id = str(config["vector_store"])
        store = await self.vector_stores.require(store_id)
        extension_available = self.vector_stores.sqlite_vec_available(store_id)
        requested_acceleration = str(config["vector_acceleration"])
        using_extension = extension_available and requested_acceleration != "python"
        return {
            **await self.vector_stores.counts(store_id),
            "enabled": bool(config["enabled"]),
            "provider": self.provider.capability(config),
            "vector_store": store_id,
            "vector_store_available": True,
            "vector_store_details": store,
            "vector_stores": await self.vector_stores.catalog(),
            "sqlite_vec_available": extension_available,
            "vector_acceleration": (
                "sqlite-vec"
                if using_extension
                else ("python-cosine" if store_id == "sqlite" else "plugin")
            ),
        }

    async def preload_default(self) -> None:
        config = self.config()
        if not bool(config["enabled"]) or not bool(config["preload_on_start"]):
            return
        capability = self.provider.capability(config)
        models = capability.get("models", [])
        selected = next(
            (
                item
                for item in models
                if isinstance(item, dict) and item.get("id") == config.get("profile_id")
            ),
            None,
        )
        if selected is not None and not selected.get("installed"):
            return
        await self.provider.prepare(config, allow_model_download=False)

    async def index(
        self,
        *,
        meeting_id: int | None = None,
        force: bool = False,
        _release_model: bool = True,
        progress: Callable[[float, str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        config = self.config()
        if not bool(config["enabled"]):
            raise ValidationError("Historical RAG is disabled in Settings")
        store_id = str(config["vector_store"])
        await self.vector_stores.require(store_id)
        if meeting_id is not None:
            meeting = self.meetings.get(meeting_id)
            if not meeting:
                raise NotFoundError("Meeting not found")
            meetings = [meeting]
        else:
            meetings = self.meetings.list(limit=10_000)

        if progress:
            await progress(
                0.02,
                f"Preparing {len(meetings)} meeting{'s' if len(meetings) != 1 else ''}",
            )

        indexed_meetings = 0
        indexed_chunks = 0
        skipped_meetings = 0
        provider = str(config["embedding_provider"])
        model = self._embedding_index_id(config)
        batch_size = int(config["embedding_batch_size"])

        meeting_total = max(1, len(meetings))
        for meeting_position, meeting in enumerate(meetings):
            meeting_progress = meeting_position / meeting_total
            if progress:
                await progress(
                    0.05 + 0.9 * meeting_progress,
                    f"Checking meeting {meeting_position + 1}/{len(meetings)}: {meeting.title}",
                )
            transcription = self.transcriptions.active_for_meeting(meeting.id)
            if not transcription or transcription.status != "completed":
                skipped_meetings += 1
                if progress:
                    await progress(
                        0.05 + 0.9 * ((meeting_position + 1) / meeting_total),
                        f"Skipped {meeting.title}: no completed transcript",
                    )
                continue
            chunks = self._chunks_for_transcription(meeting, transcription.id, config)
            existing = await self.vector_stores.rows_for_transcription(
                store_id, transcription.id
            )
            current = [
                (
                    item["chunk_index"], item["content_hash"], provider, model
                )
                for item in chunks
            ]
            persisted = [
                (
                    item["chunk_index"], item["content_hash"],
                    item["embedding_provider"], item["embedding_model"],
                )
                for item in existing
            ]
            if not force and current == persisted:
                skipped_meetings += 1
                if progress:
                    await progress(
                        0.05 + 0.9 * ((meeting_position + 1) / meeting_total),
                        f"Skipped {meeting.title}: index is already current",
                    )
                continue
            vectors: list[list[float]] = []
            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset : offset + batch_size]
                if progress:
                    batch_fraction = (offset + len(batch)) / max(1, len(chunks))
                    await progress(
                        0.05 + 0.9 * (
                            (meeting_position + batch_fraction) / meeting_total
                        ),
                        (
                            f"Embedding {meeting.title}: chunks "
                            f"{offset + 1}-{offset + len(batch)} of {len(chunks)}"
                        ),
                    )
                vectors.extend(await self.provider.embed([item["text"] for item in batch], config))
            self._validate_vectors(vectors)
            await self.vector_stores.replace_transcription(
                store_id,
                transcription.id,
                meeting.id,
                chunks,
                vectors,
                provider=provider,
                model=model,
            )
            indexed_meetings += 1
            indexed_chunks += len(chunks)
            if progress:
                await progress(
                    0.05 + 0.9 * ((meeting_position + 1) / meeting_total),
                    f"Indexed {meeting.title}: {len(chunks)} chunks",
                )
        if progress:
            await progress(0.97, "Finalizing vector index statistics")
        result = {
            "indexed_meetings": indexed_meetings,
            "indexed_chunks": indexed_chunks,
            "skipped_meetings": skipped_meetings,
            **await self.vector_stores.counts(store_id),
        }
        if _release_model:
            await self._release_model_if_configured(config)
        return result

    async def start_rebuild(
        self,
        *,
        meeting_id: int | None = None,
        force: bool = True,
    ) -> Job:
        if not bool(self.config()["enabled"]):
            raise ValidationError("Historical RAG is disabled in Settings")
        existing = next(
            (
                item for item in self.jobs.list(
                    meeting_id=meeting_id, active_only=True, limit=100
                )
                if item.job_type == JobType.INDEX_SEARCH
                and item.payload.get("action") == "rag_rebuild"
                and item.payload.get("meeting_id") == meeting_id
            ),
            None,
        )
        if existing:
            return existing
        job = self.jobs.create(
            meeting_id=meeting_id,
            job_type=JobType.INDEX_SEARCH,
            payload={
                "action": "rag_rebuild",
                "meeting_id": meeting_id,
                "force": force,
            },
            message="Waiting to rebuild the historical RAG index",
        )
        await self.queue.submit(job.uuid)
        return job

    async def process_rebuild(self, job: Job, context: JobContext) -> dict[str, Any]:
        if job.payload.get("action") != "rag_rebuild":
            raise ValidationError("Unsupported index job")

        async def report(progress: float, message: str) -> None:
            await context.raise_if_cancelled()
            await context.update(progress, message)

        return await self.index(
            meeting_id=job.payload.get("meeting_id"),
            force=bool(job.payload.get("force", True)),
            progress=report,
        )

    async def search(
        self,
        query: str,
        *,
        meeting_id: int | None = None,
        top_k: int | None = None,
        ensure_index: bool = True,
    ) -> dict[str, Any]:
        clean_query = query.strip()
        if not clean_query:
            raise ValidationError("Enter a question to search the meeting history")
        config = self.config()
        if not bool(config["enabled"]):
            raise ValidationError("Historical RAG is disabled in Settings")
        store_id = str(config["vector_store"])
        await self.vector_stores.require(store_id)
        indexing = (
            await self._ensure_search_index(meeting_id)
            if ensure_index
            else {"indexed_meetings": 0, "indexed_chunks": 0, "skipped_meetings": 0}
        )
        query_vectors = await self.provider.embed([clean_query], config)
        self._validate_vectors(query_vectors)
        acceleration = str(config["vector_acceleration"])
        extension_available = self.vector_stores.sqlite_vec_available(store_id)
        if acceleration == "sqlite-vec" and not extension_available:
            raise ValidationError(
                "sqlite-vec acceleration is required but the optional package is not installed"
            )
        use_sqlite_vec = acceleration != "python" and extension_available
        provider = str(config["embedding_provider"])
        model = self._embedding_index_id(config)
        candidate_limit = int(config["candidate_k"])
        selected_meeting_ids = [meeting_id] if meeting_id is not None else None
        if selected_meeting_ids is None:
            initial_dense, initial_lexical = await self._candidate_pools(
                clean_query,
                query_vectors[0],
                store_id=store_id,
                provider=provider,
                model=model,
                meeting_ids=None,
                candidate_limit=candidate_limit,
                sqlite_vec=use_sqlite_vec,
                config=config,
            )
            selected_meeting_ids = self._meeting_shortlist(
                initial_dense,
                initial_lexical,
                maximum=5,
                semantic_weight=float(config["semantic_weight"]),
                keyword_weight=float(config["keyword_weight"]),
            )
        dense_candidates, lexical_candidates = await self._candidate_pools(
            clean_query,
            query_vectors[0],
            store_id=store_id,
            provider=provider,
            model=model,
            meeting_ids=selected_meeting_ids or None,
            candidate_limit=candidate_limit,
            sqlite_vec=use_sqlite_vec,
            config=config,
        )
        await self._release_model_if_configured(config)
        temporal_date = _requested_date(clean_query)
        if temporal_date is not None:
            dense_candidates = [
                item for item in dense_candidates
                if _iso_date(item.get("meeting_date")) == temporal_date
            ]
            lexical_candidates = [
                item for item in lexical_candidates
                if _iso_date(item.get("meeting_date")) == temporal_date
            ]
        semantic_weight = float(config["semantic_weight"])
        keyword_weight = float(config["keyword_weight"])
        scored = self._rrf_fuse(
            dense_candidates,
            lexical_candidates,
            semantic_weight=semantic_weight,
            keyword_weight=keyword_weight,
        )
        limit = top_k or int(config["top_k"])
        limit = max(1, min(limit, 50, candidate_limit))
        results = scored[:limit]
        for rank, item in enumerate(results, 1):
            item["rank"] = rank
        return {
            "query": clean_query,
            "meeting_id": meeting_id,
            "results": results,
            "candidate_count": len({
                item["id"] for item in [*dense_candidates, *lexical_candidates]
            }),
            "reranked_candidate_count": len(scored),
            "shortlisted_meeting_ids": selected_meeting_ids or [],
            "temporal_filter": temporal_date.isoformat() if temporal_date else None,
            "ranking": {
                "method": "hybrid-dense-bm25-rrf",
                "semantic_weight": semantic_weight,
                "keyword_weight": keyword_weight,
                "min_score": float(config["min_score"]),
                "vector_acceleration": (
                    "sqlite-vec" if use_sqlite_vec else "python-cosine"
                ),
            },
            "indexing": indexing,
        }

    async def _ensure_search_index(self, meeting_id: int | None) -> dict[str, Any]:
        config = self.config()
        store_id = str(config["vector_store"])
        if meeting_id is not None:
            transcription = self.transcriptions.active_for_meeting(meeting_id)
            if transcription and transcription.status == "completed":
                rows = await self.vector_stores.rows_for_transcription(
                    store_id, transcription.id
                )
                expected_model = self._embedding_index_id(config)
                if rows and all(
                    row.get("embedding_provider") == config["embedding_provider"]
                    and row.get("embedding_model") == expected_model
                    for row in rows
                ):
                    return {
                        "indexed_meetings": 0,
                        "indexed_chunks": 0,
                        "skipped_meetings": 1,
                    }
            return await self.index(meeting_id=meeting_id, _release_model=False)
        counts = await self.vector_stores.counts_for_index(
            store_id,
            provider=str(config["embedding_provider"]),
            model=self._embedding_index_id(config),
        )
        if counts["chunks"]:
            return {
                "indexed_meetings": 0,
                "indexed_chunks": 0,
                "skipped_meetings": counts["meetings"],
            }
        return await self.index(_release_model=False)

    async def _candidate_pools(
        self,
        query: str,
        query_vector: list[float],
        *,
        store_id: str,
        provider: str,
        model: str,
        meeting_ids: list[int] | None,
        candidate_limit: int,
        sqlite_vec: bool,
        config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        dense = await self.vector_stores.candidates(
            store_id,
            provider=provider,
            model=model,
            meeting_ids=meeting_ids,
            query_vector=query_vector,
            sqlite_vec=sqlite_vec,
            limit=candidate_limit,
        )
        dense_scored: list[dict[str, Any]] = []
        for item in dense:
            semantic = (
                float(item.pop("vector_score"))
                if sqlite_vec
                else _cosine(query_vector, item.pop("embedding"))
            )
            if semantic < float(config["min_score"]):
                continue
            item["semantic_score"] = round(semantic, 6)
            dense_scored.append(item)
        dense_scored = sorted(
            dense_scored, key=lambda item: item["semantic_score"], reverse=True
        )[:candidate_limit]
        lexical = await self.vector_stores.lexical_candidates(
            store_id,
            query=query,
            provider=provider,
            model=model,
            meeting_ids=meeting_ids,
            limit=candidate_limit,
        )
        for item in lexical:
            item["keyword_score"] = round(max(0.0, -float(item["bm25_score"])), 6)
        return dense_scored, lexical

    @staticmethod
    def _meeting_shortlist(
        dense: list[dict[str, Any]],
        lexical: list[dict[str, Any]],
        *,
        maximum: int,
        semantic_weight: float,
        keyword_weight: float,
    ) -> list[int]:
        scores: dict[int, float] = {}
        for pool, weight in ((dense, semantic_weight), (lexical, keyword_weight)):
            for rank, item in enumerate(pool, 1):
                meeting_id = int(item["meeting_id"])
                scores[meeting_id] = scores.get(meeting_id, 0.0) + weight / (60 + rank)
        return [
            meeting_id
            for meeting_id, _score in sorted(
                scores.items(), key=lambda item: item[1], reverse=True
            )[:maximum]
        ]

    @staticmethod
    def _rrf_fuse(
        dense: list[dict[str, Any]],
        lexical: list[dict[str, Any]],
        *,
        semantic_weight: float,
        keyword_weight: float,
    ) -> list[dict[str, Any]]:
        fused: dict[int, dict[str, Any]] = {}
        for pool, method, weight in (
            (dense, "dense", semantic_weight),
            (lexical, "bm25", keyword_weight),
        ):
            for rank, source in enumerate(pool, 1):
                chunk_id = int(source["id"])
                item = fused.setdefault(
                    chunk_id,
                    {
                        "chunk_id": chunk_id,
                        "meeting_id": source["meeting_id"],
                        "transcription_id": source["transcription_id"],
                        "meeting_title": source["meeting_title"],
                        "meeting_date": source["meeting_date"],
                        "start_ms": source["start_ms"],
                        "end_ms": source["end_ms"],
                        "text": source["text"],
                        "score": 0.0,
                        "semantic_score": 0.0,
                        "keyword_score": 0.0,
                        "retrieval_methods": [],
                    },
                )
                item["score"] += weight / (60 + rank)
                item["retrieval_methods"].append(method)
                if method == "dense":
                    item["semantic_score"] = source.get("semantic_score", 0.0)
                else:
                    item["keyword_score"] = source.get("keyword_score", 0.0)
        normalizer = max(semantic_weight + keyword_weight, 0.0001)
        for item in fused.values():
            item["score"] = round(item["score"] * 61 / normalizer, 6)
        return sorted(fused.values(), key=lambda item: item["score"], reverse=True)

    def meeting_context(self, meeting_id: int, maximum_chars: int) -> str:
        meeting = self.meetings.get(meeting_id)
        if not meeting:
            raise NotFoundError("Meeting not found")
        transcription = self.transcriptions.active_for_meeting(meeting_id)
        if not transcription or transcription.status != "completed":
            raise ValidationError("This meeting does not have a completed transcript")
        segments = self.transcriptions.segments(transcription.id)
        speakers = {
            speaker.id: speaker.display_name
            for speaker in self.transcriptions.speakers_for_transcription(transcription.id)
        }
        lines = [
            f"Meeting: {meeting.title}",
            f"Date: {meeting.started_at or meeting.created_at}",
            f"Description: {meeting.description or ''}",
        ]
        lines.extend(
            f"[{_clock(segment.start_ms)}] "
            f"{_speaker_label(speakers, segment.speaker_id)}: "
            f"{segment.text.strip()}"
            for segment in segments
        )
        return "\n".join(lines)[:maximum_chars]

    def _chunks_for_transcription(
        self,
        meeting: Any,
        transcription_id: int,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        segments = self.transcriptions.segments(transcription_id)
        speakers = {
            speaker.id: speaker.display_name
            for speaker in self.transcriptions.speakers_for_transcription(transcription_id)
        }
        prefix = (
            f"Meeting: {meeting.title}\n"
            f"Date: {meeting.started_at or meeting.created_at}\n"
            f"Description: {meeting.description or ''}\n"
        )
        rows: list[dict[str, Any]] = [
            {
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": (
                    f"[{_clock(segment.start_ms)}] "
                    f"{_speaker_label(speakers, segment.speaker_id)}: "
                    f"{segment.text.strip()}"
                ),
            }
            for segment in segments if segment.text.strip()
        ]
        if not rows:
            return []
        maximum = int(config["chunk_size_chars"])
        overlap = int(config["chunk_overlap_chars"])
        chunks: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(rows):
            selected: list[dict[str, Any]] = []
            length = len(prefix)
            next_cursor = cursor
            while next_cursor < len(rows):
                row = rows[next_cursor]
                addition = len(str(row["text"])) + 1
                if selected and length + addition > maximum:
                    break
                selected.append(row)
                length += addition
                next_cursor += 1
            text = prefix + "\n".join(str(item["text"]) for item in selected)
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "start_ms": int(selected[0]["start_ms"]),
                    "end_ms": int(selected[-1]["end_ms"]),
                    "text": text,
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
            if next_cursor >= len(rows):
                break
            carried = 0
            new_cursor = next_cursor
            while new_cursor > cursor and carried < overlap:
                new_cursor -= 1
                carried += len(str(rows[new_cursor]["text"])) + 1
            cursor = max(cursor + 1, new_cursor)
        return chunks

    @staticmethod
    def _validate_vectors(vectors: list[list[float]]) -> None:
        if not vectors or not vectors[0]:
            raise ValidationError("The embedding provider returned no vectors")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise ValidationError("Embedding vectors have inconsistent dimensions")

    @staticmethod
    def _embedding_index_id(config: dict[str, Any]) -> str:
        """Fingerprint settings that can change the meaning of stored vectors."""
        path_value = str(config.get("model_path") or "").strip()
        path_signature: dict[str, Any] | None = None
        if path_value:
            path = Path(path_value).expanduser()
            try:
                stat = path.stat()
                path_signature = {
                    "path": str(path.resolve()),
                    "size": stat.st_size,
                    "modified_ns": stat.st_mtime_ns,
                }
            except OSError:
                path_signature = {"path": str(path)}
        identity = {
            "profile": config.get("profile_id"),
            "provider": config.get("embedding_provider"),
            "model": config.get("embedding_model"),
            "base_url": config.get("base_url"),
            "model_file": path_signature,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return f"{config.get('embedding_model', 'embedding')}@{digest}"

    async def _release_model_if_configured(self, config: dict[str, Any]) -> None:
        if bool(config.get("keep_model_loaded", True)):
            return
        unload = getattr(self.provider, "unload", None)
        if callable(unload):
            await unload(str(config.get("profile_id") or "bge-m3"))

class PromptService:
    """Grounded question answering over one transcript or the historical RAG."""

    def __init__(
        self,
        *,
        rag: RagService,
        summary_engine: SummaryEngine,
        preferences: SettingsRepository,
        summaries: SummaryRepository,
    ) -> None:
        self.rag = rag
        self.summary_engine = summary_engine
        self.preferences = preferences
        self.summaries = summaries

    async def ask(
        self,
        question: str,
        *,
        meeting_id: int | None,
        use_rag: bool,
        history: list[dict[str, str]],
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rag_config = self.rag.config()
        config = configured_values(
            self.preferences, "summary_engine", SUMMARY_DEFAULTS
        )
        context_length = int(config.get("context_length", 16384))
        output_tokens = min(
            int(config.get("max_output_tokens", 1024)), context_length // 2
        )
        reserve_tokens = max(256, context_length // 20)
        instruction_tokens = _estimated_tokens(
            str(config.get("system_prompt") or "") + question
        ) + 220
        input_budget = max(
            512,
            context_length - output_tokens - reserve_tokens - instruction_tokens,
        )
        history_budget = max(256, min(context_length // 6, input_budget // 4))
        conversation, history_tokens = self._bounded_history(history, history_budget)
        evidence_budget = max(256, input_budget - history_tokens)
        attachment_blocks, attached, attachment_tokens = self._attachment_context(
            attachments or [], meeting_id=meeting_id
        )
        if attachment_tokens > evidence_budget - 128:
            raise ValidationError(
                "The selected raw documents do not fit in the AI context window. "
                "Remove a document or use RAG excerpts instead."
            )
        retrieval_budget = max(0, evidence_budget - attachment_tokens)
        sources: list[dict[str, Any]] = []
        retrieval: dict[str, Any] | None = None
        attached_transcriptions = {
            item["id"] for item in attached if item["kind"] == "transcription"
        }
        if use_rag and retrieval_budget >= 200:
            retrieval = await self.rag.search(question, meeting_id=meeting_id)
            sources = [
                source for source in retrieval["results"]
                if int(source["transcription_id"]) not in attached_transcriptions
            ]
            rag_chars = min(
                int(rag_config["max_context_chars"]), retrieval_budget * 3
            )
            rag_context = self._rag_context(sources, rag_chars)
        else:
            rag_context = ""
        if attachment_blocks:
            sections = ["## ATTACHED DOCUMENTS", *attachment_blocks]
            if rag_context:
                sections.extend(["## RETRIEVED EVIDENCE", rag_context])
            context = "\n\n".join(sections)
        elif rag_context:
            context = rag_context
        elif meeting_id is not None and not use_rag:
            context = self.rag.meeting_context(
                meeting_id,
                min(int(rag_config["max_context_chars"]), retrieval_budget * 3),
            )
        else:
            context = "No meeting context was selected."
        config.update(
            {
                "prompt_mode": True,
                "prompt_question": question,
                "prompt_history": conversation,
                "keep_model_loaded": config.get("keep_model_loaded", True),
            }
        )

        result = await self.summary_engine.summarize(
            context,
            config,
            _NoopProgress(),
            lambda: False,
        )
        return {
            "answer": result.content_markdown,
            "sources": sources,
            "retrieval": retrieval,
            "scope": "rag" if use_rag else ("meeting" if meeting_id else "model"),
            "attachments": attached,
            "context_usage": {
                "context_window_tokens": context_length,
                "input_budget_tokens": input_budget,
                "history_tokens": history_tokens,
                "attachment_tokens": attachment_tokens,
                "retrieval_tokens": _estimated_tokens(rag_context),
                "estimated_total_input_tokens": (
                    instruction_tokens
                    + history_tokens
                    + attachment_tokens
                    + _estimated_tokens(rag_context)
                ),
                "reserved_output_tokens": output_tokens,
            },
        }

    def _attachment_context(
        self,
        attachments: list[dict[str, Any]],
        *,
        meeting_id: int | None,
    ) -> tuple[list[str], list[dict[str, Any]], int]:
        blocks: list[str] = []
        metadata: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for attachment in attachments:
            kind = str(attachment.get("kind") or "")
            attachment_id = int(attachment.get("id") or 0)
            key = (kind, attachment_id)
            if attachment_id < 1 or key in seen:
                continue
            seen.add(key)
            attachment_meeting_id: int
            if kind == "transcription":
                transcription = self.rag.transcriptions.get(attachment_id)
                if not transcription or transcription.status != "completed":
                    raise ValidationError("The selected transcript is not available")
                if meeting_id is not None and transcription.meeting_id != meeting_id:
                    raise ValidationError("The selected transcript is outside the meeting scope")
                meeting = self.rag.meetings.get(transcription.meeting_id)
                segments = self.rag.transcriptions.segments(transcription.id)
                speakers = {
                    speaker.id: speaker.display_name
                    for speaker in self.rag.transcriptions.speakers_for_transcription(
                        transcription.id
                    )
                }
                content = "\n".join(
                    f"[{_clock(segment.start_ms)}] "
                    f"{_speaker_label(speakers, segment.speaker_id)}: {segment.text.strip()}"
                    for segment in segments
                )
                label = transcription.title
                attachment_meeting_id = transcription.meeting_id
                meeting_title = meeting.title if meeting else f"Meeting {transcription.meeting_id}"
            elif kind == "summary":
                summary = self.summaries.get(attachment_id)
                if not summary or summary.status != "completed" or not summary.content_markdown:
                    raise ValidationError("The selected AI notes are not available")
                if meeting_id is not None and summary.meeting_id != meeting_id:
                    raise ValidationError("The selected AI notes are outside the meeting scope")
                meeting = self.rag.meetings.get(summary.meeting_id)
                content = summary.content_markdown
                label = f"AI notes {summary.id}"
                attachment_meeting_id = summary.meeting_id
                meeting_title = meeting.title if meeting else f"Meeting {summary.meeting_id}"
            else:
                raise ValidationError("Unsupported prompt attachment")
            index = len(blocks) + 1
            blocks.append(
                f"[A{index}] {kind.title()}: {label}\n"
                f"Meeting: {meeting_title}\n{content}"
            )
            metadata.append(
                {
                    "kind": kind,
                    "id": attachment_id,
                    "label": label,
                    "meeting_id": attachment_meeting_id,
                    "estimated_tokens": _estimated_tokens(content),
                }
            )
        return blocks, metadata, sum(_estimated_tokens(block) for block in blocks)

    @staticmethod
    def _bounded_history(
        history: list[dict[str, str]], maximum_tokens: int
    ) -> tuple[str, int]:
        selected: list[str] = []
        used = 0
        for turn in reversed(history[-20:]):
            line = f"{turn['role'].upper()}: {turn['content']}"
            tokens = _estimated_tokens(line)
            if selected and used + tokens > maximum_tokens:
                break
            if tokens > maximum_tokens:
                continue
            selected.append(line)
            used += tokens
        return "\n".join(reversed(selected)), used

    @staticmethod
    def _rag_context(sources: list[dict[str, Any]], maximum_chars: int) -> str:
        blocks: list[str] = []
        length = 0
        for index, source in enumerate(sources, 1):
            block = (
                f"[R{index}] Meeting: {source['meeting_title']}\n"
                f"Date: {source['meeting_date']}\n"
                f"Time: {_clock(source['start_ms'])}-{_clock(source['end_ms'])}\n"
                f"{source['text']}"
            )
            if blocks and length + len(block) > maximum_chars:
                break
            blocks.append(block)
            length += len(block)
        return "\n\n".join(blocks)


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return {
        token
        for token in _WORD.findall(normalized)
        if len(token) > 1 and token not in _STOPWORDS
    }


def _estimated_tokens(text: str) -> int:
    """Conservative model-independent estimate for multilingual prompt budgeting."""
    return max(1, math.ceil(len(text) / 3)) if text else 0


class _NoopProgress:
    def __call__(self, progress: float, message: str) -> None:
        del progress, message


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


def _clock(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _speaker_label(speakers: dict[int, str], speaker_id: int | None) -> str:
    return speakers.get(speaker_id, "Speaker") if speaker_id is not None else "Speaker"


def _iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _requested_date(query: str, today: date | None = None) -> date | None:
    normalized = " ".join(_tokens(query))
    current = today or date.today()
    if "ayer" in normalized or "yesterday" in normalized:
        return current - timedelta(days=1)
    weekdays = {
        "lunes": 0, "monday": 0, "martes": 1, "tuesday": 1,
        "miercoles": 2, "wednesday": 2, "jueves": 3, "thursday": 3,
        "viernes": 4, "friday": 4, "sabado": 5, "saturday": 5,
        "domingo": 6, "sunday": 6,
    }
    if not ({"pasado", "last"} & set(normalized.split())):
        return None
    for word, weekday in weekdays.items():
        if word in normalized:
            days = (current.weekday() - weekday) % 7
            if days == 0:
                days = 7
            return current - timedelta(days=days)
    return None
