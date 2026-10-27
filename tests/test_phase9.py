"""Catalog scheduling, stored incidents, and the serve login."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.cli import main
from pulseguard.core.engine import Engine
from pulseguard.exceptions import ConfigValidationError
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


class _Down:
    """A check that fails without leaving the process."""

    kind = "http"
    name = "web"
    target = "http://127.0.0.1/health"
    interval = 30.0

    def run(self) -> CheckResult:
        """Return a stored failure. No socket is opened."""
        return CheckResult(
            monitor="web",
            kind="http",
            ok=False,
            latency_ms=1,
            message="down",
            severity=Severity.CRITICAL,
            checked_at=utc_now(),
        )


def test_version_1_database_gains_incidents_and_settings(tmp_path: Path) -> None:
    """A file that only knows version 1 picks up the new tables."""
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) "
        "VALUES (1, '2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    moment = datetime(2026, 10, 27, tzinfo=UTC)
    with SqliteRepository(path) as repo:
        assert repo.schema_version == 2
        repo.open_incident("web", moment, "down", "web")
        repo.add_alert(
            created_at=moment,
            monitor="web",
            kind="open",
            recipient="ops@example.com",
            status="not-configured",
            error_text="",
        )
        repo.save_options("web", {"keyword": "Acme"})
        repo.set_setting("failures_before_open", "3")
        repo.save_user("ada", "aa", "bb")
        repo.save_ping("web", moment)
        assert repo.incidents(state="open")[0].reason == "down"
        assert repo.alerts()[0].status == "not-configured"
        assert repo.monitor_options("web")["keyword"] == "Acme"
        assert repo.get_setting("failures_before_open") == "3"
        assert repo.user_count() == 1
        assert repo.last_ping("web") is not None


def test_catalog_changes_show_up_within_five_seconds(tmp_path: Path) -> None:
    """Add, edit, pause, and delete change what the scheduler will run."""
    database = tmp_path / "history.db"
    first = HeartbeatCheck.new_token()
    second = HeartbeatCheck.new_token()
    engine = Engine(
        (),
        repository_factory=lambda: SqliteRepository(database),
        catalog=True,
        tick_seconds=0.05,
    )
    engine.start()
    try:
        _save_heartbeat(database, first)
        _wait(lambda: engine.heartbeat_for(first) is not None)
        _save_heartbeat(database, second)
        _wait(lambda: engine.heartbeat_for(second) is not None)
        assert engine.heartbeat_for(first) is None
        with SqliteRepository(database) as repo:
            repo.update_monitor("job", enabled=False)
        _wait(lambda: "job" not in engine.catalog_names())
        with SqliteRepository(database) as repo:
            repo.update_monitor("job", enabled=True)
        _wait(lambda: "job" in engine.catalog_names())
        with SqliteRepository(database) as repo:
            repo.delete_monitor("job")
        _wait(lambda: "job" not in engine.catalog_names())
    finally:
        engine.stop()
    assert _pulseguard_threads() == []


def test_a_failed_alert_is_stored(tmp_path: Path) -> None:
    """A notifier that raises still leaves a failed row, including the recipient."""

    class _Mail:
        def send(self, _result: CheckResult) -> None:
            raise RuntimeError("nope")

        def note(self, _result: CheckResult) -> None:
            return None

    database = tmp_path / "mail.db"
    with SqliteRepository(database) as repo:
        repo.set_setting("smtp_to", "ops@example.com")
    engine = Engine(
        (_Down(),),
        repository_factory=lambda: SqliteRepository(database),
        tick_seconds=0.05,
        failures_before_open=1,
        notifier=_Mail(),  # type: ignore[arg-type]
    )
    engine.start()
    try:
        _wait(_opened(database))
    finally:
        engine.stop()
    with SqliteRepository(database) as repo:
        row = repo.alerts()[0]
        assert row.status == "failed"
        assert row.error_text == "RuntimeError"
        assert row.recipient == "ops@example.com"


def test_open_incident_and_alert_survive_a_restart(tmp_path: Path) -> None:
    """No SMTP server is required. The open incident is still open after stop."""
    database = tmp_path / "history.db"
    engine = Engine(
        (_Down(),),
        repository_factory=lambda: SqliteRepository(database),
        tick_seconds=0.05,
        failures_before_open=1,
    )
    engine.start()
    try:
        _wait(_opened(database))
    finally:
        engine.stop()
    with SqliteRepository(database) as repo:
        assert repo.incidents(state="open")[0].monitor == "web"
        assert repo.alerts()[0].status == "not-configured"
        assert repo.alerts()[0].kind == "open"
    again = Engine(
        (_Down(),),
        repository_factory=lambda: SqliteRepository(database),
        tick_seconds=0.05,
        failures_before_open=1,
    )
    again.start()
    try:
        assert again.incidents.is_open("web")
    finally:
        again.stop()
    assert _pulseguard_threads() == []


def test_catalog_restores_a_ping_and_skips_a_bad_row(tmp_path: Path) -> None:
    """A stored ping comes back, and one invalid monitor does not stop the rest."""
    from pulseguard.core.catalog import checks_from_catalog, default_thresholds

    database = tmp_path / "history.db"
    token = HeartbeatCheck.new_token()
    seen: list[str] = []
    with SqliteRepository(database) as repo:
        repo.save_monitor(
            MonitorRecord(
                name="job",
                kind="heartbeat",
                target=token,
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.save_monitor(
            MonitorRecord(
                name="bad",
                kind="http",
                target="not a url",
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.save_monitor(
            MonitorRecord(
                name="site",
                kind="http",
                target="http://127.0.0.1/",
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.save_monitor(
            MonitorRecord(
                name="quiet",
                kind="heartbeat",
                target=HeartbeatCheck.new_token(),
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.save_options("job", {"grace_seconds": 60, "depends_on": ["web", 3]})
        repo.save_ping("job", datetime(2026, 10, 27, tzinfo=UTC))
        repo.set_setting("failures_before_open", "4")
        repo.set_setting("successes_before_resolve", "nope")
        checks, edges = checks_from_catalog(repo, on_ping=lambda name, _when: seen.append(name))
        assert {check.name for check in checks} == {"job", "quiet", "site"}
        assert edges["job"] == ("web",)
        assert default_thresholds(repo) == (4, 1)
        job = next(check for check in checks if check.name == "job")
        assert isinstance(job, HeartbeatCheck)
        assert job._last_seen is not None
        job.record_ping()
        assert seen == ["job"]
        cache: dict[str, tuple[str, object]] = {}
        held, _edges = checks_from_catalog(repo, cache=cache)  # type: ignore[arg-type]
        again, _edges = checks_from_catalog(repo, cache=cache)  # type: ignore[arg-type]
        assert again[0] is held[0]
        repo.set_setting("successes_before_resolve", "2")
        assert default_thresholds(repo) == (4, 2)
        assert default_thresholds(object()) == (3, 1)  # type: ignore[arg-type]


def test_stored_rows_reject_a_bad_shape(tmp_path: Path) -> None:
    """Resolve, a bad alert, and an oversized setting fail in the repository."""
    database = tmp_path / "history.db"
    moment = datetime(2026, 10, 27, tzinfo=UTC)
    with SqliteRepository(database) as repo:
        repo.open_incident("web", moment, "down", "")
        repo.resolve_incident("web", moment)
        assert repo.incidents()[0].state == "resolved"
        assert repo.open_incident_names() == []
        repo.resolve_incident("web", moment)
        with pytest.raises(ConfigValidationError):
            repo.incidents(state="maybe")
        with pytest.raises(ConfigValidationError):
            repo.add_alert(
                created_at=moment,
                monitor="web",
                kind="nope",
                recipient="",
                status="sent",
                error_text="",
            )
        with pytest.raises(ConfigValidationError):
            repo.add_alert(
                created_at=moment,
                monitor="web",
                kind="open",
                recipient="",
                status="nope",
                error_text="",
            )
        with pytest.raises(ConfigValidationError):
            repo.set_setting("", "x")
        with pytest.raises(ConfigValidationError):
            repo.set_setting("smtp_host", "x" * 3000)
        with pytest.raises(ConfigValidationError):
            repo.save_options("web", {"blob": "x" * 9000})
        assert repo.user_hash("missing") is None
        assert repo.monitor_options("missing") == {}
        assert repo.last_ping("missing") is None
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO monitor_options (name, body) VALUES ('bad', 'nope')")
    connection.execute("INSERT INTO monitor_options (name, body) VALUES ('list', '[1]')")
    connection.commit()
    connection.close()
    with SqliteRepository(database) as repo:
        assert repo.monitor_options("bad") == {}
        assert repo.monitor_options("list") == {}
        assert "bad" not in repo.option_map()


def test_setup_is_once_and_login_is_limited(tmp_path: Path) -> None:
    """Setup closes after the first account, and the sixth failure waits."""
    client = _client(tmp_path / "history.db")
    assert client.get("/dashboard").status_code == 302
    assert client.get("/login").status_code == 302
    page = client.get("/setup")
    assert page.status_code == 200
    short = client.post(
        "/setup",
        data={
            "csrf_token": _csrf(page.get_data(as_text=True)),
            "username": "ada",
            "password": "short",
        },
    )
    assert short.status_code == 400
    assert b"8" in short.data
    rejected = client.post("/setup", data={"username": "ada", "password": "long-enough"})
    assert rejected.status_code == 400
    assert b"csrf" in rejected.data
    token = _csrf(page.get_data(as_text=True))
    created = client.post(
        "/setup",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    assert created.status_code == 302
    assert client.get("/setup").status_code == 302
    assert (
        client.post(
            "/setup",
            data={"csrf_token": token, "username": "ada", "password": "long-enough"},
        ).status_code
        == 302
    )
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    for _attempt in range(5):
        failed = client.post(
            "/login",
            data={"csrf_token": token, "username": "ada", "password": "wrong-password"},
        )
        assert failed.status_code == 401
        assert b"login failed" in failed.data
    locked = client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "wrong-password"},
    )
    assert locked.status_code == 429
    signed = client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    assert signed.status_code == 429
    home = client.post(
        "/login",
        data={
            "csrf_token": _csrf(client.get("/login").get_data(as_text=True)),
            "username": "ada",
            "password": "long-enough",
        },
        follow_redirects=False,
    )
    assert home.status_code == 429


def test_login_cookie_and_public_page_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful login sets an HttpOnly cookie. The public page can be turned off."""
    monkeypatch.setattr("pulseguard.web.auth.time.monotonic", lambda: 1_000.0)
    database = tmp_path / "history.db"
    client = _client(database)
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    signed = client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    cookie = signed.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert client.get("/dashboard").status_code == 200
    left = client.post(
        "/logout", data={"csrf_token": _csrf(client.get("/dashboard").get_data(as_text=True))}
    )
    assert left.status_code == 302
    assert client.get("/dashboard").status_code == 302
    with SqliteRepository(database) as repo:
        repo.set_setting("public_status", "0")
    hidden = client.get("/")
    assert hidden.status_code == 404
    assert b"turned off" in hidden.data
    assert client.get("/status").status_code == 404


def test_login_rejects_a_bad_token_and_a_missing_account(tmp_path: Path) -> None:
    """CSRF, an unknown user, and a broken hash all fail closed."""
    database = tmp_path / "history.db"
    client = _client(database)
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    assert client.post("/login", data={"username": "ada", "password": "x"}).status_code == 400
    assert (
        client.post(
            "/login",
            data={"csrf_token": token, "username": "", "password": "long-enough"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/login",
            data={"csrf_token": token, "username": "missing", "password": "long-enough"},
        ).status_code
        == 401
    )
    with SqliteRepository(database) as repo:
        repo.save_user("ada", "zz", "yy")
    assert (
        client.post(
            "/login",
            data={"csrf_token": token, "username": "ada", "password": "long-enough"},
        ).status_code
        == 401
    )
    with client.session_transaction() as stored:
        stored["user"] = "gone"
    assert client.get("/dashboard").status_code == 302
    assert client.post("/logout", data={"username": "ada"}).status_code == 400


def test_serve_root_redirects_to_setup_login_or_dashboard(tmp_path: Path) -> None:
    """GET / follows the account. The public page stays on /status."""
    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
        serve=True,
    )
    client = app.test_client()
    fresh = client.get("/")
    assert fresh.status_code == 302
    assert fresh.headers["Location"].endswith("/setup")
    setup = client.get("/setup")
    token = _csrf(setup.get_data(as_text=True))
    client.post(
        "/setup",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    signed_out = client.get("/")
    assert signed_out.status_code == 302
    assert signed_out.headers["Location"].endswith("/login")
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    home = client.get("/")
    assert home.status_code == 302
    assert home.headers["Location"].endswith("/dashboard")
    board = client.get("/status")
    assert board.status_code == 200
    assert b"/admin/login" not in board.data
    assert b'href="/login"' in board.data
    assert client.get("/admin/login").status_code == 404
    assert client.get("/admin").status_code == 404


def test_serve_creates_the_secret_and_refuses_every_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``serve`` needs no environment variables and does not bind 0.0.0.0."""
    for name in (
        "PULSEGUARD_SECRET_KEY",
        "PULSEGUARD_ADMIN_USER",
        "PULSEGUARD_ADMIN_PASSWORD",
        "PULSEGUARD_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))

    def _stop(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("waitress.serve", _stop)
    before = _thread_names()
    code, text, err = _run(["serve", "--data-dir", str(tmp_path / "data"), "--port", "8766"])
    assert code == 0, err
    assert "web http://127.0.0.1:8766" in text
    assert opened == ["http://127.0.0.1:8766/"]
    secret = tmp_path / "data" / "secret.key"
    assert secret.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "data" / "pulseguard.db").exists()
    opened.clear()
    code, _text, _err = _run(
        ["serve", "--data-dir", str(tmp_path / "quiet"), "--no-browser", "--port", "8766"]
    )
    assert code == 0
    assert opened == []
    code, _text, err = _run(["serve", "--host", "0.0.0.0", "--data-dir", str(tmp_path / "no")])
    assert code == 2
    assert _thread_names() == before


def _save_heartbeat(database: Path, token: str) -> None:
    """Store the heartbeat the catalog scheduler should pick up."""
    with SqliteRepository(database) as repo:
        repo.save_monitor(
            MonitorRecord(
                name="job",
                kind="heartbeat",
                target=token,
                interval_seconds=30,
                enabled=True,
            )
        )


def _opened(database: Path):
    """Return a probe that is true once an incident row exists."""

    def ready() -> bool:
        with SqliteRepository(database) as repo:
            return bool(repo.incidents(state="open"))

    return ready


def _wait(ready: object) -> None:
    """Poll until ``ready`` is true, or five seconds pass."""
    import time

    if not callable(ready):
        raise AssertionError("ready must be callable")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.05)
    raise AssertionError("change was not visible within 5 seconds")


def _client(database: Path):
    """Return a Flask client with the test admin secret."""
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )
    return app.test_client()


def _csrf(html: str) -> str:
    """Read the hidden token from a rendered form."""
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    import contextlib
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()


def _thread_names() -> list[str]:
    """Return live thread names."""
    return sorted(thread.name for thread in threading.enumerate())


def _pulseguard_threads() -> list[str]:
    """Return engine threads that should have stopped."""
    return [name for name in _thread_names() if name.startswith("pulseguard")]
