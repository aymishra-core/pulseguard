"""The five inheritance forms, plus swappable notifiers and repositories."""

from __future__ import annotations

from abc import ABC
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
from pulseguard.notifiers import ConsoleNotifier, Notifier
from pulseguard.storage import Repository

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

    def store(repo: Repository) -> list[CheckResult]:
        repo.add_result(result)
        return repo.results_for(result.monitor)

    assert store(memory) == [result]
    assert store(counting) == [result]
    assert counting.count == 1


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
        self._rows: dict[str, list[CheckResult]] = {}

    def add_result(self, result: CheckResult) -> None:
        """Append ``result`` under its monitor name."""
        self._rows.setdefault(result.monitor, []).append(result)

    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return a copy of the stored results."""
        return list(self._rows.get(monitor, []))


class _CountingRepository(Repository):
    """Test double that counts writes."""

    def __init__(self) -> None:
        """Start at zero."""
        self.count = 0
        self._rows: list[CheckResult] = []

    def add_result(self, result: CheckResult) -> None:
        """Count and store ``result``."""
        self.count += 1
        self._rows.append(result)

    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return results for ``monitor``."""
        return [row for row in self._rows if row.monitor == monitor]


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
