"""The five inheritance forms, plus swappable notifiers and repositories."""

from __future__ import annotations

from abc import ABC
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path

import pytest

from pulseguard.checks import (
    AlertThresholdMixin,
    BaseCheck,
    CheckMeta,
    DefacementCheck,
    DnsCheck,
    HeartbeatCheck,
    HttpCheck,
    KeywordCheck,
    SerializableMixin,
    TcpCheck,
    TlsExpiryCheck,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.cli import load_monitor_file
from pulseguard.exceptions import StorageError
from pulseguard.notifiers import ConsoleNotifier, Notifier
from pulseguard.storage import MonitorRecord, Repository

ROOT = Path(__file__).resolve().parents[1]
_HASH = "2689367b205c16ce32ed4200942b8b8b1e262dfc70d9bc9fbc77c49699a4f1df"


def test_mro_covers_every_inheritance_form() -> None:
    """Single, multilevel, hierarchical, multiple, and hybrid inheritance."""
    assert HttpCheck.__mro__ == (HttpCheck, BaseCheck, ABC, object)
    assert KeywordCheck.__mro__ == (KeywordCheck, HttpCheck, BaseCheck, ABC, object)
    hierarchical = (TcpCheck, DnsCheck, HeartbeatCheck)
    for check_cls in hierarchical:
        assert check_cls.__mro__ == (check_cls, BaseCheck, ABC, object)
    assert TlsExpiryCheck.__mro__ == (
        TlsExpiryCheck,
        SerializableMixin,
        BaseCheck,
        ABC,
        object,
    )
    assert DefacementCheck.__mro__ == (
        DefacementCheck,
        KeywordCheck,
        HttpCheck,
        BaseCheck,
        ABC,
        AlertThresholdMixin,
        object,
    )
    assert issubclass(KeywordCheck, HttpCheck)
    assert issubclass(DefacementCheck, KeywordCheck)
    assert issubclass(TlsExpiryCheck, SerializableMixin)
    assert issubclass(DefacementCheck, AlertThresholdMixin)


def test_example_config_loads_every_kind() -> None:
    """Every shipped kind builds through the metaclass registry and is not run."""
    loaded = load_monitor_file(ROOT / "config" / "monitors.example.json")
    kinds = [check.kind for check in loaded]
    assert kinds == ["http", "keyword", "defacement", "tcp", "dns", "tls", "heartbeat"]
    for check in loaded:
        assert type(check) is CheckMeta.registry[check.kind]
        assert isinstance(check, BaseCheck)


def test_threshold_mixin_is_name_mangled() -> None:
    """``alert_after`` is stored on the mixin, not as a public ``__alert_after``."""
    check = DefacementCheck(
        "page",
        "http://127.0.0.1/health",
        keyword="ok",
        expected_sha256=_HASH,
        alert_after=3,
    )
    assert check.alert_after == 3
    assert check._AlertThresholdMixin__alert_after == 3  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del check.__alert_after  # type: ignore[attr-defined]


def test_tls_mapping_comes_from_the_mixin() -> None:
    """``to_mapping`` is the multiple-inheritance behaviour, not a BaseCheck method."""
    check = TlsExpiryCheck("cert", "127.0.0.1", port=443)
    assert not hasattr(BaseCheck, "to_mapping")
    assert check.to_mapping() == {
        "name": "cert",
        "kind": "tls",
        "target": "127.0.0.1",
        "port": 443,
        "allow_private": True,
    }


def test_notifiers_are_swappable() -> None:
    """``send`` works for a console notifier and a second implementation."""
    result = _sample()
    console = ConsoleNotifier()
    bucket = _BucketNotifier()

    def deliver(notifier: Notifier) -> None:
        notifier.send(result)

    deliver(console)
    deliver(bucket)
    assert console.lines == ("web ok=True severity=ok HTTP 200",)
    assert bucket.results == [result]


def test_repositories_are_swappable() -> None:
    """The same call stores a result in two ``Repository`` implementations."""
    result = _sample()
    memory = _MemoryRepository()
    counting = _CountingRepository()
    record = MonitorRecord(
        name="web",
        kind="http",
        target="http://127.0.0.1/health",
        interval_seconds=60,
        enabled=True,
    )

    def store(repo: Repository) -> list[CheckResult]:
        repo.add_result(result)
        repo.save_monitor(record)
        repo.update_monitor("web", enabled=False)
        return repo.results_for(result.monitor)

    assert store(memory) == [result]
    assert list(memory.iter_results("web", chunk_size=1)) == [result]
    assert store(counting) == [result]
    assert counting.count == 1
    assert memory.monitors()[0].enabled is False
    assert counting.monitors()[0].enabled is False


class _BucketNotifier(Notifier):
    """Test double that keeps the results it was sent."""

    def __init__(self) -> None:
        """Start empty."""
        self.results: list[CheckResult] = []

    def send(self, result: CheckResult) -> None:
        """Remember ``result``."""
        self.results.append(result)


class _MemoryRepository(Repository):
    """Test double that groups results by monitor name."""

    def __init__(self) -> None:
        """Start empty."""
        self._rows: dict[str, list[tuple[int, CheckResult]]] = {}
        self._next = 1
        self._monitors: dict[str, MonitorRecord] = {}

    def add_result(self, result: CheckResult) -> int:
        """Append ``result`` under its monitor name."""
        new_id = self._next
        self._next += 1
        self._rows.setdefault(result.monitor, []).append((new_id, result))
        return new_id

    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return a copy of the stored results."""
        return [result for _row_id, result in self._rows.get(monitor, [])]

    def delete_result(self, result_id: int) -> bool:
        """Delete one stored result."""
        for name, rows in self._rows.items():
            kept = [pair for pair in rows if pair[0] != result_id]
            if len(kept) != len(rows):
                self._rows[name] = kept
                return True
        return False

    def delete_older_than(self, cutoff: datetime) -> int:
        """Delete results older than ``cutoff``."""
        removed = 0
        for name, rows in list(self._rows.items()):
            kept = [pair for pair in rows if pair[1].checked_at >= cutoff]
            removed += len(rows) - len(kept)
            self._rows[name] = kept
        return removed

    def save_monitor(self, record: MonitorRecord) -> None:
        """Store ``record`` under its name."""
        self._monitors[record.name] = record

    def update_monitor(
        self,
        name: str,
        *,
        interval_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Change the interval, the enabled flag, or both."""
        current = self._monitors.get(name)
        if current is None:
            raise StorageError(f"unknown monitor {name!r}")
        self._monitors[name] = MonitorRecord(
            name=current.name,
            kind=current.kind,
            target=current.target,
            interval_seconds=current.interval_seconds
            if interval_seconds is None
            else interval_seconds,
            enabled=current.enabled if enabled is None else enabled,
        )

    def monitors(self) -> list[MonitorRecord]:
        """Return catalog rows sorted by name."""
        return [self._monitors[name] for name in sorted(self._monitors)]

    def delete_monitor(self, name: str) -> bool:
        """Delete a catalog row."""
        return self._monitors.pop(name, None) is not None

    def transaction(self) -> AbstractContextManager[None]:
        """Roll the double back when the body raises."""
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Restore the previous rows if the body raises."""
        saved_rows = {key: list(value) for key, value in self._rows.items()}
        saved_next = self._next
        saved_monitors = dict(self._monitors)
        try:
            yield
        except Exception:
            self._rows = saved_rows
            self._next = saved_next
            self._monitors = saved_monitors
            raise


class _CountingRepository(_MemoryRepository):
    """Test double that counts writes."""

    def __init__(self) -> None:
        """Start at zero."""
        super().__init__()
        self.count = 0

    def add_result(self, result: CheckResult) -> int:
        """Count and store ``result``."""
        self.count += 1
        return super().add_result(result)


def _sample() -> CheckResult:
    """A passing HTTP result for the polymorphism tests."""
    return CheckResult(
        monitor="web",
        kind="http",
        ok=True,
        latency_ms=1,
        message="HTTP 200",
        severity=Severity.OK,
        checked_at=utc_now(),
        status_code=200,
    )
