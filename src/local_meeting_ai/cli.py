from __future__ import annotations

import argparse
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings


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
    parser.add_argument("--ffmpeg-path", type=Path, help="Path to the FFmpeg executable")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a web browser")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"])
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    settings = AppSettings()
    overrides = {
        key: value
        for key, value in {
            "host": arguments.host,
            "port": arguments.port,
            "data_dir": arguments.data_dir,
            "ffmpeg_path": arguments.ffmpeg_path,
            "log_level": arguments.log_level.upper() if arguments.log_level else None,
            "open_browser": not arguments.no_browser,
        }.items()
        if value is not None
    }
    settings = settings.model_copy(update=overrides)
    url = f"http://{settings.host}:{settings.port}"
    if settings.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
