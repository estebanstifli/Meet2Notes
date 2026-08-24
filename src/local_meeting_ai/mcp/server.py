from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from local_meeting_ai.mcp.gateway import Meet2NotesGateway
from local_meeting_ai.mcp.schemas import (
    MeetingDetailResult,
    MeetingListResult,
    RagSearchResult,
    StatusResult,
    SummaryResult,
    TranscriptPageResult,
    TranscriptSearchResult,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

mcp = MCPServer(
    "Meet2Notes",
    instructions=(
        "Read private meeting metadata, active transcripts, completed AI notes, "
        "and grounded search evidence from the user's local Meet2Notes application. "
        "All tools are read-only. Use find_in_transcripts for exact terms and "
        "search_meetings for conceptual RAG retrieval."
    ),
    log_level="WARNING",
)
gateway = Meet2NotesGateway()


@mcp.tool(annotations=READ_ONLY)
async def meet2notes_status() -> StatusResult:
    """Check whether the local Meet2Notes app and historical RAG are available."""
    return await gateway.status()


@mcp.tool(annotations=READ_ONLY)
async def list_meetings(
    query: Annotated[str | None, Field(max_length=200)] = None,
    date_from: Annotated[str | None, Field(description="Inclusive ISO date: YYYY-MM-DD")] = None,
    date_to: Annotated[str | None, Field(description="Inclusive ISO date: YYYY-MM-DD")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> MeetingListResult:
    """List meetings, optionally filtered by text and inclusive date range."""
    return await gateway.list_meetings(
        query=query,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
async def get_meeting(
    meeting_id: Annotated[int, Field(ge=1)],
) -> MeetingDetailResult:
    """Get one meeting plus its transcript versions and available AI notes."""
    return await gateway.get_meeting(meeting_id)


@mcp.tool(annotations=READ_ONLY)
async def get_transcript(
    meeting_id: Annotated[int, Field(ge=1)],
    start_ms: Annotated[int | None, Field(ge=0)] = None,
    end_ms: Annotated[int | None, Field(ge=0)] = None,
    cursor: Annotated[int, Field(ge=-1)] = -1,
    segment_limit: Annotated[int, Field(ge=1, le=200)] = 50,
    max_chars: Annotated[int, Field(ge=1000, le=30000)] = 20000,
) -> TranscriptPageResult:
    """Read a bounded page of the completed active transcript with speakers and timestamps."""
    return await gateway.get_transcript(
        meeting_id,
        start_ms=start_ms,
        end_ms=end_ms,
        cursor=cursor,
        segment_limit=segment_limit,
        max_chars=max_chars,
    )


@mcp.tool(annotations=READ_ONLY)
async def get_summary(
    meeting_id: Annotated[int, Field(ge=1)],
    summary_id: Annotated[int | None, Field(ge=1)] = None,
    cursor: Annotated[int, Field(ge=0)] = 0,
    max_chars: Annotated[int, Field(ge=1000, le=30000)] = 20000,
) -> SummaryResult:
    """Read a bounded page of specific or newest completed AI notes for a meeting."""
    return await gateway.get_summary(
        meeting_id,
        summary_id=summary_id,
        cursor=cursor,
        max_chars=max_chars,
    )


@mcp.tool(annotations=READ_ONLY)
async def find_in_transcripts(
    query: Annotated[str, Field(min_length=1, max_length=1000)],
    meeting_id: Annotated[int | None, Field(ge=1)] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> TranscriptSearchResult:
    """Find exact names, phrases, identifiers, or numbers using local keyword search."""
    return await gateway.find_in_transcripts(
        query,
        meeting_id=meeting_id,
        limit=limit,
    )


@mcp.tool(annotations=READ_ONLY)
async def search_meetings(
    query: Annotated[str, Field(min_length=1, max_length=4000)],
    meeting_id: Annotated[int | None, Field(ge=1)] = None,
    top_k: Annotated[int, Field(ge=1, le=10)] = 8,
) -> RagSearchResult:
    """Retrieve conceptual evidence from an existing RAG index without rebuilding it."""
    return await gateway.search_meetings(
        query,
        meeting_id=meeting_id,
        top_k=top_k,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
