from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.infrastructure.database.repositories import SettingsRepository
from local_meeting_ai.instance_lock import (
    AlreadyRunningError,
    InstanceLock,
    instance_metadata,
)
from local_meeting_ai.paths import AppPaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet2notes",
        description="Run the private Meet2Notes local AI application.",
    )
    parser.add_argument("--host", help="Listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Listen port (default: 8765)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Private application data directory",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="AI model directory (default: <Meet2Notes installation>/models)",
    )
    parser.add_argument("--ffmpeg-path", type=Path, help="Path to the FFmpeg executable")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a web browser")
    parser.add_argument(
        "--browser-delay",
        type=float,
        default=0,
        help="Seconds to wait before opening the browser (default: 0)",
    )
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    if arguments.browser_delay < 0:
        build_parser().error("--browser-delay must be zero or greater")
    settings = AppSettings()
    overrides = {
        key: value
        for key, value in {
            "host": arguments.host,
            "port": arguments.port,
            "data_dir": arguments.data_dir,
            "models_dir": arguments.models_dir,
            "ffmpeg_path": arguments.ffmpeg_path,
            "log_level": arguments.log_level.upper() if arguments.log_level else None,
            "open_browser": not arguments.no_browser,
        }.items()
        if value is not None
    }
    settings = settings.model_copy(update=overrides)
    paths = AppPaths.from_settings(settings)
    paths.ensure(include_models=False)
    settings = _with_saved_port(settings, paths, arguments.port is not None)
    metadata = instance_metadata(host=settings.host, port=settings.port)
    url = str(metadata["url"])
    lock = InstanceLock(
        paths.root / "meet2notes.instance.lock",
        metadata,
    )
    try:
        lock.acquire()
    except AlreadyRunningError as error:
        existing_url = str(error.metadata.get("url") or url)
        print(
            f"Meet2Notes is already running at {existing_url}; no duplicate process was started.",
            file=sys.stderr,
        )
        if settings.open_browser:
            time.sleep(arguments.browser_delay)
            webbrowser.open(existing_url)
        return

    try:
        _ensure_port_available(settings.host, settings.port)
        print(f"Meet2Notes is starting at {url}", flush=True)
        if settings.open_browser:
            browser_timer = threading.Timer(
                max(arguments.browser_delay, 1.0),
                webbrowser.open,
                args=(url,),
            )
            browser_timer.daemon = True
            browser_timer.start()
        app = create_app(settings)
        server = uvicorn.Server(
            uvicorn.Config(
                app=app,
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
                # A browser keeps an SSE status stream open. It is asked to
                # close during shutdown, but this timeout guarantees that a
                # stale browser connection can never keep models resident.
                timeout_graceful_shutdown=10,
            )
        )
        app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
        server.run()
    finally:
        lock.release()


def _ensure_port_available(host: str, port: int) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError as error:
        raise SystemExit(
            f"Cannot start Meet2Notes: {host}:{port} is already in use."
        ) from error
    finally:
        probe.close()


def _with_saved_port(
    settings: AppSettings,
    paths: AppPaths,
    command_line_port: bool,
) -> AppSettings:
    """Use the saved local port unless a startup override was explicitly given."""
    if command_line_port or os.environ.get("M2N_PORT"):
        return settings
    database = Database(paths.database)
    MigrationRunner(database).apply()
    saved = SettingsRepository(database).get_all().get("http_port")
    if isinstance(saved, int) and 1024 <= saved <= 65535:
        return settings.model_copy(update={"port": saved})
    return settings
