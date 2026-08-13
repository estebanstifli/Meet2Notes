from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.application.webhooks import WebhookHttpResult


def _endpoint_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Local automation",
        "url": "http://127.0.0.1:9999/events",
        "enabled": False,
        "mode": "notification",
        "events": ["live.segment.batch", "summary.completed"],
        "content_level": "metadata",
        "timeout_seconds": 3,
        "max_attempts": 2,
        "allow_private_network": False,
    }
    payload.update(overrides)
    return payload


def test_webhook_settings_and_endpoint_lifecycle(client: TestClient) -> None:
    catalog = client.get("/api/webhooks")
    assert catalog.status_code == 200
    assert catalog.json()["settings"]["enabled"] is False
    assert catalog.json()["secure_storage_available"] is True
    assert any(
        item["id"] == "live.segment.batch"
        for item in catalog.json()["event_catalog"]
    )

    saved = client.put(
        "/api/webhooks/settings",
        json={"enabled": True, "retention_days": 14, "max_concurrency": 2},
    )
    assert saved.status_code == 200
    assert saved.json() == {
        "enabled": True,
        "retention_days": 14,
        "max_concurrency": 2,
    }

    created = client.post("/api/webhooks/endpoints", json=_endpoint_payload())
    assert created.status_code == 201
    body = created.json()
    assert len(body["signing_secret"]) >= 32
    endpoint = body["endpoint"]
    assert endpoint["secret_configured"] is True

    updated = client.put(
        f"/api/webhooks/endpoints/{endpoint['id']}",
        json=_endpoint_payload(name="Renamed", content_level="segments"),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["content_level"] == "segments"

    rotated = client.post(
        f"/api/webhooks/endpoints/{endpoint['id']}/rotate-secret"
    )
    assert rotated.status_code == 200
    assert rotated.json()["signing_secret"] != body["signing_secret"]

    deleted = client.delete(f"/api/webhooks/endpoints/{endpoint['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/webhooks").json()["endpoints"] == []


def test_webhook_validation_protects_destinations_and_events(
    client: TestClient,
) -> None:
    plain_public = client.post(
        "/api/webhooks/endpoints",
        json=_endpoint_payload(url="http://example.com/events"),
    )
    assert plain_public.status_code == 422
    assert "must use HTTPS" in plain_public.json()["detail"]

    unknown_event = client.post(
        "/api/webhooks/endpoints",
        json=_endpoint_payload(events=["meeting.magic.happened"]),
    )
    assert unknown_event.status_code == 422
    assert "Unknown webhook event" in unknown_event.json()["detail"]

    no_events = client.post(
        "/api/webhooks/endpoints", json=_endpoint_payload(events=[])
    )
    assert no_events.status_code == 422


def test_dispatcher_signs_and_delivers_from_the_durable_outbox(
    client: TestClient,
) -> None:
    received: dict[str, Any] = {}

    async def fake_sender(
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
        allow_private_network: bool,
    ) -> WebhookHttpResult:
        received.update(
            url=url,
            body=body,
            headers=headers,
            timeout=timeout,
            allow_private_network=allow_private_network,
        )
        return WebhookHttpResult(status_code=204, body=b"", duration_ms=2)

    client.put(
        "/api/webhooks/settings",
        json={"enabled": True, "retention_days": 30, "max_concurrency": 2},
    )
    created = client.post(
        "/api/webhooks/endpoints",
        json=_endpoint_payload(
            enabled=True,
            events=["recording.ready"],
            content_level="metadata",
        ),
    ).json()
    service = client.app.state.container.webhook_service
    service.sender = fake_sender
    event = service.publish("recording.ready", {"recording": {"id": 7}})
    assert event is not None

    deadline = time.monotonic() + 2
    deliveries: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        deliveries = client.get("/api/webhooks/deliveries").json()
        if deliveries and deliveries[0]["status"] == "delivered":
            break
        time.sleep(0.02)

    assert deliveries[0]["status"] == "delivered"
    assert received["url"] == "http://127.0.0.1:9999/events"
    timestamp = received["headers"]["X-Meet2Notes-Timestamp"]
    expected = hmac.new(
        created["signing_secret"].encode(),
        timestamp.encode() + b"." + received["body"],
        hashlib.sha256,
    ).hexdigest()
    assert received["headers"]["X-Meet2Notes-Signature-256"] == f"sha256={expected}"
