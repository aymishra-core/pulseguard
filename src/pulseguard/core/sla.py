"""SLA rollups over stored results.

``map``, ``filter``, and ``reduce`` do the work. ``LatencyStats.__add__`` is
the reducer. Targets passed in here are configuration, not measured uptime.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import reduce

from pulseguard.checks.models import CheckResult, LatencyStats
from pulseguard.exceptions import ConfigValidationError


@dataclass(frozen=True)
class SlaReport:
    """One window of results. ``availability`` is None when the window is empty."""

    count: int
    successes: int
    availability: float | None
    latency: LatencyStats


def rollup(results: Sequence[CheckResult]) -> SlaReport:
    """Count successes and fold latency. An empty window stays empty."""
    flags = list(map(lambda result: result.ok, results))
    successes = list(filter(lambda flag: flag, flags))
    empty = LatencyStats(0, 0.0, 0.0)
    if not results:
        return SlaReport(count=0, successes=0, availability=None, latency=empty)
    latency = reduce(
        lambda total, result: total + LatencyStats(1, result.latency_ms, result.latency_ms),
        results,
        empty,
    )
    return SlaReport(
        count=len(results),
        successes=len(successes),
        availability=len(successes) / len(results),
        latency=latency,
    )


def error_budget_remaining(report: SlaReport, *, target: float = 0.99) -> float | None:
    """Return how much of the error budget is left. Negative means it is spent.

    ``target`` is the configured availability, such as 0.99. It is not a
    measurement from this project.
    """
    if report.availability is None:
        return None
    goal = _require_target(target)
    spent = (1 - report.availability) / (1 - goal)
    return 1 - spent


def _require_target(target: object) -> float:
    """Reject a target outside (0, 1). Booleans are not numbers here."""
    if isinstance(target, bool) or not isinstance(target, (int, float)):
        raise ConfigValidationError("sla target must be a number")
    if target <= 0 or target >= 1:
        raise ConfigValidationError("sla target must be between 0 and 1")
    return float(target)
