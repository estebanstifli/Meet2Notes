from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from local_meeting_ai import __version__
from local_meeting_ai.api.routes import router as api_router
from local_meeting_ai.bootstrap import Container, build_container
from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    ConfirmationRequiredError,
    DomainError,
    NotFoundError,
    UploadTooLargeError,
    ValidationError,
)
from local_meeting_ai.domain.protocols import (
    AudioCaptureBackend,
    AudioNormalizer,
    DiarizationEngine,
    SummaryEngine,
    TranscriptionEngine,
)


def create_app(
    settings: AppSettings | None = None,
    *,
    transcription_engine: TranscriptionEngine | None = None,
    audio_normalizer: AudioNormalizer | None = None,
    audio_capture_backend: AudioCaptureBackend | None = None,
    diarization_engine: DiarizationEngine | None = None,
    summary_engine: SummaryEngine | None = None,
) -> FastAPI:
    resolved_settings = settings or AppSettings()
    container = build_container(
        resolved_settings,
        transcription_engine=transcription_engine,
        audio_normalizer=audio_normalizer,
        audio_capture_backend=audio_capture_backend,
        diarization_engine=diarization_engine,
        summary_engine=summary_engine,
    )

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        await container.queue.start()

        async def preload_engines() -> None:
            # Native runtimes own independent workers, but their first loads are
            # sequenced to avoid simultaneous RAM/VRAM allocation spikes.
            for preloader in (
                container.transcription_service.preload_default,
                container.diarization_service.preload_default,
                container.summary_service.preload_default,
            ):
                await asyncio.gather(preloader(), return_exceptions=True)

        preload_task = asyncio.create_task(
            preload_engines(),
            name="preload-local-ai-engines",
        )
        try:
            yield
        finally:
            await container.capture_service.shutdown()
            await container.queue.stop()
            await asyncio.gather(preload_task, return_exceptions=True)
            background_tasks = list(lifespan_app.state.background_tasks)
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            container.transcription_engine.shutdown()
            container.diarization_engine.shutdown()
            container.summary_engine.shutdown()

    app = FastAPI(
        title="Meet2Notes API",
        description="Private local AI meeting transcription and notes workspace",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.background_tasks = set()

    allowed_hosts = ["127.0.0.1", "localhost", "testserver", resolved_settings.host]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(dict.fromkeys(allowed_hosts)))

    @app.middleware("http")
    async def security_headers(request: Request, call_next: object) -> object:
        response = await call_next(request)  # type: ignore[operator]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'"
        )
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, error: DomainError) -> JSONResponse:
        status_code = 400
        if isinstance(error, NotFoundError):
            status_code = 404
        elif isinstance(error, UploadTooLargeError):
            status_code = 413
        elif isinstance(error, CapabilityUnavailableError):
            status_code = 503
        elif isinstance(error, ConfirmationRequiredError):
            status_code = 409
        elif isinstance(error, ValidationError):
            status_code = 422
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(error), "error": type(error).__name__},
        )

    package_root = Path(__file__).resolve().parents[1]
    static_dir = package_root / "web" / "static"
    template_dir = package_root / "web" / "templates"
    templates = Jinja2Templates(directory=template_dir)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(api_router)
    _register_web_routes(app, templates, container)
    return app


def _register_web_routes(
    app: FastAPI, templates: Jinja2Templates, container: Container
) -> None:
    @app.get("/", include_in_schema=False)
    async def dashboard(request: Request) -> object:
        meetings = container.meeting_service.list(limit=100)
        requested_meeting = request.query_params.get("meeting")
        selected_meeting = None
        if requested_meeting:
            try:
                requested_id = int(requested_meeting)
            except ValueError:
                requested_id = 0
            selected_meeting = next(
                (meeting for meeting in meetings if meeting.id == requested_id),
                None,
            )
        if selected_meeting is None and meetings:
            selected_meeting = meetings[0]
        return templates.TemplateResponse(
            request=request,
            name="transcript.html",
            context={
                "version": __version__,
                "page": "dashboard",
                "meeting": selected_meeting,
                "root_workspace": True,
                "default_title": container.transcriptions.next_default_title(),
            },
        )

    @app.get("/meetings/{meeting_id}", include_in_schema=False)
    async def meeting_detail(request: Request, meeting_id: int) -> object:
        meeting = container.meetings.get(meeting_id)
        if not meeting:
            return templates.TemplateResponse(
                request=request,
                name="not_found.html",
                context={"version": __version__, "page": "meetings"},
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request,
            name="meeting.html",
            context={
                "version": __version__,
                "page": "meetings",
                "meeting": meeting,
                "root_workspace": False,
            },
        )

    @app.get("/settings", include_in_schema=False)
    async def settings_page(request: Request) -> object:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={"version": __version__, "page": "settings"},
        )

    @app.get("/meetings/{meeting_id}/transcript", include_in_schema=False)
    async def transcript_editor(request: Request, meeting_id: int) -> object:
        meeting = container.meetings.get(meeting_id)
        if not meeting:
            return templates.TemplateResponse(
                request=request,
                name="not_found.html",
                context={"version": __version__, "page": "meetings"},
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request,
            name="transcript.html",
            context={
                "version": __version__,
                "page": "meetings",
                "meeting": meeting,
                "default_title": container.transcriptions.next_default_title(),
                "root_workspace": False,
            },
        )
