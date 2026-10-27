"""SQLite CRUD, MySQL against a scripted connection, export, and backup."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.cli import main
from pulseguard.exceptions import ConfigValidationError, StorageError
from pulseguard.exporters import (
    read_monitors_csv,
    read_verified_backup,
    restore_verified_backup,
    verify_backup,
    write_monitors_csv,
    write_monitors_json,
    write_results_csv,
    write_results_json,
    write_verified_backup,
)
from pulseguard.storage import (
    DEFAULT_RETENTION_DAYS,
    MonitorRecord,
    MysqlRepository,
    MysqlSettings,
    Repository,
    SqliteRepository,
    mysql_settings_from_env,
    retention_cutoff,
)
from pulseguard.storage.mysql_repo import _MYSQL_SCHEMA
from pulseguard.storage.records import MAX_STORED_MESSAGE_LENGTH, format_timestamp
from tests.mock_servers import MockHttpServer

_OLDER = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_NEWER = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def test_sqlite_round_trip_orders_results_and_reopens(tmp_path: Path) -> None:
    """Create, read, update, and delete survive closing the file."""
    path = tmp_path / "history.db"
    older = _result("web", when=_OLDER, message="HTTP 200")
    newer = _result("web", when=_NEWER, message="HTTP 503", ok=False)
    other = _result("job", when=_NEWER, message="ping is within the grace period")
    with SqliteRepository(path) as repo:
        assert repo.schema_version == 2
        repo.migrate()
        first = repo.add_result(newer)
        second = repo.add_result(older)
        repo.add_result(other)
        assert first == 1
        assert second == 2
        assert repo.results_for("web")[0].message == "HTTP 200"
        assert repo.results_for("web")[1].message == "HTTP 503"
        assert [row.monitor for row in repo.iter_results(None)] == ["web", "web", "job"]
        assert repo.delete_result(first) is True
        assert repo.delete_result(99) is False
        assert [row.message for row in repo.results_for("web")] == ["HTTP 200"]
        repo.save_monitor(_monitor())
        repo.update_monitor("web", interval_seconds=120, enabled=False)
        stored = repo.monitors()
        assert stored[0].interval_seconds == 120
        assert stored[0].enabled is False
        assert repo.delete_monitor("web") is True
        assert repo.results_for("web")[0].message == "HTTP 200"
    with SqliteRepository(path) as again:
        assert again.schema_version == 2
        assert len(again.results_for("web")) == 1
        connection = sqlite3.connect(path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        finally:
            connection.close()
        assert count is not None
        assert count[0] == 1


def test_transaction_rolls_back_and_a_later_write_commits(tmp_path: Path) -> None:
    """A failed transaction leaves the file unchanged."""
    path = tmp_path / "history.db"
    with SqliteRepository(path) as repo:
        with pytest.raises(RuntimeError, match="cancel"), repo.transaction():
            repo.add_result(_result("web"))
            with repo.transaction():
                repo.add_result(_result("web", message="HTTP 204"))
            raise RuntimeError("cancel")
        assert repo.results_for("web") == []
        repo.add_result(_result("web"))
    with SqliteRepository(path) as again:
        assert len(again.results_for("web")) == 1


def test_history_pages_with_limit(tmp_path: Path) -> None:
    """Five rows and a page of two issue three limited queries. Generator."""
    path = tmp_path / "history.db"
    with SqliteRepository(path) as repo:
        for index in range(5):
            moment = _OLDER + timedelta(minutes=index)
            repo.add_result(_result("web", when=moment, message=f"HTTP {200 + index}"))
        queries: list[str] = []
        repo._conn.set_trace_callback(queries.append)
        try:
            listed = list(repo.iter_results("web", chunk_size=2))
        finally:
            repo._conn.set_trace_callback(None)
    assert [row.message for row in listed] == [f"HTTP {200 + index}" for index in range(5)]
    assert len([sql for sql in queries if "LIMIT" in sql]) == 3


def test_quoted_monitor_name_does_not_match_other_rows(tmp_path: Path) -> None:
    """A quote in a name stays a bound parameter."""
    tricky = "web' OR '1'='1"
    path = tmp_path / "history.db"
    with SqliteRepository(path) as repo:
        repo.add_result(_result(tricky, message="HTTP 200"))
        repo.add_result(_result("other", message="HTTP 204"))
        assert [row.monitor for row in repo.results_for(tricky)] == [tricky]


def test_retention_deletes_only_older_rows(tmp_path: Path) -> None:
    """The default cutoff is 180 days, and an equal timestamp stays."""
    now = datetime(2026, 10, 26, tzinfo=UTC)
    cutoff = retention_cutoff(now)
    assert DEFAULT_RETENTION_DAYS == 180
    assert cutoff == now - timedelta(days=180)
    path = tmp_path / "history.db"
    with SqliteRepository(path) as repo:
        repo.add_result(_result("web", when=cutoff - timedelta(seconds=1), message="HTTP 500"))
        repo.add_result(_result("web", when=cutoff, message="HTTP 200"))
        removed = repo.delete_older_than(cutoff)
        assert removed == 1
        assert [row.message for row in repo.results_for("web")] == ["HTTP 200"]
    with pytest.raises(ConfigValidationError):
        retention_cutoff(datetime(2026, 10, 26))


def test_closed_repo_and_bad_rows_are_rejected(tmp_path: Path) -> None:
    """Closed handles, a future schema, and a bad stored flag fail clearly."""
    path = tmp_path / "history.db"
    repo = SqliteRepository(path)
    repo.close()
    with pytest.raises(StorageError, match="closed"):
        repo.add_result(_result("web"))
    with pytest.raises(StorageError, match="unsupported schema version"):
        _stamp_version(path, 3)
        SqliteRepository(path)
    fresh = tmp_path / "bad.db"
    with SqliteRepository(fresh) as repo:
        repo.add_result(_result("web"))
    connection = sqlite3.connect(fresh)
    try:
        connection.execute("UPDATE results SET checked_at = 'bogus' WHERE monitor = 'web'")
        connection.commit()
    finally:
        connection.close()
    with SqliteRepository(fresh) as repo, pytest.raises(StorageError, match="checked_at"):
        repo.results_for("web")
    with SqliteRepository(":memory:") as repo:
        huge = _result("web", message="x" * (MAX_STORED_MESSAGE_LENGTH + 1))
        with pytest.raises(StorageError, match="message"):
            repo.add_result(huge)


def test_backup_round_trip_and_a_flipped_byte(tmp_path: Path) -> None:
    """A snapshot restores, and a changed byte fails the SHA-256 check."""
    path = tmp_path / "history.db"
    with SqliteRepository(path) as repo:
        repo.add_result(_result("web"))
        payload = repo.snapshot_bytes()
    backup = tmp_path / "history.pgdb"
    digest = write_verified_backup(payload, backup)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert verify_backup(backup) == digest
    restored = tmp_path / "restored.db"
    assert restore_verified_backup(backup, restored) == digest
    with SqliteRepository(restored) as repo:
        assert repo.results_for("web")[0].message == "HTTP 200"
    flipped = bytearray(backup.read_bytes())
    flipped[-1] ^= 0x01
    bad = tmp_path / "bad.pgdb"
    bad.write_bytes(flipped)
    with pytest.raises(StorageError, match="checksum"):
        read_verified_backup(bad)
    short = tmp_path / "short.pgdb"
    short.write_bytes(b"PGDB")
    with pytest.raises(StorageError, match="truncated"):
        read_verified_backup(short)
    with pytest.raises(StorageError, match="empty"):
        write_verified_backup(b"", tmp_path / "empty.pgdb")
    with pytest.raises(StorageError, match="already exists"):
        write_verified_backup(payload, backup)
    with pytest.raises(StorageError, match="already exists"):
        restore_verified_backup(backup, restored)
    bad_magic = tmp_path / "magic.pgdb"
    bad_magic.write_bytes(b"XXXX" + backup.read_bytes()[4:])
    with pytest.raises(StorageError, match="not a PulseGuard"):
        read_verified_backup(bad_magic)
    bad_version = bytearray(backup.read_bytes())
    bad_version[4:8] = (2).to_bytes(4, "big")
    (tmp_path / "version.pgdb").write_bytes(bad_version)
    with pytest.raises(StorageError, match="unsupported backup version"):
        read_verified_backup(tmp_path / "version.pgdb")
    bad_length = bytearray(backup.read_bytes())
    bad_length[8:16] = (1).to_bytes(8, "big")
    (tmp_path / "length.pgdb").write_bytes(bad_length)
    with pytest.raises(StorageError, match="length"):
        read_verified_backup(tmp_path / "length.pgdb")


def test_csv_and_json_exports_round_trip_text() -> None:
    """CSV and JSON carry the result fields, and the catalog CSV reloads."""
    result = _result("web", when=_OLDER)
    handle = StringIO()
    assert write_results_csv([result], handle) == 1
    text = handle.getvalue()
    assert "HTTP 200" in text
    assert text.startswith("monitor,kind,ok,")
    json_handle = StringIO()
    assert write_results_json([result], json_handle) == 1
    payload = json.loads(json_handle.getvalue())
    assert payload[0]["monitor"] == "web"
    assert payload[0]["severity"] == "ok"
    catalog = StringIO()
    write_monitors_csv([_monitor(target="http://127.0.0.1/a,b")], catalog)
    loaded = read_monitors_csv(StringIO(catalog.getvalue()))
    assert loaded[0].target == "http://127.0.0.1/a,b"
    with pytest.raises(ConfigValidationError, match="header"):
        read_monitors_csv(StringIO("name,kind\n"))
    json_handle = StringIO()
    assert write_monitors_json([_monitor()], json_handle) == 1
    assert json.loads(json_handle.getvalue())[0]["enabled"] is True
    with pytest.raises(ConfigValidationError, match="no rows"):
        read_monitors_csv(StringIO("name,kind,target,interval_seconds,enabled\n"))
    with pytest.raises(ConfigValidationError, match="enabled"):
        read_monitors_csv(
            StringIO(
                "name,kind,target,interval_seconds,enabled\n"
                "web,http,http://127.0.0.1/health,60,yes\n"
            )
        )
    with pytest.raises(ConfigValidationError, match="duplicate"):
        read_monitors_csv(
            StringIO(
                "name,kind,target,interval_seconds,enabled\n"
                "web,http,http://127.0.0.1/health,60,1\n"
                "web,http,http://127.0.0.1/health,60,1\n"
            )
        )


def test_cli_stores_a_local_check_and_exports_it(tmp_path: Path) -> None:
    """``check --db`` writes SQLite, and export reads that file back."""
    database = tmp_path / "history.db"
    exported = tmp_path / "results.json"
    with MockHttpServer() as server:
        code, payload = _run(
            [
                "check",
                "--name",
                "demo",
                "--url",
                f"{server.base_url}/ok?token=super-secret-value",
                "--db",
                str(database),
            ]
        )
    assert code == 0
    assert payload[0]["ok"] is True
    assert b"super-secret-value" not in database.read_bytes()
    with SqliteRepository(database) as repo:
        assert repo.results_for("demo")[0].ok is True
    code, text = _run_text(
        ["export", "--db", str(database), "--format", "json", "--output", str(exported)]
    )
    assert code == 0
    assert "wrote 1 results" in text
    saved = json.loads(exported.read_text(encoding="utf-8"))
    assert saved[0]["monitor"] == "demo"


def test_cli_import_and_backup(tmp_path: Path) -> None:
    """Catalog import and a checksummed backup run through the CLI."""
    database = tmp_path / "history.db"
    catalog = tmp_path / "monitors.csv"
    catalog.write_text(
        "name,kind,target,interval_seconds,enabled\nweb,http,http://127.0.0.1/health,60,1\n",
        encoding="utf-8",
    )
    code, text = _run_text(["import-monitors", "--db", str(database), "--csv", str(catalog)])
    assert code == 0
    assert text == "imported 1 monitors\n"
    backup = tmp_path / "history.pgdb"
    code, text = _run_text(["backup", "--db", str(database), "--output", str(backup)])
    assert code == 0
    digest = text.strip().split("=", maxsplit=1)[1]
    code, verified = _run_text(["backup", "--verify", str(backup)])
    assert code == 0
    assert digest in verified
    restored = tmp_path / "restored.db"
    code, _restored_text = _run_text(
        ["backup", "--restore", str(backup), "--output", str(restored)]
    )
    assert code == 0
    with SqliteRepository(restored) as repo:
        assert repo.monitors()[0].name == "web"
    exported = tmp_path / "monitors.json"
    code, text = _run_text(
        [
            "export",
            "--db",
            str(database),
            "--table",
            "monitors",
            "--format",
            "json",
            "--output",
            str(exported),
        ]
    )
    assert code == 0
    assert json.loads(exported.read_text(encoding="utf-8"))[0]["name"] == "web"
    code, _text, err = _run_raw(["backup", "--db", str(database)])
    assert code == 2
    assert "requires --output" in err
    code, _text, err = _run_raw(
        [
            "export",
            "--db",
            str(database),
            "--table",
            "monitors",
            "--monitor",
            "web",
            "--format",
            "csv",
            "--output",
            str(tmp_path / "nope.csv"),
        ]
    )
    assert code == 2
    assert "--monitor" in err
    code, _text, err = _run_raw(["backup", "--db", str(database), "--verify", str(backup)])
    assert code == 2
    assert "use one of" in err


def test_catalog_paths_and_mysql_settings_are_validated(tmp_path: Path) -> None:
    """Bad files, kinds, and MySQL settings fail before a connection."""
    folder = tmp_path / "not-a-file"
    folder.mkdir()
    with pytest.raises(ConfigValidationError, match="not a file"):
        SqliteRepository(folder)
    with pytest.raises(ConfigValidationError, match="unknown check kind"):
        _monitor_bad_kind()
    with pytest.raises(ConfigValidationError, match="host"):
        MysqlSettings(
            host="bad host",
            port=3306,
            user="pulseguard",
            password="",
            database="pulseguard",
        )
    with pytest.raises(ConfigValidationError, match="PORT"):
        mysql_settings_from_env(
            {
                "PULSEGUARD_MYSQL_HOST": "127.0.0.1",
                "PULSEGUARD_MYSQL_PORT": "nope",
                "PULSEGUARD_MYSQL_USER": "pulseguard",
                "PULSEGUARD_MYSQL_DATABASE": "pulseguard",
            }
        )
    with SqliteRepository(":memory:") as repo:
        with pytest.raises(ConfigValidationError, match="result id"):
            repo.delete_result(True)  # type: ignore[arg-type]
        with pytest.raises(ConfigValidationError, match="chunk_size"):
            list(repo.iter_results("web", chunk_size=0))


def _monitor_bad_kind() -> MonitorRecord:
    """Build a catalog row whose kind is not registered."""
    return MonitorRecord(
        name="web",
        kind="icmp",
        target="http://127.0.0.1/health",
        interval_seconds=60,
        enabled=True,
    )


def test_mysql_scripted_connection_uses_the_same_statements() -> None:
    """MySQL CRUD is the same port, exercised without a server."""
    connection = _FakeConnection(_migration_steps() + _crud_steps())
    repo = MysqlRepository(connection=connection)
    assert repo.schema_version == 2
    assert issubclass(MysqlRepository, Repository)
    new_id = repo.add_result(_result("web", when=_OLDER))
    assert new_id == 7
    loaded = repo.results_for("web")
    assert loaded[0].message == "HTTP 200"
    assert loaded[0].checked_at == _OLDER
    repo.save_monitor(_monitor())
    repo.update_monitor("web", enabled=False)
    assert repo.monitors()[0].enabled is False
    assert repo.delete_result(7) is True
    assert repo.delete_older_than(_NEWER) == 0
    assert repo.delete_monitor("web") is True
    joined = "\n".join(connection.statements)
    assert "INSERT INTO results" in joined
    assert "UPDATE monitors" in joined
    assert "DELETE FROM results" in joined
    assert "DELETE FROM monitors" in joined
    assert "%s" in joined
    assert connection.steps == []
    repo.close()


def test_mysql_transaction_rolls_back_on_the_scripted_connection() -> None:
    """A raised body rolls back and does not commit the insert."""
    connection = _FakeConnection([*_migration_steps(), _Step(lastrowid=4, rowcount=1)])
    repo = MysqlRepository(connection=connection)
    with pytest.raises(RuntimeError), repo.transaction():
        repo.add_result(_result("web"))
        raise RuntimeError("cancel")
    insert_at = max(
        index for index, event in enumerate(connection.events) if "INSERT INTO results" in event
    )
    assert "commit" not in connection.events[insert_at:]
    assert connection.events[-1] == "rollback"
    repo.close()


def test_mysql_settings_hide_the_password_and_skip_without_a_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings omit the password, and a missing driver is a storage error."""
    settings = mysql_settings_from_env(
        {
            "PULSEGUARD_MYSQL_HOST": "127.0.0.1",
            "PULSEGUARD_MYSQL_USER": "pulseguard",
            "PULSEGUARD_MYSQL_PASSWORD": "secret-value",
            "PULSEGUARD_MYSQL_DATABASE": "pulseguard",
        }
    )
    assert settings.port == 3306
    assert "secret-value" not in repr(settings)
    with pytest.raises(ConfigValidationError, match="PULSEGUARD_MYSQL_HOST"):
        mysql_settings_from_env({})

    real_import = __import__

    def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mysql.connector" or name.startswith("mysql."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    with pytest.raises(StorageError, match="mysql extra"):
        MysqlRepository(settings)


@pytest.mark.skipif(
    not os.environ.get("PULSEGUARD_MYSQL_HOST"),
    reason="MySQL settings are not configured",
)
def test_mysql_live_round_trip() -> None:
    """Run CRUD when the operator has pointed the environment at MySQL."""
    settings = mysql_settings_from_env()
    stamp = format_timestamp(datetime(2026, 10, 26, tzinfo=UTC))
    name = f"pgtest-{stamp[0:10]}"
    with MysqlRepository(settings) as repo:
        repo.delete_monitor(name)
        row_id = repo.add_result(_result(name))
        repo.save_monitor(_monitor(name=name))
        assert repo.results_for(name)[0].monitor == name
        repo.delete_result(row_id)
        repo.delete_monitor(name)


def _result(
    monitor: str,
    *,
    when: datetime | None = None,
    message: str = "HTTP 200",
    ok: bool = True,
) -> CheckResult:
    """Build one stored result. ``ok`` selects a legal severity."""
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=12.5,
        message=message,
        severity=Severity.OK if ok else Severity.WARNING,
        checked_at=when or _OLDER,
        status_code=200 if ok else None,
    )


def _monitor(
    *,
    name: str = "web",
    target: str = "http://127.0.0.1/health",
) -> MonitorRecord:
    """Build one catalog row. Nothing is contacted."""
    return MonitorRecord(
        name=name,
        kind="http",
        target=target,
        interval_seconds=60,
        enabled=True,
    )


def _stamp_version(path: Path, version: int) -> None:
    """Insert a schema version the current code does not understand."""
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, format_timestamp(_OLDER)),
        )
        connection.commit()
    finally:
        connection.close()


def _run(argv: list[str]) -> tuple[int, list[dict[str, Any]]]:
    """Run the CLI and parse a JSON array from stdout."""
    code, text, _err = _run_raw(argv)
    payload = json.loads(text)
    assert isinstance(payload, list)
    return code, payload


def _run_text(argv: list[str]) -> tuple[int, str]:
    """Run the CLI and return stdout."""
    code, text, _err = _run_raw(argv)
    return code, text


def _run_raw(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()


class _MissingTable(Exception):
    """Stand-in for MySQL error 1146, unknown table."""

    errno = 1146

    def __str__(self) -> str:
        """Match the text the repository also accepts."""
        return "Table 'pulseguard.schema_migrations' doesn't exist"


class _Step:
    """One scripted cursor outcome."""

    def __init__(
        self,
        *,
        rows: list[tuple[object, ...]] | None = None,
        lastrowid: int = 0,
        rowcount: int = 0,
        error: BaseException | None = None,
    ) -> None:
        """Store the outcome for the next ``execute``."""
        self.rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.error = error


class _FakeCursor:
    """Cursor that pops one scripted step per ``execute``."""

    def __init__(self, connection: _FakeConnection) -> None:
        """Bind the shared step queue."""
        self._connection = connection
        self._rows: list[tuple[object, ...]] = []
        self.lastrowid = 0
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        """Record ``sql`` and apply the next step."""
        self._connection.statements.append(sql)
        self._connection.events.append(sql)
        if not self._connection.steps:
            raise AssertionError(f"unexpected SQL: {sql}")
        step = self._connection.steps.pop(0)
        if step.error is not None:
            raise step.error
        self._rows = list(step.rows or [])
        self.lastrowid = step.lastrowid
        self.rowcount = step.rowcount

    def fetchone(self) -> tuple[object, ...] | None:
        """Return the first scripted row."""
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return the scripted rows."""
        return list(self._rows)

    def close(self) -> None:
        """Match the DB-API cursor."""
        return None


class _FakeConnection:
    """Connection that records commits and rollbacks."""

    def __init__(self, steps: list[_Step]) -> None:
        """Start from ``steps``."""
        self.steps = steps
        self.statements: list[str] = []
        self.events: list[str] = []

    def cursor(self) -> _FakeCursor:
        """Return a cursor over this queue."""
        return _FakeCursor(self)

    def commit(self) -> None:
        """Record a commit."""
        self.events.append("commit")

    def rollback(self) -> None:
        """Record a rollback."""
        self.events.append("rollback")

    def close(self) -> None:
        """Match the DB-API connection."""
        return None


def _migration_steps() -> list[_Step]:
    """Version probe misses the table, then the DDL and version insert succeed."""
    return [
        _Step(error=_MissingTable()),
        *[_Step() for _statement in _MYSQL_SCHEMA],
        _Step(rowcount=1),
    ]


def _result_row() -> tuple[object, ...]:
    """One stored result row in SELECT order."""
    return (
        "web",
        "http",
        1,
        12.5,
        "HTTP 200",
        "ok",
        format_timestamp(_OLDER),
        200,
    )


def _crud_steps() -> list[_Step]:
    """Scripted outcomes for insert, read, catalog update, and deletes."""
    monitor = ("web", "http", "http://127.0.0.1/health", 60, 1)
    disabled = ("web", "http", "http://127.0.0.1/health", 60, 0)
    return [
        _Step(lastrowid=7, rowcount=1),
        _Step(rows=[_result_row()]),
        _Step(rows=[]),
        _Step(rowcount=1),
        _Step(rows=[monitor]),
        _Step(rowcount=1),
        _Step(rows=[disabled]),
        _Step(rowcount=1),
        _Step(rowcount=0),
        _Step(rowcount=1),
    ]
