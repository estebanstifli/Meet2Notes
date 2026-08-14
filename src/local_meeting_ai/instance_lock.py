from __future__ import annotations

import importlib
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO


class AlreadyRunningError(RuntimeError):
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        super().__init__("Meet2Notes is already running")
        self.metadata = metadata or {}


class InstanceLock:
    """Cross-platform, process-owned lock scoped to an application data directory."""

    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self.path = path
        self.metadata_path = path.with_suffix(f"{path.suffix}.json")
        self.metadata = metadata
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _lock_file(handle)
        except OSError as error:
            handle.close()
            raise AlreadyRunningError(self.read_metadata()) from error
        self._handle = handle
        try:
            self.metadata_path.write_text(
                json.dumps(self.metadata, separators=(",", ":")),
                encoding="utf-8",
            )
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()
            self._handle = None

    def read_metadata(self) -> dict[str, Any]:
        try:
            payload = self.metadata_path.read_text(encoding="utf-8")
            decoded = json.loads(payload)
            return decoded if isinstance(decoded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def instance_metadata(*, host: str, port: int) -> dict[str, Any]:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": f"http://{browser_host}:{port}",
        "started_at": datetime.now(UTC).isoformat(),
        "platform": platform.system().lower(),
    }


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        windows_api: Any = msvcrt
        windows_api.locking(handle.fileno(), windows_api.LK_NBLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        windows_api: Any = msvcrt
        windows_api.locking(handle.fileno(), windows_api.LK_UNLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
