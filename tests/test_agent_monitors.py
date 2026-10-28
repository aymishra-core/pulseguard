"""Agent pushes are ordinary monitors: history, incidents, SLA, and silence."""

from __future__ import annotations

import re
import time
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pulseguard.agent.client import AgentClient
from pulseguard.agent.roster import add_agent, ensure_agent_monitor, load_agents
from pulseguard.checks.agent_check import STALE_MESSAGE, AgentCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.catalog import checks_from_catalog
from pulseguard.core.engine import Engine
from pulseguard.exceptions import ProtocolError
from pulseguard.notifiers.email_notifier import EmailNotifier, SmtpSettings
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


def test_agent_check_has_no_target() -> None:
    """The registry accepts an agent monitor with an empty target."""
    check = AgentCheck.from_config({"kind": "agent", "name": "edge", "target": ""})
    assert check.target == ""
    missed = check.run()
    assert missed.ok is False
    assert missed.message == STALE_MESSAGE
    omitted = AgentCheck.from_config({"kind": "agent", "name": "edge"})
    assert omitted.target == ""


def test_agent_monitors_are_not_scheduled(tmp_path: Path) -> None:
    """An agent row stays in the catalog and out of the scheduler, with its dependency."""
    database = tmp_path / "catalog.db"
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("web", "http", "http://127.0.0.1/health", 60, True))
        repo.save_monitor(MonitorRecord("edge", "agent", "", 60, True))
        repo.save_options("edge", {"depends_on": ["web"]})
        checks, edges = checks_from_catalog(repo)
    assert [item.name for item in checks] == ["web"]
    assert edges["edge"] == ("web",)


def test_a_push_creates_a_monitor_on_the_dashboard(tmp_path: Path) -> None:
    """One successful push is a card, a sidebar row, a detail page, and an SLA sample."""
    client, database = _login(tmp_path)
    token = "a" * 16
    engine = _start(database, token)
    try:
        port = _port(engine)
        _push(port, token, "edge", message="edge ok", latency=42)
        _wait(lambda: _monitor(database, "edge") is not None)
    finally:
        engine.stop()
    record = _monitor(database, "edge")
    assert record is not None
    assert record.kind == "agent"
    assert record.target == ""
    with SqliteRepository(database) as repo:
        stored = repo.results_for("edge")
    assert stored[-1].ok is True
    assert stored[-1].message == "edge ok"
    assert stored[-1].latency_ms == 42
    payload = client.get("/api/dashboard").get_json()
    assert isinstance(payload, dict)
    card = next(item for item in payload["cards"] if item["name"] == "edge")
    assert card["kind"] == "agent"
    assert card["target"] == ""
    assert card["latency"] == 42
    assert "edge ok" in card["plain"]
    assert any(item["name"] == "edge" for item in payload["nav_items"])
    assert any(item["name"] == "edge" and item["latency"] == 42 for item in payload["log"])
    page = client.get("/dashboard").get_data(as_text=True)
    assert 'data-kind="agent"' in page
    assert "edge ok" in page
    monitors = client.get("/monitors").get_data(as_text=True)
    assert "edge" in monitors
    detail = client.get("/monitors/edge/view").get_data(as_text=True)
    assert "data-bar=" in detail
    assert "42 ms" in detail
    assert "edge ok" in detail
    eyebrow = BeautifulSoup(detail, "html.parser").select_one(".dhead .eyebrow")
    assert eyebrow is not None
    assert "agent" in eyebrow.get_text()
    assert "http" not in eyebrow.get_text()
    agents = client.get("/agents").get_data(as_text=True)
    assert 'href="/monitors/edge/view"' in agents
    assert "Open monitor" in agents
    sla_page = client.get("/sla")
    sla = client.post(
        "/sla",
        data={
            "csrf_token": _csrf(sla_page.get_data(as_text=True)),
            "source": "data",
            "monitor": "edge",
            "hours": "24",
            "target": "0.99",
        },
        follow_redirects=True,
    )
    assert b"Actual failures: 0" in sla.data
    assert b"edge" in sla.data


def test_three_failures_open_an_incident_and_send_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--fail three times opens one incident and sends one email."""
    sent = _smtp(monkeypatch)
    database = tmp_path / "fail.db"
    token = "b" * 16
    engine = _start(database, token, notifier=_mailer())
    try:
        port = _port(engine)
        for _ in range(3):
            _push(port, token, "edge", ok=False, message="HTTP 503")
        _wait(lambda: _open(database, "edge"))
    finally:
        engine.stop()
    subjects = [str(item["Subject"]) for item in sent]
    opened = [item for item in subjects if "incident opened" in item and "HTTP 503" in item]
    assert opened == [subjects[0]]
    assert len(subjects) == 1


def test_silence_opens_an_incident_and_a_push_resolves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No push within 3 intervals opens an incident. The next push resolves it."""
    sent = _smtp(monkeypatch)
    database = tmp_path / "stale.db"
    token = "c" * 16
    engine = _start(database, token, notifier=_mailer())
    try:
        port = _port(engine)
        _push(
            port,
            token,
            "edge",
            message="was up",
            when=utc_now() - timedelta(seconds=181),
        )
        _wait(lambda: _open(database, "edge"))
        _push(port, token, "edge", message="back")
        _wait(lambda: _resolved(database, "edge"))
    finally:
        engine.stop()
    with SqliteRepository(database) as repo:
        messages = [item.message for item in repo.results_for("edge")]
    assert STALE_MESSAGE in messages
    subjects = [str(item["Subject"]) for item in sent]
    assert any("incident opened" in item and STALE_MESSAGE in item for item in subjects)
    assert any("incident resolved" in item for item in subjects)


def test_a_down_dependency_suppresses_the_silence(tmp_path: Path) -> None:
    """A failing dependency keeps the missed report from opening an incident."""
    database = tmp_path / "deps.db"
    engine = Engine((), catalog=True, dependencies={"edge": ("parent",)})
    result = CheckResult(
        monitor="edge",
        kind="agent",
        ok=False,
        latency_ms=0,
        message=STALE_MESSAGE,
        severity=Severity.CRITICAL,
        checked_at=utc_now(),
    )
    engine._down.add("parent")
    with SqliteRepository(database) as repo:
        engine._observe(result, repo)
        assert engine.incidents.is_open("edge") is False
        assert repo.results_for("edge")[-1].message == STALE_MESSAGE
        assert repo.incidents(state="open") == []


def test_a_rejected_token_creates_nothing(tmp_path: Path) -> None:
    """A wrong token stores no monitor and no result."""
    database = tmp_path / "reject.db"
    token = "d" * 16
    engine = _start(database, token)
    try:
        port = _port(engine)
        with pytest.raises(ProtocolError), AgentClient("127.0.0.1", port, "e" * 16) as remote:
            remote.send(_result("ghost"))
        time.sleep(0.3)
    finally:
        engine.stop()
    with SqliteRepository(database) as repo:
        assert repo.monitors() == []
        assert list(repo.iter_results(None)) == []


def test_deleting_an_agent_keeps_or_drops_its_history(tmp_path: Path) -> None:
    """Keep leaves the monitor. Delete removes the monitor and its results."""
    client, database = _login(tmp_path)
    token = "f" * 16
    _seed(database, "edge", token, "kept")
    page = client.get("/agents").get_data(as_text=True)
    assert "Open monitor" in page
    assert "Keep history" in page
    assert "Delete history" in page
    kept = client.post(
        "/agents/delete",
        data={"csrf_token": _csrf(page), "name": "edge", "history": "keep"},
        follow_redirects=True,
    )
    assert b"history stay" in kept.data
    record = _monitor(database, "edge")
    assert record is not None
    assert record.enabled is True
    with SqliteRepository(database) as repo:
        assert repo.results_for("edge")[-1].message == "kept"
        assert load_agents(repo.get_setting("agents")) == []
    _seed(database, "edge", token, "gone")
    again = client.get("/agents").get_data(as_text=True)
    deleted = client.post(
        "/agents/delete",
        data={"csrf_token": _csrf(again), "name": "edge", "history": "delete"},
        follow_redirects=True,
    )
    assert b"history are deleted" in deleted.data
    assert _monitor(database, "edge") is None
    with SqliteRepository(database) as repo:
        assert repo.results_for("edge") == []


def _seed(database: Path, name: str, token: str, message: str) -> None:
    """Store one agent, its monitor, and one result."""
    with SqliteRepository(database) as repo:
        if not any(row["name"] == name for row in load_agents(repo.get_setting("agents"))):
            add_agent(repo, name, token)
        ensure_agent_monitor(repo, name)
        repo.add_result(_result(name, message=message))


def _result(
    name: str,
    *,
    ok: bool = True,
    message: str = "ok",
    latency: float = 0,
    when: object = None,
) -> CheckResult:
    """Build one push result."""
    moment = when if isinstance(when, type(utc_now())) else utc_now()
    return CheckResult(
        monitor=name,
        kind="http",
        ok=ok,
        latency_ms=latency,
        message=message,
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=moment,
    )


def _push(
    port: int,
    token: str,
    name: str,
    *,
    ok: bool = True,
    message: str = "ok",
    latency: float = 0,
    when: object = None,
) -> None:
    """Send one result through the collector."""
    with AgentClient("127.0.0.1", port, token) as client:
        client.send(_result(name, ok=ok, message=message, latency=latency, when=when))


def _start(database: Path, token: str, notifier: EmailNotifier | None = None) -> Engine:
    """Start a catalog engine whose collector listens on an ephemeral port."""
    with SqliteRepository(database) as repo:
        repo.set_setting("agent_enabled", "1")
        repo.set_setting("agent_token", token)
        repo.set_setting("agent_host", "127.0.0.1")
        repo.set_setting("agent_port", "0")
    engine = Engine(
        (),
        repository_factory=lambda: SqliteRepository(database),
        catalog=True,
        tick_seconds=0.05,
        notifier=notifier,
    )
    engine.start()
    return engine


def _port(engine: Engine) -> int:
    """Wait until the collector has a bound port."""
    _wait(lambda: (engine.agent_port or 0) > 0)
    port = engine.agent_port
    assert isinstance(port, int)
    return port


def _mailer() -> EmailNotifier:
    """Return a notifier aimed at the mocked SMTP client."""
    return EmailNotifier(
        SmtpSettings(
            host="mail.example",
            port=587,
            username="",
            password="",
            from_addr="ops@example.com",
            to_addrs=("oncall@example.com",),
        )
    )


def _smtp(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    """Replace SMTP and return the messages it was asked to send."""
    sent: list[EmailMessage] = []

    class _Client:
        def __init__(self, host: str, port: int = 0, timeout: float | None = None) -> None:
            del host, port, timeout

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr("pulseguard.notifiers.email_notifier.smtplib.SMTP", _Client)
    return sent


def _monitor(database: Path, name: str) -> MonitorRecord | None:
    """Return one catalog row."""
    with SqliteRepository(database) as repo:
        return next((item for item in repo.monitors() if item.name == name), None)


def _open(database: Path, name: str) -> bool:
    """Return True when this monitor has an open incident."""
    with SqliteRepository(database) as repo:
        return any(row.state == "open" and row.monitor == name for row in repo.incidents())


def _resolved(database: Path, name: str) -> bool:
    """Return True when this monitor's incident is resolved and nothing is open."""
    with SqliteRepository(database) as repo:
        rows = [row for row in repo.incidents() if row.monitor == name]
    return any(row.state == "resolved" for row in rows) and all(row.state != "open" for row in rows)


def _login(tmp_path: Path) -> tuple[object, Path]:
    """Create the admin account and return a signed-in client."""
    database = tmp_path / "agents.db"
    client = _app(database).test_client()
    setup = client.get("/setup")
    token = _csrf(setup.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    return client, database


def _app(database: Path) -> object:
    """Build an app around ``database``."""
    return create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _wait(ready: object) -> None:
    """Poll for up to eight seconds."""
    if not callable(ready):
        raise AssertionError("ready must be callable")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")
