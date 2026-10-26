"""Incident streaks, dependency cycles, and SLA rollups."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pulseguard.checks.models import CheckResult, LatencyStats, Severity, utc_now
from pulseguard.core.dependencies import dependency_edges, flatten_monitors, root_cause
from pulseguard.core.engine import Engine, load_plan
from pulseguard.core.incidents import IncidentTracker
from pulseguard.core.sla import error_budget_remaining, rollup
from pulseguard.exceptions import ConfigValidationError


def test_incident_opens_after_three_failures_and_cannot_close_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """K is 3. Open and resolve are each logged once, at INFO, with the monitor name."""
    caplog.set_level(logging.INFO, logger="pulseguard.core.incidents")
    tracker = IncidentTracker(failures_before_open=3, successes_before_resolve=2)
    when = utc_now()
    assert tracker.observe(_result(ok=True, when=when), suppressed=False) is None
    assert tracker.observe(_result(ok=False, when=when), suppressed=False) is None
    assert tracker.observe(_result(ok=False, when=when), suppressed=False) is None
    assert tracker.observe(_result(ok=False, when=when), suppressed=False) == "open"
    assert tracker.is_open("web") is True
    assert tracker.observe(_result(ok=False, when=when), suppressed=False) is None
    assert tracker.observe(_result(ok=True, when=when), suppressed=False) is None
    assert tracker.observe(_result(ok=True, when=when), suppressed=False) == "resolved"
    assert tracker.is_open("web") is False
    assert [item.action for item in tracker.transitions] == ["open", "resolved"]
    with pytest.raises(AssertionError, match="cannot close before it opens"):
        tracker.mark_resolved("web", when)
    records = [record for record in caplog.records if record.name == "pulseguard.core.incidents"]
    messages = [record.getMessage() for record in records]
    assert messages.count("incident opened monitor=web") == 1
    assert messages.count("incident resolved monitor=web") == 1
    assert all(record.levelname == "INFO" for record in records)


def test_a_suppressed_failure_does_not_open(caplog: pytest.LogCaptureFixture) -> None:
    """A failure hidden by a down dependency leaves the streak alone and is not logged."""
    caplog.set_level(logging.INFO, logger="pulseguard.core.incidents")
    tracker = IncidentTracker(failures_before_open=1, successes_before_resolve=1)
    assert tracker.observe(_result(ok=False), suppressed=True) is None
    assert tracker.is_open("web") is False
    assert "incident opened" not in caplog.text


def test_root_cause_walks_to_the_furthest_down_monitor() -> None:
    """C depends on B and B depends on A. A is the alert."""
    edges = {"b": ("a",), "c": ("b",)}
    assert root_cause("c", {"a", "b"}, edges) == "a"
    assert root_cause("a", set(), edges) is None
    assert root_cause("b", set(), edges) is None


def test_dependency_cycles_and_groups_are_rejected(tmp_path: Path) -> None:
    """Cycles fail at load. A nested group flattens before the checks are built."""
    names = {"a", "b"}
    specs = (
        {"name": "a", "depends_on": ["b"]},
        {"name": "b", "depends_on": ["a"]},
    )
    with pytest.raises(ConfigValidationError, match="cycle"):
        dependency_edges(specs, names)
    with pytest.raises(ConfigValidationError, match="itself"):
        dependency_edges(({"name": "a", "depends_on": ["a"]},), {"a"})
    flat = flatten_monitors(
        [
            {"kind": "http", "name": "edge", "target": "http://127.0.0.1/health"},
            {
                "monitors": [
                    {"kind": "tcp", "name": "port", "target": "127.0.0.1", "port": 9},
                ]
            },
        ]
    )
    assert [item["name"] for item in flat] == ["edge", "port"]
    path = tmp_path / "monitors.json"
    path.write_text(
        json.dumps(
            {
                "monitors": [
                    {
                        "kind": "http",
                        "name": "root",
                        "target": "http://127.0.0.1/health",
                        "interval": 30,
                    },
                    {
                        "monitors": [
                            {
                                "kind": "http",
                                "name": "child",
                                "target": "http://127.0.0.1/health",
                                "interval": 30,
                                "depends_on": ["root"],
                            }
                        ]
                    },
                ],
                "engine": {"workers": 1, "queue_size": 2, "failures_before_open": 3},
            }
        ),
        encoding="utf-8",
    )
    checks, edges, settings = load_plan(path)
    assert [check.name for check in checks] == ["root", "child"]
    assert edges["child"] == ("root",)
    assert settings["workers"] == 1
    engine = Engine(
        checks,
        dependencies=edges,
        failures_before_open=1,
        workers=1,
        queue_size=2,
    )
    engine._observe(_result(ok=False, monitor="root"), None)
    engine._observe(_result(ok=False, monitor="child"), None)
    assert engine.incidents.is_open("root") is True
    assert engine.incidents.is_open("child") is False


def test_sla_rollup_uses_map_filter_and_reduce() -> None:
    """Two samples: one success. The error budget is the configured target, not a measurement."""
    report = rollup([_result(ok=True, latency=10), _result(ok=False, latency=30)])
    assert report.count == 2
    assert report.successes == 1
    assert report.availability == 0.5
    assert report.latency == LatencyStats(count=2, total_ms=40, max_ms=30)
    assert error_budget_remaining(report, target=0.5) == 0
    empty = rollup([])
    assert empty.availability is None
    assert error_budget_remaining(empty) is None
    with pytest.raises(ConfigValidationError):
        error_budget_remaining(report, target=1)


def _result(
    *,
    ok: bool,
    monitor: str = "web",
    when: object | None = None,
    latency: float = 1,
) -> CheckResult:
    """Build one result for the state machine. Nothing is contacted."""
    from datetime import datetime

    moment = when if isinstance(when, datetime) else utc_now()
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=latency,
        message="HTTP 200" if ok else "HTTP 503",
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=moment,
        status_code=200 if ok else 503,
    )
