from __future__ import annotations

import logging
from pathlib import Path

from local_meeting_ai.infrastructure.database.connection import Database

logger = logging.getLogger(__name__)


class MigrationRunner:
    def __init__(self, database: Database, migrations_dir: Path | None = None) -> None:
        self.database = database
        self.migrations_dir = migrations_dir or Path(__file__).with_name("sql")

    def apply(self) -> list[int]:
        applied_now: list[int] = []
        with self.database.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }

            for path in sorted(self.migrations_dir.glob("*.sql")):
                version_text = path.name.split("_", maxsplit=1)[0]
                if not version_text.isdigit():
                    continue
                version = int(version_text)
                if version in applied:
                    continue
                logger.info("Applying database migration %s", path.name)
                connection.executescript(path.read_text(encoding="utf-8"))
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, filename, applied_at)
                    VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (version, path.name),
                )
                applied_now.append(version)
        return applied_now
