from __future__ import annotations

import json

import httpx
import pytest

from local_meeting_ai.mcp.configuration import desktop_client_configurations
from local_meeting_ai.mcp.discovery import DiscoveryError, candidate_base_urls
from local_meeting_ai.mcp.gateway import GatewayError, Meet2NotesGateway
from local_meeting_ai.mcp.server import mcp


@pytest.mark.asyncio
async def test_gateway_starts_disconnected_without_requiring_app() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    gateway = Meet2NotesGateway(
        base_urls=["http://127.0.0.1:8765"],
        transport=httpx.MockTransport(unavailable),
    )
    status = await gateway.status()
    assert status.connected is False
    assert status.error_code == "meet2notes_not_running"
    assert "Open Meet2Notes" in str(status.message)


@pytest.mark.asyncio
async def test_gateway_rag_search_never_requests_index_rebuild() -> None:
    request_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/mcp/status":
            return httpx.Response(200, json={"enabled": True})
        if request.url.path == "/api/rag/status":
            return httpx.Response(200, json={"enabled": True, "chunks": 3})
        if request.url.path == "/api/rag/search":
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "query": "decisión Atlas",
                    "meeting_id": None,
                    "results": [
                        {
                            "rank": 1,
                            "meeting_id": 4,
                            "transcription_id": 8,
                            "meeting_title": "Atlas",
                            "meeting_date": "2026-08-20",
                            "start_ms": 1000,
                            "end_ms": 3000,
                            "text": "Se aprobó Atlas.",
                            "score": 0.9,
                            "semantic_score": 0.8,
                            "keyword_score": 0.7,
                            "retrieval_methods": ["dense", "bm25"],
                        }
                    ],
                    "ranking": {"method": "hybrid-dense-bm25-rrf"},
                },
            )
        raise AssertionError(request.url.path)

    gateway = Meet2NotesGateway(
        base_urls=["http://127.0.0.1:8765"],
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.search_meetings(
        "decisión Atlas",
        meeting_id=None,
        top_k=8,
    )
    assert result.results[0].meeting_id == 4
    assert request_payload["ensure_index"] is False


@pytest.mark.asyncio
async def test_gateway_pages_summary_content_and_fetches_metadata_first() -> None:
    list_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal list_query
        if request.url.path == "/api/mcp/status":
            return httpx.Response(200, json={"enabled": True})
        if request.url.path == "/api/meetings/4/summaries":
            list_query = request.url.query.decode()
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 9,
                        "meeting_id": 4,
                        "transcription_id": 8,
                        "provider": "test",
                        "model": "test",
                        "status": "completed",
                        "created_at": "2026-08-20T09:00:00Z",
                        "completed_at": "2026-08-20T09:01:00Z",
                    }
                ],
            )
        if request.url.path == "/api/summaries/9":
            return httpx.Response(
                200,
                json={
                    "id": 9,
                    "meeting_id": 4,
                    "transcription_id": 8,
                    "provider": "test",
                    "model": "test",
                    "status": "completed",
                    "content_markdown": "x" * 1500,
                    "structured": None,
                    "created_at": "2026-08-20T09:00:00Z",
                    "completed_at": "2026-08-20T09:01:00Z",
                },
            )
        raise AssertionError(request.url.path)

    gateway = Meet2NotesGateway(
        base_urls=["http://127.0.0.1:8765"],
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.get_summary(
        4,
        summary_id=None,
        cursor=0,
        max_chars=1000,
    )
    assert len(result.content_markdown or "") == 1000
    assert result.truncated is True
    assert result.next_cursor == 1000
    assert "include_content=false" in list_query


def test_desktop_client_configurations_use_stdio_and_platform_paths(tmp_path) -> None:
    configurations = desktop_client_configurations(
        platform_name="win32",
        home=tmp_path / "home",
        environment={"APPDATA": str(tmp_path / "appdata")},
        python_executable=str(tmp_path / "venv" / "Scripts" / "python.exe"),
    )

    claude = configurations["claude-desktop"]
    codex = configurations["codex-chatgpt"]
    assert claude.path == tmp_path / "appdata" / "Claude" / "claude_desktop_config.json"
    assert json.loads(claude.content)["mcpServers"]["meet2notes"]["args"] == [
        "-m",
        "local_meeting_ai.mcp.server",
    ]
    assert codex.path == tmp_path / "home" / ".codex" / "config.toml"
    assert "[mcp_servers.meet2notes]" in codex.content


@pytest.mark.asyncio
async def test_gateway_rejects_reads_when_mcp_is_disabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/mcp/status"
        return httpx.Response(200, json={"enabled": False})

    gateway = Meet2NotesGateway(
        base_urls=["http://127.0.0.1:8765"],
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(GatewayError, match="disabled") as error:
        await gateway.list_meetings(query=None, date_from=None, date_to=None, limit=10)
    assert error.value.code == "mcp_disabled"


def test_discovery_rejects_remote_backend_without_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("M2N_MCP_BASE_URL", "https://meetings.example.com")
    monkeypatch.delenv("M2N_MCP_ALLOW_REMOTE", raising=False)
    with pytest.raises(DiscoveryError):
        candidate_base_urls()


@pytest.mark.asyncio
async def test_server_exposes_only_the_small_read_surface() -> None:
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "meet2notes_status",
        "list_meetings",
        "get_meeting",
        "get_transcript",
        "get_summary",
        "find_in_transcripts",
        "search_meetings",
    }
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools)
    assert all(tool.annotations and tool.annotations.open_world_hint is False for tool in tools)
