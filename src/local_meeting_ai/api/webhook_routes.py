from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.domain.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
ContainerDependency = Annotated[Container, Depends(get_container)]


class WebhookSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    retention_days: int = Field(default=30, ge=1, le=365)
    max_concurrency: int = Field(default=4, ge=1, le=16)


class WebhookEndpointWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = False
    mode: Literal["notification", "live_agent"] = "notification"
    events: list[str] = Field(min_length=1, max_length=40)
    content_level: Literal["metadata", "segments", "full"] = "metadata"
    timeout_seconds: float = Field(default=10, ge=1, le=30)
    max_attempts: int = Field(default=4, ge=1, le=10)
    allow_private_network: bool = False


class WebhookInsightWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["new", "accepted", "dismissed"]


@router.get("")
def webhook_catalog(container: ContainerDependency) -> dict[str, Any]:
    return container.webhook_service.catalog()


@router.put("/settings")
def update_webhook_settings(
    payload: WebhookSettingsWrite,
    container: ContainerDependency,
) -> dict[str, Any]:
    return container.webhook_service.update_settings(payload.model_dump())


@router.post("/endpoints", status_code=status.HTTP_201_CREATED)
def create_webhook_endpoint(
    payload: WebhookEndpointWrite,
    container: ContainerDependency,
) -> dict[str, Any]:
    endpoint, signing_secret = container.webhook_service.create_endpoint(
        payload.model_dump()
    )
    return {
        "endpoint": container.webhook_service.endpoint_payload(endpoint),
        "signing_secret": signing_secret,
    }


@router.put("/endpoints/{endpoint_id}")
def update_webhook_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointWrite,
    container: ContainerDependency,
) -> dict[str, Any]:
    endpoint = container.webhook_service.update_endpoint(
        endpoint_id, payload.model_dump()
    )
    return container.webhook_service.endpoint_payload(endpoint)


@router.delete("/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook_endpoint(
    endpoint_id: str,
    container: ContainerDependency,
) -> Response:
    container.webhook_service.delete_endpoint(endpoint_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/endpoints/{endpoint_id}/rotate-secret")
def rotate_webhook_secret(
    endpoint_id: str,
    container: ContainerDependency,
) -> dict[str, str]:
    return {"signing_secret": container.webhook_service.rotate_secret(endpoint_id)}


@router.post("/endpoints/{endpoint_id}/test", status_code=status.HTTP_202_ACCEPTED)
def test_webhook_endpoint(
    endpoint_id: str,
    container: ContainerDependency,
) -> dict[str, str]:
    event = container.webhook_service.test_endpoint(endpoint_id)
    return {"event_id": event.id, "status": "queued"}


@router.get("/deliveries")
def webhook_deliveries(
    container: ContainerDependency,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return container.webhook_repository.recent_deliveries(limit)


@router.post("/deliveries/{delivery_id}/retry")
def retry_webhook_delivery(
    delivery_id: str,
    container: ContainerDependency,
) -> dict[str, Any]:
    delivery = container.webhook_repository.retry_delivery(delivery_id)
    if delivery is None:
        raise NotFoundError("Webhook delivery not found or cannot be retried")
    container.webhook_service.wake()
    return asdict(delivery)


@router.get("/meetings/{meeting_id}/insights")
def webhook_insights(
    meeting_id: int,
    container: ContainerDependency,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    if container.meetings.get(meeting_id) is None:
        raise NotFoundError("Meeting not found")
    return [
        asdict(item) for item in container.webhook_repository.insights(meeting_id, limit)
    ]


@router.put("/insights/{insight_id}")
def update_webhook_insight(
    insight_id: str,
    payload: WebhookInsightWrite,
    container: ContainerDependency,
) -> dict[str, Any]:
    insight = container.webhook_repository.update_insight(insight_id, payload.status)
    if insight is None:
        raise NotFoundError("Webhook insight not found")
    if payload.status == "accepted" and not insight.text.strip():
        raise ValidationError("An empty insight cannot be accepted")
    return asdict(insight)
