from __future__ import annotations

from fastapi.testclient import TestClient


def test_plugin_catalog_and_final_pipeline_are_exposed(client: TestClient) -> None:
    catalog = client.get("/api/plugins")
    assert catalog.status_code == 200
    assert catalog.json()["plugin_api"] == "1"
    cleanup = next(
        item
        for item in catalog.json()["plugins"]
        if item["id"] == "meet2notes.analysis-cleanup"
    )
    assert cleanup["enabled"] is True
    assert cleanup["permissions"] == [
        "read_transcript",
        "write_derived_artifact",
    ]
    assert cleanup["hooks"][0]["name"] == "analysis.before"

    pipeline = client.get("/api/processing/pipeline")
    assert pipeline.status_code == 200
    assert pipeline.json()["live_transcription_unchanged"] is True
    assert [stage["id"] for stage in pipeline.json()["stages"]] == [
        "final_transcription",
        "diarization",
        "saved_voice_matching",
        "analysis_filters",
        "analysis",
    ]


def test_plugin_can_be_disabled_enabled_and_rescanned(client: TestClient) -> None:
    plugin_id = "meet2notes.analysis-cleanup"
    disabled = client.put(f"/api/plugins/{plugin_id}/state", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    rescanned = client.post("/api/plugins/rescan")
    assert rescanned.status_code == 200
    current = next(
        item for item in rescanned.json()["plugins"] if item["id"] == plugin_id
    )
    assert current["enabled"] is False

    enabled = client.put(f"/api/plugins/{plugin_id}/state", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    executions = client.get("/api/plugins/executions")
    assert executions.status_code == 200
    assert executions.json() == []
