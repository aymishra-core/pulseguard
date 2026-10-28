"""Open incidents follow the monitor. The dashboard hero says what is true now."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.engine import Engine
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_WHEN = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)


def test_deleting_or_pausing_a_monitor_closes_its_incidents(tmp_path: Path) -> None:
    """A check that no longer runs does not keep an incident open."""
    database = tmp_path / "history.db"
    client = _client(database)
    token = _csrf(client.get("/dashboard").get_data(as_text=True))
    with SqliteRepository(database) as repo:
        _monitor(repo, "keyword", enabled=True)
        repo.open_incident("keyword", _WHEN, "missing word", "keyword")
        repo.open_incident("keyword", _WHEN, "missing word again", "keyword")
        _monitor(repo, "shop", enabled=True)
        repo.open_incident("shop", _WHEN, "connection refused", "http")
    removed = client.post("/monitors/keyword/delete", data={"csrf_token": token})
    assert removed.status_code == 302
    with SqliteRepository(database) as repo:
        closed = [row for row in repo.incidents() if row.monitor == "keyword"]
        assert len(closed) == 2
        assert {row.state for row in closed} == {"resolved"}
        assert {row.reason for row in closed} == {"monitor removed"}
        assert all(row.ended_at for row in closed)
        assert repo.incidents(state="open")[0].monitor == "shop"
        repo.update_monitor("shop", enabled=False)
        paused = next(row for row in repo.incidents() if row.monitor == "shop")
        assert paused.state == "resolved"
        assert paused.reason == "monitor paused"
        assert paused.ended_at
        _monitor(repo, "web", enabled=True)
        repo.open_incident("web", _WHEN, "down", "http")
        repo.save_monitor(MonitorRecord("web", "http", "http://127.0.0.1/", 30, False))
        saved = next(row for row in repo.incidents() if row.monitor == "web")
        assert saved.state == "resolved"
        assert saved.reason == "monitor paused"


def test_startup_and_reload_close_incidents_for_inactive_monitors(
    tmp_path: Path, caplog: logging.LogCaptureFixture
) -> None:
    """Startup and the catalog reload each log one line per incident they close."""
    caplog.set_level(logging.INFO, logger="pulseguard.core.engine")
    database = tmp_path / "history.db"
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("web", "heartbeat", HeartbeatCheck.new_token(), 60, True))
        repo.open_incident("web", _WHEN, "connection refused", "http")
        repo.save_monitor(MonitorRecord("old", "heartbeat", HeartbeatCheck.new_token(), 60, False))
        repo.open_incident("old", _WHEN, "down", "http")
        repo.open_incident("Keyword", _WHEN, "missing word", "keyword")
    engine = Engine(
        (),
        repository_factory=lambda: SqliteRepository(database),
        catalog=True,
        tick_seconds=0.05,
    )
    engine.start()
    try:
        assert engine.incidents.is_open("web")
        assert not engine.incidents.is_open("Keyword")
        with SqliteRepository(database) as repo:
            rows = repo.incidents()
            gone = next(row for row in rows if row.monitor == "Keyword")
            disabled = next(row for row in rows if row.monitor == "old")
            kept = next(row for row in rows if row.monitor == "web")
            assert gone.reason == "monitor no longer active"
            assert gone.state == "resolved"
            assert disabled.reason == "monitor no longer active"
            assert kept.state == "open"
            repo.open_incident("later", utc_now(), "down", "http")
        assert _closed(database, "later")
        assert not engine.incidents.is_open("later")
    finally:
        engine.stop()
    lines = [
        record.message for record in caplog.records if record.message.startswith("incident closed")
    ]
    assert len(lines) == 3
    assert sum("monitor=Keyword" in line for line in lines) == 1
    assert sum("monitor=old" in line for line in lines) == 1
    assert sum("monitor=later" in line for line in lines) == 1
    assert all("reason=monitor no longer active" in line for line in lines)


def test_manual_close_resolves_only_an_open_incident(tmp_path: Path) -> None:
    """The incident page posts a close, and a resolved incident has no button."""
    database = tmp_path / "history.db"
    app = _app(database)
    released: list[str] = []

    class _Tracker:
        def release(self, name: str) -> None:
            released.append(name)

    class _Engine:
        incidents = _Tracker()

    app.config["ENGINE"] = _Engine()
    client = app.test_client()
    _sign_in(client)
    with SqliteRepository(database) as repo:
        _monitor(repo, "web", enabled=True)
        repo.open_incident("web", _WHEN, "connection refused", "http")
        incident_id = repo.incidents(state="open")[0].id
    page = client.get(f"/incidents/{incident_id}")
    assert b"Close incident" in page.data
    token = _csrf(page.get_data(as_text=True))
    closed = client.post(f"/incidents/{incident_id}/close", data={"csrf_token": token})
    assert closed.status_code == 302
    with SqliteRepository(database) as repo:
        row = repo.incidents()[0]
        assert row.state == "resolved"
        assert row.reason == "closed manually"
        assert row.ended_at
        ended = row.ended_at
    assert released == ["web"]
    again = client.get(f"/incidents/{incident_id}")
    assert b"Close incident" not in again.data
    token = _csrf(again.get_data(as_text=True))
    client.post(f"/incidents/{incident_id}/close", data={"csrf_token": token})
    with SqliteRepository(database) as repo:
        row = repo.incidents()[0]
        assert row.reason == "closed manually"
        assert row.ended_at == ended


def test_dashboard_hero_matches_the_monitors_that_are_running(tmp_path: Path) -> None:
    """The hero has one sentence for down, waiting, first checks, and a clean board."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "web", enabled=True)
    waiting = client.get("/dashboard").get_data(as_text=True)
    assert "Awaiting first sweep" in waiting
    assert "hero--danger" not in waiting
    assert "hero--ok" not in waiting
    assert 'data-ring="neutral"' in waiting
    assert "1 incident" not in waiting

    moment = utc_now()
    with SqliteRepository(database) as repo:
        repo.add_result(_result("web", True, "HTTP 200", moment))
    clean = client.get("/dashboard").get_data(as_text=True)
    assert "All 1 monitor healthy" in clean
    assert "NOMINAL" in clean
    assert "hero--ok" in clean
    assert "Awaiting first sweep" not in clean

    with SqliteRepository(database) as repo:
        repo.add_result(_result("web", False, "connection refused", moment))
        repo.open_incident("web", moment, "connection refused", "http")
        _monitor(repo, "shop", enabled=True)
        repo.add_result(_result("shop", False, "connection refused", moment))
        repo.open_incident("shop", moment, "connection refused", "http")
    down = client.get("/dashboard").get_data(as_text=True)
    assert "2 of 2 monitors failing" in down
    assert "OUTAGE" in down
    assert "hero--danger" in down
    assert "<b>shop</b>" in down
    assert "The site did not answer (connection refused)" in down
    assert "Open incident" in down
    assert "Awaiting first sweep" not in down
    assert 'class="badge">2<' in down
    assert "2 failing" in down

    with SqliteRepository(database) as repo:
        repo.add_result(_result("shop", True, "HTTP 200", moment))
        repo.add_result(_result("web", True, "HTTP 200", moment))
    between = client.get("/dashboard").get_data(as_text=True)
    assert "Incident open, awaiting next check" in between
    assert "hero--warn" in between
    assert "Awaiting first sweep" not in between
    assert "2 open" in between


def test_chip_banner_and_badge_ignore_an_orphan_incident(tmp_path: Path) -> None:
    """An incident for a deleted monitor is not a current incident."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "web", enabled=True)
        repo.add_result(_result("web", True, "HTTP 200", utc_now()))
        repo.open_incident("Keyword", _WHEN, "missing word", "keyword")
    page = client.get("/dashboard").get_data(as_text=True)
    assert "All 1 monitor healthy" in page
    assert "Awaiting first sweep" not in page
    assert "1 incident" not in page
    assert 'class="badge"' not in page
    assert "Open incident <" not in page
    assert "Keyword" not in page.split("hero", 1)[-1].split("</section>", 1)[0]


def _closed(database: Path, monitor: str) -> bool:
    """Wait until ``monitor`` has no open incident."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with SqliteRepository(database) as repo:
            if monitor not in repo.open_incident_names():
                return True
        time.sleep(0.05)
    return False


def _monitor(repo: SqliteRepository, name: str, *, enabled: bool) -> None:
    """Store one HTTP monitor. Nothing is contacted."""
    repo.save_monitor(MonitorRecord(name, "http", "http://127.0.0.1/", 30, enabled))


def _result(monitor: str, ok: bool, message: str, moment: datetime) -> CheckResult:
    """Return one stored result."""
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=12,
        message=message,
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=moment,
    )


def _client(database: Path):
    """Return a signed-in client."""
    client = _app(database).test_client()
    _sign_in(client)
    return client


def _app(database: Path):
    """Build an app around ``database``."""
    return create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )


def _sign_in(client) -> None:
    """Create the account and sign in."""
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": "ada", "password": "long-enough"})


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
