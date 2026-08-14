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
        if ensure_index:
            indexing = await self.index(meeting_id=meeting_id, _release_model=False)
        else:
            indexing = {"indexed_meetings": 0, "indexed_chunks": 0}
        query_vectors = await self.provider.embed([clean_query], config)
        self._validate_vectors(query_vectors)
        acceleration = str(config["vector_acceleration"])
        extension_available = self.vector_stores.sqlite_vec_available(store_id)
        if acceleration == "sqlite-vec" and not extension_available:
            raise ValidationError(
                "sqlite-vec acceleration is required but the optional package is not installed"
            )
        use_sqlite_vec = acceleration != "python" and extension_available
        candidates = await self.vector_stores.candidates(
            store_id,
            provider=str(config["embedding_provider"]),
            model=self._embedding_index_id(config),
            meeting_id=meeting_id,
            query_vector=query_vectors[0],
            sqlite_vec=use_sqlite_vec,
        )
        await self._release_model_if_configured(config)
        temporal_date = _requested_date(clean_query)
        if temporal_date is not None:
            candidates = [
                item for item in candidates
                if _iso_date(item.get("meeting_date")) == temporal_date
            ]
        query_tokens = _tokens(clean_query)
        semantic_weight = float(config["semantic_weight"])
        keyword_weight = float(config["keyword_weight"])
        scored: list[dict[str, Any]] = []
        for item in candidates:
            semantic = (
                float(item.pop("vector_score"))
                if use_sqlite_vec
                else _cosine(query_vectors[0], item.pop("embedding"))
            )
            text_tokens = _tokens(str(item["text"]))
            keyword = (
                len(query_tokens & text_tokens) / len(query_tokens)
                if query_tokens else 0.0
            )
            score = semantic_weight * max(0.0, semantic) + keyword_weight * keyword
            if score < float(config["min_score"]):
                continue
            scored.append(
                {
                    "chunk_id": item["id"],
                    "meeting_id": item["meeting_id"],
                    "transcription_id": item["transcription_id"],
                    "meeting_title": item["meeting_title"],
                    "meeting_date": item["meeting_date"],
                    "start_ms": item["start_ms"],
                    "end_ms": item["end_ms"],
                    "text": item["text"],
                    "score": round(score, 6),
                    "semantic_score": round(semantic, 6),
                    "keyword_score": round(keyword, 6),
                }
            )
        candidate_limit = int(config["candidate_k"])
        semantic_pool = sorted(
            scored, key=lambda item: item["semantic_score"], reverse=True
        )[:candidate_limit]
        keyword_pool = sorted(
            scored, key=lambda item: item["keyword_score"], reverse=True
        )[:candidate_limit]
        rerank_pool = {
            item["chunk_id"]: item for item in [*semantic_pool, *keyword_pool]
        }
        scored = sorted(
            rerank_pool.values(), key=lambda item: item["score"], reverse=True
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
            "candidate_count": len(candidates),
            "reranked_candidate_count": len(rerank_pool),
            "temporal_filter": temporal_date.isoformat() if temporal_date else None,
            "ranking": {
                "method": "hybrid-cosine-keyword",
                "semantic_weight": semantic_weight,
                "keyword_weight": keyword_weight,
                "min_score": float(config["min_score"]),
                "vector_acceleration": (
                    "sqlite-vec" if use_sqlite_vec else "python-cosine"
                ),
            },
            "indexing": indexing,
        }

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
    ) -> None:
        self.rag = rag
        self.summary_engine = summary_engine
        self.preferences = preferences

    async def ask(
        self,
        question: str,
        *,
        meeting_id: int | None,
        use_rag: bool,
        history: list[dict[str, str]],
    ) -> dict[str, Any]:
        rag_config = self.rag.config()
        sources: list[dict[str, Any]] = []
        retrieval: dict[str, Any] | None = None
        if use_rag:
            retrieval = await self.rag.search(question, meeting_id=meeting_id)
            sources = retrieval["results"]
            context = self._rag_context(sources, int(rag_config["max_context_chars"]))
        elif meeting_id is not None:
            context = self.rag.meeting_context(
                meeting_id, int(rag_config["max_context_chars"])
            )
        else:
            context = "No meeting context was selected."
        conversation = "\n".join(
            f"{turn['role'].upper()}: {turn['content']}" for turn in history[-8:]
        )
        config = configured_values(
            self.preferences, "summary_engine", SUMMARY_DEFAULTS
        )
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
        }

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
