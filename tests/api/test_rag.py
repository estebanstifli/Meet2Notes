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
from local_meeting_ai.domain.entities import SegmentDraft
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
        assert payload["ranking"]["method"] == "hybrid-cosine-keyword"
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
        assert "Use historical RAG" in prompt_page.text
        assert "In which meeting was topic X discussed?" in prompt_page.text

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
