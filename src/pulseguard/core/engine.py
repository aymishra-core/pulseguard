"""Scheduler, bounded queue, sliding-window workers, and one SQLite writer.

The repository factory runs on the writer thread, so the SQLite connection is
created there. Other threads never call ``add_result``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any

from pulseguard.checks import build_check
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.context import HostLimiter, Timer
from pulseguard.core.dependencies import dependency_edges, flatten_monitors, root_cause
from pulseguard.core.incidents import (
    DEFAULT_FAILURES_BEFORE_OPEN,
    DEFAULT_SUCCESSES_BEFORE_RESOLVE,
    IncidentTracker,
)
from pulseguard.core.scheduler import (
    ScheduledCheck,
    index_checks,
    require_schedule_interval,
    select_due,
)
from pulseguard.exceptions import AlertError, ConfigValidationError, EngineError
from pulseguard.exporters.evidence_pack import write_evidence_pack
from pulseguard.logging_setup import LOGGER_NAME
from pulseguard.notifiers.base import Notifier
from pulseguard.storage.base import Repository

LOGGER = logging.getLogger(f"{LOGGER_NAME}.core.engine")

# Validation limits, not measurements.
MIN_WORKERS = 1
MAX_WORKERS = 32
MIN_QUEUE = 1
MAX_QUEUE = 1000
MAX_HISTORY = 1000
_REASON_LIMIT = 200
MIN_TICK_SECONDS = 0.01
MAX_TICK_SECONDS = 60.0
DEFAULT_WORKERS = 4
DEFAULT_QUEUE_SIZE = 16
DEFAULT_HOST_LIMIT = 4
DEFAULT_TICK_SECONDS = 0.2
_POLL_SECONDS = 0.05
_THREAD_PREFIX = "pulseguard"

RepositoryFactory = Callable[[], Repository]


class Engine:
    """Run due checks until ``stop`` drains the queues and joins every thread."""

    def __init__(
        self,
        checks: Sequence[ScheduledCheck],
        *,
        repository_factory: RepositoryFactory | None = None,
        dependencies: Mapping[str, Sequence[str]] | None = None,
        workers: int = DEFAULT_WORKERS,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        host_limit: int = DEFAULT_HOST_LIMIT,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
        failures_before_open: int = DEFAULT_FAILURES_BEFORE_OPEN,
        successes_before_resolve: int = DEFAULT_SUCCESSES_BEFORE_RESOLVE,
        notifier: Notifier | None = None,
        evidence_dir: Path | None = None,
    ) -> None:
        """Validate the plan. Nothing runs until ``start``."""
        if not checks:
            raise ConfigValidationError("engine needs at least one monitor")
        self._checks = tuple(checks)
        self._by_name = index_checks(self._checks)
        if len(self._by_name) != len(self._checks):
            raise ConfigValidationError("monitor names must be unique")
        for check in self._checks:
            require_schedule_interval(check.interval, check.name)
        self._factory = repository_factory
        self._edges = dict(dependencies or {})
        self._workers = _require_bound(workers, "workers", MIN_WORKERS, MAX_WORKERS)
        self._queue_size = _require_bound(queue_size, "queue_size", MIN_QUEUE, MAX_QUEUE)
        self._tick = _require_tick(tick_seconds)
        self._hosts = HostLimiter(host_limit)
        self._notifier = notifier
        self._evidence_dir = evidence_dir
        self._history: dict[str, list[CheckResult]] = {}
        self.incidents = IncidentTracker(
            failures_before_open=failures_before_open,
            successes_before_resolve=successes_before_resolve,
        )
        self._jobs: Queue[ScheduledCheck] = Queue(maxsize=self._queue_size)
        self._results: Queue[CheckResult] = Queue(maxsize=self._queue_size)
        self._stop = Event()
        self._dispatch_done = Event()
        self._lock = Lock()
        self._next_due = {check.name: 0.0 for check in self._checks}
        self._deferred = 0
        self._stored = 0
        self._max_queued = 0
        self._down: set[str] = set()
        self._writer_error: BaseException | None = None
        self._started = False
        self._scheduler: Thread | None = None
        self._dispatcher: Thread | None = None
        self._writer: Thread | None = None
        self._executor: ThreadPoolExecutor | None = None

    @property
    def deferred(self) -> int:
        """Return how many submissions were refused because a queue or host was full."""
        with self._lock:
            return self._deferred

    @property
    def stored(self) -> int:
        """Return how many results the writer stored or observed."""
        with self._lock:
            return self._stored

    @property
    def max_queued(self) -> int:
        """Return the deepest the job queue got. It cannot exceed ``queue_size``."""
        with self._lock:
            return self._max_queued

    @property
    def queue_size(self) -> int:
        """Return the configured job-queue bound."""
        return self._queue_size

    def start(self) -> None:
        """Start the scheduler, the dispatcher, the pool, and the writer."""
        if self._started:
            raise EngineError("engine is already running")
        self._stop.clear()
        self._dispatch_done.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._workers,
            thread_name_prefix=f"{_THREAD_PREFIX}-worker",
        )
        self._writer = Thread(target=self._write_loop, name=f"{_THREAD_PREFIX}-writer")
        self._dispatcher = Thread(target=self._dispatch_loop, name=f"{_THREAD_PREFIX}-dispatcher")
        self._scheduler = Thread(target=self._schedule_loop, name=f"{_THREAD_PREFIX}-scheduler")
        self._writer.start()
        self._dispatcher.start()
        self._scheduler.start()
        self._started = True

    def wait(self, seconds: float | None) -> None:
        """Block until ``seconds`` pass, or until ``stop`` if ``seconds`` is None."""
        if seconds is None:
            while not self._stop.is_set():
                self._stop.wait(0.5)
            return
        _require_duration(seconds)
        self._stop.wait(seconds)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop scheduling, drain work, and join every engine thread."""
        self._stop.set()
        self._join(self._scheduler, timeout)
        self._join(self._dispatcher, timeout)
        self._join(self._writer, timeout)
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None
        self._started = False
        if self._writer_error is not None:
            raise EngineError("writer failed") from self._writer_error

    def thread_names(self) -> list[str]:
        """Return the engine threads that are still alive."""
        alive = [self._scheduler, self._dispatcher, self._writer]
        return [thread.name for thread in alive if thread is not None and thread.is_alive()]

    def _schedule_loop(self) -> None:
        """Put due checks on the bounded queue. A full queue defers the check."""
        while not self._stop.is_set():
            now = time.monotonic()
            for check in select_due(self._checks, self._snapshot_due(), now):
                try:
                    self._jobs.put_nowait(check)
                except Full:
                    self._note_defer()
                    with self._lock:
                        self._max_queued = self._queue_size
                    continue
                with self._lock:
                    self._next_due[check.name] = now + check.interval
                    self._max_queued = max(self._max_queued, self._jobs.qsize())
            self._stop.wait(self._tick)

    def _dispatch_loop(self) -> None:
        """Keep at most ``workers`` checks in flight. Wait for FIRST_COMPLETED."""
        in_flight: set[Future[None]] = set()
        executor = self._executor
        if executor is None:
            raise EngineError("dispatcher started without a pool")
        try:
            while True:
                while len(in_flight) < self._workers:
                    try:
                        check = self._jobs.get(timeout=_POLL_SECONDS)
                    except Empty:
                        break
                    in_flight.add(executor.submit(self._run_check, check))
                if in_flight:
                    done, in_flight = wait(
                        in_flight,
                        timeout=_POLL_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        future.result()
                elif self._stop.is_set() and self._jobs.empty():
                    break
        finally:
            self._dispatch_done.set()

    def _run_check(self, check: ScheduledCheck) -> None:
        """Run one check. A host at its cap is deferred. Errors become results."""
        try:
            with self._hosts.slot(check.target) as acquired:
                if not acquired:
                    self._note_defer()
                    return
                with Timer():
                    result = check.run()
        except Exception as exc:
            LOGGER.warning(
                "check failed monitor=%s error=%s",
                check.name,
                type(exc).__name__,
            )
            result = _failed_result(check, exc)
        self._results.put(result)

    def _write_loop(self) -> None:
        """Open the repository here and write results. This is the only writer."""
        repository: Repository | None = None
        try:
            if self._factory is not None:
                repository = self._factory()
            while True:
                try:
                    result = self._results.get(timeout=_POLL_SECONDS)
                except Empty:
                    if self._dispatch_done.is_set() and self._results.empty():
                        break
                    continue
                self._observe(result, repository)
        except Exception as exc:
            LOGGER.warning("writer failed error=%s", type(exc).__name__)
            self._writer_error = exc
        finally:
            if repository is not None:
                repository.close()

    def _observe(self, result: CheckResult, repository: Repository | None) -> None:
        """Update incidents, then store the row when a repository exists."""
        cause = root_cause(result.monitor, set(self._down), self._edges)
        if result.ok:
            self._down.discard(result.monitor)
        else:
            self._down.add(result.monitor)
        suppressed = cause is not None and not result.ok
        action = self.incidents.observe(result, suppressed=suppressed)
        self._alert(result, action)
        if repository is not None:
            repository.add_result(result)
        with self._lock:
            self._stored += 1

    def _alert(self, result: CheckResult, action: str | None) -> None:
        """Write the evidence pack on open, then deliver one alert per transition.

        The incident log line is already written inside the tracker, before this
        method runs. A suppressed result has ``action is None`` and is not mailed.
        """
        if self._evidence_dir is not None:
            rows = self._history.setdefault(result.monitor, [])
            rows.append(result)
            if len(rows) > MAX_HISTORY:
                del rows[: len(rows) - MAX_HISTORY]
            if action == "open":
                try:
                    write_evidence_pack(
                        self._evidence_dir,
                        monitor=result.monitor,
                        opened_at=result.checked_at,
                        results=list(rows),
                        now=utc_now(),
                    )
                except AlertError:
                    LOGGER.warning("evidence pack failed monitor=%s", result.monitor)
        if self._notifier is None:
            return
        if action is not None:
            try:
                self._notifier.send(_incident_notice(result, action))
            except Exception as exc:
                LOGGER.warning(
                    "alert delivery failed monitor=%s error=%s",
                    result.monitor,
                    type(exc).__name__,
                )
        note = getattr(self._notifier, "note", None)
        if callable(note):
            try:
                note(result)
            except Exception as exc:
                LOGGER.warning(
                    "digest note failed monitor=%s error=%s",
                    result.monitor,
                    type(exc).__name__,
                )

    def _snapshot_due(self) -> dict[str, float]:
        """Copy due times so the scheduler can read them without holding the lock."""
        with self._lock:
            return dict(self._next_due)

    def _note_defer(self) -> None:
        """Count one refused submission."""
        with self._lock:
            self._deferred += 1

    def _join(self, thread: Thread | None, timeout: float) -> None:
        """Join one thread. A thread that is still alive after the timeout is an error."""
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout)
        if thread.is_alive():
            raise EngineError(f"{thread.name} did not stop within {timeout:g} seconds")


def load_plan(
    path: Path,
) -> tuple[list[ScheduledCheck], dict[str, tuple[str, ...]], dict[str, int]]:
    """Load monitors, optional groups, and the engine object from JSON."""
    data = _read_object(path)
    raw_monitors = data.get("monitors")
    if not isinstance(raw_monitors, list) or not raw_monitors:
        raise ConfigValidationError("monitors must be a non-empty list")
    flat = flatten_monitors(raw_monitors)
    if not flat:
        raise ConfigValidationError("monitors must be a non-empty list")
    names = {item["name"] for item in flat if isinstance(item.get("name"), str)}
    edges = dependency_edges(flat, names)
    checks: list[ScheduledCheck] = []
    for item in flat:
        spec = {key: value for key, value in item.items() if key != "depends_on"}
        checks.append(build_check(spec))
    settings = _engine_settings(data.get("engine", {}))
    return checks, edges, settings


def _engine_settings(raw: object) -> dict[str, int]:
    """Read the optional engine object. Missing keys keep the defaults."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigValidationError("engine must be an object")
    allowed = {
        "workers",
        "queue_size",
        "host_limit",
        "failures_before_open",
        "successes_before_resolve",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigValidationError(f"unknown engine fields: {', '.join(unknown)}")
    return {
        "workers": _optional_int(raw, "workers", DEFAULT_WORKERS),
        "queue_size": _optional_int(raw, "queue_size", DEFAULT_QUEUE_SIZE),
        "host_limit": _optional_int(raw, "host_limit", DEFAULT_HOST_LIMIT),
        "failures_before_open": _optional_int(
            raw, "failures_before_open", DEFAULT_FAILURES_BEFORE_OPEN
        ),
        "successes_before_resolve": _optional_int(
            raw, "successes_before_resolve", DEFAULT_SUCCESSES_BEFORE_RESOLVE
        ),
    }


def _read_object(path: Path) -> Mapping[str, Any]:
    """Read a JSON object from ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ConfigValidationError("config root must be an object")
    return data


def _optional_int(raw: Mapping[str, Any], field: str, default: int) -> int:
    """Return an int field or the default when it is absent."""
    if field not in raw:
        return default
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    return value


def _require_bound(value: object, field: str, low: int, high: int) -> int:
    """Return an integer inside ``low``..``high``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    if value < low or value > high:
        raise ConfigValidationError(f"{field} must be from {low} to {high}")
    return value


def _require_tick(value: object) -> float:
    """Return the scheduler poll interval."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError("tick_seconds must be a number")
    if value < MIN_TICK_SECONDS or value > MAX_TICK_SECONDS:
        raise ConfigValidationError(
            f"tick_seconds must be from {MIN_TICK_SECONDS:g} to {MAX_TICK_SECONDS:g}"
        )
    return float(value)


def _require_duration(value: float) -> float:
    """Reject a non-positive duration. The upper bound matches the longest interval."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError("duration must be a number")
    if value <= 0 or value > 86_400:
        raise ConfigValidationError("duration must be greater than 0 and at most 86400 seconds")
    return float(value)


def _failed_result(check: ScheduledCheck, exc: Exception) -> CheckResult:
    """Turn a worker exception into a failed result. The target is not included."""
    return CheckResult(
        monitor=check.name,
        kind=check.kind,
        ok=False,
        latency_ms=0,
        message=f"check failed: {type(exc).__name__}",
        severity=Severity.CRITICAL,
        checked_at=utc_now(),
    )


def _incident_notice(result: CheckResult, action: str) -> CheckResult:
    """Build the one result a notifier sees for an open or resolved incident."""
    reason = " ".join(result.message.split())[:_REASON_LIMIT]
    opened = action == "open"
    severity = result.severity if opened else Severity.OK
    if opened and severity <= Severity.OK:
        severity = Severity.CRITICAL
    label = "incident opened" if opened else "incident resolved"
    return CheckResult(
        monitor=result.monitor,
        kind=result.kind,
        ok=not opened,
        latency_ms=result.latency_ms,
        message=f"{label}: {reason}",
        severity=severity,
        checked_at=result.checked_at,
        status_code=result.status_code,
    )
