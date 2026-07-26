from __future__ import annotations

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
            root = user_data_path("Meet2Notes", appauthor=False).resolve()
            legacy_root = user_data_path(
                "LocalMeet2Resume",
                appauthor=False,
            ).resolve()
            # Keep existing installations usable. Moving this directory
            # automatically would invalidate absolute recording paths stored in
            # the database, so only brand-new installations use the new path.
            if legacy_root.exists() and not root.exists():
                root = legacy_root
        return cls(
            root=root,
            database=root / "app.db",
            meetings=root / "meetings",
            models=root / "models",
            cache=root / "cache",
            logs=root / "logs",
            temp=root / "temp",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.meetings,
            self.models,
            self.cache,
            self.logs,
            self.temp,
        ):
            path.mkdir(parents=True, exist_ok=True)
