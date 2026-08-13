from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from local_meeting_ai.paths import AppPaths


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    id: int
    timestamp: str
    level: str
    source: str
    message: str


class ActivityLog:
    """Thread-safe, bounded activity feed for the local user interface."""

    def __init__(self, capacity: int = 500) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_id = 1

    def append(self, level: str, source: str, message: str) -> None:
        with self._lock:
            entry = ActivityEntry(
                id=self._next_id,
                timestamp=datetime.now(UTC).isoformat(),
                level=level.lower(),
                source=source,
                message=message,
            )
            self._next_id += 1
            self._entries.append(entry)

    def snapshot(self, *, after: int = 0, limit: int = 250) -> list[dict[str, Any]]:
        with self._lock:
            entries = [entry for entry in self._entries if entry.id > after][-limit:]
        return [asdict(entry) for entry in entries]


class ActivityLogHandler(logging.Handler):
    def __init__(self, activity_log: ActivityLog) -> None:
        super().__init__(level=logging.INFO)
        self.activity_log = activity_log

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "uvicorn.access":
            return
        try:
            message = record.getMessage().strip()
            if message:
                self.activity_log.append(record.levelname, record.name, message)
        except Exception:
            self.handleError(record)


def configure_logging(paths: AppPaths, level: str) -> ActivityLog:
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

    activity_log = ActivityLog()
    activity_handler = ActivityLogHandler(activity_log)

    root = logging.getLogger()
    for existing_handler in root.handlers[:]:
        root.removeHandler(existing_handler)
        existing_handler.close()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(rotating_file)
    root.addHandler(activity_handler)
    logging.getLogger(__name__).info("Meet2Notes activity logging initialized")
    return activity_log
