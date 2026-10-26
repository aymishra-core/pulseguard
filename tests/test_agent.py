"""Agent frames, the collector, and the one client socket. Nothing leaves the machine."""

from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue

import pytest

from pulseguard.agent.client import AgentClient
from pulseguard.agent.protocol import (
    HEADER,
    MAGIC,
    MAX_PAYLOAD,
    decode_result,
    encode_message,
    encode_result,
    parse_listen,
    read_message,
    require_agent_host,
)
from pulseguard.agent.server import Collector
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.cli import main
from pulseguard.core.engine import Engine
from pulseguard.exceptions import ConfigValidationError, ProtocolError
from pulseguard.storage import SqliteRepository

_TOKEN = "example-agent-token1"


def test_round_trip_and_malformed_frames() -> None:
    """A frame survives a socket pair. Bad magic, size, and a short read do not."""
    original = _result(ok=True)
    left, right = socket.socketpair()
    try:
        right.sendall(encode_result(original, _TOKEN))
        right.shutdown(socket.SHUT_WR)
        left.settimeout(1)
        frame = read_message(left)
    finally:
        left.close()
        right.close()
    decoded = decode_result(frame.payload, _TOKEN)
    assert decoded.monitor == "web"
    assert decoded.ok is True
    assert decoded.message == "HTTP 200"
    assert decoded.as_dict()["checked_at"] == original.checked_at.isoformat()
    assert encode_message(1, b"ok")[:4] == MAGIC
    assert not encode_message(1, b"ok").startswith(b"PGDB")

    with pytest.raises(ProtocolError, match="frame too large"):
        encode_message(1, b"x" * (MAX_PAYLOAD + 1))
    _expect_frame(HEADER.pack(b"PGDB", 1, 1, 0), "bad magic")
    _expect_frame(HEADER.pack(MAGIC, 2, 1, 0), "bad version")
    _expect_frame(HEADER.pack(MAGIC, 1, 1, MAX_PAYLOAD + 1), "frame too large")
    _expect_frame(b"short", "truncated")
    _expect_frame(HEADER.pack(MAGIC, 1, 1, 4) + b"{", "truncated")
    packed = encode_result(original, _TOKEN)
    with pytest.raises(ProtocolError, match="agent token rejected"):
        decode_result(packed[HEADER.size :], "another-agent-token")
    with pytest.raises(ProtocolError, match="frame is not json"):
        decode_result(b"not-json", _TOKEN)


def test_collector_survives_garbage_and_then_accepts_one_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad connection is dropped. The next connection is acknowledged once."""
    caplog.set_level(logging.INFO, logger="pulseguard.agent.server")
    results: Queue[CheckResult] = Queue()
    with Collector("127.0.0.1", 0, _TOKEN, results) as collector:
        _send_raw(collector.port, b"this is not a frame")
        _send_raw(collector.port, HEADER.pack(MAGIC, 1, 1, 999999))
        with (
            pytest.raises(ProtocolError, match="agent did not acknowledge"),
            AgentClient("127.0.0.1", collector.port, "another-agent-token") as client,
        ):
            client.send(_result(ok=False))
        with AgentClient("127.0.0.1", collector.port, _TOKEN) as client:
            client.send(_result(ok=False))
        got = results.get(timeout=2)
    assert got.monitor == "web"
    assert got.ok is False
    assert results.empty()
    text = caplog.text
    assert "agent result monitor=web" in text
    assert "agent frame rejected" in text
    assert _TOKEN not in text
    assert "another-agent-token" not in text
    assert _agent_threads() == []


def test_a_full_queue_refuses_without_dropping_the_collector() -> None:
    """The queue bound is a refusal, and a later result can still land."""
    results: Queue[CheckResult] = Queue(maxsize=1)
    results.put_nowait(_result(ok=True, monitor="held"))
    with Collector("127.0.0.1", 0, _TOKEN, results) as collector:
        with (
            pytest.raises(ProtocolError, match="agent did not acknowledge"),
            AgentClient("127.0.0.1", collector.port, _TOKEN) as client,
        ):
            client.send(_result(ok=False, monitor="extra"))
        assert results.get(timeout=1).monitor == "held"
        with AgentClient("127.0.0.1", collector.port, _TOKEN) as client:
            client.send(_result(ok=False, monitor="extra"))
        assert results.get(timeout=1).monitor == "extra"
    assert _agent_threads() == []


def test_forgotten_socket_is_closed_by_del() -> None:
    """``__del__`` closes the socket. The context manager is the normal path."""
    results: Queue[CheckResult] = Queue()
    with Collector("127.0.0.1", 0, _TOKEN, results) as collector:
        client = AgentClient("127.0.0.1", collector.port, _TOKEN)
        client.connect()
        sock = client._sock
        assert sock is not None
        client.__del__()
        with pytest.raises(OSError):
            sock.getpeername()
        client.__del__()
        with AgentClient("127.0.0.1", collector.port, _TOKEN) as managed:
            managed.send(_result(ok=True))
        assert managed._sock is None
    assert results.get(timeout=1).ok is True
    AgentClient("127.0.0.1", 9, _TOKEN).__del__()


def test_listen_addresses_are_literals() -> None:
    """Public addresses, wildcards, and names are refused before a socket opens."""
    assert require_agent_host("127.0.0.1") == "127.0.0.1"
    assert parse_listen("127.0.0.1:9700") == ("127.0.0.1", 9700)
    for host in ("1.2.3.4", "0.0.0.0", "localhost", "example.com"):
        with pytest.raises(ConfigValidationError):
            require_agent_host(host)
    with pytest.raises(ConfigValidationError, match="agent port"):
        parse_listen("127.0.0.1:0")


def test_engine_stores_a_pushed_result(tmp_path: Path) -> None:
    """A remote failure uses the writer and opens one incident."""
    database = tmp_path / "history.db"
    engine = Engine(
        [_Local()],
        repository_factory=lambda: SqliteRepository(database),
        workers=1,
        queue_size=4,
        tick_seconds=0.05,
        failures_before_open=1,
        agent_host="127.0.0.1",
        agent_port=0,
        agent_token=_TOKEN,
    )
    engine.start()
    try:
        port = engine.agent_port
        assert port is not None and port != 0
        with AgentClient("127.0.0.1", port, _TOKEN) as client:
            client.send(_result(ok=False, monitor="remote"))
        _wait_until(lambda: engine.incidents.is_open("remote"))
    finally:
        engine.stop()
    assert _agent_threads() == []
    with SqliteRepository(database) as repo:
        rows = repo.results_for("remote")
    assert len(rows) == 1
    assert rows[0].message == "HTTP 503"
    assert [item.action for item in engine.incidents.transitions if item.monitor == "remote"] == [
        "open"
    ]


def test_push_command_and_a_missing_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``push`` delivers one result. ``run`` without a token does not listen."""
    monkeypatch.delenv("PULSEGUARD_SMTP_HOST", raising=False)
    monkeypatch.setenv("PULSEGUARD_AGENT_TOKEN", _TOKEN)
    results: Queue[CheckResult] = Queue()
    with Collector("127.0.0.1", 0, _TOKEN, results) as collector:
        code, text, err = _run(["push", "--port", str(collector.port), "--name", "web", "--fail"])
    assert code == 0, err
    assert text == "sent monitor=web\n"
    assert _TOKEN not in text
    assert _TOKEN not in err
    assert results.get(timeout=1).ok is False

    config = tmp_path / "monitors.json"
    config.write_text(
        '{"monitors":[{"kind":"http","name":"demo","target":"http://127.0.0.1/ok","interval":30}]}',
        encoding="utf-8",
    )
    monkeypatch.delenv("PULSEGUARD_AGENT_TOKEN", raising=False)
    code, _text, err = _run(
        [
            "run",
            "--config",
            str(config),
            "--db",
            str(tmp_path / "history.db"),
            "--agent-listen",
            "127.0.0.1:9700",
            "--duration",
            "1",
        ]
    )
    assert code == 2
    assert "agent token" in err
    with pytest.raises(ConfigValidationError):
        parse_listen("1.2.3.4:9700")


class _Local:
    """One local check so the engine has a schedule. It does not open a socket."""

    kind = "http"

    def __init__(self) -> None:
        """Store the fields the scheduler reads."""
        self.name = "local"
        self.target = "http://127.0.0.1/local"
        self.interval = 30.0

    def run(self) -> CheckResult:
        """Return a passing result."""
        return _result(ok=True, monitor="local", message="HTTP 200")


def _result(
    *,
    ok: bool,
    monitor: str = "web",
    message: str | None = None,
) -> CheckResult:
    """Build one result. Nothing is contacted."""
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=1,
        message=message if message is not None else ("HTTP 200" if ok else "HTTP 503"),
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
        status_code=200 if ok else 503,
    )


def _expect_frame(payload: bytes, match: str) -> None:
    """Feed one buffer to the reader and expect a protocol error."""
    left, right = socket.socketpair()
    try:
        right.sendall(payload)
        right.shutdown(socket.SHUT_WR)
        left.settimeout(1)
        with pytest.raises(ProtocolError, match=match):
            read_message(left)
    finally:
        left.close()
        right.close()


def _send_raw(port: int, payload: bytes) -> None:
    """Connect, write bytes, and close. The collector must stay up."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    try:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(1)
        sock.recv(64)
    finally:
        sock.close()


def _agent_threads() -> list[str]:
    """Return live collector or client handler threads."""
    return sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.name in {"pulseguard-collector", "pulseguard-agent"}
    )


def _wait_until(ready: object) -> None:
    """Poll until ``ready`` is true or two seconds pass."""
    if not callable(ready):
        raise AssertionError("ready must be callable")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.01)
    raise AssertionError("engine did not reach the expected state")


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    import contextlib
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()
