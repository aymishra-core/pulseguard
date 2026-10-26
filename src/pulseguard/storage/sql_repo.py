"""SQL shared by the SQLite and MySQL repositories.

Each backend opens its own connection and passes the placeholder it uses.
One connection is not shared across threads. The engine's writer comes later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError, StorageError
from pulseguard.logging_setup import LOGGER_NAME
from pulseguard.storage.base import Repository
from pulseguard.storage.records import (
    DEFAULT_CHUNK_SIZE,
    SCHEMA_VERSION,
    MonitorRecord,
    format_timestamp,
    monitor_from_row,
    require_chunk_size,
    require_enabled,
    require_interval,
    require_monitor_name,
    require_row_id,
    result_from_row,
    result_params,
)

LOGGER = logging.getLogger(f"{LOGGER_NAME}.storage")
_ApplyDdl = Callable[[Any], None]
_MissingTable = Callable[[BaseException], bool]
_RESULT_SELECT = (
    "SELECT monitor, kind, ok, latency_ms, message, severity, checked_at, status_code FROM results"
)


class SqlRepository(Repository):
    """CRUD against a DB-API connection. Subclasses own the dialect."""

    def __init__(
        self,
        connection: Any,
        *,
        mark: str,
        apply_ddl: _ApplyDdl,
        missing_table: _MissingTable,
        query_error: str,
    ) -> None:
        """Store the open connection and apply the schema when it is behind."""
        self._conn = connection
        self._mark = mark
        self._apply_ddl = apply_ddl
        self._missing_table = missing_table
        self._query_error = query_error
        self._txn = False
        self._closed = False
        self._schema_version = 0
        try:
            self.migrate()
        except Exception:
            self._conn.close()
            self._closed = True
            raise

    def close(self) -> None:
        """Close the connection. An open transaction is rolled back."""
        if self._closed:
            return
        try:
            if self._txn:
                self._conn.rollback()
        finally:
            self._conn.close()
            self._closed = True
            self._txn = False

    def __enter__(self) -> SqlRepository:
        """Return this repository. Prefer the context manager over ``__del__``."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the connection when the ``with`` block ends."""
        self.close()

    @property
    def schema_version(self) -> int:
        """Return the schema version recorded for this connection."""
        return self._schema_version

    def migrate(self) -> int:
        """Apply schema version 1 once. A second call is a no-op.

        Idempotent migrations: the version table skips work that already ran,
        and ``CREATE TABLE IF NOT EXISTS`` lets a crashed apply finish later.
        """
        self._require_open()
        if self._txn:
            raise StorageError("cannot migrate inside a transaction")
        current = self._version()
        if current >= SCHEMA_VERSION:
            if current > SCHEMA_VERSION:
                raise StorageError(f"unsupported schema version {current}")
            self._schema_version = current
            return current
        self._rollback_quietly()
        self._apply_ddl(self._conn)
        version_slots = self._slots(2)
        self._change(
            f"INSERT INTO schema_migrations (version, applied_at) VALUES ({version_slots})",
            (SCHEMA_VERSION, format_timestamp(datetime.now(UTC))),
        )
        self._conn.commit()
        self._schema_version = SCHEMA_VERSION
        LOGGER.info("schema ready version=%s", SCHEMA_VERSION)
        return SCHEMA_VERSION

    def add_result(self, result: CheckResult) -> int:
        """Insert one result and return the new id."""
        _row_id, _count = self._change(
            "INSERT INTO results "
            "(monitor, kind, ok, latency_ms, message, severity, checked_at, status_code) "
            f"VALUES ({self._slots(8)})",
            result_params(result),
        )
        if _row_id < 1:
            raise StorageError("database did not return a result id")
        self._commit_if_needed()
        return _row_id

    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return every stored result for ``monitor``, oldest first."""
        return list(self.iter_results(monitor))

    def iter_results(
        self,
        monitor: str | None = None,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> Iterator[CheckResult]:
        """Yield pages with ``LIMIT`` and ``OFFSET``. Generator with ``yield``."""
        size = require_chunk_size(chunk_size)
        chosen = None if monitor is None else require_monitor_name(monitor)
        offset = 0
        while True:
            rows = self._fetch_page(chosen, size, offset)
            if not rows:
                return
            for row in rows:
                yield result_from_row(row)
            if len(rows) < size:
                return
            offset += size

    def delete_result(self, result_id: int) -> bool:
        """Delete one result row."""
        chosen = require_row_id(result_id)
        _row_id, count = self._change(
            f"DELETE FROM results WHERE id = {self._mark}",
            (chosen,),
        )
        self._commit_if_needed()
        return count == 1

    def delete_older_than(self, cutoff: datetime) -> int:
        """Delete rows whose stored timestamp is strictly before ``cutoff``."""
        stamp = format_timestamp(cutoff)
        _row_id, count = self._change(
            f"DELETE FROM results WHERE checked_at < {self._mark}",
            (stamp,),
        )
        self._commit_if_needed()
        return count

    def save_monitor(self, record: MonitorRecord) -> None:
        """Insert a catalog row, or update it when the name already exists."""
        if not isinstance(record, MonitorRecord):
            raise ConfigValidationError("monitor must be a MonitorRecord")
        existing = self._monitor_row(record.name)
        if existing is None:
            self._change(
                "INSERT INTO monitors (name, kind, target, interval_seconds, enabled) "
                f"VALUES ({self._slots(5)})",
                _monitor_params(record),
            )
        else:
            mark = self._mark
            self._change(
                f"UPDATE monitors SET kind = {mark}, target = {mark}, "
                f"interval_seconds = {mark}, enabled = {mark} WHERE name = {mark}",
                (
                    record.kind,
                    record.target,
                    record.interval_seconds,
                    1 if record.enabled else 0,
                    record.name,
                ),
            )
        self._commit_if_needed()

    def update_monitor(
        self,
        name: str,
        *,
        interval_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Update the interval, the enabled flag, or both."""
        if interval_seconds is None and enabled is None:
            raise ConfigValidationError("update_monitor needs interval_seconds or enabled")
        current = self._load_monitor(name)
        interval = current.interval_seconds if interval_seconds is None else interval_seconds
        flag = current.enabled if enabled is None else enabled
        updated = MonitorRecord(
            name=current.name,
            kind=current.kind,
            target=current.target,
            interval_seconds=require_interval(interval),
            enabled=require_enabled(flag),
        )
        mark = self._mark
        self._change(
            f"UPDATE monitors SET interval_seconds = {mark}, enabled = {mark} WHERE name = {mark}",
            (updated.interval_seconds, 1 if updated.enabled else 0, updated.name),
        )
        self._commit_if_needed()

    def monitors(self) -> list[MonitorRecord]:
        """Return catalog rows ordered by name."""
        rows = self._fetchall(
            "SELECT name, kind, target, interval_seconds, enabled FROM monitors ORDER BY name ASC"
        )
        return [monitor_from_row(row) for row in rows]

    def delete_monitor(self, name: str) -> bool:
        """Delete a catalog row and leave its results stored."""
        chosen = require_monitor_name(name)
        _row_id, count = self._change(
            f"DELETE FROM monitors WHERE name = {self._mark}",
            (chosen,),
        )
        self._commit_if_needed()
        return count == 1

    def transaction(self) -> AbstractContextManager[None]:
        """Commit on success and roll back when the body raises."""
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Hold a transaction flag so nested calls share the outer commit."""
        self._require_open()
        if self._txn:
            yield
            return
        self._txn = True
        try:
            yield
            self._conn.commit()
        except Exception:
            self._rollback_quietly()
            raise
        finally:
            self._txn = False

    def _fetch_page(
        self,
        monitor: str | None,
        limit: int,
        offset: int,
    ) -> list[tuple[object, ...]]:
        """Return one page of result rows, oldest first."""
        if monitor is None:
            sql = (
                f"{_RESULT_SELECT} ORDER BY checked_at ASC, id ASC "
                f"LIMIT {self._mark} OFFSET {self._mark}"
            )
            params: tuple[object, ...] = (limit, offset)
        else:
            sql = (
                f"{_RESULT_SELECT} WHERE monitor = {self._mark} "
                f"ORDER BY checked_at ASC, id ASC LIMIT {self._mark} OFFSET {self._mark}"
            )
            params = (monitor, limit, offset)
        return self._fetchall(sql, params)

    def _monitor_row(self, name: str) -> tuple[object, ...] | None:
        """Return one catalog row, or None."""
        return self._fetchone(
            "SELECT name, kind, target, interval_seconds, enabled "
            f"FROM monitors WHERE name = {self._mark}",
            (name,),
        )

    def _load_monitor(self, name: str) -> MonitorRecord:
        """Return a catalog row or raise when the name is unknown."""
        chosen = require_monitor_name(name)
        row = self._monitor_row(chosen)
        if row is None:
            raise StorageError(f"unknown monitor {chosen!r}")
        return monitor_from_row(row)

    def _version(self) -> int:
        """Return the newest schema version, or 0 when the table is absent."""
        cursor = self._conn.cursor()
        try:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
            row = cursor.fetchone()
        except Exception as exc:
            if self._missing_table(exc):
                return 0
            LOGGER.warning("storage query failed error=%s", type(exc).__name__)
            raise StorageError("could not read schema version") from exc
        finally:
            cursor.close()
        if row is None:
            return 0
        return int(row[0])

    def _change(self, sql: str, params: tuple[object, ...]) -> tuple[int, int]:
        """Run one INSERT, UPDATE, or DELETE and return ``(lastrowid, rowcount)``."""
        cursor = self._execute(sql, params)
        try:
            row_id = int(cursor.lastrowid or 0)
            count = int(cursor.rowcount)
        finally:
            cursor.close()
        if count < 0:
            raise StorageError("database did not report a row count")
        return row_id, count

    def _fetchall(
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> list[tuple[object, ...]]:
        """Return every row from a SELECT."""
        cursor = self._execute(sql, params)
        try:
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _fetchone(self, sql: str, params: tuple[object, ...]) -> tuple[object, ...] | None:
        """Return one row, or None."""
        cursor = self._execute(sql, params)
        try:
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            return None
        return tuple(row)

    def _execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        """Run ``sql`` and leave the caller to close the cursor."""
        self._require_open()
        cursor = None
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params)
        except Exception as exc:
            if cursor is not None:
                cursor.close()
            LOGGER.warning("storage query failed error=%s", type(exc).__name__)
            raise StorageError(self._query_error) from exc
        return cursor

    def _commit_if_needed(self) -> None:
        """Commit a single statement that is not inside ``transaction``."""
        if not self._txn:
            self._conn.commit()

    def _slots(self, count: int) -> str:
        """Return ``count`` placeholders for this dialect."""
        return ", ".join([self._mark] * count)

    def _rollback_quietly(self) -> None:
        """Roll back, keeping the original failure if the rollback also fails."""
        try:
            self._conn.rollback()
        except Exception as exc:
            LOGGER.warning("storage rollback failed error=%s", type(exc).__name__)

    def _require_open(self) -> None:
        """Refuse work after ``close``."""
        if self._closed:
            raise StorageError("repository is closed")


def _monitor_params(record: MonitorRecord) -> tuple[object, ...]:
    """Return bound parameters for a catalog INSERT."""
    return (
        record.name,
        record.kind,
        record.target,
        record.interval_seconds,
        1 if record.enabled else 0,
    )
