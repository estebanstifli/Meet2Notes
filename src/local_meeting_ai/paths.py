from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from local_meeting_ai.config import AppSettings


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    database: Path
    meetings: Path
    models: Path
    cache: Path
    logs: Path
    temp: Path

    @classmethod
    def from_settings(cls, settings: AppSettings) -> AppPaths:
        if settings.data_dir:
            root = settings.data_dir.expanduser().resolve()
        else:
            root = _resolve_portable_data_directory()
            legacy_root = user_data_path(
                "LocalMeet2Resume",
                appauthor=False,
            ).resolve()
            # Keep existing installations usable. Moving this directory
            # automatically would invalidate absolute recording paths stored in
            # the database, so only brand-new installations use the new path.
            if legacy_root.exists() and not root.exists() and not data_location_file().exists():
                root = legacy_root
        return cls(
            root=root,
            database=root / "app.db",
            meetings=root / "meetings",
            models=(
                settings.models_dir.expanduser().resolve()
                if settings.models_dir
                else default_models_directory()
            ),
            cache=root / "cache",
            logs=root / "logs",
            temp=root / "temp",
        )

    def with_models_directory(self, directory: Path) -> AppPaths:
        return AppPaths(
            root=self.root,
            database=self.database,
            meetings=self.meetings,
            models=directory.expanduser().resolve(),
            cache=self.cache,
            logs=self.logs,
            temp=self.temp,
        )

    def ensure(self, *, include_models: bool = True) -> None:
        directories = [
            self.root,
            self.meetings,
            self.cache,
            self.logs,
            self.temp,
        ]
        if include_models:
            directories.append(self.models)
        for path in directories:
            path.mkdir(parents=True, exist_ok=True)


def installation_directory() -> Path:
    """Return the stable folder that owns the Meet2Notes installation."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "local_meeting_ai"
        ).is_dir():
            return candidate

    prefix = Path(sys.prefix).resolve()
    if prefix.name.lower() in {".venv", "venv", "env"}:
        return prefix.parent
    return prefix / ("Meet2Notes" if sys.platform == "win32" else "share/meet2notes")


def default_models_directory() -> Path:
    return (installation_directory() / "models").resolve()


def default_data_directory() -> Path:
    return (installation_directory() / "data").resolve()


def data_location_file() -> Path:
    return installation_directory() / ".meet2notes-data-location.json"


def schedule_data_directory_move(source: Path, target: Path) -> None:
    marker = data_location_file()
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "root": str(target.expanduser().resolve()),
                "source": str(source.expanduser().resolve()),
                "pending": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(marker)


def _resolve_portable_data_directory() -> Path:
    marker = data_location_file()
    if marker.is_file():
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
            target = Path(state["root"]).expanduser().resolve()
            source_value = state.get("source")
            if state.get("pending") and source_value:
                _complete_data_directory_move(Path(source_value).expanduser().resolve(), target)
                marker.write_text(
                    json.dumps({"root": str(target), "pending": False}, indent=2),
                    encoding="utf-8",
                )
            return target
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            # A malformed marker must never prevent Meet2Notes from starting.
            pass

    current = user_data_path("Meet2Notes", appauthor=False).resolve()
    return current if current.exists() else default_data_directory()


def _complete_data_directory_move(source: Path, target: Path) -> None:
    if source == target:
        target.mkdir(parents=True, exist_ok=True)
        return
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    if target.exists() and any(target.iterdir()):
        raise OSError(f"The selected data directory is not empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rmdir()
    shutil.move(str(source), str(target))
    _rewrite_moved_database_paths(target / "app.db", source, target)


def _rewrite_moved_database_paths(database: Path, source: Path, target: Path) -> None:
    if not database.is_file():
        return
    with sqlite3.connect(database) as connection:
        for table, column in (
            ("recordings", "local_path"),
            ("speaker_profiles", "sample_path"),
        ):
            try:
                rows = connection.execute(
                    f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row_id, raw_path in rows:
                try:
                    relative = Path(raw_path).resolve().relative_to(source)
                except (OSError, ValueError):
                    continue
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE id = ?",
                    (str(target / relative), row_id),
                )
