from __future__ import annotations

from fastapi.testclient import TestClient

from local_meeting_ai.domain.entities import SegmentDraft
from local_meeting_ai.domain.enums import SourceType


def _completed_meeting(client: TestClient, title: str, started_at: str) -> tuple[int, int]:
    container = client.app.state.container
    meeting = container.meetings.create(
        title=title,
        description="MCP read API fixture",
        source_type=SourceType.MANUAL,
        language="es",
    )
    container.meetings.update(meeting.id, {"started_at": started_at})
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
        segments=[
            SegmentDraft(
                index=0,
                start_ms=1000,
                end_ms=3000,
                text="Se aprobó el presupuesto del proyecto Atlas.",
            ),
            SegmentDraft(
                index=1,
                start_ms=4000,
                end_ms=7000,
                text="Laura enviará el contrato el viernes.",
            ),
        ],
    )
    return meeting.id, transcription.id


def test_mcp_read_endpoints_are_bounded_and_search_active_transcript(
    client: TestClient,
) -> None:
    meeting_id, transcription_id = _completed_meeting(
        client, "Plan Atlas", "2026-08-20T09:00:00+00:00"
    )

    first_page = client.get(
        f"/api/meetings/{meeting_id}/transcript",
        params={"limit": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["transcription"]["id"] == transcription_id
    assert [item["segment_index"] for item in first_page.json()["segments"]] == [0]
    assert first_page.json()["has_more"] is True
    assert first_page.json()["next_cursor"] == 0

    second_page = client.get(
        f"/api/meetings/{meeting_id}/transcript",
        params={"limit": 1, "cursor": 0},
    )
    assert [item["segment_index"] for item in second_page.json()["segments"]] == [1]
    assert second_page.json()["has_more"] is False
    assert second_page.json()["next_cursor"] is None

    search = client.get(
        "/api/transcriptions/search",
        params={"query": "presupuesto Atlas", "meeting_id": meeting_id},
    )
    assert search.status_code == 200
    assert search.json()["results"][0]["meeting_id"] == meeting_id
    assert "presupuesto" in search.json()["results"][0]["text"]
    assert search.json()["results"][0]["keyword_score"] > 0


def test_meeting_date_filters_and_summary_read_endpoint(client: TestClient) -> None:
    august_id, transcription_id = _completed_meeting(
        client, "August review", "2026-08-20T09:00:00+00:00"
    )
    _completed_meeting(client, "July review", "2026-07-20T09:00:00+00:00")

    listed = client.get(
        "/api/meetings",
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [august_id]
    assert (
        client.get(
            "/api/meetings",
            params={"date_from": "2026-09-01", "date_to": "2026-08-01"},
        ).status_code
        == 422
    )

    container = client.app.state.container
    summary = container.summaries.create(
        meeting_id=august_id,
        transcription_id=transcription_id,
        provider="test",
        model="test",
    )
    container.summaries.complete(summary.id, "# Approved plan")
    response = client.get(f"/api/summaries/{summary.id}")
    assert response.status_code == 200
    assert response.json()["content_markdown"] == "# Approved plan"
    metadata_only = client.get(
        f"/api/meetings/{august_id}/summaries",
        params={"include_content": False},
    )
    assert metadata_only.json()[0]["content_markdown"] is None
    assert metadata_only.json()[0]["structured"] is None


def test_mcp_configuration_and_enabled_preference(client: TestClient) -> None:
    configuration = client.get("/api/mcp/configuration")
    assert configuration.status_code == 200
    payload = configuration.json()
    assert payload["enabled"] is True
    assert '"-m"' in payload["claude_desktop"]["content"]
    assert "[mcp_servers.meet2notes]" in payload["codex_chatgpt"]["content"]
    assert client.get("/api/mcp/status").json() == {"enabled": True}

    updated = client.put("/api/settings", json={"mcp": {"enabled": False}})
    assert updated.status_code == 200
    assert updated.json()["mcp"] == {"enabled": False}
    assert client.get("/api/mcp/status").json() == {"enabled": False}
