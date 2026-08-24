from __future__ import annotations

import asyncio
from typing import Any

import httpx

from local_meeting_ai.mcp.discovery import DiscoveryError, candidate_base_urls
from local_meeting_ai.mcp.schemas import (
    MeetingDetailResult,
    MeetingItem,
    MeetingListResult,
    RagSearchResult,
    StatusResult,
    SummaryBrief,
    SummaryResult,
    TranscriptionBrief,
    TranscriptPageResult,
    TranscriptSearchResult,
    TranscriptSegmentItem,
)


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Meet2NotesGateway:
    def __init__(
        self,
        *,
        base_urls: list[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_urls = base_urls
        self._transport = transport
        self._timeout = timeout

    async def status(self) -> StatusResult:
        try:
            health, base_url = await self._request("GET", "/api/health")
        except (DiscoveryError, GatewayError) as error:
            return StatusResult(
                connected=False,
                error_code=getattr(error, "code", "backend_unavailable"),
                message=str(error),
            )
        try:
            mcp, base_url = await self._request("GET", "/api/mcp/status", base_url=base_url)
        except GatewayError as error:
            return StatusResult(
                connected=True,
                app_version=str(health.get("version") or ""),
                database=str(health.get("database") or "unknown"),
                queue=str(health.get("queue") or "unknown"),
                backend_url=base_url,
                error_code=error.code,
                message=str(error),
            )
        enabled = bool(mcp.get("enabled", True))
        if not enabled:
            return StatusResult(
                connected=True,
                enabled=False,
                app_version=str(health.get("version") or ""),
                database=str(health.get("database") or "unknown"),
                queue=str(health.get("queue") or "unknown"),
                backend_url=base_url,
                message="Desktop MCP access is disabled in Meet2Notes settings.",
            )
        rag: dict[str, Any] | None
        try:
            rag, _ = await self._request("GET", "/api/rag/status", base_url=base_url)
        except GatewayError as error:
            rag = {"available": False, "error": str(error)}
        return StatusResult(
            connected=True,
            enabled=True,
            app_version=str(health.get("version") or ""),
            database=str(health.get("database") or "unknown"),
            queue=str(health.get("queue") or "unknown"),
            backend_url=base_url,
            rag=rag,
        )

    async def list_meetings(
        self,
        *,
        query: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
    ) -> MeetingListResult:
        base_url = await self._ensure_enabled()
        payload, _ = await self._request(
            "GET",
            "/api/meetings",
            params={
                "search": query,
                "date_from": date_from,
                "date_to": date_to,
                "limit": max(1, min(limit, 50)),
            },
            base_url=base_url,
        )
        return MeetingListResult(
            query=query,
            date_from=date_from,
            date_to=date_to,
            meetings=[MeetingItem.model_validate(item) for item in payload],
        )

    async def get_meeting(self, meeting_id: int) -> MeetingDetailResult:
        base_url = await self._ensure_enabled()
        meeting_call = self._request("GET", f"/api/meetings/{meeting_id}", base_url=base_url)
        transcriptions_call = self._request(
            "GET",
            f"/api/meetings/{meeting_id}/transcriptions",
            base_url=base_url,
        )
        summaries_call = self._request(
            "GET",
            f"/api/meetings/{meeting_id}/summaries",
            params={"include_content": False},
            base_url=base_url,
        )
        meeting_response, transcription_response, summary_response = await asyncio.gather(
            meeting_call,
            transcriptions_call,
            summaries_call,
        )
        transcriptions = [
            TranscriptionBrief.model_validate(item) for item in transcription_response[0]
        ]
        return MeetingDetailResult(
            meeting=MeetingItem.model_validate(meeting_response[0]),
            active_transcription=next(
                (item for item in transcriptions if item.is_active),
                None,
            ),
            transcriptions=transcriptions,
            summaries=[SummaryBrief.model_validate(item) for item in summary_response[0]],
        )

    async def get_transcript(
        self,
        meeting_id: int,
        *,
        start_ms: int | None,
        end_ms: int | None,
        cursor: int,
        segment_limit: int,
        max_chars: int,
    ) -> TranscriptPageResult:
        base_url = await self._ensure_enabled()
        payload, _ = await self._request(
            "GET",
            f"/api/meetings/{meeting_id}/transcript",
            params={
                "start_ms": start_ms,
                "end_ms": end_ms,
                "cursor": cursor,
                "limit": max(1, min(segment_limit, 200)),
            },
            base_url=base_url,
        )
        speakers = {
            int(item["id"]): str(item["display_name"]) for item in payload.get("speakers", [])
        }
        selected: list[TranscriptSegmentItem] = []
        used = 0
        char_limited = False
        for item in payload.get("segments", []):
            speaker = speakers.get(item.get("speaker_id"), "Speaker")
            text = str(item.get("text") or "")
            cost = len(speaker) + len(text) + 24
            if selected and used + cost > max_chars:
                char_limited = True
                break
            text_truncated = False
            if not selected and cost > max_chars:
                available = max(1, max_chars - len(speaker) - 25)
                text = text[:available].rstrip() + "…"
                text_truncated = True
                char_limited = True
            selected.append(
                TranscriptSegmentItem(
                    segment_index=int(item["segment_index"]),
                    start_ms=int(item["start_ms"]),
                    end_ms=int(item["end_ms"]),
                    speaker=speaker,
                    text=text,
                    text_truncated=text_truncated,
                )
            )
            used += cost
        truncated = bool(payload.get("has_more")) or char_limited
        next_cursor = selected[-1].segment_index if truncated and selected else None
        return TranscriptPageResult(
            meeting_id=meeting_id,
            transcription=TranscriptionBrief.model_validate(payload["transcription"]),
            segments=selected,
            truncated=truncated,
            next_cursor=next_cursor,
        )

    async def get_summary(
        self,
        meeting_id: int,
        *,
        summary_id: int | None,
        cursor: int,
        max_chars: int,
    ) -> SummaryResult:
        base_url = await self._ensure_enabled()
        if summary_id is None:
            summaries, _ = await self._request(
                "GET",
                f"/api/meetings/{meeting_id}/summaries",
                params={"include_content": False},
                base_url=base_url,
            )
            selected = next(
                (item for item in summaries if item.get("status") == "completed"),
                None,
            )
            if selected is None:
                raise GatewayError("summary_not_found", "No completed AI notes are available")
            selected, _ = await self._request(
                "GET", f"/api/summaries/{selected['id']}", base_url=base_url
            )
        else:
            selected, _ = await self._request(
                "GET", f"/api/summaries/{summary_id}", base_url=base_url
            )
            if int(selected.get("meeting_id", 0)) != meeting_id:
                raise GatewayError(
                    "summary_outside_meeting",
                    "The requested AI notes do not belong to this meeting",
                )
        content = selected.get("content_markdown")
        if isinstance(content, str):
            end = min(len(content), cursor + max_chars)
            selected = {
                **selected,
                "content_markdown": content[cursor:end],
                "truncated": end < len(content),
                "next_cursor": end if end < len(content) else None,
            }
        return SummaryResult.model_validate(selected)

    async def find_in_transcripts(
        self,
        query: str,
        *,
        meeting_id: int | None,
        limit: int,
    ) -> TranscriptSearchResult:
        base_url = await self._ensure_enabled()
        payload, _ = await self._request(
            "GET",
            "/api/transcriptions/search",
            params={
                "query": query,
                "meeting_id": meeting_id,
                "limit": max(1, min(limit, 50)),
            },
            base_url=base_url,
        )
        return TranscriptSearchResult.model_validate(payload)

    async def search_meetings(
        self,
        query: str,
        *,
        meeting_id: int | None,
        top_k: int,
    ) -> RagSearchResult:
        base_url = await self._ensure_enabled()
        status, base_url = await self._request("GET", "/api/rag/status", base_url=base_url)
        if not status.get("enabled"):
            raise GatewayError("rag_disabled", "Historical RAG is disabled in Meet2Notes")
        if int(status.get("chunks") or 0) < 1:
            raise GatewayError(
                "rag_index_not_ready",
                "The historical RAG index is empty. Build it from Meet2Notes first.",
            )
        payload, _ = await self._request(
            "POST",
            "/api/rag/search",
            base_url=base_url,
            json={
                "query": query,
                "meeting_id": meeting_id,
                "top_k": max(1, min(top_k, 10)),
                "ensure_index": False,
            },
            timeout=120.0,
        )
        return RagSearchResult.model_validate(payload)

    async def _ensure_enabled(self) -> str:
        payload, base_url = await self._request("GET", "/api/mcp/status")
        if not bool(payload.get("enabled", True)):
            raise GatewayError(
                "mcp_disabled",
                "Desktop MCP access is disabled in Meet2Notes settings.",
            )
        return base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> tuple[Any, str]:
        try:
            urls = [base_url] if base_url else (self._base_urls or candidate_base_urls())
        except DiscoveryError:
            raise
        connection_errors: list[str] = []
        for candidate in urls:
            try:
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=timeout or self._timeout,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(
                        method,
                        f"{candidate}{path}",
                        params={
                            key: value for key, value in (params or {}).items() if value is not None
                        },
                        json=json,
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as error:
                connection_errors.append(str(error))
                continue
            if response.is_error:
                self._raise_response_error(response)
            try:
                return response.json(), candidate
            except ValueError as error:
                raise GatewayError(
                    "invalid_backend_response",
                    "Meet2Notes returned an invalid JSON response",
                ) from error
        detail = connection_errors[-1] if connection_errors else "no active instance was found"
        raise GatewayError(
            "meet2notes_not_running",
            f"Meet2Notes is not reachable ({detail}). Open Meet2Notes and retry.",
        )

    @staticmethod
    def _raise_response_error(response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        code = payload.get("error") if isinstance(payload, dict) else None
        raise GatewayError(
            str(code or f"http_{response.status_code}").casefold(),
            str(detail or f"Meet2Notes returned HTTP {response.status_code}"),
        )
