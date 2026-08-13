from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from local_meeting_ai import __version__
from local_meeting_ai.api.routes import router as api_router
from local_meeting_ai.api.webhook_routes import router as webhook_router
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
    AudioRangeExporter,
    DiarizationEngine,
    EmbeddingProvider,
    SummaryEngine,
    TranscriptionEngine,
)

logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    *,
    transcription_engine: TranscriptionEngine | None = None,
    audio_normalizer: AudioNormalizer | None = None,
    audio_capture_backend: AudioCaptureBackend | None = None,
    diarization_engine: DiarizationEngine | None = None,
    summary_engine: SummaryEngine | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    audio_range_exporter: AudioRangeExporter | None = None,
) -> FastAPI:
    resolved_settings = settings or AppSettings()
    container = build_container(
        resolved_settings,
        transcription_engine=transcription_engine,
        audio_normalizer=audio_normalizer,
        audio_capture_backend=audio_capture_backend,
        diarization_engine=diarization_engine,
        summary_engine=summary_engine,
        embedding_provider=embedding_provider,
        audio_range_exporter=audio_range_exporter,
    )

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        logger.info("Starting Meet2Notes services")
        await container.queue.start()
        logger.info("Background job queue is ready")
        await container.webhook_service.start()
        logger.info("Webhook dispatcher is ready")

        async def preload_engines() -> None:
            # Native runtimes own independent workers, but their first loads are
            # sequenced to avoid simultaneous RAM/VRAM allocation spikes.
            for engine_name, preloader in (
                (
                    "selected Live and Final transcription models",
                    container.transcription_service.preload_default,
                ),
                ("selected diarization engine", container.diarization_service.preload_default),
                ("LFM2.5", container.summary_service.preload_default),
                ("selected embedding model", container.rag_service.preload_default),
            ):
                logger.info("Checking %s preload configuration", engine_name)
                try:
                    await preloader()
                except Exception:
                    logger.exception("%s preload failed", engine_name)
                else:
                    logger.info("%s preload check completed", engine_name)

        try:
            # Lifespan startup completes before Uvicorn accepts connections and
            # prints its ready line. This makes the console truthful: ready
            # means the chosen local models have completed their startup load.
            await preload_engines()
            logger.info(
                "Meet2Notes is ready at http://%s:%d",
                resolved_settings.host,
                resolved_settings.port,
            )
            yield
        finally:
            logger.info("Stopping Meet2Notes services")
            await container.capture_service.shutdown()
            await container.queue.stop()
            # Keep the dispatcher alive until producers have stopped so terminal
            # job events cannot be stranded during application shutdown.
            await container.webhook_service.stop()
            background_tasks = list(lifespan_app.state.background_tasks)
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            container.transcription_engine.shutdown()
            container.diarization_service.shutdown()
            container.summary_engine.shutdown()
            embedding_shutdown = getattr(container.embedding_provider, "shutdown", None)
            if callable(embedding_shutdown):
                embedding_shutdown()
            logger.info("Meet2Notes stopped cleanly")

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
    app.state.request_shutdown = None
    app.state.shutdown_requested = False

    allowed_hosts = ["127.0.0.1", "localhost", "testserver", resolved_settings.host]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(dict.fromkeys(allowed_hosts)))

    @app.middleware("http")
    async def security_headers(request: Request, call_next: object) -> object:
        started_at = time.perf_counter()
        response = await call_next(request)  # type: ignore[operator]
        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; worker-src 'self' blob:; connect-src 'self'"
        )
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if elapsed_ms >= 250:
            logger.warning(
                "Slow HTTP request: %s %s completed with %s in %d ms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
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
    latest_static_change = max(
        (
            path.stat().st_mtime_ns
            for path in static_dir.rglob("*")
            if path.is_file()
        ),
        default=0,
    )
    templates.env.globals["asset_version"] = (
        f"{__version__}-{latest_static_change:x}"
    )

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(api_router)
    app.include_router(webhook_router)
    _register_web_routes(app, templates, container)
    return app


def _register_web_routes(
    app: FastAPI, templates: Jinja2Templates, container: Container
) -> None:
    @app.get("/", include_in_schema=False)
    async def dashboard(request: Request) -> object:
        meetings = container.meeting_service.list(limit=100)
        new_meeting = request.query_params.get("new") == "1"
        requested_meeting = request.query_params.get("meeting")
        selected_meeting = None
        if requested_meeting and not new_meeting:
            try:
                requested_id = int(requested_meeting)
            except ValueError:
                requested_id = 0
            selected_meeting = next(
                (meeting for meeting in meetings if meeting.id == requested_id),
                None,
            )
        if selected_meeting is None and meetings and not new_meeting:
            selected_meeting = meetings[0]
        return templates.TemplateResponse(
            request=request,
            name="transcript.html",
            context={
                "version": __version__,
                "page": "dashboard" if new_meeting else "meetings",
                "meeting": selected_meeting,
                "root_workspace": True,
                "default_title": container.transcriptions.next_default_title(),
            },
        )

    @app.get("/meetings", include_in_schema=False)
    async def meetings_library(request: Request) -> object:
        saved_meetings = [
            meeting
            for meeting in container.meeting_service.list(limit=250)
            if meeting.recording_count > 0
        ]
        return templates.TemplateResponse(
            request=request,
            name="meetings.html",
            context={
                "version": __version__,
                "page": "meetings",
                "meetings": saved_meetings,
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
        return RedirectResponse(url=f"/?meeting={meeting.id}", status_code=307)

    @app.get("/settings", include_in_schema=False)
    async def settings_page(request: Request) -> object:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={"version": __version__, "page": "settings"},
        )

    @app.get("/prompt", include_in_schema=False)
    async def prompt_page(request: Request) -> object:
        return templates.TemplateResponse(
            request=request,
            name="prompt.html",
            context={
                "version": __version__,
                "page": "prompt",
                "meetings": container.meeting_service.list(limit=500),
                "selected_meeting_id": request.query_params.get("meeting"),
            },
        )

    @app.get("/speakers", include_in_schema=False)
    async def speakers_page(request: Request) -> object:
        return templates.TemplateResponse(
            request=request,
            name="speakers.html",
            context={"version": __version__, "page": "speakers"},
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
