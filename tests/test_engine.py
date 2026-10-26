"""Shutdown joins every engine thread, and a full queue defers work."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from threading import Event

import pytest

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.cli import main
from pulseguard.core.context import HostLimiter, Timer
from pulseguard.core.engine import Engine
from pulseguard.core.scheduler import ScheduledCheck
from pulseguard.exceptions import ConfigValidationError, EngineError
from pulseguard.storage import SqliteRepository
from tests.mock_servers import MockHttpServer


def test_stop_joins_every_engine_thread(tmp_path: Path) -> None:
    """After stop, no pulseguard thread is alive and the writer was the only inserter."""
    names: list[str] = []
    database = tmp_path / "history.db"
    engine = Engine(
        [_OkCheck("web")],
        repository_factory=lambda: WatchingRepository(database, names),
        workers=2,
        queue_size=4,
        tick_seconds=0.05,
    )
    before = _pulseguard_threads()
    engine.start()
    _wait_until(lambda: engine.stored >= 1)
    engine.stop()
    assert engine.stored >= 1
    assert names
    assert set(names) == {"pulseguard-writer"}
    assert _pulseguard_threads() == before
    with SqliteRepository(database) as repo:
        assert repo.results_for("web")[0].message == "HTTP 200"
    engine.start()
    with pytest.raises(EngineError, match="already running"):
        engine.start()
    engine.stop()


def test_a_full_queue_defers_instead_of_growing(tmp_path: Path) -> None:
    """One blocked worker and a queue of two refuse the rest. Depth stays at the cap."""
    del tmp_path
    gate = Event()
    checks: list[ScheduledCheck] = [
        _GateCheck(f"job-{index}", gate, target=f"http://127.0.0.1/{index}") for index in range(6)
    ]
    engine = Engine(checks, workers=1, queue_size=2, host_limit=4, tick_seconds=0.05)
    engine.start()
    try:
        _wait_until(lambda: engine.deferred >= 1 and engine.max_queued == 2)
        assert engine.max_queued == engine.queue_size
        assert engine.deferred >= 1
    finally:
        gate.set()
        engine.stop()
    assert _pulseguard_threads() == []


def test_a_worker_exception_is_stored_without_the_target(tmp_path: Path) -> None:
    """The pool stays up. The message names the exception type and not the URL."""
    database = tmp_path / "history.db"
    engine = Engine(
        [_BoomCheck()],
        repository_factory=lambda: SqliteRepository(database),
        workers=1,
        queue_size=2,
        tick_seconds=0.05,
        failures_before_open=1,
    )
    engine.start()
    try:
        _wait_until(lambda: engine.stored >= 1)
    finally:
        engine.stop()
    assert engine.incidents.is_open("boom") is True
    with SqliteRepository(database) as repo:
        row = repo.results_for("boom")[0]
    assert row.ok is False
    assert row.message == "check failed: RuntimeError"
    assert "secret.example" not in row.message


def test_host_limiter_refuses_a_second_slot() -> None:
    """The per-host cap yields False instead of waiting."""
    limiter = HostLimiter(1)
    with limiter.slot("127.0.0.1") as first:
        assert first is True
        with limiter.slot("127.0.0.1") as second:
            assert second is False
    with limiter.slot("127.0.0.1") as third:
        assert third is True
    with Timer() as clock:
        time.sleep(0.01)
    assert clock.elapsed >= 0.01
    with pytest.raises(ConfigValidationError):
        HostLimiter(0)


def test_run_command_stores_a_local_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``pulseguard run`` writes through the writer thread and then exits."""
    monkeypatch.delenv("PULSEGUARD_SMTP_HOST", raising=False)
    database = tmp_path / "history.db"
    config = tmp_path / "monitors.json"
    with MockHttpServer() as server:
        config.write_text(
            json.dumps(
                {
                    "monitors": [
                        {
                            "kind": "http",
                            "name": "demo",
                            "target": f"{server.base_url}/ok",
                            "interval": 30,
                            "timeout": 2,
                            "retries": 1,
                        }
                    ],
                    "engine": {"workers": 1, "queue_size": 2},
                }
            ),
            encoding="utf-8",
        )
        code, text, err = _run(
            ["run", "--config", str(config), "--db", str(database), "--duration", "1"]
        )
    assert code == 0, err
    assert "stored" in text
    assert _pulseguard_threads() == []
    with SqliteRepository(database) as repo:
        assert repo.results_for("demo")[0].ok is True


class _OkCheck:
    """Instant passing check. It does not open a socket."""

    kind = "http"

    def __init__(self, name: str) -> None:
        """Store the monitor name."""
        self.name = name
        self.target = f"http://127.0.0.1/{name}"
        self.interval = 30.0

    def run(self) -> CheckResult:
        """Return one passing result."""
        return CheckResult(
            monitor=self.name,
            kind=self.kind,
            ok=True,
            latency_ms=1,
            message="HTTP 200",
            severity=Severity.OK,
            checked_at=utc_now(),
            status_code=200,
        )


class _GateCheck:
    """Blocks in ``run`` until the gate opens."""

    kind = "http"

    def __init__(self, name: str, gate: Event, *, target: str) -> None:
        """Store the gate and a distinct target so hosts do not share a cap."""
        self.name = name
        self.target = target
        self.interval = 30.0
        self._gate = gate

    def run(self) -> CheckResult:
        """Wait, then return a passing result."""
        self._gate.wait(timeout=3)
        return CheckResult(
            monitor=self.name,
            kind=self.kind,
            ok=True,
            latency_ms=1,
            message="HTTP 200",
            severity=Severity.OK,
            checked_at=utc_now(),
            status_code=200,
        )


class _BoomCheck:
    """Raises before any I/O. The target must not leak into the result."""

    kind = "http"

    def __init__(self) -> None:
        """Store a target the assertion can look for."""
        self.name = "boom"
        self.target = "http://secret.example/health"
        self.interval = 30.0

    def run(self) -> CheckResult:
        """Fail with a runtime error."""
        raise RuntimeError("secret.example")


class WatchingRepository(SqliteRepository):
    """Record the thread that inserts. The factory builds this on the writer thread."""

    def __init__(self, path: Path, names: list[str]) -> None:
        """Open the file and keep the shared name list."""
        super().__init__(path)
        self._names = names

    def add_result(self, result: CheckResult) -> int:
        """Append the writer thread name, then insert."""
        self._names.append(threading.current_thread().name)
        return super().add_result(result)


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


def _pulseguard_threads() -> list[str]:
    """Return live threads the engine started."""
    return sorted(
        thread.name for thread in threading.enumerate() if thread.name.startswith("pulseguard")
    )


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    import contextlib
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()
