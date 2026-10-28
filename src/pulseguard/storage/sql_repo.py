"""SQL shared by the SQLite and MySQL repositories.

Each backend opens its own connection and passes the placeholder it uses.
One connection is not shared across threads. The engine's writer comes later.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any

from pulseguard.checks.models import CheckResult, utc_now
from pulseguard.exceptions import ConfigValidationError, StorageError
from pulseguard.logging_setup import LOGGER_NAME
from pulseguard.storage.base import Repository
from pulseguard.storage.records import (
    DEFAULT_CHUNK_SIZE,
    SCHEMA_VERSION,
    AlertRow,
    IncidentRow,
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
        """Apply the schema once. A database already at this version is left as it is.

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
            try:
                self._change(
                    "INSERT INTO monitors (name, kind, target, interval_seconds, enabled) "
                    f"VALUES ({self._slots(5)})",
                    _monitor_params(record),
                )
            except StorageError:
                try:
                    self._conn.rollback()
                except Exception:
                    LOGGER.warning("monitor insert rollback failed")
                existing = self._monitor_row(record.name)
                if existing is None:
                    raise
            else:
                self._commit_if_needed()
                return
        if existing is not None:
            mark = self._mark
            was_enabled = monitor_from_row(existing).enabled
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
            if was_enabled and not record.enabled:
                self._close_open_for(record.name, utc_now(), "monitor paused")
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
        if current.enabled and not updated.enabled:
            self._close_open_for(updated.name, utc_now(), "monitor paused")
        self._commit_if_needed()

    def monitors(self) -> list[MonitorRecord]:
        """Return catalog rows ordered by name."""
        rows = self._fetchall(
            "SELECT name, kind, target, interval_seconds, enabled FROM monitors ORDER BY name ASC"
        )
        return [monitor_from_row(row) for row in rows]

    def delete_monitor_details(self, name: str) -> None:
        """Drop options and the stored ping for one monitor. Results stay."""
        chosen = require_monitor_name(name)
        self._change(f"DELETE FROM monitor_options WHERE name = {self._mark}", (chosen,))
        self._change(f"DELETE FROM heartbeat_pings WHERE monitor = {self._mark}", (chosen,))
        self._commit_if_needed()

    def delete_history(self, name: str) -> None:
        """Delete stored results and incidents for one monitor. The catalog row stays."""
        chosen = require_monitor_name(name)
        self._change(f"DELETE FROM results WHERE monitor = {self._mark}", (chosen,))
        self._change(f"DELETE FROM incidents WHERE monitor = {self._mark}", (chosen,))
        self._commit_if_needed()

    def delete_monitor(self, name: str) -> bool:
        """Delete a catalog row and leave its results stored."""
        chosen = require_monitor_name(name)
        _row_id, count = self._change(
            f"DELETE FROM monitors WHERE name = {self._mark}",
            (chosen,),
        )
        if count == 1:
            self._close_open_for(chosen, utc_now(), "monitor removed")
        self._commit_if_needed()
        return count == 1

    def save_options(self, name: str, options: Mapping[str, object]) -> None:
        """Store kind-specific fields for one monitor. The catalog row stays separate."""
        chosen = require_monitor_name(name)
        body = json.dumps(dict(options), sort_keys=True, separators=(",", ":"))
        if len(body) > 8000:
            raise ConfigValidationError("monitor options are too large")
        self._change(
            "DELETE FROM monitor_options WHERE name = " + self._mark,
            (chosen,),
        )
        self._change(
            f"INSERT INTO monitor_options (name, body) VALUES ({self._slots(2)})",
            (chosen, body),
        )
        self._commit_if_needed()

    def monitor_options(self, name: str) -> dict[str, object]:
        """Return stored options, or an empty mapping when the monitor has none."""
        chosen = require_monitor_name(name)
        row = self._fetchone(
            f"SELECT body FROM monitor_options WHERE name = {self._mark}",
            (chosen,),
        )
        if row is None or not isinstance(row[0], str):
            return {}
        try:
            parsed = json.loads(row[0])
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): value for key, value in parsed.items()}

    def option_map(self) -> dict[str, dict[str, object]]:
        """Return every monitor's options, keyed by name."""
        rows = self._fetchall("SELECT name, body FROM monitor_options")
        found: dict[str, dict[str, object]] = {}
        for row in rows:
            if not isinstance(row[0], str) or not isinstance(row[1], str):
                continue
            try:
                parsed = json.loads(row[1])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                found[row[0]] = {str(key): value for key, value in parsed.items()}
        return found

    def open_incident(self, monitor: str, started_at: datetime, reason: str, cause: str) -> None:
        """Record an incident that is still open."""
        chosen = require_monitor_name(monitor)
        self._change(
            "INSERT INTO incidents (monitor, state, started_at, ended_at, reason, cause) "
            f"VALUES ({self._slots(6)})",
            (chosen, "open", format_timestamp(started_at), None, reason[:2000], cause[:128]),
        )
        self._commit_if_needed()

    def resolve_incident(self, monitor: str, ended_at: datetime) -> None:
        """Close the newest open incident for ``monitor``."""
        chosen = require_monitor_name(monitor)
        row = self._fetchone(
            "SELECT id FROM incidents WHERE monitor = "
            f"{self._mark} AND state = 'open' ORDER BY started_at DESC LIMIT 1",
            (chosen,),
        )
        if row is None:
            return
        self._change(
            "UPDATE incidents SET state = 'resolved', ended_at = "
            f"{self._mark} WHERE id = {self._mark}",
            (format_timestamp(ended_at), _as_int(row[0])),
        )
        self._commit_if_needed()

    def close_incident(self, incident_id: int, ended_at: datetime, reason: str) -> bool:
        """Close one open incident and store ``reason``. A resolved row stays as it is."""
        chosen = require_row_id(incident_id)
        row = self._fetchone(
            f"SELECT state FROM incidents WHERE id = {self._mark}",
            (chosen,),
        )
        if row is None or row[0] != "open":
            return False
        self._close_id(chosen, ended_at, reason)
        self._commit_if_needed()
        return True

    def reconcile_open_incidents(self, ended_at: datetime) -> list[tuple[int, str]]:
        """Close open incidents whose monitor is missing or disabled.

        The reason is ``monitor no longer active``. The caller logs each close.
        """
        live = {record.name: record.enabled for record in self.monitors()}
        closed: list[tuple[int, str]] = []
        for row in self.incidents(state="open"):
            if live.get(row.monitor) is True:
                continue
            self._close_id(row.id, ended_at, "monitor no longer active")
            closed.append((row.id, row.monitor))
        self._commit_if_needed()
        return closed

    def incidents(self, *, state: str | None = None) -> list[IncidentRow]:
        """Return incidents, newest first. ``state`` is ``open`` or ``resolved``."""
        if state is None:
            rows = self._fetchall(
                "SELECT id, monitor, state, started_at, ended_at, reason, cause "
                "FROM incidents ORDER BY started_at DESC"
            )
        else:
            if state not in {"open", "resolved"}:
                raise ConfigValidationError("incident state must be open or resolved")
            rows = self._fetchall(
                "SELECT id, monitor, state, started_at, ended_at, reason, cause "
                f"FROM incidents WHERE state = {self._mark} ORDER BY started_at DESC",
                (state,),
            )
        return [_incident_row(row) for row in rows]

    def open_incident_names(self) -> list[str]:
        """Return monitors that still have an open incident."""
        rows = self._fetchall("SELECT monitor FROM incidents WHERE state = 'open'")
        return [row[0] for row in rows if isinstance(row[0], str)]

    def add_alert(
        self,
        *,
        created_at: datetime,
        monitor: str,
        kind: str,
        recipient: str,
        status: str,
        error_text: str,
    ) -> None:
        """Append one alert-log row. The password is not a column."""
        if kind not in {"open", "resolved", "test"}:
            raise ConfigValidationError("alert kind is not recognised")
        if status not in {"sent", "failed", "not-configured"}:
            raise ConfigValidationError("alert status is not recognised")
        self._change(
            "INSERT INTO alerts (created_at, monitor, kind, recipient, status, error_text) "
            f"VALUES ({self._slots(6)})",
            (
                format_timestamp(created_at),
                require_monitor_name(monitor),
                kind,
                recipient[:254],
                status,
                error_text[:500],
            ),
        )
        self._commit_if_needed()

    def alerts(self) -> list[AlertRow]:
        """Return the alert log, newest first."""
        rows = self._fetchall(
            "SELECT id, created_at, monitor, kind, recipient, status, error_text "
            "FROM alerts ORDER BY created_at DESC, id DESC"
        )
        return [_alert_row(row) for row in rows]

    def user_count(self) -> int:
        """Return how many admin accounts exist."""
        row = self._fetchone("SELECT COUNT(*) FROM users", ())
        if row is None:
            return 0
        return _as_int(row[0])

    def save_user(self, username: str, salt_hex: str, digest_hex: str) -> None:
        """Insert one admin account. A second insert of the same name replaces it."""
        self._change(f"DELETE FROM users WHERE username = {self._mark}", (username,))
        self._change(
            f"INSERT INTO users (username, salt_hex, digest_hex) VALUES ({self._slots(3)})",
            (username, salt_hex, digest_hex),
        )
        self._commit_if_needed()

    def user_hash(self, username: str) -> tuple[str, str] | None:
        """Return ``(salt_hex, digest_hex)`` or None."""
        row = self._fetchone(
            f"SELECT salt_hex, digest_hex FROM users WHERE username = {self._mark}",
            (username,),
        )
        if row is None or not isinstance(row[0], str) or not isinstance(row[1], str):
            return None
        return row[0], row[1]

    def get_setting(self, key: str) -> str | None:
        """Return one setting, or None when it has not been stored."""
        row = self._fetchone(
            f"SELECT value FROM settings WHERE setting_key = {self._mark}",
            (key,),
        )
        if row is None or not isinstance(row[0], str):
            return None
        return row[0]

    def set_setting(self, key: str, value: str) -> None:
        """Insert or replace one setting. Callers keep secrets out of the logs."""
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ConfigValidationError("setting name must be 1..64 characters")
        if len(value) > 2048:
            raise ConfigValidationError("setting value is too long")
        self._change(f"DELETE FROM settings WHERE setting_key = {self._mark}", (key,))
        self._change(
            f"INSERT INTO settings (setting_key, value) VALUES ({self._slots(2)})",
            (key, value),
        )
        self._commit_if_needed()

    def save_ping(self, monitor: str, when: datetime) -> None:
        """Remember the latest heartbeat ping for one monitor."""
        chosen = require_monitor_name(monitor)
        stamp = format_timestamp(when)
        self._change(f"DELETE FROM heartbeat_pings WHERE monitor = {self._mark}", (chosen,))
        self._change(
            f"INSERT INTO heartbeat_pings (monitor, last_ping) VALUES ({self._slots(2)})",
            (chosen, stamp),
        )
        self._commit_if_needed()

    def last_ping(self, monitor: str) -> str | None:
        """Return the stored ping timestamp, or None."""
        chosen = require_monitor_name(monitor)
        row = self._fetchone(
            f"SELECT last_ping FROM heartbeat_pings WHERE monitor = {self._mark}",
            (chosen,),
        )
        if row is None or not isinstance(row[0], str):
            return None
        return row[0]

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

    def _close_open_for(self, monitor: str, ended_at: datetime, reason: str) -> None:
        """Resolve every open incident for ``monitor``."""
        rows = self._fetchall(
            f"SELECT id FROM incidents WHERE monitor = {self._mark} AND state = 'open'",
            (monitor,),
        )
        for row in rows:
            self._close_id(_as_int(row[0]), ended_at, reason)

    def _close_id(self, incident_id: int, ended_at: datetime, reason: str) -> None:
        """Mark one open incident resolved. The caller commits."""
        stamp, text = _close_fields(ended_at, reason)
        self._change(
            "UPDATE incidents SET state = 'resolved', ended_at = "
            f"{self._mark}, reason = {self._mark} WHERE id = {self._mark} AND state = 'open'",
            (stamp, text, incident_id),
        )

    def _require_open(self) -> None:
        """Refuse work after ``close``."""
        if self._closed:
            raise StorageError("repository is closed")


def _close_fields(ended_at: datetime, reason: str) -> tuple[str, str]:
    """Return the timestamp and reason stored on a closed incident."""
    return format_timestamp(ended_at), reason[:2000]


def _incident_row(row: tuple[object, ...]) -> IncidentRow:
    """Map one SELECT onto an incident."""
    if len(row) != 7:
        raise StorageError("stored incident has the wrong shape")
    ended = row[4] if isinstance(row[4], str) else ""
    return IncidentRow(
        id=_as_int(row[0]),
        monitor=str(row[1]),
        state=str(row[2]),
        started_at=str(row[3]),
        ended_at=ended,
        reason=str(row[5]),
        cause=str(row[6]),
    )


def _alert_row(row: tuple[object, ...]) -> AlertRow:
    """Map one SELECT onto an alert."""
    if len(row) != 7:
        raise StorageError("stored alert has the wrong shape")
    return AlertRow(
        id=_as_int(row[0]),
        created_at=str(row[1]),
        monitor=str(row[2]),
        kind=str(row[3]),
        recipient=str(row[4]),
        status=str(row[5]),
        error_text=str(row[6]),
    )


def _as_int(value: object) -> int:
    """Return a stored integer id."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise StorageError("stored id is invalid")
    return value


def _monitor_params(record: MonitorRecord) -> tuple[object, ...]:
    """Return bound parameters for a catalog INSERT."""
    return (
        record.name,
        record.kind,
        record.target,
        record.interval_seconds,
        1 if record.enabled else 0,
    )
