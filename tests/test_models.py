"""Operator-overloading tests for the phase 1 models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pulseguard.checks.models import (
    CheckResult,
    IncidentCursor,
    IncidentLog,
    LatencyStats,
    Severity,
    utc_now,
)
from pulseguard.exceptions import ConfigValidationError


def test_severity_orders_and_rejects_unknown_names() -> None:
    """``Severity.__lt__`` orders ok < info < warning < critical."""
    assert Severity.OK < Severity.INFO < Severity.WARNING < Severity.CRITICAL
    assert not Severity.CRITICAL < Severity.WARNING
    assert Severity("OK") == Severity.OK
    assert hash(Severity("critical")) == hash(Severity.CRITICAL)
    ordered = sorted([Severity.CRITICAL, Severity.OK, Severity.WARNING])
    assert ordered == [Severity.OK, Severity.WARNING, Severity.CRITICAL]
    with pytest.raises(TypeError):
        _ = Severity.OK < "warning"  # type: ignore[operator]
    assert Severity.OK != "ok"
    assert repr(Severity.WARNING) == "Severity('warning')"
    with pytest.raises(ConfigValidationError):
        Severity("panic")
    with pytest.raises(ConfigValidationError):
        Severity(1)  # type: ignore[arg-type]


def test_latency_stats_add_and_mean() -> None:
    """``LatencyStats.__add__`` combines two windows and ignores an empty one."""
    left = LatencyStats(count=1, total_ms=10, max_ms=10)
    right = LatencyStats(count=3, total_ms=30, max_ms=20)
    combined = left + right
    assert combined.count == 4
    assert combined.total_ms == 40
    assert combined.max_ms == 20
    assert combined.mean_ms == 10
    empty = LatencyStats(count=0, total_ms=0, max_ms=0)
    assert empty.mean_ms is None
    assert left + empty == left
    assert empty + right == right
    with pytest.raises(TypeError):
        _ = left + 1  # type: ignore[operator]
    with pytest.raises(AssertionError):
        LatencyStats(count=-1, total_ms=0, max_ms=0)
    with pytest.raises(AssertionError):
        LatencyStats(count=0, total_ms=1, max_ms=0)


def _result(**overrides: object) -> CheckResult:
    """Build a valid result and apply overrides."""
    payload: dict[str, object] = {
        "monitor": "web",
        "kind": "http",
        "ok": True,
        "latency_ms": 1.5,
        "message": "HTTP 200",
        "severity": Severity.OK,
        "checked_at": utc_now(),
        "status_code": 200,
    }
    payload.update(overrides)
    return CheckResult(**payload)  # type: ignore[arg-type]


def test_check_result_equality_ignores_clock_and_latency() -> None:
    """``__eq__`` and ``__hash__`` dedupe the same outcome."""
    first = _result()
    second = _result(latency_ms=50.0, checked_at=utc_now() + timedelta(minutes=5))
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert first != _result(
        message="HTTP 500", ok=False, severity=Severity.CRITICAL, status_code=500
    )
    assert first != "HTTP 200"
    assert "status_code" in first.as_dict()


def test_check_result_invariants() -> None:
    """Assertions reject a negative latency, a naive clock, and a broken severity."""
    with pytest.raises(AssertionError):
        _result(latency_ms=-0.1)
    with pytest.raises(AssertionError):
        _result(checked_at=datetime(2026, 1, 1))
    with pytest.raises(AssertionError):
        _result(ok=True, severity=Severity.CRITICAL)
    with pytest.raises(AssertionError):
        _result(ok=False, severity=Severity.OK, message="down", status_code=500)
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    created = _result(checked_at=aware)
    assert created.checked_at == aware


def test_incident_log_len_iter_and_contains() -> None:
    """``IncidentLog`` supports len, a custom iterator, and membership."""
    log = IncidentLog([" opened ", "acked"])
    log.append("closed")
    assert len(log) == 3
    assert list(log) == ["opened", "acked", "closed"]
    assert list(log) == ["opened", "acked", "closed"]
    cursor = iter(log)
    assert isinstance(cursor, IncidentCursor)
    assert iter(cursor) is cursor
    assert "acked" in log
    assert "missing" not in log
    assert 1 not in log
    with pytest.raises(ValueError):
        log.append("  ")
    with pytest.raises(TypeError):
        log.append(None)  # type: ignore[arg-type]
