from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from local_meeting_ai.paths import AppPaths


def configure_logging(paths: AppPaths, level: str) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    rotating_file = RotatingFileHandler(
        paths.logs / "meet2notes.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    rotating_file.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(rotating_file)
