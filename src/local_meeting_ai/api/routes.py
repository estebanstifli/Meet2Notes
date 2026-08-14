from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse

from local_meeting_ai import __version__
from local_meeting_ai.adapters.summary.credentials import (
    delete_litellm_api_key,
    secure_storage_status,
    set_litellm_api_key,
)
from local_meeting_ai.api.dependencies import get_container
from local_meeting_ai.api.schemas import (
    AudioCaptureSourceResponse,
    AudioSourcesResponse,
    FindReplaceRequest,
    FindReplaceResponse,
    ImportResponse,
    JobResponse,
    LiveCaptureSessionResponse,
    LiveCaptureStart,
    LiveCaptureStop,
    LiveCaptureStopResponse,
    MeetingCreate,
    MeetingResponse,
    MeetingUpdate,
    ModelDirectoryMoveRequest,
    ModelProfileResponse,
    PluginSettingsUpdate,
    PluginStateUpdate,
    PreferenceResponse,
    PreferenceUpdate,
    PromptRequest,
    RagIndexRequest,
    RagSearchRequest,
    RecordingResponse,
    SegmentUpdate,
    SpeakerNameUpdate,
    SpeakerProfileResponse,
    SpeakerProfileUpdate,
    SpeakerResponse,
    SpeakerSummaryStartResponse,
    SpeakerTurnResponse,
    SummaryApiKeyUpdate,
    SummaryContentUpdate,
    SummaryResponse,
    SummaryStartRequest,
    SummaryStartResponse,
    SummaryTemplateResponse,
    SummaryTemplateWrite,
    TranscriptionCreate,
    TranscriptionDetailResponse,
    TranscriptionResponse,
    TranscriptionStartResponse,
    TranscriptionTitleUpdate,
    TranscriptSegmentResponse,
)
from local_meeting_ai.application.ai_services import (
    DIARIZATION_DEFAULTS,
    SUMMARY_DEFAULTS,
    configured_values,
)
from local_meeting_ai.application.rag import RAG_DEFAULTS
from local_meeting_ai.bootstrap import Container
from local_meeting_ai.domain.enums import JobType
from local_meeting_ai.domain.errors import (
    CapabilityUnavailableError,
    NotFoundError,
    ValidationError,
)
from local_meeting_ai.paths import default_models_directory, schedule_data_directory_move

router = APIRouter(prefix="/api")
ContainerDependency = Annotated[Container, Depends(get_container)]
logger = logging.getLogger(__name__)


@router.get("/rag/status")
async def rag_status(container: ContainerDependency) -> dict[str, Any]:
    return await container.rag_service.status()


@router.post("/rag/index")
async def index_rag(
    payload: RagIndexRequest,
    container: ContainerDependency,
) -> dict[str, Any]:
    result = await container.rag_service.index(
        meeting_id=payload.meeting_id,
        force=payload.force,
    )
    logger.info(
        "Historical RAG index refreshed: %d meetings, %d chunks",
        result["indexed_meetings"],
        result["indexed_chunks"],
    )
    return result


@router.post(
    "/rag/index/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_rag_index_job(
    payload: RagIndexRequest,
    container: ContainerDependency,
) -> JobResponse:
    job = await container.rag_service.start_rebuild(
        meeting_id=payload.meeting_id,
        force=payload.force,
    )
    return JobResponse.model_validate(job)


@router.post("/rag/search")
async def search_rag(
    payload: RagSearchRequest,
    container: ContainerDependency,
) -> dict[str, Any]:
    return await container.rag_service.search(
        payload.query,
        meeting_id=payload.meeting_id,
        top_k=payload.top_k,
        ensure_index=payload.ensure_index,
    )


@router.post("/prompt")
async def prompt_meetings(
    payload: PromptRequest,
    container: ContainerDependency,
) -> dict[str, Any]:
    return await container.prompt_service.ask(
        payload.question,
        meeting_id=payload.meeting_id,
        use_rag=payload.use_rag,
        history=[turn.model_dump() for turn in payload.history],
    )


@router.get("/processing/pipeline")
def processing_pipeline(container: ContainerDependency) -> dict[str, Any]:
    """Describe the post-recording pipeline; Live ASR is intentionally excluded."""
    return container.final_pipeline.description()


@router.get("/plugins")
def plugins(container: ContainerDependency) -> dict[str, Any]:
    return {
        **container.plugin_manager.api_info,
        "plugins": container.plugin_manager.list(),
    }


@router.post("/plugins/rescan")
def rescan_plugins(container: ContainerDependency) -> dict[str, Any]:
    container.plugin_manager.reload()
    logger.info("Plugin entry points rescanned")
    return {
        **container.plugin_manager.api_info,
        "plugins": container.plugin_manager.list(),
    }


@router.put("/plugins/{plugin_id}/state")
def set_plugin_state(
    plugin_id: str,
    payload: PluginStateUpdate,
    container: ContainerDependency,
) -> dict[str, Any]:
    plugin = container.plugin_manager.set_enabled(plugin_id, payload.enabled)
    logger.info(
        "Plugin %s %s",
        plugin_id,
        "enabled" if payload.enabled else "disabled",
    )
    return plugin


@router.put("/plugins/{plugin_id}/settings")
def update_plugin_settings(
    plugin_id: str,
    payload: PluginSettingsUpdate,
    container: ContainerDependency,
) -> dict[str, Any]:
    settings = container.plugin_manager.update_settings(plugin_id, payload.settings)
    logger.info("Plugin %s settings updated", plugin_id)
    return {"plugin_id": plugin_id, "settings": settings}


@router.get("/plugins/providers")
def plugin_providers(container: ContainerDependency) -> dict[str, Any]:
    return {
        **container.plugin_manager.api_info,
        "providers": container.provider_registry.catalog(),
    }


@router.get("/plugins/executions")
def plugin_executions(
    container: ContainerDependency,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    return container.plugin_executions.recent(limit)


@router.get("/health")
def health(container: ContainerDependency) -> dict[str, Any]:
    database_status = "ok"
    try:
        with container.database.read() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        database_status = "error"
    return {
        "status": "ok" if database_status == "ok" else "degraded",
        "version": __version__,
        "database": database_status,
        "queue": "running",
    }


@router.post("/application/shutdown")
def application_shutdown(request: Request) -> dict[str, str]:
    """Ask the CLI-owned Uvicorn server to perform its normal graceful shutdown."""
    request_shutdown = getattr(request.app.state, "request_shutdown", None)
    if not callable(request_shutdown):
        raise CapabilityUnavailableError(
            "This server was not started by Meet2Notes. Stop it with Ctrl+C in its terminal."
        )
    logger.info("Clean shutdown requested from the local interface")
    request.app.state.shutdown_requested = True
    request_shutdown()
    return {
        "status": "shutting_down",
        "message": "Meet2Notes is stopping cleanly.",
    }


@router.get("/info")
def info(container: ContainerDependency) -> dict[str, Any]:
    return {
        "name": container.settings.app_name,
        "version": __version__,
        "platform": platform.system(),
        "python": platform.python_version(),
        "data_directory": str(container.paths.root),
        "models_directory": str(container.paths.models),
        "default_models_directory": str(default_models_directory()),
        "listen_address": f"http://{container.settings.host}:{container.settings.port}",
        "privacy": {
            "telemetry": False,
            "remote_processing": False,
            "local_only_default": container.settings.host in {"127.0.0.1", "localhost"},
        },
    }


@router.post("/storage/{location}/select")
def select_storage_location(location: str, container: ContainerDependency) -> dict[str, str | None]:
    paths = {"data": container.paths.root, "models": container.paths.models}
    target = paths.get(location)
    if not target:
        raise ValidationError("Unknown storage location")
    target.mkdir(parents=True, exist_ok=True)
    try:
        import tkinter as tk
        from tkinter import filedialog

        owner = tk.Tk()
        owner.withdraw()
        owner.attributes("-topmost", True)
        owner.lift()
        owner.focus_force()
        owner.update()
        try:
            selected = filedialog.askdirectory(
                parent=owner,
                initialdir=str(target),
                mustexist=True,
                title=(
                    "Choose the Meet2Notes data folder"
                    if location == "data"
                    else "Choose the Meet2Notes AI models folder"
                ),
            )
        finally:
            owner.destroy()
    except Exception as error:
        raise ValidationError(
            "The native folder picker is unavailable in this desktop session"
        ) from error
    return {"directory": str(Path(selected).resolve()) if selected else None}


@router.post("/models/summary/select-file")
def select_gguf_file(container: ContainerDependency) -> dict[str, str | None]:
    """Open the native file picker without uploading or copying the user's GGUF."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        owner = tk.Tk()
        owner.withdraw()
        owner.attributes("-topmost", True)
        owner.lift()
        owner.focus_force()
        owner.update()
        try:
            selected = filedialog.askopenfilename(
                parent=owner,
                initialdir=str(container.paths.models),
                title="Choose a GGUF model",
                filetypes=(("GGUF models", "*.gguf"), ("All files", "*.*")),
            )
        finally:
            owner.destroy()
    except Exception as error:
        raise ValidationError(
            "The native model file picker is unavailable in this desktop session"
        ) from error
    return {"file": str(Path(selected).resolve()) if selected else None}


@router.post("/models/embeddings/select-file")
def select_embedding_gguf_file(
    container: ContainerDependency,
) -> dict[str, str | None]:
    return select_gguf_file(container)


@router.post("/settings/data-directory/schedule")
def schedule_data_directory(
    payload: ModelDirectoryMoveRequest,
    container: ContainerDependency,
) -> dict[str, Any]:
    source = container.paths.root.resolve()
    target = Path(payload.models_directory).expanduser().resolve()
    if not target.is_absolute():
        raise ValidationError("Choose an absolute data directory")
    if target == source:
        return {
            "directory": str(source),
            "active_directory": str(source),
            "restart_required": False,
        }
    if source in target.parents or target in source.parents:
        raise ValidationError(
            "The new data folder cannot contain, or be inside, the current folder"
        )
    if target.exists() and any(target.iterdir()):
        raise ValidationError("Choose an empty directory for the data transfer")
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".meet2notes-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        schedule_data_directory_move(source, target)
    except OSError as error:
        raise ValidationError(f"Meet2Notes cannot use {target}: {error}") from error
    logger.info("Application data move scheduled from %s to %s", source, target)
    return {
        "directory": str(target),
        "active_directory": str(source),
        "restart_required": True,
    }


@router.get("/diagnostics/report")
def diagnostic_report(container: ContainerDependency) -> dict[str, str]:
    lines = [
        "Meet2Notes diagnostic report",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "No test aborts the report; individual failures are shown as [ERROR].",
    ]

    def section(title: str, collector: Any) -> None:
        lines.extend(("", f"== {title} =="))
        try:
            result = collector()
            if isinstance(result, dict):
                lines.extend(f"{key}: {value}" for key, value in result.items())
            elif isinstance(result, (list, tuple)):
                lines.extend(str(item) for item in result)
            else:
                lines.append(str(result))
        except Exception as error:
            lines.append(f"[ERROR] {type(error).__name__}: {error}")

    def hardware() -> dict[str, Any]:
        details: dict[str, Any] = {
            "CPU": platform.processor() or platform.machine() or "Unknown",
            "Logical CPU cores": os.cpu_count() or "Unknown",
            "Architecture": platform.machine(),
        }
        try:
            import psutil  # type: ignore[import-untyped]

            memory = psutil.virtual_memory()
            details["RAM total"] = f"{memory.total / (1024**3):.2f} GiB"
            details["RAM available"] = f"{memory.available / (1024**3):.2f} GiB"
        except Exception as error:
            details.update(_diagnostic_memory_summary())
            details["RAM detector note"] = f"psutil unavailable ({error}); OS fallback used"
        usage = shutil.disk_usage(container.paths.root)
        details["Data drive free"] = f"{usage.free / (1024**3):.2f} GiB"
        details["GPU"] = _diagnostic_gpu_summary()
        return details

    def software() -> dict[str, Any]:
        return {
            "Operating system": platform.platform(),
            "OS version": platform.version(),
            "Python": sys.version.replace("\n", " "),
            "Executable": sys.executable,
            "Meet2Notes": __version__,
        }

    def dependencies() -> list[str]:
        packages = (
            "fastapi",
            "uvicorn",
            "faster-whisper",
            "ctranslate2",
            "numpy",
            "sounddevice",
            "PyAudioWPatch",
            "sherpa-onnx",
            "llama-cpp-python",
            "psutil",
        )
        versions = []
        for package in packages:
            try:
                versions.append(f"{package}: {importlib.metadata.version(package)}")
            except importlib.metadata.PackageNotFoundError:
                versions.append(f"{package}: not installed")
        return versions

    def checks() -> list[str]:
        results: list[str] = []

        def check(name: str, operation: Any) -> None:
            try:
                value = operation()
                results.append(f"[OK] {name}: {value}")
            except Exception as error:
                results.append(f"[ERROR] {name}: {type(error).__name__}: {error}")

        def database_check() -> Any:
            with container.database.read() as connection:
                return connection.execute("SELECT 1").fetchone()[0]

        check("Database", database_check)
        check("FFmpeg", lambda: json.dumps(container.ffmpeg.capabilities(), default=str))
        check(
            "Transcription engine",
            lambda: json.dumps(container.transcription_service.capability(), default=str),
        )
        check(
            "Diarization engine",
            lambda: json.dumps(container.diarization_service.capability(), default=str),
        )
        check(
            "AI summary engine",
            lambda: json.dumps(container.summary_service.capability(), default=str),
        )
        return results

    def configuration() -> list[str]:
        preferences = container.preferences.get_all()
        sanitized: dict[str, Any] = {}
        for key, value in preferences.items():
            lowered = key.lower()
            if any(secret in lowered for secret in ("password", "secret", "token", "api_key")):
                sanitized[key] = "<redacted>"
            elif key in {"summary_engine", "live_assistant"} and isinstance(value, dict):
                sanitized[key] = {
                    nested_key: ("<redacted>" if "key" in nested_key.lower() else nested_value)
                    for nested_key, nested_value in value.items()
                    if nested_key != "system_prompt"
                }
            else:
                sanitized[key] = value
        return [
            f"Data directory: {container.paths.root}",
            f"Models directory: {container.paths.models}",
            f"Listen address: http://{container.settings.host}:{container.settings.port}",
            "Preferences:",
            json.dumps(sanitized, indent=2, sort_keys=True, default=str),
        ]

    def recent_log() -> list[str]:
        log_files = sorted(
            container.paths.logs.glob("*.log"), key=lambda path: path.stat().st_mtime
        )
        if not log_files:
            return ["No log file found."]
        content = log_files[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        return [f"Source: {log_files[-1]}", *content[-200:]]

    section("Hardware", hardware)
    section("Operating system & runtime", software)
    section("Installed components", dependencies)
    section("Non-blocking component checks", checks)
    section("Meet2Notes configuration", configuration)
    section("Recent application log (last 200 lines)", recent_log)
    filename = f"meet2notes-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.txt"
    return {"report": "\n".join(lines), "filename": filename}


def _diagnostic_gpu_summary() -> str:
    command = shutil.which("nvidia-smi")
    if command:
        try:
            result = subprocess.run(
                [command, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            return result.stdout.strip() or "NVIDIA GPU detected"
        except (OSError, subprocess.SubprocessError):
            pass
    return "No NVIDIA GPU reported (other GPU backends may still be available)"


def _diagnostic_memory_summary() -> dict[str, str]:
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return {
            "RAM total": f"{status.total_physical / (1024**3):.2f} GiB",
            "RAM available": f"{status.available_physical / (1024**3):.2f} GiB",
        }
    sysconf = os.sysconf  # type: ignore[attr-defined]
    page_size = sysconf("SC_PAGE_SIZE")
    physical_pages = sysconf("SC_PHYS_PAGES")
    return {"RAM total": f"{page_size * physical_pages / (1024**3):.2f} GiB"}


@router.get("/capabilities")
def capabilities(container: ContainerDependency) -> dict[str, Any]:
    transcription = container.transcription_service.capability()
    capture = container.capture_service.capability()
    diarization = container.diarization_service.capability()
    summaries = container.summary_service.capability()
    return {
        "ffmpeg": container.ffmpeg.capabilities(),
        "compute": {
            "cpu": True,
            "cuda": bool(transcription.get("cuda_available")),
            "apple_silicon": "not_implemented",
        },
        "features": {
            "media_import": "available",
            "meeting_management": "available",
            "microphone_recording": (
                "available" if capture.get("supports_microphones") else "requires_install"
            ),
            "system_audio_capture": (
                "available" if capture.get("supports_system_audio") else "unavailable"
            ),
            "transcription": (
                "available" if transcription.get("available") else "requires_install"
            ),
            "diarization": (
                "available"
                if diarization.get("available") and diarization.get("installed")
                else "requires_install"
            ),
            "summaries": (
                "available"
                if summaries.get("available") and summaries.get("installed")
                else "requires_install"
            ),
            "chat": "not_implemented",
            "exports": "not_implemented",
        },
        "supported_import_extensions": [
            "wav",
            "mp3",
            "m4a",
            "flac",
            "ogg",
            "aac",
            "mp4",
            "mkv",
            "webm",
            "mov",
        ],
        "transcription": transcription,
        "transcription_engines": _transcription_engine_summaries(transcription),
        "audio_capture": capture,
        "diarization": diarization,
        "summaries": summaries,
    }


def _transcription_engine_summaries(
    transcription: dict[str, Any],
) -> list[dict[str, Any]]:
    engines = transcription.get("engines")
    if not isinstance(engines, dict):
        engines = {"faster-whisper": transcription}
    return [
        {
            "id": engine_id,
            "name": capability.get("display_name", engine_id),
            "kind": capability.get("kind", "local"),
            "available": bool(capability.get("available")),
            "installed": bool(capability.get("installed") or capability.get("installed_models")),
            "supports_live": bool(capability.get("supports_live", engine_id == "faster-whisper")),
            "supports_final": bool(capability.get("supports_final", True)),
            "download_size": capability.get("download_size"),
            "memory_note": capability.get("memory_note"),
        }
        for engine_id, capability in engines.items()
    ]


@router.get("/sidebar-engine")
def sidebar_engine_status(container: ContainerDependency) -> dict[str, Any]:
    """Fast status used by every page's sidebar.

    Full capabilities enumerate native audio devices and query every AI runtime;
    those expensive probes must not run during ordinary navigation.
    """
    transcription = container.transcription_service.capability()
    return {
        "available": bool(transcription.get("available")),
        "cuda_available": bool(transcription.get("cuda_available")),
    }


@router.get("/sidebar-system")
def sidebar_system_status(container: ContainerDependency) -> dict[str, Any]:
    """Compact live engine and memory state for the persistent sidebar card."""
    preferences = container.preferences.get_all()
    transcription = container.transcription_service.capability()
    transcription_engines = transcription.get("engines")
    if not isinstance(transcription_engines, dict):
        transcription_engines = {"faster-whisper": transcription}

    live_id = str(preferences.get("live_transcription_engine") or "faster-whisper")
    final_id = str(preferences.get("final_transcription_engine") or "faster-whisper")
    diarization = container.diarization_service.capability()
    summary = container.summary_service.capability()
    summary_config = preferences.get("summary_engine")
    if not isinstance(summary_config, dict):
        summary_config = {}
    summary_provider = str(summary_config.get("provider") or "local")

    engines = [
        _sidebar_engine_entry(
            "live_transcription",
            live_id,
            transcription_engines.get(live_id, {}),
        ),
        _sidebar_engine_entry(
            "final_transcription",
            final_id,
            transcription_engines.get(final_id, {}),
        ),
        _sidebar_engine_entry(
            "diarization",
            str(diarization.get("engine") or "sherpa-onnx"),
            diarization,
        ),
    ]
    if summary_provider == "disabled":
        engines.append(
            {
                "role": "summary",
                "id": "disabled",
                "name": "AI summaries",
                "status": "disabled",
                "worker_state": "disabled",
                "in_memory": False,
                "active_requests": 0,
                "process_id": None,
                "execution": None,
            }
        )
    elif summary_provider == "local":
        engines.append(
            _sidebar_engine_entry(
                "summary",
                str(summary.get("engine") or "llama-cpp"),
                summary,
            )
        )
    else:
        engines.append(
            {
                "role": "summary",
                "id": summary_provider,
                "name": str(summary_config.get("model") or summary_provider),
                "status": "ready",
                "worker_state": "remote",
                "in_memory": False,
                "active_requests": 0,
                "process_id": None,
                "execution": "remote",
            }
        )

    return {
        "engines": engines,
        "memory": _sidebar_memory_status(),
        "gpus": _sidebar_gpu_status(),
        "process_id": os.getpid(),
    }


def _sidebar_engine_entry(
    role: str,
    engine_id: str,
    capability: Any,
) -> dict[str, Any]:
    details = capability if isinstance(capability, dict) else {}
    worker = details.get("worker")
    if not isinstance(worker, dict):
        worker = {}
    available = bool(details.get("available"))
    installed = bool(details.get("installed") or details.get("installed_models"))
    worker_state = str(worker.get("state") or "idle")
    active_requests = int(worker.get("active_requests") or 0)
    in_memory = bool(worker.get("model_resident") or details.get("loaded_models"))
    if worker_state == "error" or worker.get("last_error"):
        status_value = "error"
    elif active_requests or worker_state in {"loading", "inferencing", "running"}:
        status_value = "running"
    elif not available:
        status_value = "unavailable"
    elif not installed:
        status_value = "not_installed"
    elif in_memory:
        status_value = "ready"
    else:
        status_value = "idle"
    return {
        "role": role,
        "id": engine_id,
        "name": str(details.get("display_name") or engine_id),
        "status": status_value,
        "worker_state": worker_state,
        "in_memory": in_memory,
        "active_requests": active_requests,
        "process_id": os.getpid() if worker.get("dedicated") else None,
        "execution": "thread" if worker.get("dedicated") else None,
        "last_error": worker.get("last_error"),
    }


def _sidebar_memory_status() -> dict[str, Any]:
    try:
        psutil = __import__("psutil")
        memory = psutil.virtual_memory()
        process = psutil.Process(os.getpid()).memory_info()
        return {
            "total_bytes": int(memory.total),
            "available_bytes": int(memory.available),
            "used_bytes": int(memory.used),
            "percent": float(memory.percent),
            "process_bytes": int(process.rss),
        }
    except (ImportError, AttributeError, OSError):
        return {
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "percent": None,
            "process_bytes": None,
        }


def _sidebar_gpu_status() -> list[dict[str, Any]]:
    command = shutil.which("nvidia-smi")
    if not command:
        return []
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus: list[dict[str, Any]] = []
    for index, line in enumerate(result.stdout.splitlines()):
        values = [value.strip() for value in line.split(",")]
        if len(values) != 5:
            continue
        try:
            total_mib, used_mib, free_mib, utilization = (
                int(value) for value in values[1:]
            )
        except ValueError:
            continue
        gpus.append(
            {
                "index": index,
                "name": values[0],
                "total_bytes": total_mib * 1024 * 1024,
                "used_bytes": used_mib * 1024 * 1024,
                "free_bytes": free_mib * 1024 * 1024,
                "utilization_percent": utilization,
            }
        )
    return gpus


@router.get("/audio/sources", response_model=AudioSourcesResponse)
def audio_sources(container: ContainerDependency) -> AudioSourcesResponse:
    return AudioSourcesResponse(
        capability=container.capture_service.capability(),
        sources=[
            AudioCaptureSourceResponse.model_validate(source)
            for source in container.capture_service.sources()
        ],
    )


@router.post("/engines/transcription/prepare")
async def prepare_transcription_engine(
    container: ContainerDependency,
    profile_id: str = Query(default="default", max_length=40),
    download: bool = Query(default=False),
) -> dict[str, Any]:
    """Install/load one catalog model without starting a transcription."""
    profile = container.transcription_profiles.get(profile_id)
    if download:
        logger.info(
            "Downloading %s model %s into %s",
            profile.engine,
            profile.model,
            container.paths.models,
        )
    await container.transcription_engine.prepare(
        profile,
        allow_model_download=download,
    )
    logger.info("%s model %s is ready", profile.engine, profile.model)
    return container.transcription_engine.capability()


@router.post("/engines/transcription/unload")
def unload_transcription_engine(
    container: ContainerDependency,
) -> dict[str, Any]:
    container.transcription_engine.unload()
    return container.transcription_engine.capability()


@router.post("/engines/transcription/uninstall")
async def uninstall_transcription_engine(
    container: ContainerDependency,
    profile_id: str = Query(..., min_length=1, max_length=80),
) -> dict[str, Any]:
    """Remove one inactive local transcription model and release its memory."""
    profile = container.transcription_profiles.get(profile_id)
    for purpose in ("live", "final"):
        selected = container.transcription_profiles.resolve("default", purpose=purpose)
        if selected.engine == profile.engine and selected.model == profile.model:
            raise ValidationError(
                f"Select another {purpose} transcription model before uninstalling "
                f"{profile.display_name}"
            )
    await container.transcription_engine.uninstall(profile)
    logger.info("Uninstalled transcription model %s", profile.display_name)
    return container.transcription_engine.capability()


@router.get("/runtimes/pytorch-cuda")
def pytorch_cuda_runtime(container: ContainerDependency) -> dict[str, Any]:
    """Report whether the private Python environment has CUDA-enabled PyTorch."""
    return container.pytorch_cuda.status()


@router.post("/runtimes/pytorch-cuda/install")
async def install_pytorch_cuda_runtime(container: ContainerDependency) -> dict[str, Any]:
    """Replace CPU-only PyTorch with the CUDA wheel inside Meet2Notes' .venv."""
    return await container.pytorch_cuda.install()


@router.get("/audio/sources/{source_id}/level")
async def audio_source_level(
    source_id: str,
    container: ContainerDependency,
) -> dict[str, Any]:
    level = await asyncio.to_thread(
        container.audio_capture_backend.probe_level,
        source_id,
    )
    return {
        "source_id": source_id,
        "level": max(0.0, min(1.0, float(level))),
    }


@router.post("/engines/diarization/prepare")
async def prepare_diarization_engine(
    container: ContainerDependency,
    download: bool = Query(default=False),
    engine_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, Any]:
    config = configured_values(
        container.preferences,
        "diarization",
        DIARIZATION_DEFAULTS,
    )
    if engine_id is not None:
        config["engine"] = engine_id
    if download:
        logger.info(
            "Downloading %s diarization files into %s",
            config["engine"],
            container.paths.models,
        )
    await container.diarization_service.prepare(
        config,
        allow_model_download=download,
    )
    logger.info("%s diarization engine is ready", config["engine"])
    return container.diarization_service.capability()


@router.post("/engines/diarization/unload")
def unload_diarization_engine(
    container: ContainerDependency,
) -> dict[str, Any]:
    container.diarization_service.unload()
    return container.diarization_service.capability()


@router.post("/engines/diarization/uninstall")
async def uninstall_diarization_engine(
    container: ContainerDependency,
    engine_id: str = Query(..., min_length=1, max_length=80),
) -> dict[str, Any]:
    """Remove one inactive diarization runtime and its local model files."""
    config = configured_values(
        container.preferences,
        "diarization",
        DIARIZATION_DEFAULTS,
    )
    if config["engine"] == engine_id:
        raise ValidationError(
            "Select another speaker engine before uninstalling the active one"
        )
    await container.diarization_service.uninstall(engine_id)
    logger.info("Uninstalled diarization engine %s", engine_id)
    return container.diarization_service.capability()


@router.post("/engines/summary/prepare")
async def prepare_summary_engine(
    container: ContainerDependency,
    download: bool = Query(default=False),
    profile_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, Any]:
    config = configured_values(
        container.preferences,
        "summary_engine",
        SUMMARY_DEFAULTS,
    )
    if profile_id:
        model = next(
            (
                item
                for item in container.summary_engine.capability().get("models", [])
                if item.get("id") == profile_id
                and (item.get("managed") or item.get("external_file"))
            ),
            None,
        )
        if model is None:
            raise ValidationError("The requested local AI model is unavailable")
        config.update(
            {
                "engine": model.get("engine", "llama-cpp"),
                "provider": model.get("provider", "local"),
                "profile_id": profile_id,
                "model": model.get("repository") or model.get("model") or profile_id,
                "model_file": model.get("model_file") or "provider-managed.model",
            }
        )
        if model.get("external_file"):
            persisted = configured_values(
                container.preferences,
                "summary_engine",
                SUMMARY_DEFAULTS,
            )
            config["model_path"] = persisted.get("model_path")
    if download:
        logger.info("Downloading local summary model files into %s", container.paths.models)
    await container.summary_engine.prepare(
        config,
        allow_model_download=download,
    )
    logger.info("Local summary engine is ready")
    return container.summary_engine.capability()


@router.get("/models/summary")
def list_summary_models(container: ContainerDependency) -> list[dict[str, Any]]:
    models = list(container.summary_engine.capability().get("models", []))
    config = configured_values(container.preferences, "summary_engine", SUMMARY_DEFAULTS)
    custom_path = Path(str(config.get("model_path") or "")).expanduser()
    for model in models:
        if model.get("id") == "custom-gguf":
            model["installed"] = custom_path.suffix.lower() == ".gguf" and custom_path.is_file()
            model["configured_path"] = str(custom_path) if str(custom_path) != "." else None
    return models


@router.get("/models/embeddings")
def list_embedding_models(container: ContainerDependency) -> list[dict[str, Any]]:
    config = configured_values(container.preferences, "rag", RAG_DEFAULTS)
    return list(container.embedding_provider.capability(config).get("models", []))


@router.post("/engines/embeddings/prepare")
async def prepare_embedding_engine(
    container: ContainerDependency,
    download: bool = Query(default=False),
    profile_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, Any]:
    config = configured_values(container.preferences, "rag", RAG_DEFAULTS)
    if profile_id:
        model = next(
            (
                item
                for item in container.embedding_provider.capability(config).get("models", [])
                if isinstance(item, dict) and item.get("id") == profile_id
            ),
            None,
        )
        if model is None:
            raise ValidationError("The requested embedding model is unavailable")
        config.update(
            {
                "profile_id": profile_id,
                "embedding_provider": model["provider"],
                "embedding_model": (
                    "BAAI/bge-m3"
                    if profile_id == "bge-m3"
                    else model.get("repository") or model.get("model")
                    or config.get("embedding_model", "")
                ),
            }
        )
    await container.embedding_provider.prepare(
        config,
        allow_model_download=download,
    )
    return container.embedding_provider.capability(config)


@router.post("/engines/embeddings/uninstall")
async def uninstall_embedding_model(
    container: ContainerDependency,
    profile_id: str = Query(..., min_length=1, max_length=80),
) -> dict[str, Any]:
    config = configured_values(container.preferences, "rag", RAG_DEFAULTS)
    await container.embedding_provider.uninstall(profile_id, config)
    return container.embedding_provider.capability(config)


@router.post("/engines/embeddings/unload")
async def unload_embedding_engine(
    container: ContainerDependency,
    profile_id: str | None = Query(default=None, min_length=1, max_length=80),
) -> dict[str, Any]:
    await container.embedding_provider.unload(profile_id)
    config = configured_values(container.preferences, "rag", RAG_DEFAULTS)
    return container.embedding_provider.capability(config)


def _summary_template_response(
    template: Any,
    default_id: int,
) -> SummaryTemplateResponse:
    return SummaryTemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        system_prompt=template.system_prompt,
        user_prompt_template=template.user_prompt_template,
        sections=template.sections,
        is_builtin=template.is_builtin,
        is_default=template.id == default_id,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.get("/summary-templates", response_model=list[SummaryTemplateResponse])
def list_summary_templates(container: ContainerDependency) -> list[SummaryTemplateResponse]:
    configured = container.preferences.get_all().get("default_summary_template_id")
    default = container.summary_templates.default(configured)
    return [
        _summary_template_response(template, default.id)
        for template in container.summary_templates.list()
    ]


@router.post(
    "/summary-templates",
    response_model=SummaryTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_summary_template(
    payload: SummaryTemplateWrite,
    container: ContainerDependency,
) -> SummaryTemplateResponse:
    template = container.summary_templates.create(payload.model_dump())
    default = container.summary_templates.default(
        container.preferences.get_all().get("default_summary_template_id")
    )
    return _summary_template_response(template, default.id)


@router.put("/summary-templates/{template_id}", response_model=SummaryTemplateResponse)
def update_summary_template(
    template_id: int,
    payload: SummaryTemplateWrite,
    container: ContainerDependency,
) -> SummaryTemplateResponse:
    try:
        template = container.summary_templates.update(template_id, payload.model_dump())
    except ValueError as error:
        raise ValidationError(str(error)) from error
    if not template:
        raise NotFoundError("Note format not found")
    default = container.summary_templates.default(
        container.preferences.get_all().get("default_summary_template_id")
    )
    return _summary_template_response(template, default.id)


@router.post(
    "/summary-templates/{template_id}/default",
    response_model=list[SummaryTemplateResponse],
)
def select_default_summary_template(
    template_id: int,
    container: ContainerDependency,
) -> list[SummaryTemplateResponse]:
    if not container.summary_templates.get(template_id):
        raise NotFoundError("Note format not found")
    container.preferences.update({"default_summary_template_id": template_id})
    return list_summary_templates(container)


@router.delete("/summary-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_summary_template(template_id: int, container: ContainerDependency) -> Response:
    template = container.summary_templates.get(template_id)
    if not template:
        raise NotFoundError("Note format not found")
    try:
        container.summary_templates.delete(template_id)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    configured = container.preferences.get_all().get("default_summary_template_id")
    if configured == template_id:
        fallback = container.summary_templates.default(None)
        container.preferences.update({"default_summary_template_id": fallback.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/engines/summary/uninstall")
async def uninstall_summary_model(
    container: ContainerDependency,
    profile_id: str = Query(..., min_length=1, max_length=80),
) -> dict[str, Any]:
    await container.summary_engine.uninstall(profile_id)
    logger.info("Uninstalled local AI model %s", profile_id)
    return container.summary_engine.capability()


@router.get("/settings/summary-api-key")
def summary_api_key_status() -> dict[str, bool]:
    return secure_storage_status()


@router.put("/settings/summary-api-key")
def update_summary_api_key(payload: SummaryApiKeyUpdate) -> dict[str, bool]:
    set_litellm_api_key(payload.api_key)
    return secure_storage_status()


@router.delete("/settings/summary-api-key")
def remove_summary_api_key() -> dict[str, bool]:
    delete_litellm_api_key()
    return secure_storage_status()


@router.post("/engines/summary/unload")
def unload_summary_engine(
    container: ContainerDependency,
) -> dict[str, Any]:
    container.summary_engine.unload()
    return container.summary_engine.capability()


@router.post(
    "/transcriptions/{transcription_id}/diarize",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_diarization(
    transcription_id: int,
    container: ContainerDependency,
) -> JobResponse:
    job = await container.diarization_service.start(transcription_id)
    return JobResponse.model_validate(job)


@router.post(
    "/transcriptions/{transcription_id}/summaries",
    response_model=SummaryStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_summary(
    transcription_id: int,
    container: ContainerDependency,
    payload: SummaryStartRequest | None = None,
) -> SummaryStartResponse:
    summary, job = await container.summary_service.start(
        transcription_id,
        template_id=payload.template_id if payload else None,
    )
    return SummaryStartResponse(
        summary=SummaryResponse.model_validate(summary),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/summaries",
    response_model=list[SummaryResponse],
)
def list_summaries(
    meeting_id: int,
    container: ContainerDependency,
) -> list[SummaryResponse]:
    if not container.meetings.get(meeting_id):
        raise NotFoundError("Meeting not found")
    return [
        SummaryResponse.model_validate(summary)
        for summary in container.summaries.list_for_meeting(meeting_id)
    ]


@router.patch("/summaries/{summary_id}", response_model=SummaryResponse)
def update_summary_content(
    summary_id: int,
    payload: SummaryContentUpdate,
    container: ContainerDependency,
) -> SummaryResponse:
    content = payload.content_markdown.strip()
    if not content:
        raise ValidationError("AI notes cannot be empty")
    try:
        summary = container.summaries.update_content(
            summary_id,
            content,
        )
    except ValueError as error:
        raise ValidationError(str(error)) from error
    if not summary:
        raise NotFoundError("AI notes not found")
    return SummaryResponse.model_validate(summary)


@router.get(
    "/capture/session",
    response_model=LiveCaptureSessionResponse | None,
)
def current_capture(
    container: ContainerDependency,
) -> LiveCaptureSessionResponse | None:
    session = container.capture_service.status()
    return LiveCaptureSessionResponse.model_validate(session) if session else None


@router.post(
    "/capture/sessions",
    response_model=LiveCaptureSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_capture(
    payload: LiveCaptureStart,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    session = await container.capture_service.start(**payload.model_dump())
    return LiveCaptureSessionResponse.model_validate(session)


@router.post(
    "/capture/sessions/{session_id}/pause",
    response_model=LiveCaptureSessionResponse,
)
def pause_capture(
    session_id: str,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    return LiveCaptureSessionResponse.model_validate(container.capture_service.pause(session_id))


@router.post(
    "/capture/sessions/{session_id}/resume",
    response_model=LiveCaptureSessionResponse,
)
def resume_capture(
    session_id: str,
    container: ContainerDependency,
) -> LiveCaptureSessionResponse:
    return LiveCaptureSessionResponse.model_validate(container.capture_service.resume(session_id))


@router.post(
    "/capture/sessions/{session_id}/stop",
    response_model=LiveCaptureStopResponse,
)
async def stop_capture(
    session_id: str,
    container: ContainerDependency,
    payload: LiveCaptureStop | None = None,
) -> LiveCaptureStopResponse:
    (
        session,
        recording,
        import_job,
        transcription,
        transcription_job,
    ) = await container.capture_service.stop(
        session_id,
        **(payload.model_dump() if payload else {}),
    )
    return LiveCaptureStopResponse(
        session=LiveCaptureSessionResponse.model_validate(session),
        recording=RecordingResponse.model_validate(recording),
        import_job=JobResponse.model_validate(import_job),
        transcription=TranscriptionResponse.model_validate(transcription),
        transcription_job=JobResponse.model_validate(transcription_job),
    )


@router.post(
    "/capture/sessions/{session_id}/discard",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def discard_capture(
    session_id: str,
    container: ContainerDependency,
) -> Response:
    await container.capture_service.discard(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings", response_model=PreferenceResponse)
def get_settings(container: ContainerDependency) -> PreferenceResponse:
    return _preference_response(container)


@router.put("/settings", response_model=PreferenceResponse)
async def update_settings(
    payload: PreferenceUpdate,
    request: Request,
    container: ContainerDependency,
) -> PreferenceResponse:
    values = payload.model_dump(exclude_unset=True)
    if (
        values.get("default_summary_template_id") is not None
        and not container.summary_templates.get(values["default_summary_template_id"])
    ):
        raise ValidationError("Note format not found")
    if "models_directory" in values:
        values["models_directory"] = _validate_models_directory(values["models_directory"])
    _validate_provider_preferences(container, values)
    updated = container.preferences.update(values)
    if values:
        logger.info("Settings updated: %s", _setting_change_summary(values))
    if "faster_whisper" in values or "transcription_engine" in values:
        profile = container.transcription_profiles.get("default")
        if profile.keep_model_loaded and profile.installed:
            reload_task = asyncio.create_task(
                container.transcription_engine.prepare(
                    profile,
                    allow_model_download=False,
                ),
                name="reload-default-transcription-engine",
            )
            request.app.state.background_tasks.add(reload_task)
            reload_task.add_done_callback(
                lambda finished: _finish_background_task(
                    finished,
                    request.app.state.background_tasks,
                )
            )
        elif not profile.keep_model_loaded:
            container.transcription_engine.unload()
    if "diarization" in values:
        config = configured_values(
            container.preferences,
            "diarization",
            DIARIZATION_DEFAULTS,
        )
        if (
            config["keep_model_loaded"]
            and container.diarization_service.capability().get("installed")
        ):
            _track_background_task(
                request,
                container.diarization_service.prepare(
                    config,
                    allow_model_download=False,
                ),
                "reload-diarization-engine",
            )
        elif not config["keep_model_loaded"]:
            container.diarization_service.unload()
    if "summary_engine" in values:
        config = configured_values(
            container.preferences,
            "summary_engine",
            SUMMARY_DEFAULTS,
        )
        summary_models = container.summary_engine.capability().get("models", [])
        selected_summary_installed = (config.get("profile_id") == "custom-gguf" and (
            Path(str(config.get("model_path") or "")).expanduser().is_file()
        )) or any(
            item.get("id") == config.get("profile_id") and item.get("installed")
            for item in summary_models
            if isinstance(item, dict)
        )
        if (
            config["provider"] == "local"
            and config["keep_model_loaded"]
            and selected_summary_installed
        ):
            _track_background_task(
                request,
                container.summary_engine.prepare(
                    config,
                    allow_model_download=False,
                ),
                "reload-summary-engine",
            )
        elif config["provider"] != "local" or not config["keep_model_loaded"]:
            container.summary_engine.unload()
    if "rag" in values:
        config = configured_values(container.preferences, "rag", RAG_DEFAULTS)
        capability = container.embedding_provider.capability(config)
        selected = next(
            (
                item
                for item in capability.get("models", [])
                if isinstance(item, dict) and item.get("id") == config.get("profile_id")
            ),
            None,
        )
        prepare = getattr(container.embedding_provider, "prepare", None)
        unload = getattr(container.embedding_provider, "unload", None)
        if (
            bool(config["enabled"])
            and bool(config["keep_model_loaded"])
            and selected is not None
            and bool(selected.get("installed"))
            and callable(prepare)
        ):
            _track_background_task(
                request,
                prepare(config, allow_model_download=False),
                "reload-embedding-engine",
            )
        elif callable(unload):
            _track_background_task(
                request,
                unload(),
                "unload-embedding-engine",
            )
    return _preference_response(container, updated)


def _validate_provider_preferences(
    container: Container,
    values: dict[str, Any],
) -> None:
    current = container.preferences.get_all()
    for purpose in ("live", "final"):
        engine_key = f"{purpose}_transcription_engine"
        profile_key = f"{purpose}_transcription_profile"
        if engine_key not in values and profile_key not in values:
            continue
        engine_id = str(values.get(engine_key) or current.get(engine_key) or "")
        profile_id = str(values.get(profile_key) or current.get(profile_key) or "default")
        profile = container.transcription_profiles.get(profile_id)
        if profile.engine != engine_id:
            raise ValidationError(
                f"Transcription profile {profile_id} belongs to {profile.engine}, "
                f"not {engine_id}"
            )
        supported = profile.supports_live if purpose == "live" else profile.supports_final
        if not supported:
            raise ValidationError(
                f"{profile.display_name} cannot be used for {purpose} transcription"
            )

    diarization = values.get("diarization")
    if isinstance(diarization, dict) and "engine" in diarization:
        current_diarization = current.get("diarization")
        config = {
            **DIARIZATION_DEFAULTS,
            **(current_diarization if isinstance(current_diarization, dict) else {}),
            **diarization,
        }
        engine_id = str(config.get("engine") or "")
        registered = {
            item.descriptor.id
            for item in container.provider_registry.registrations("diarization")
        }
        if engine_id not in registered:
            raise ValidationError(f"Unknown diarization provider: {engine_id}")

    summary = values.get("summary_engine")
    if isinstance(summary, dict) and ({"engine", "profile_id"} & summary.keys()):
        current_summary = current.get("summary_engine")
        config = {
            **SUMMARY_DEFAULTS,
            **(current_summary if isinstance(current_summary, dict) else {}),
            **summary,
        }
        profile_id = str(config.get("profile_id") or "")
        engine_id = str(config.get("engine") or "llama-cpp")
        models = container.summary_engine.capability().get("models", [])
        selected = next(
            (
                item
                for item in models
                if isinstance(item, dict) and item.get("id") == profile_id
            ),
            None,
        )
        if selected is None:
            raise ValidationError(f"Unknown summary model profile: {profile_id}")
        if selected.get("engine", "llama-cpp") != engine_id:
            raise ValidationError(
                f"Summary profile {profile_id} belongs to "
                f"{selected.get('engine', 'llama-cpp')}, not {engine_id}"
            )

    rag = values.get("rag")
    if isinstance(rag, dict) and ({"profile_id", "embedding_provider"} & rag.keys()):
        current_rag = current.get("rag")
        config = {
            **RAG_DEFAULTS,
            **(current_rag if isinstance(current_rag, dict) else {}),
            **rag,
        }
        profile_id = str(config.get("profile_id") or "")
        provider_id = str(config.get("embedding_provider") or "")
        models = container.embedding_provider.capability(config).get("models", [])
        selected = next(
            (
                item
                for item in models
                if isinstance(item, dict) and item.get("id") == profile_id
            ),
            None,
        )
        if selected is None:
            raise ValidationError(f"Unknown embedding model profile: {profile_id}")
        if selected.get("provider") != provider_id:
            raise ValidationError(
                f"Embedding profile {profile_id} belongs to "
                f"{selected.get('provider')}, not {provider_id}"
            )


@router.post("/settings/models-directory/move", response_model=PreferenceResponse)
async def move_models_directory(
    payload: ModelDirectoryMoveRequest,
    container: ContainerDependency,
) -> PreferenceResponse:
    """Move the portable model cache, then request a clean worker restart.

    Engine workers keep native model handles open, particularly on Windows.  They
    are unloaded before moving and the running container intentionally keeps its
    old immutable path until the user restarts the app.
    """
    source = container.paths.models.resolve()
    target = Path(_validate_models_directory(payload.models_directory)).resolve()
    if source == target:
        return _preference_response(
            container,
            container.preferences.update({"models_directory": str(target)}),
        )
    if source in target.parents:
        raise ValidationError("The new model directory cannot be inside the current one")
    target_has_files = target.exists() and any(target.iterdir())
    if target_has_files and not payload.overwrite_existing:
        raise ValidationError(
            "The selected model folder already contains files. Confirm overwriting "
            "existing model files before moving."
        )

    try:
        container.transcription_engine.unload()
        container.diarization_service.unload()
        container.summary_engine.unload()
        container.live_assistant_service.engine.unload()
        unload_embeddings = getattr(container.embedding_provider, "unload", None)
        if callable(unload_embeddings):
            await unload_embeddings()
        if source.exists() and any(source.iterdir()):
            if target_has_files:
                # Preserve unrelated files in the selected folder, while files
                # with the same model-cache path are intentionally replaced by
                # the source cache after the user's explicit confirmation.
                shutil.copytree(source, target, dirs_exist_ok=True)
                shutil.rmtree(source)
            else:
                # shutil.move creates a nested source folder when the
                # destination exists, so remove only the empty directory made
                # during directory validation.
                target.rmdir()
                shutil.move(str(source), str(target))
        else:
            target.mkdir(parents=True, exist_ok=True)
        updated = container.preferences.update({"models_directory": str(target)})
    except OSError as error:
        raise ValidationError(
            f"Meet2Notes could not move the models to {target}: {error}"
        ) from error
    logger.info("Local AI models moved from %s to %s; restart required", source, target)
    return _preference_response(container, updated)


@router.post("/settings/models-directory/inspect")
def inspect_models_directory(
    payload: ModelDirectoryMoveRequest,
    container: ContainerDependency,
) -> dict[str, Any]:
    """Report whether an explicit confirmation is needed before a model move."""
    source = container.paths.models.resolve()
    target = Path(_validate_models_directory(payload.models_directory)).resolve()
    if source != target and source in target.parents:
        raise ValidationError("The new model directory cannot be inside the current one")
    entries = list(target.iterdir()) if target.exists() else []
    return {
        "directory": str(target),
        "requires_overwrite_confirmation": source != target and bool(entries),
        "existing_entry_count": len(entries),
    }


def _preference_response(
    container: Container,
    values: dict[str, Any] | None = None,
) -> PreferenceResponse:
    preferences = dict(values or container.preferences.get_all())
    active = container.paths.models.resolve()
    runtime_override = container.settings.models_dir is not None
    configured = preferences.get("models_directory")
    desired = (
        active
        if runtime_override
        else (
            Path(configured).expanduser().resolve()
            if isinstance(configured, str) and configured.strip()
            else default_models_directory()
        )
    )
    preferences.update(
        {
            "models_directory": str(desired),
            "active_models_directory": str(active),
            "models_directory_restart_required": desired != active,
            "models_directory_runtime_override": runtime_override,
            "http_port": int(preferences.get("http_port") or container.settings.port),
        }
    )
    return PreferenceResponse.model_validate(preferences)


def _validate_models_directory(value: str | None) -> str:
    candidate = Path(value).expanduser() if value else default_models_directory()
    if value and not candidate.is_absolute():
        raise ValidationError("The model directory must be an absolute path")
    requested = candidate.resolve()
    try:
        requested.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".meet2notes-write-test-", dir=requested):
            pass
    except OSError as error:
        raise ValidationError(
            f"Meet2Notes cannot write to the selected model directory: {requested}"
        ) from error
    return str(requested)


def _track_background_task(
    request: Request,
    coroutine: Any,
    name: str,
) -> None:
    task = asyncio.create_task(coroutine, name=name)
    request.app.state.background_tasks.add(task)
    task.add_done_callback(
        lambda finished: _finish_background_task(
            finished,
            request.app.state.background_tasks,
        )
    )


def _setting_change_summary(values: dict[str, Any]) -> str:
    changes: list[str] = []
    for key, value in sorted(values.items()):
        if isinstance(value, dict):
            nested = ", ".join(
                f"{nested_key}={nested_value!r}"
                for nested_key, nested_value in sorted(value.items())
                if nested_key != "system_prompt"
            )
            changes.append(f"{key}({nested})")
        else:
            changes.append(f"{key}={value!r}")
    return "; ".join(changes)


def _finish_background_task(
    task: asyncio.Task[Any],
    tasks: set[asyncio.Task[Any]],
) -> None:
    tasks.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Background transcription-engine reload failed")


@router.get("/meetings", response_model=list[MeetingResponse])
def list_meetings(
    container: ContainerDependency,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[MeetingResponse]:
    return [
        MeetingResponse.model_validate(item)
        for item in container.meeting_service.list(search=search, limit=limit)
    ]


@router.post("/meetings", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
def create_meeting(payload: MeetingCreate, container: ContainerDependency) -> MeetingResponse:
    meeting = container.meeting_service.create(**payload.model_dump())
    return MeetingResponse.model_validate(meeting)


@router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
def get_meeting(meeting_id: int, container: ContainerDependency) -> MeetingResponse:
    return MeetingResponse.model_validate(container.meeting_service.get(meeting_id))


@router.patch("/meetings/{meeting_id}", response_model=MeetingResponse)
def update_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    container: ContainerDependency,
) -> MeetingResponse:
    values = payload.model_dump(exclude_unset=True)
    return MeetingResponse.model_validate(container.meeting_service.update(meeting_id, values))


@router.delete("/meetings/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: int, container: ContainerDependency) -> Response:
    container.meeting_service.delete(meeting_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/meetings/{meeting_id}/import",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_media(
    meeting_id: int,
    container: ContainerDependency,
    file: Annotated[UploadFile, File(...)],
) -> ImportResponse:
    recording, job = await container.import_service.import_media(meeting_id, file)
    return ImportResponse(
        recording=RecordingResponse.model_validate(recording),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/recordings",
    response_model=list[RecordingResponse],
)
def list_recordings(meeting_id: int, container: ContainerDependency) -> list[RecordingResponse]:
    if not container.meetings.get(meeting_id):
        raise NotFoundError("Meeting not found")
    return [
        RecordingResponse.model_validate(item)
        for item in container.recordings.list_for_meeting(meeting_id)
    ]


@router.get("/recordings/{recording_id}/media")
def recording_media(recording_id: int, container: ContainerDependency) -> FileResponse:
    recording = container.recordings.get(recording_id)
    if not recording:
        raise NotFoundError("Recording not found")
    path = Path(recording.local_path).resolve()
    storage_root = container.paths.meetings.resolve()
    if not path.is_relative_to(storage_root) or not path.is_file():
        raise NotFoundError("Recording file not found")
    return FileResponse(
        path,
        media_type=recording.media_type or "application/octet-stream",
        filename=recording.original_filename or path.name,
        content_disposition_type="inline",
    )


@router.get(
    "/models/transcription",
    response_model=list[ModelProfileResponse],
)
def transcription_models(
    container: ContainerDependency,
) -> list[ModelProfileResponse]:
    return [
        ModelProfileResponse.model_validate(profile)
        for profile in container.transcription_service.model_profiles()
    ]


@router.post(
    "/meetings/{meeting_id}/transcriptions",
    response_model=TranscriptionStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_transcription(
    meeting_id: int,
    payload: TranscriptionCreate,
    container: ContainerDependency,
) -> TranscriptionStartResponse:
    transcription, job = await container.transcription_service.start(
        meeting_id,
        **payload.model_dump(),
    )
    return TranscriptionStartResponse(
        transcription=TranscriptionResponse.model_validate(transcription),
        job=JobResponse.model_validate(job),
    )


@router.get(
    "/meetings/{meeting_id}/transcriptions",
    response_model=list[TranscriptionResponse],
)
def list_transcriptions(
    meeting_id: int,
    container: ContainerDependency,
) -> list[TranscriptionResponse]:
    return [
        TranscriptionResponse.model_validate(item)
        for item in container.transcription_service.list(meeting_id)
    ]


@router.get(
    "/transcriptions/{transcription_id}",
    response_model=TranscriptionDetailResponse,
)
def get_transcription(
    transcription_id: int,
    container: ContainerDependency,
) -> TranscriptionDetailResponse:
    transcription, segments = container.transcription_service.get(transcription_id)
    speakers, speaker_turns = container.speaker_service.list_for_transcription(transcription_id)
    return TranscriptionDetailResponse(
        transcription=TranscriptionResponse.model_validate(transcription),
        segments=[TranscriptSegmentResponse.model_validate(segment) for segment in segments],
        speakers=[SpeakerResponse.model_validate(speaker) for speaker in speakers],
        speaker_turns=[SpeakerTurnResponse.model_validate(turn) for turn in speaker_turns],
    )


@router.patch("/speakers/{speaker_id}", response_model=SpeakerResponse)
def update_speaker(
    speaker_id: int,
    payload: SpeakerNameUpdate,
    container: ContainerDependency,
) -> SpeakerResponse:
    speaker = container.speaker_service.rename(speaker_id, payload.display_name)
    return SpeakerResponse.model_validate(speaker)


@router.post(
    "/transcriptions/{transcription_id}/speakers/{speaker_id}/remember",
    response_model=SpeakerProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def remember_speaker_voice(
    transcription_id: int, speaker_id: int, container: ContainerDependency
) -> SpeakerProfileResponse:
    profile = await container.speaker_service.create_profile_from_speaker(
        transcription_id, speaker_id
    )
    return SpeakerProfileResponse.model_validate(profile)


@router.get("/speaker-profiles", response_model=list[SpeakerProfileResponse])
def list_speaker_profiles(
    container: ContainerDependency,
    search: str | None = Query(default=None, max_length=100),
) -> list[SpeakerProfileResponse]:
    return [
        SpeakerProfileResponse.model_validate(item)
        for item in container.speaker_service.profiles_list(search)
    ]


@router.post(
    "/speaker-profiles", response_model=SpeakerProfileResponse, status_code=status.HTTP_201_CREATED
)
async def create_speaker_profile(
    container: ContainerDependency,
    name: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
) -> SpeakerProfileResponse:
    profile = await container.speaker_service.create_profile_from_upload(name, file)
    return SpeakerProfileResponse.model_validate(profile)


@router.patch("/speaker-profiles/{profile_id}", response_model=SpeakerProfileResponse)
def update_speaker_profile(
    profile_id: int, payload: SpeakerProfileUpdate, container: ContainerDependency
) -> SpeakerProfileResponse:
    return SpeakerProfileResponse.model_validate(
        container.speaker_service.rename_profile(profile_id, payload.name)
    )


@router.delete("/speaker-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_speaker_profile(profile_id: int, container: ContainerDependency) -> Response:
    container.speaker_service.delete_profile(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/speaker-profiles/meetings", response_model=list[MeetingResponse])
def speaker_profile_meetings(
    container: ContainerDependency,
    profile_ids: list[int] = Query(default=[]),
) -> list[MeetingResponse]:
    return [
        MeetingResponse.model_validate(item)
        for item in container.speaker_service.profile_meetings(profile_ids)
    ]


@router.post(
    "/transcriptions/{transcription_id}/speakers/{speaker_id}/summary",
    response_model=SpeakerSummaryStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_speaker_summary(
    transcription_id: int,
    speaker_id: int,
    container: ContainerDependency,
) -> SpeakerSummaryStartResponse:
    speaker, job = await container.summary_service.start_speaker(
        transcription_id,
        speaker_id,
    )
    return SpeakerSummaryStartResponse(
        speaker=SpeakerResponse.model_validate(speaker),
        job=JobResponse.model_validate(job),
    )


@router.get("/transcriptions/{transcription_id}/speakers/{speaker_id}/text")
def export_speaker_text(
    transcription_id: int,
    speaker_id: int,
    container: ContainerDependency,
) -> Response:
    content, filename = container.speaker_service.export_text(
        transcription_id,
        speaker_id,
    )
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/transcriptions/{transcription_id}/speakers/{speaker_id}/audio")
async def export_speaker_audio(
    transcription_id: int,
    speaker_id: int,
    container: ContainerDependency,
    output_format: str = Query(default="wav", alias="format", pattern="^(wav|mp3)$"),
) -> FileResponse:
    path, filename, media_type = await container.speaker_service.export_audio(
        transcription_id,
        speaker_id,
        output_format,
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment",
    )


@router.patch(
    "/transcriptions/{transcription_id}",
    response_model=TranscriptionResponse,
)
def update_transcription(
    transcription_id: int,
    payload: TranscriptionTitleUpdate,
    container: ContainerDependency,
) -> TranscriptionResponse:
    transcription = container.transcription_service.update_title(
        transcription_id,
        payload.title,
    )
    return TranscriptionResponse.model_validate(transcription)


@router.patch(
    "/transcript-segments/{segment_id}",
    response_model=TranscriptSegmentResponse,
)
def update_transcript_segment(
    segment_id: int,
    payload: SegmentUpdate,
    container: ContainerDependency,
) -> TranscriptSegmentResponse:
    segment = container.transcription_service.update_segment(segment_id, payload.text)
    return TranscriptSegmentResponse.model_validate(segment)


@router.post(
    "/transcriptions/{transcription_id}/activate",
    response_model=TranscriptionResponse,
)
def activate_transcription(
    transcription_id: int,
    container: ContainerDependency,
) -> TranscriptionResponse:
    transcription = container.transcription_service.activate(transcription_id)
    return TranscriptionResponse.model_validate(transcription)


@router.post(
    "/transcriptions/{transcription_id}/find-replace",
    response_model=FindReplaceResponse,
)
def find_replace(
    transcription_id: int,
    payload: FindReplaceRequest,
    container: ContainerDependency,
) -> FindReplaceResponse:
    replacements = container.transcription_service.find_replace(
        transcription_id,
        **payload.model_dump(),
    )
    return FindReplaceResponse(replacements=replacements)


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    container: ContainerDependency,
    meeting_id: int | None = None,
    active_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[JobResponse]:
    return [
        JobResponse.model_validate(item)
        for item in container.jobs.list(
            meeting_id=meeting_id,
            active_only=active_only,
            limit=limit,
        )
    ]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, container: ContainerDependency) -> JobResponse:
    job = container.jobs.get(job_id)
    if not job:
        raise NotFoundError("Job not found")
    return JobResponse.model_validate(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, container: ContainerDependency) -> JobResponse:
    existing = container.jobs.get(job_id)
    if not existing:
        raise NotFoundError("Job not found")
    if existing.status not in {"queued", "running", "paused"}:
        raise ValidationError(f"A {existing.status.value} job cannot be cancelled")
    job = container.jobs.request_cancel(job_id)
    assert job is not None
    if existing.job_type == JobType.TRANSCRIBE:
        transcription_id = existing.payload.get("transcription_id")
        if isinstance(transcription_id, int):
            container.transcriptions.set_status(transcription_id, "cancelled")
    return JobResponse.model_validate(job)


@router.get("/events")
async def events(request: Request, container: ContainerDependency) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        previous = ""
        last_activity_id = 0
        while (
            not request.app.state.shutdown_requested
            and not await request.is_disconnected()
        ):
            snapshot = [
                JobResponse.model_validate(job).model_dump(mode="json")
                for job in container.jobs.list(limit=50)
            ]
            encoded = json.dumps(snapshot, separators=(",", ":"))
            jobs_changed = encoded != previous
            if jobs_changed:
                yield f"event: jobs\ndata: {encoded}\n\n"
                previous = encoded
            activity = container.activity_log.snapshot(after=last_activity_id)
            if activity:
                last_activity_id = activity[-1]["id"]
                encoded_activity = json.dumps(activity, separators=(",", ":"))
                yield f"event: activity\ndata: {encoded_activity}\n\n"
            if not jobs_changed and not activity:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/activity")
def activity(
    container: ContainerDependency,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=250, ge=1, le=500),
) -> list[dict[str, Any]]:
    return container.activity_log.snapshot(after=after, limit=limit)
