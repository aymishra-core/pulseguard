"""SQLite repository. This is the default database."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Self

from pulseguard.exceptions import ConfigValidationError, StorageError
from pulseguard.storage.sql_repo import SqlRepository

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
# How long SQLite waits on a lock. A limit, not a measurement.
_CONNECT_TIMEOUT_SECONDS = 5.0


class SqliteRepository(SqlRepository):
    """SQLite CRUD. One instance owns one connection and should stay on one thread."""

    def __init__(self, path: str | Path) -> None:
        """Open ``path`` (or ``:memory:``) and migrate it."""
        self._path, connection = _open_sqlite(path)
        super().__init__(
            connection,
            mark="?",
            apply_ddl=_apply_sqlite_ddl,
            missing_table=_sqlite_missing_table,
            query_error="SQLite query failed",
        )

    def __enter__(self) -> Self:
        """Return this SQLite repository so callers can take a snapshot."""
        return self

    @property
    def path(self) -> Path | None:
        """Return the database file, or None for an in-memory database."""
        return self._path

    def snapshot_bytes(self) -> bytes:
        """Copy a consistent snapshot with the SQLite backup API and return the bytes."""
        self._require_open()
        fd, name = tempfile.mkstemp(prefix="pulseguard-", suffix=".db")
        os.close(fd)
        temporary = Path(name)
        try:
            clone = sqlite3.connect(temporary)
            try:
                self._conn.backup(clone)
            finally:
                clone.close()
            payload = temporary.read_bytes()
        except sqlite3.Error as exc:
            raise StorageError("could not snapshot the database") from exc
        finally:
            temporary.unlink(missing_ok=True)
        if not payload:
            raise StorageError("database snapshot was empty")
        return payload


def _open_sqlite(path: str | Path) -> tuple[Path | None, sqlite3.Connection]:
    """Open a database file, creating parent directories when needed."""
    if str(path) == ":memory:":
        return None, sqlite3.connect(":memory:")
    file_path = Path(path)
    if file_path.exists() and not file_path.is_file():
        raise ConfigValidationError(f"database path is not a file: {file_path}")
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(file_path, timeout=_CONNECT_TIMEOUT_SECONDS)
    except OSError as exc:
        raise StorageError(f"cannot create database directory: {exc.strerror or exc}") from exc
    except sqlite3.Error as exc:
        raise StorageError("could not open SQLite database") from exc
    return file_path, connection


def _apply_sqlite_ddl(connection: Any) -> None:
    """Run ``schema.sql``. ``executescript`` is idempotent via ``IF NOT EXISTS``."""
    try:
        script = _SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise StorageError("SQLite schema file is missing") from exc
    try:
        connection.executescript(script)
    except sqlite3.Error as exc:
        raise StorageError("could not apply SQLite schema") from exc


def _sqlite_missing_table(exc: BaseException) -> bool:
    """Return True when the version table has not been created yet."""
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower()
