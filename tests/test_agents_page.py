"""The Agents page explains the push command and shows each agent's state."""

from __future__ import annotations

import json
import re
import shlex
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pulseguard.agent.client import AgentClient
from pulseguard.agent.protocol import RESULT, decode_result, encode_message
from pulseguard.agent.roster import add_agent, remember_rejection
from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.cli import _parser
from pulseguard.core.engine import Engine
from pulseguard.exceptions import ConfigValidationError, ProtocolError
from pulseguard.storage import SqliteRepository
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_FLAGS = ("--host", "--port", "--name", "--kind", "--message")


def test_agents_show_the_command_and_each_state(tmp_path: Path) -> None:
    """A new agent, a fresh push, a stale agent, and a public address are all visible."""
    client, database = _login(tmp_path)
    page = _add(client, "edge")
    command = _command(page)
    _assert_push_parses(command, "edge")
    visible = BeautifulSoup(page, "html.parser").select_one("[data-command-box] code")
    assert visible is not None
    shown = visible.get_text()
    assert "••••••••" in shown
    assert _token_from(command) not in shown
    for flag in _FLAGS:
        assert flag in shown
    assert "Run this on the agent machine" in page
    assert "Never seen" in _row(page, "edge")
    assert "No result yet" in page
    assert "Saved. Restart PulseGuard to start listening." in page

    moment = utc_now()
    with SqliteRepository(database) as repo:
        add_agent(repo, "recent", HeartbeatCheck.new_token())
        add_agent(repo, "old", HeartbeatCheck.new_token())
        repo.set_setting(
            "agent_seen",
            json.dumps(
                {
                    "recent": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "old": (moment - timedelta(seconds=1000))
                    .astimezone(UTC)
                    .strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                }
            ),
        )
    board = client.get("/agents").get_data(as_text=True)
    assert "Never seen" in _row(board, "edge")
    assert re.search(r"Seen \d+ s ago", _row(board, "recent"))
    assert "Stale" in _row(board, "old")
    sent = client.post(
        "/agents/test",
        data={"csrf_token": _csrf(board), "name": "edge"},
        follow_redirects=True,
    )
    sent_page = sent.get_data(as_text=True)
    assert re.search(r"Seen \d+ s ago", _row(sent_page, "edge"))
    assert "Last result: ok" in _row(sent_page, "edge")

    refused = client.post(
        "/agents",
        data={"csrf_token": _csrf(board), "host": "8.8.8.8", "port": "8791"},
    )
    assert "agent host must be a loopback or private address" in refused.get_data(as_text=True)
    assert b"--token" not in refused.data
    assert b"--collector" not in refused.data


def test_a_bad_token_and_a_long_message_are_listed(tmp_path: Path) -> None:
    """The collector records a wrong token and an oversized message on the page."""
    client, database = _login(tmp_path)
    token = HeartbeatCheck.new_token()
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
    )
    engine.start()
    try:
        _wait(lambda: (engine.agent_port or 0) > 0)
        port = engine.agent_port
        assert isinstance(port, int)
        result = _sample("edge")
        with (
            pytest.raises(ProtocolError),
            AgentClient("127.0.0.1", port, HeartbeatCheck.new_token()) as remote,
        ):
            remote.send(result)
        _push_frame(port, _oversized(token))
    finally:
        engine.stop()
    page = client.get("/agents").get_data(as_text=True)
    assert "agent token rejected" in page
    assert "message is too long" in page


def test_reveal_shows_the_token_and_hides_it_again(tmp_path: Path) -> None:
    """The command stays masked until Reveal, and Hide puts the mask back."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    from threading import Thread

    from werkzeug.serving import make_server

    database = tmp_path / "agents.db"
    app = _app(database)
    server = make_server("127.0.0.1", 0, app)
    Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(base + "/setup")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/login")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/dashboard")
            page.goto(base + "/agents")
            page.fill("#agent-name", "edge")
            page.click('button:has-text("Add agent")')
            page.wait_for_selector("[data-command-box]")
            secret = page.get_attribute("[data-command-box]", "data-shown") or ""
            token = _token_from(secret)
            assert token not in page.inner_text("[data-command-box] code")
            page.click("[data-reveal]")
            assert token in page.inner_text("[data-command-box] code")
            page.click("[data-reveal]")
            assert token not in page.inner_text("[data-command-box] code")
            browser.close()
    finally:
        server.shutdown()


def test_roster_limits_and_a_second_token_still_match(tmp_path: Path) -> None:
    """Eight agents fit, the ninth does not, and either stored token opens a frame."""
    database = tmp_path / "roster.db"
    with SqliteRepository(database) as repo:
        for index in range(8):
            add_agent(repo, f"a{index}", HeartbeatCheck.new_token())
        with pytest.raises(ConfigValidationError, match="at most 8"):
            add_agent(repo, "extra", HeartbeatCheck.new_token())
        reason = "x" * 180
        for _ in range(12):
            remember_rejection(repo, reason, utc_now())
        stored = repo.get_setting("agent_rejected") or ""
        assert len(stored) <= 2048
        assert stored.count(reason) < 12
    payload = json.dumps({"token": "a" * 16, "result": _sample("edge").as_dict()}).encode()
    assert decode_result(payload, ("b" * 16, "a" * 16)).monitor == "edge"
    with pytest.raises(ProtocolError, match="agent token rejected"):
        decode_result(payload, ())


def _assert_push_parses(command: str, name: str) -> None:
    """Require every flag in the shown command to be a real push flag."""
    lines = command.splitlines()
    assert lines[0].startswith('export PULSEGUARD_AGENT_TOKEN="')
    argv = shlex.split(lines[1])
    assert argv[0] == "pulseguard"
    parsed = _parser().parse_args(argv[1:])
    assert parsed.command == "push"
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 8791
    assert parsed.name == name
    assert parsed.kind == "http"
    assert parsed.message == "ok"
    for flag in _FLAGS:
        assert flag in lines[1]
    with pytest.raises(SystemExit):
        _parser().parse_args(["push", "--token", "x", "--port", "8791", "--name", "edge"])
    with pytest.raises(SystemExit):
        _parser().parse_args(
            ["push", "--collector", "127.0.0.1:8791", "--port", "1", "--name", "a"]
        )


def _command(page: str) -> str:
    """Return the command the Copy button holds."""
    node = BeautifulSoup(page, "html.parser").select_one("[data-copy]")
    assert node is not None
    value = node.get("data-copy")
    assert isinstance(value, str)
    return value


def _token_from(command: str) -> str:
    """Return the token inside the export line."""
    match = re.search(r'PULSEGUARD_AGENT_TOKEN="([^"]+)"', command)
    assert match is not None
    return match.group(1)


def _row(page: str, name: str) -> str:
    """Return the text of the agent row with this name."""
    for node in BeautifulSoup(page, "html.parser").select(".agent"):
        if name in node.get_text(" ", strip=True):
            return node.get_text(" ", strip=True)
    raise AssertionError(f"missing agent {name}")


def _add(client: object, name: str) -> str:
    """Create one agent and return the page that shows its command."""
    home = client.get("/agents")  # type: ignore[attr-defined]
    created = client.post(  # type: ignore[attr-defined]
        "/agents/add",
        data={"csrf_token": _csrf(home.get_data(as_text=True)), "name": name},
        follow_redirects=True,
    )
    return created.get_data(as_text=True)


def _sample(name: str) -> CheckResult:
    """Return one short successful result."""
    return CheckResult(
        monitor=name,
        kind="http",
        ok=True,
        latency_ms=0,
        message="ok",
        severity=Severity.OK,
        checked_at=datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
    )


def _oversized(token: str) -> bytes:
    """Return a frame whose message is one character over the limit."""
    body = _sample("edge").as_dict()
    body["message"] = "x" * 2001
    payload = json.dumps({"token": token, "result": body}).encode()
    return encode_message(RESULT, payload)


def _push_frame(port: int, frame: bytes) -> None:
    """Send one raw frame and wait until the collector answers."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    try:
        sock.sendall(frame)
        sock.settimeout(2)
        header = _read(sock, 10)
        length = int.from_bytes(header[6:10], "big")
        if length:
            _read(sock, length)
    finally:
        sock.close()


def _read(sock: socket.socket, size: int) -> bytes:
    """Read an exact number of bytes."""
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


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
    """Poll for up to five seconds."""
    import time

    if not callable(ready):
        raise AssertionError("ready must be callable")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.05)
    raise AssertionError("collector did not listen within 5 seconds")
