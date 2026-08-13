from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        # sqlite-vec is an optional accelerator. Embeddings remain ordinary
        # SQLite BLOBs, so the database stays readable without the extension.
        if importlib.util.find_spec("sqlite_vec") is not None:
            try:
                sqlite_vec = importlib.import_module("sqlite_vec")
                connection.enable_load_extension(True)
                sqlite_vec.load(connection)
            except (ImportError, AttributeError, OSError, sqlite3.Error):
                pass
            finally:
                connection.enable_load_extension(False)
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
