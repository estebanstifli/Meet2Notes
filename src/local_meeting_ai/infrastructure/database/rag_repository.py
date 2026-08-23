from __future__ import annotations

import re
import sqlite3
from array import array
from collections.abc import Sequence
from typing import Any

from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.repositories import utc_now


def vector_blob(values: Sequence[float]) -> bytes:
    return array("f", (float(value) for value in values)).tobytes()


def blob_vector(value: bytes) -> list[float]:
    result = array("f")
    result.frombytes(value)
    return list(result)


class RagRepository:
    """Portable SQLite vector persistence with optional sqlite-vec ranking."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def extension_available(self) -> bool:
        try:
            with self.database.read() as connection:
                connection.execute("SELECT vec_version()").fetchone()
            return True
        except sqlite3.Error:
            return False

    def rows_for_transcription(self, transcription_id: int) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                """
                SELECT id, chunk_index, content_hash, embedding_provider,
                       embedding_model, embedding_dimensions
                FROM rag_chunks WHERE transcription_id = ? ORDER BY chunk_index
                """,
                (transcription_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_transcription(
        self,
        transcription_id: int,
        meeting_id: int,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
        *,
        provider: str,
        model: str,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Every RAG chunk must have one embedding")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM rag_chunks WHERE meeting_id = ?",
                (meeting_id,),
            )
            connection.executemany(
                """
                INSERT INTO rag_chunks(
                    meeting_id, transcription_id, chunk_index, start_ms, end_ms,
                    text, content_hash, embedding_provider, embedding_model,
                    embedding_dimensions, embedding, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        meeting_id,
                        transcription_id,
                        chunk["chunk_index"],
                        chunk["start_ms"],
                        chunk["end_ms"],
                        chunk["text"],
                        chunk["content_hash"],
                        provider,
                        model,
                        len(vector),
                        vector_blob(vector),
                        now,
                        now,
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ],
            )

    def candidates(
        self,
        *,
        provider: str,
        model: str,
        meeting_ids: Sequence[int] | None = None,
        query_vector: Sequence[float] | None = None,
        sqlite_vec: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where = "WHERE rc.embedding_provider = ? AND rc.embedding_model = ?"
        parameters: list[Any] = [provider, model]
        vector_score = ""
        if sqlite_vec and query_vector is not None:
            vector_score = ", 1.0 - vec_distance_cosine(rc.embedding, ?) AS vector_score"
            parameters.insert(0, vector_blob(query_vector))
            where += " AND rc.embedding_dimensions = ?"
            parameters.append(len(query_vector))
        if meeting_ids:
            placeholders = ", ".join("?" for _ in meeting_ids)
            where += f" AND rc.meeting_id IN ({placeholders})"
            parameters.extend(int(value) for value in meeting_ids)
        order_limit = "ORDER BY vector_score DESC LIMIT ?" if sqlite_vec and limit else (
            "ORDER BY rc.meeting_id, rc.chunk_index"
        )
        if sqlite_vec and limit:
            parameters.append(int(limit))
        with self.database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT rc.*, m.title AS meeting_title, m.description AS meeting_description,
                       COALESCE(m.started_at, m.created_at) AS meeting_date
                       {vector_score}
                FROM rag_chunks rc
                JOIN meetings m ON m.id = rc.meeting_id
                {where}
                {order_limit}
                """,
                parameters,
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            if sqlite_vec:
                item.pop("embedding", None)
            else:
                item["embedding"] = blob_vector(item["embedding"])
        return result

    def lexical_candidates(
        self,
        *,
        query: str,
        provider: str,
        model: str,
        meeting_ids: Sequence[int] | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        terms = [
            value
            for value in re.findall(r"[^\W_]+", query, flags=re.UNICODE)
            if len(value) > 1
        ]
        if not terms:
            return []
        match_query = " OR ".join(
            f'"{value.replace(chr(34), chr(34) * 2)}"' for value in terms[:32]
        )
        where = "rc.embedding_provider = ? AND rc.embedding_model = ?"
        parameters: list[Any] = [match_query, provider, model]
        if meeting_ids:
            placeholders = ", ".join("?" for _ in meeting_ids)
            where += f" AND rc.meeting_id IN ({placeholders})"
            parameters.extend(int(value) for value in meeting_ids)
        parameters.append(max(1, int(limit)))
        with self.database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT rc.*, m.title AS meeting_title,
                       m.description AS meeting_description,
                       COALESCE(m.started_at, m.created_at) AS meeting_date,
                       bm25(rag_chunks_fts, 1.0, 2.5, 1.5, 0.0, 0.0) AS bm25_score
                FROM rag_chunks_fts
                JOIN rag_chunks rc ON rc.id = rag_chunks_fts.chunk_id
                JOIN meetings m ON m.id = rc.meeting_id
                WHERE rag_chunks_fts MATCH ? AND {where}
                ORDER BY bm25_score
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item.pop("embedding", None)
        return result

    def counts(self) -> dict[str, int]:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS chunks, COUNT(DISTINCT meeting_id) AS meetings,
                       COUNT(DISTINCT transcription_id) AS transcriptions
                FROM rag_chunks
                """
            ).fetchone()
        return {key: int(row[key]) for key in ("chunks", "meetings", "transcriptions")}

    def counts_for_index(self, *, provider: str, model: str) -> dict[str, int]:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS chunks, COUNT(DISTINCT meeting_id) AS meetings,
                       COUNT(DISTINCT transcription_id) AS transcriptions
                FROM rag_chunks
                WHERE embedding_provider = ? AND embedding_model = ?
                """,
                (provider, model),
            ).fetchone()
        return {key: int(row[key]) for key in ("chunks", "meetings", "transcriptions")}

    def clear(self, meeting_id: int | None = None) -> int:
        with self.database.transaction() as connection:
            if meeting_id is None:
                cursor = connection.execute("DELETE FROM rag_chunks")
            else:
                cursor = connection.execute(
                    "DELETE FROM rag_chunks WHERE meeting_id = ?", (meeting_id,)
                )
        return cursor.rowcount
