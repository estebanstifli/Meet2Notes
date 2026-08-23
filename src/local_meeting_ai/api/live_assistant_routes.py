from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.api.schemas import LiveAssistantPreference, SummaryApiKeyUpdate
from local_meeting_ai.application.live_assistant import LIVE_ASSISTANT_DEFAULTS
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.domain.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/live-assistant", tags=["live-assistant"])
ContainerDependency = Annotated[Container, Depends(get_container)]


class LiveAssistantInsightUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["new", "accepted", "dismissed"]


class LiveAssistantQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)


def _settings(container: Container) -> LiveAssistantPreference:
    configured = container.preferences.get_all().get("live_assistant")
    legacy_mode = None
    if isinstance(configured, dict) and "behavior_mode" not in configured:
        legacy_mode = "triggers" if configured.get("trigger_phrases") else "continuous"
    return LiveAssistantPreference.model_validate(
        {
            **LIVE_ASSISTANT_DEFAULTS,
            **(configured if isinstance(configured, dict) else {}),
            **({"behavior_mode": legacy_mode} if legacy_mode else {}),
        }
    )


@router.get("")
def live_assistant_catalog(container: ContainerDependency) -> dict[str, Any]:
    settings = _settings(container)
    capability = container.live_assistant_service.capability()
    return {
        "settings": settings.model_dump(mode="json"),
        "models": capability.get("models", []),
        "capability": capability,
        "credential": container.live_assistant_credentials.status(),
    }


@router.put("/settings")
async def update_live_assistant_settings(
    payload: LiveAssistantPreference,
    container: ContainerDependency,
) -> dict[str, Any]:
    values = payload.model_dump(mode="json")
    models = [
        item
        for item in container.live_assistant_service.capability().get("models", [])
        if isinstance(item, dict)
    ]
    if payload.provider == "local":
        selected = next(
            (item for item in models if item.get("id") == payload.profile_id),
            None,
        )
        if selected is None or payload.profile_id == "litellm-custom":
            raise ValidationError("Unknown local model for the Live AI Assistant")
    container.preferences.update({"live_assistant": values})
    await container.live_assistant_service.reconfigure(container.capture_service.status())
    return live_assistant_catalog(container)


@router.put("/api-key")
def update_live_assistant_api_key(
    payload: SummaryApiKeyUpdate,
    container: ContainerDependency,
) -> dict[str, bool]:
    container.live_assistant_credentials.set(payload.api_key)
    return container.live_assistant_credentials.status()


@router.delete("/api-key")
def delete_live_assistant_api_key(
    container: ContainerDependency,
) -> dict[str, bool]:
    container.live_assistant_credentials.delete()
    return container.live_assistant_credentials.status()


@router.get("/meetings/{meeting_id}")
def live_assistant_meeting(
    meeting_id: int,
    container: ContainerDependency,
    limit: int = Query(default=30, ge=1, le=200),
) -> dict[str, Any]:
    if container.meetings.get(meeting_id) is None:
        raise NotFoundError("Meeting not found")
    config = container.live_assistant_service.config()
    return {
        "enabled": bool(config["enabled"]),
        "behavior_mode": config["behavior_mode"],
        "provider": config["provider"],
        "model": config["model"],
        "runtime": container.live_assistant_service.status(meeting_id),
        "insights": [
            asdict(item) for item in container.live_assistant_repository.insights(meeting_id, limit)
        ],
    }


@router.post("/meetings/{meeting_id}/questions")
async def ask_live_assistant(
    meeting_id: int,
    payload: LiveAssistantQuestion,
    container: ContainerDependency,
) -> dict[str, Any]:
    if container.meetings.get(meeting_id) is None:
        raise NotFoundError("Meeting not found")
    capture = container.capture_service.status()
    if capture is not None and capture.meeting_id == meeting_id:
        container.live_assistant_service.ensure_session(capture)
    return await container.live_assistant_service.ask(meeting_id, payload.question)


@router.put("/insights/{insight_id}")
def update_live_assistant_insight(
    insight_id: str,
    payload: LiveAssistantInsightUpdate,
    container: ContainerDependency,
) -> dict[str, Any]:
    insight = container.live_assistant_repository.update_insight(
        insight_id,
        payload.status,
    )
    if insight is None:
        raise NotFoundError("Live AI Assistant insight not found")
    return asdict(insight)
