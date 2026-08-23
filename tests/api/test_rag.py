from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.adapters.embeddings import (
    EmbeddingEngineRouter,
    FastEmbedBgeM3Provider,
)
from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.entities import SegmentDraft, SummaryResult
from local_meeting_ai.domain.enums import SourceType


class KeywordEmbeddingProvider:
    name = "fastembed"

    def capability(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": config["embedding_model"],
            "available": True,
            "batch_embeddings": True,
        }

    async def embed(
        self, texts: list[str], config: dict[str, Any]
    ) -> list[list[float]]:
        del config
        vectors = []
        for text in texts:
            lowered = text.casefold()
            vector = [
                float("cliente" in lowered or "visitar" in lowered),
                float("presupuesto" in lowered),
                float("contratación" in lowered),
            ]
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


class RecordingSummaryEngine:
    name = "recording-summary"

    def __init__(self) -> None:
        self.contexts: list[str] = []
        self.configs: list[dict[str, Any]] = []

    def capability(self) -> dict[str, Any]:
        return {"available": True, "installed": True, "models": []}

    async def prepare(self, config: dict[str, Any], *, allow_model_download: bool) -> None:
        del config, allow_model_download

    async def uninstall(self, profile_id: str) -> None:
        del profile_id

    async def summarize(self, transcript, config, progress, is_cancelled):
        del progress, is_cancelled
        self.contexts.append(transcript)
        self.configs.append(dict(config))
        return SummaryResult(
            content_markdown="Grounded answer",
            prompt_tokens=50,
            completion_tokens=8,
        )

    def unload(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def _completed_transcript(client: TestClient, title: str, text: str) -> int:
    container = client.app.state.container
    meeting = container.meetings.create(
        title=title,
        description=None,
        source_type=SourceType.MANUAL,
        language="es",
    )
    transcription = container.transcriptions.create(
        meeting_id=meeting.id,
        title=f"Transcript · {title}",
        engine="test",
        model="test",
        language="es",
        settings={},
    )
    container.transcriptions.complete(
        transcription.id,
        language="es",
        segments=[SegmentDraft(index=0, start_ms=1000, end_ms=5000, text=text)],
    )
    return meeting.id


def test_rag_indexes_searches_and_exposes_ranking(settings: AppSettings) -> None:
    with TestClient(
        create_app(settings, embedding_provider=KeywordEmbeddingProvider())
    ) as client:
        customer_meeting = _completed_transcript(
            client,
            "Seguimiento comercial",
            "Se decidió visitar al cliente X el martes y preparar una demostración.",
        )
        _completed_transcript(
            client,
            "Revisión financiera",
            "El presupuesto anual se aprobará durante el próximo trimestre.",
        )

        response = client.post(
            "/api/rag/search",
            json={"query": "¿Cuándo se decidió visitar al cliente X?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["results"][0]["meeting_id"] == customer_meeting
        assert payload["results"][0]["rank"] == 1
        assert payload["results"][0]["semantic_score"] > 0.99
        assert payload["ranking"]["method"] == "hybrid-dense-bm25-rrf"
        assert "bm25" in payload["results"][0]["retrieval_methods"]
        assert len(payload["shortlisted_meeting_ids"]) <= 5
        assert payload["indexing"]["indexed_meetings"] == 2

        second = client.post(
            "/api/rag/search",
            json={"query": "visitar cliente", "meeting_id": customer_meeting},
        )
        assert second.status_code == 200
        assert second.json()["indexing"]["indexed_meetings"] == 0
        assert second.json()["indexing"]["skipped_meetings"] == 1

        rebuild = client.post("/api/rag/index/jobs", json={"force": True})
        assert rebuild.status_code == 202
        job_uuid = rebuild.json()["uuid"]
        for _ in range(100):
            job = client.get(f"/api/jobs/{job_uuid}").json()
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "completed"
        assert job["job_type"] == "index_search"
        assert job["result"]["indexed_meetings"] == 2
        assert job["result"]["indexed_chunks"] == 2


def test_rag_settings_and_prompt_window_are_available(settings: AppSettings) -> None:
    with TestClient(
        create_app(settings, embedding_provider=KeywordEmbeddingProvider())
    ) as client:
        defaults = client.get("/api/settings").json()["rag"]
        assert defaults["profile_id"] == "bge-m3"
        assert defaults["embedding_model"] == "BAAI/bge-m3"
        assert defaults["vector_store"] == "sqlite"
        assert defaults["semantic_weight"] == 0.8

        updated = client.put(
            "/api/settings",
            json={"rag": {"top_k": 5, "candidate_k": 20, "chunk_size_chars": 1200}},
        )
        assert updated.status_code == 200
        assert updated.json()["rag"]["top_k"] == 5

        prompt_page = client.get("/prompt")
        assert prompt_page.status_code == 200
        assert 'data-context="prompt"' in prompt_page.text
        assert 'id="post-meeting-assistant"' in prompt_page.text
        assert "What do you want to recover?" not in prompt_page.text
        assert "Ask about your meetings" in prompt_page.text

        settings_page = client.get("/settings#rag")
        assert settings_page.status_code == 200
        assert "Loading embedding models" in settings_page.text
        assert "Semantic search &amp; ranking test" in settings_page.text


def test_embedding_catalog_has_only_the_three_supported_profiles(tmp_path: Path) -> None:
    router = EmbeddingEngineRouter(tmp_path)
    try:
        capability = router.capability(
            {
                "profile_id": "bge-m3",
                "embedding_model": "BAAI/bge-m3",
                "base_url": "",
            }
        )
    finally:
        router.shutdown()

    assert [item["id"] for item in capability["models"]] == [
        "bge-m3",
        "custom-gguf",
        "litellm-custom",
    ]
    assert capability["models"][0]["managed"] is True
    assert capability["models"][0]["dimensions"] == 1024


def test_fastembed_registers_bge_m3_without_downloading(tmp_path: Path) -> None:
    provider = FastEmbedBgeM3Provider(tmp_path)
    try:
        embedding_class = provider._text_embedding_class()
        profile = next(
            item
            for item in embedding_class.list_supported_models()
            if item["model"] == "BAAI/bge-m3"
        )
    finally:
        provider.shutdown()

    assert profile["dim"] == 1024
    assert profile["model_file"] == "onnx/model.onnx"
    assert "onnx/model.onnx_data" in profile["additional_files"]
    assert provider.capability({})["installed"] is False


def test_prompt_accepts_raw_transcript_and_summary_attachments(settings: AppSettings) -> None:
    summary_engine = RecordingSummaryEngine()
    with TestClient(
        create_app(
            settings,
            embedding_provider=KeywordEmbeddingProvider(),
            summary_engine=summary_engine,
        )
    ) as client:
        meeting_id = _completed_transcript(
            client,
            "Proyecto Atlas",
            "María confirmó el lanzamiento para el martes.",
        )
        container = client.app.state.container
        transcription = container.transcriptions.active_for_meeting(meeting_id)
        assert transcription is not None
        summary = container.summaries.create(
            meeting_id=meeting_id,
            transcription_id=transcription.id,
            provider="test",
            model="test",
        )
        container.summaries.complete(
            summary.id,
            "El lanzamiento se acordó para el martes.",
        )

        response = client.post(
            "/api/prompt",
            json={
                "question": "¿Cuándo es el lanzamiento?",
                "meeting_id": meeting_id,
                "use_rag": True,
                "attachments": [
                    {"kind": "transcription", "id": transcription.id},
                    {"kind": "summary", "id": summary.id},
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "Grounded answer"
        assert [item["kind"] for item in payload["attachments"]] == [
            "transcription",
            "summary",
        ]
        assert payload["context_usage"]["attachment_tokens"] > 0
        assert "[A1] Transcription" in summary_engine.contexts[-1]
        assert "[A2] Summary" in summary_engine.contexts[-1]
        assert "lanzamiento para el martes" in summary_engine.contexts[-1]
        assert "[R1]" not in summary_engine.contexts[-1]


def test_prompt_rejects_raw_documents_outside_scope(settings: AppSettings) -> None:
    summary_engine = RecordingSummaryEngine()
    with TestClient(
        create_app(
            settings,
            embedding_provider=KeywordEmbeddingProvider(),
            summary_engine=summary_engine,
        )
    ) as client:
        first = _completed_transcript(client, "Primera", "Contenido de la primera.")
        second = _completed_transcript(client, "Segunda", "Contenido de la segunda.")
        foreign = client.app.state.container.transcriptions.active_for_meeting(second)
        assert foreign is not None

        response = client.post(
            "/api/prompt",
            json={
                "question": "¿Qué ocurrió?",
                "meeting_id": first,
                "attachments": [{"kind": "transcription", "id": foreign.id}],
            },
        )

        assert response.status_code == 422
        assert "outside the meeting scope" in response.json()["detail"]


def test_transcript_edits_enqueue_and_refresh_the_incremental_index(
    settings: AppSettings,
) -> None:
    with TestClient(
        create_app(settings, embedding_provider=KeywordEmbeddingProvider())
    ) as client:
        meeting_id = _completed_transcript(
            client,
            "Seguimiento técnico",
            "La versión original no contiene el nombre interno.",
        )
        first = client.post(
            "/api/rag/search",
            json={"query": "versión original", "meeting_id": meeting_id},
        )
        assert first.status_code == 200
        transcription = client.app.state.container.transcriptions.active_for_meeting(
            meeting_id
        )
        assert transcription is not None
        detail = client.get(f"/api/transcriptions/{transcription.id}").json()
        segment_id = detail["segments"][0]["id"]

        edited = client.patch(
            f"/api/transcript-segments/{segment_id}",
            json={"text": "El código exacto del proyecto es Nebulosa-47."},
        )
        assert edited.status_code == 200
        jobs = client.get(f"/api/jobs?meeting_id={meeting_id}").json()
        index_job = next(job for job in jobs if job["job_type"] == "index_search")
        for _ in range(100):
            index_job = client.get(f"/api/jobs/{index_job['uuid']}").json()
            if index_job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert index_job["status"] == "completed"

        refreshed = client.post(
            "/api/rag/search",
            json={
                "query": "Nebulosa-47",
                "meeting_id": meeting_id,
                "ensure_index": False,
            },
        )
        assert refreshed.status_code == 200
        assert "Nebulosa-47" in refreshed.json()["results"][0]["text"]
        assert "bm25" in refreshed.json()["results"][0]["retrieval_methods"]
