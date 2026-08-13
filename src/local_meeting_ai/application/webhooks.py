from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from local_meeting_ai.domain.entities import Job, SegmentDraft
from local_meeting_ai.domain.enums import JobStatus, JobType
from local_meeting_ai.domain.errors import NotFoundError, ValidationError
from local_meeting_ai.domain.webhooks import (
    WEBHOOK_EVENT_CATALOG,
    WEBHOOK_EVENT_IDS,
    WebhookDelivery,
    WebhookEndpoint,
    WebhookEvent,
)
from local_meeting_ai.infrastructure.database.repositories import (
    JobRepository,
    MeetingRepository,
    SettingsRepository,
    SummaryRepository,
    TranscriptionRepository,
    utc_now,
)
from local_meeting_ai.infrastructure.database.webhooks import WebhookRepository
from local_meeting_ai.infrastructure.webhook_secrets import WebhookSecretStore

logger = logging.getLogger(__name__)

WEBHOOK_SETTINGS_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "retention_days": 30,
    "max_concurrency": 4,
}


@dataclass(frozen=True, slots=True)
class WebhookHttpResult:
    status_code: int | None
    body: bytes
    duration_ms: int
    error: str | None = None
    retry_after: float | None = None


WebhookSender = Callable[
    [str, bytes, dict[str, str], float, bool], Awaitable[WebhookHttpResult]
]


class WebhookService:
    """Durable outbound webhook publisher and isolated delivery dispatcher."""

    def __init__(
        self,
        *,
        repository: WebhookRepository,
        preferences: SettingsRepository,
        secrets_store: WebhookSecretStore,
        meetings: MeetingRepository,
        transcriptions: TranscriptionRepository,
        summaries: SummaryRepository,
        jobs: JobRepository,
        sender: WebhookSender | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.repository = repository
        self.preferences = preferences
        self.secrets = secrets_store
        self.meetings = meetings
        self.transcriptions = transcriptions
        self.summaries = summaries
        self.jobs = jobs
        self.sender = sender or _send_http
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def settings(self) -> dict[str, Any]:
        raw = self.preferences.get_all().get("webhooks")
        return {
            **WEBHOOK_SETTINGS_DEFAULTS,
            **(raw if isinstance(raw, dict) else {}),
        }

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        updated = {**self.settings(), **values}
        self.preferences.update({"webhooks": updated})
        self.repository.purge(int(updated["retention_days"]))
        self._wake.set()
        return updated

    def catalog(self) -> dict[str, Any]:
        return {
            "settings": self.settings(),
            "secure_storage_available": self.secrets.available(),
            "event_catalog": list(WEBHOOK_EVENT_CATALOG),
            "endpoints": [self.endpoint_payload(item) for item in self.repository.endpoints()],
            "deliveries": self.repository.recent_deliveries(100),
        }

    def endpoint_payload(self, endpoint: WebhookEndpoint) -> dict[str, Any]:
        return {
            **asdict(endpoint),
            "secret_configured": bool(self.secrets.get(endpoint.id)),
        }

    def create_endpoint(self, values: dict[str, Any]) -> tuple[WebhookEndpoint, str]:
        normalized = _validated_endpoint_values(values)
        endpoint = self.repository.create_endpoint(normalized)
        secret = secrets.token_urlsafe(32)
        try:
            self.secrets.set(endpoint.id, secret)
        except Exception:
            self.repository.delete_endpoint(endpoint.id)
            raise
        return endpoint, secret

    def update_endpoint(self, endpoint_id: str, values: dict[str, Any]) -> WebhookEndpoint:
        existing = self.repository.endpoint(endpoint_id)
        if existing is None:
            raise NotFoundError("Webhook endpoint not found")
        merged = {**asdict(existing), **values}
        updated = self.repository.update_endpoint(
            endpoint_id, _validated_endpoint_values(merged)
        )
        assert updated is not None
        return updated

    def delete_endpoint(self, endpoint_id: str) -> None:
        if self.repository.endpoint(endpoint_id) is None:
            raise NotFoundError("Webhook endpoint not found")
        self.secrets.delete(endpoint_id)
        self.repository.delete_endpoint(endpoint_id)

    def rotate_secret(self, endpoint_id: str) -> str:
        if self.repository.endpoint(endpoint_id) is None:
            raise NotFoundError("Webhook endpoint not found")
        secret = secrets.token_urlsafe(32)
        self.secrets.set(endpoint_id, secret)
        return secret

    async def start(self) -> None:
        if self._task is not None:
            return
        recovered = self.repository.recover_delivering()
        if recovered:
            logger.warning("Recovered %d interrupted webhook deliveries", recovered)
        self.repository.purge(int(self.settings()["retention_days"]))
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="webhook-dispatcher")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        meeting_id: int | None = None,
        transcription_id: int | None = None,
        subject: str | None = None,
        live_ttl_seconds: int | None = None,
        force_endpoint_id: str | None = None,
    ) -> WebhookEvent | None:
        if event_type not in WEBHOOK_EVENT_IDS and event_type != "system.webhook.test":
            raise ValidationError(f"Unknown webhook event type: {event_type}")
        if force_endpoint_id is None and not bool(self.settings()["enabled"]):
            return None
        if force_endpoint_id is not None:
            endpoint = self.repository.endpoint(force_endpoint_id)
            endpoints = [endpoint] if endpoint and endpoint.enabled else []
        else:
            endpoints = self.repository.subscribed_endpoints(event_type)
        if not endpoints:
            return None
        expires_at = None
        if live_ttl_seconds is not None:
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=live_ttl_seconds)
            ).isoformat(timespec="milliseconds")
        event = self.repository.enqueue_event(
            event_type=event_type,
            subject=subject or _subject(meeting_id, transcription_id),
            meeting_id=meeting_id,
            transcription_id=transcription_id,
            data=data,
            endpoint_ids=[endpoint.id for endpoint in endpoints],
            expires_at=expires_at,
        )
        if event is not None:
            self._wake.set()
        return event

    def publish_live_session(self, event_type: str, session: Any) -> None:
        self.publish(
            event_type,
            {
                "meeting": {
                    "id": session.meeting_id,
                    "title": session.title,
                },
                "session": {
                    "id": session.session_id,
                    "state": session.state,
                    "started_at": session.started_at,
                    "elapsed_ms": session.elapsed_ms,
                    "language": session.language,
                    "profile_id": session.profile_id,
                },
            },
            meeting_id=session.meeting_id,
            transcription_id=session.transcription_id,
            live_ttl_seconds=300,
        )

    def publish_live_segments(
        self,
        *,
        meeting_id: int,
        transcription_id: int,
        meeting_title: str,
        segments: list[SegmentDraft],
        sequence: int,
    ) -> None:
        self.publish(
            "live.segment.batch",
            {
                "meeting": {"id": meeting_id, "title": meeting_title},
                "sequence": sequence,
                "provisional": True,
                "segments": [
                    {
                        "id": f"live-{transcription_id}-{segment.index}",
                        "index": segment.index,
                        "start_ms": segment.start_ms,
                        "end_ms": segment.end_ms,
                        "text": segment.text,
                        "confidence": segment.confidence,
                    }
                    for segment in segments
                ],
            },
            meeting_id=meeting_id,
            transcription_id=transcription_id,
            live_ttl_seconds=120,
        )

    def publish_job_terminal(self, job: Job, status: JobStatus) -> None:
        # Per-speaker summaries have their own local UI lifecycle and are not
        # meeting-note completion events.
        if job.job_type == JobType.SUMMARIZE and job.payload.get("summary_scope") == "speaker":
            return
        event_type = _job_event_type(job.job_type, status)
        if event_type is None:
            return
        current = self.jobs.get(job.uuid) or job
        data: dict[str, Any] = {
            "job": {
                "id": job.uuid,
                "type": job.job_type.value,
                "status": status.value,
                "result": current.result,
                "error": current.error_text,
            }
        }
        meeting = self.meetings.get(job.meeting_id) if job.meeting_id else None
        if meeting is not None:
            data["meeting"] = {
                "id": meeting.id,
                "uuid": meeting.uuid,
                "title": meeting.title,
                "language": meeting.language,
                "started_at": meeting.started_at,
                "ended_at": meeting.ended_at,
                "duration_ms": meeting.duration_ms,
            }
        transcription_id = job.payload.get("transcription_id")
        if status == JobStatus.COMPLETED and isinstance(transcription_id, int):
            if job.job_type in {JobType.TRANSCRIBE, JobType.DIARIZE}:
                segments = self.transcriptions.segments(transcription_id)
                data["transcript"] = {
                    "id": transcription_id,
                    "segments": [
                        {
                            "id": segment.id,
                            "index": segment.segment_index,
                            "start_ms": segment.start_ms,
                            "end_ms": segment.end_ms,
                            "text": segment.text,
                            "speaker_id": segment.speaker_id,
                            "confidence": segment.confidence,
                        }
                        for segment in segments
                    ],
                }
            if job.job_type == JobType.SUMMARIZE:
                summary_id = job.payload.get("summary_id")
                summary = self.summaries.get(summary_id) if isinstance(summary_id, int) else None
                if summary is not None:
                    data["summary"] = {
                        "id": summary.id,
                        "provider": summary.provider,
                        "model": summary.model,
                        "content_markdown": summary.content_markdown,
                    }
        self.publish(
            event_type,
            data,
            meeting_id=job.meeting_id,
            transcription_id=transcription_id if isinstance(transcription_id, int) else None,
        )

    def publish_pipeline_finished(
        self,
        *,
        meeting_id: int | None,
        transcription_id: int,
        pipeline_id: str | None,
        status: str,
        options: dict[str, Any],
    ) -> None:
        self.publish(
            "meeting.processing.completed",
            {
                "meeting_id": meeting_id,
                "transcription_id": transcription_id,
                "pipeline_id": pipeline_id,
                "status": status,
                "requested_stages": options,
            },
            meeting_id=meeting_id,
            transcription_id=transcription_id,
        )

    def test_endpoint(self, endpoint_id: str) -> WebhookEvent:
        endpoint = self.repository.endpoint(endpoint_id)
        if endpoint is None:
            raise NotFoundError("Webhook endpoint not found")
        if not endpoint.enabled:
            raise ValidationError("Enable the endpoint before sending a test")
        event = self.publish(
            "system.webhook.test",
            {"message": "Meet2Notes webhook test", "sent_at": utc_now()},
            subject="webhook/test",
            force_endpoint_id=endpoint_id,
        )
        assert event is not None
        return event

    async def _run(self) -> None:
        while not self._stopping:
            if not bool(self.settings()["enabled"]):
                self._wake.clear()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                continue
            deliveries = self.repository.claim_due(20)
            if deliveries:
                limit = max(1, min(int(self.settings()["max_concurrency"]), 16))
                semaphore = asyncio.Semaphore(limit)

                async def deliver(
                    item: WebhookDelivery,
                    limiter: asyncio.Semaphore = semaphore,
                ) -> None:
                    async with limiter:
                        await self._deliver(item)

                await asyncio.gather(*(deliver(item) for item in deliveries))
                continue
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)

    async def _deliver(self, delivery: WebhookDelivery) -> None:
        endpoint = self.repository.endpoint(delivery.endpoint_id)
        event = self.repository.event(delivery.event_id)
        if endpoint is None or event is None or not endpoint.enabled:
            self.repository.finish_delivery(
                delivery.id,
                status="failed",
                status_code=None,
                error="Endpoint is disabled or no longer exists",
                duration_ms=0,
                response_excerpt=None,
            )
            return
        try:
            await asyncio.to_thread(_validate_delivery_target, endpoint)
        except Exception as error:
            self.repository.finish_delivery(
                delivery.id,
                status="failed",
                status_code=None,
                error=str(error),
                duration_ms=0,
                response_excerpt=None,
            )
            return
        envelope = _event_envelope(event, endpoint.content_level)
        body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        secret = self.secrets.get(endpoint.id)
        if not secret:
            self.repository.finish_delivery(
                delivery.id,
                status="failed",
                status_code=None,
                error="Webhook signing secret is unavailable",
                duration_ms=0,
                response_excerpt=None,
            )
            return
        signature = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/cloudevents+json; charset=utf-8",
            "User-Agent": "Meet2Notes-Webhooks/1",
            "X-Meet2Notes-Event": event.event_type,
            "X-Meet2Notes-Delivery": delivery.id,
            "X-Meet2Notes-Timestamp": timestamp,
            "X-Meet2Notes-Signature-256": f"sha256={signature}",
        }
        result = await self.sender(
            endpoint.url,
            body,
            headers,
            endpoint.timeout_seconds,
            endpoint.allow_private_network,
        )
        excerpt = result.body[:4000].decode("utf-8", errors="replace") or None
        success = result.status_code is not None and 200 <= result.status_code < 300
        retryable = result.status_code in {408, 425, 429} or (
            result.status_code is not None and result.status_code >= 500
        ) or result.status_code is None
        if success:
            self.repository.finish_delivery(
                delivery.id,
                status="delivered",
                status_code=result.status_code,
                error=None,
                duration_ms=result.duration_ms,
                response_excerpt=excerpt,
            )
            if endpoint.mode == "live_agent" and event.event_type == "live.segment.batch":
                self._capture_insights(endpoint, event, result.body)
            return
        if retryable and delivery.attempt_count < endpoint.max_attempts:
            delay = result.retry_after or _retry_delay(delivery.attempt_count)
            next_attempt = (
                datetime.now(UTC) + timedelta(seconds=delay)
            ).isoformat(timespec="milliseconds")
            self.repository.finish_delivery(
                delivery.id,
                status="retry",
                status_code=result.status_code,
                error=result.error or f"HTTP {result.status_code}",
                duration_ms=result.duration_ms,
                response_excerpt=excerpt,
                next_attempt_at=next_attempt,
            )
            return
        self.repository.finish_delivery(
            delivery.id,
            status="failed",
            status_code=result.status_code,
            error=result.error or f"HTTP {result.status_code}",
            duration_ms=result.duration_ms,
            response_excerpt=excerpt,
        )

    def _capture_insights(
        self, endpoint: WebhookEndpoint, event: WebhookEvent, body: bytes
    ) -> None:
        if not body or len(body) > 64 * 1024:
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
        if isinstance(suggestions, list):
            self.repository.add_insights(
                endpoint=endpoint,
                event=event,
                suggestions=[item for item in suggestions if isinstance(item, dict)],
            )


async def _send_http(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    _allow_private_network: bool,
) -> WebhookHttpResult:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            verify=True,
            trust_env=False,
        ) as client:
            response = await client.post(url, content=body, headers=headers)
        response_body = response.content[: 64 * 1024]
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        return WebhookHttpResult(
            status_code=response.status_code,
            body=response_body,
            duration_ms=round((time.perf_counter() - started) * 1000),
            retry_after=retry_after,
        )
    except Exception as error:
        return WebhookHttpResult(
            status_code=None,
            body=b"",
            duration_ms=round((time.perf_counter() - started) * 1000),
            error=str(error) or type(error).__name__,
        )


def _validated_endpoint_values(values: dict[str, Any]) -> dict[str, Any]:
    name = str(values.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValidationError("Webhook endpoint name must contain 1-120 characters")
    url = str(values.get("url") or "").strip()
    allow_private = bool(values.get("allow_private_network", False))
    _validate_url_shape(url, allow_private)
    events = tuple(dict.fromkeys(str(item) for item in values.get("events", [])))
    unknown = set(events) - WEBHOOK_EVENT_IDS
    if unknown:
        raise ValidationError(f"Unknown webhook event(s): {', '.join(sorted(unknown))}")
    if not events:
        raise ValidationError("Select at least one webhook event")
    mode = str(values.get("mode", "notification"))
    if mode not in {"notification", "live_agent"}:
        raise ValidationError("Unsupported webhook endpoint mode")
    content_level = str(values.get("content_level", "metadata"))
    if content_level not in {"metadata", "segments", "full"}:
        raise ValidationError("Unsupported webhook content level")
    timeout = float(values.get("timeout_seconds", 10))
    if not 1 <= timeout <= 30:
        raise ValidationError("Webhook timeout must be between 1 and 30 seconds")
    attempts = int(values.get("max_attempts", 4))
    if not 1 <= attempts <= 10:
        raise ValidationError("Webhook attempts must be between 1 and 10")
    return {
        "name": name,
        "url": url,
        "enabled": bool(values.get("enabled", False)),
        "mode": mode,
        "events": list(events),
        "content_level": content_level,
        "timeout_seconds": timeout,
        "max_attempts": attempts,
        "allow_private_network": allow_private,
    }


def _validate_url_shape(url: str, allow_private_network: bool) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValidationError("Webhook URL is invalid") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("Webhook URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValidationError("Webhook URLs cannot contain credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("Webhook URL port is invalid")
    loopback = _hostname_is_loopback(parsed.hostname)
    if parsed.scheme == "http" and not loopback and not allow_private_network:
        raise ValidationError(
            "Public webhook endpoints must use HTTPS; enable private-network access "
            "explicitly for trusted LAN HTTP endpoints"
        )


def _validate_delivery_target(endpoint: WebhookEndpoint) -> None:
    _validate_url_shape(endpoint.url, endpoint.allow_private_network)
    hostname = urlsplit(endpoint.url).hostname
    assert hostname is not None
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    }
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback:
            continue
        unsafe = (
            ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
        if unsafe and not endpoint.allow_private_network:
            raise ValidationError(
                "Webhook target resolves to a private or reserved network address"
            )


def _hostname_is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _event_envelope(event: WebhookEvent, content_level: str) -> dict[str, Any]:
    data = json.loads(json.dumps(event.data))
    if content_level == "metadata":
        data = _metadata_only(data)
    elif content_level == "segments":
        summary = data.get("summary")
        if isinstance(summary, dict):
            summary.pop("content_markdown", None)
    return {
        "specversion": "1.0",
        "id": event.id,
        "source": "meet2notes://local-instance",
        "type": f"com.meet2notes.{event.event_type}.v1",
        "time": event.occurred_at,
        "subject": event.subject,
        "datacontenttype": "application/json",
        "data": data,
    }


def _metadata_only(data: dict[str, Any]) -> dict[str, Any]:
    loaded = json.loads(json.dumps(data))
    clean: dict[str, Any] = dict(loaded) if isinstance(loaded, dict) else {}
    if isinstance(clean.get("segments"), list):
        clean["segment_count"] = len(clean.pop("segments"))
    transcript = clean.get("transcript")
    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        transcript["segment_count"] = len(transcript.pop("segments"))
    summary = clean.get("summary")
    if isinstance(summary, dict):
        summary.pop("content_markdown", None)
    return clean


def _job_event_type(job_type: JobType, status: JobStatus) -> str | None:
    terminal = status.value
    if job_type == JobType.IMPORT_MEDIA:
        return "recording.ready" if status == JobStatus.COMPLETED else "recording.failed"
    prefix = {
        JobType.TRANSCRIBE: "transcription.final",
        JobType.DIARIZE: "diarization",
        JobType.SUMMARIZE: "summary",
    }.get(job_type)
    return f"{prefix}.{terminal}" if prefix else None


def _subject(meeting_id: int | None, transcription_id: int | None) -> str:
    if meeting_id is not None:
        return f"meeting/{meeting_id}"
    if transcription_id is not None:
        return f"transcription/{transcription_id}"
    return "application"


def _retry_delay(attempt: int) -> float:
    return (5.0, 30.0, 120.0, 600.0, 1800.0)[min(max(attempt - 1, 0), 4)]


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(1.0, min(float(value), 3600.0))
    except ValueError:
        return None
