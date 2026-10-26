"""Choose monitors that are due. The engine thread calls this each tick."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar, Protocol

from pulseguard.checks.base import MIN_INTERVAL_SECONDS
from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError


class ScheduledCheck(Protocol):
    """Anything the scheduler can run. Checks satisfy this without a new base."""

    name: str
    kind: ClassVar[str]
    interval: float

    @property
    def target(self) -> str:
        """Return the target key used for the per-host cap."""
        ...

    def run(self) -> CheckResult:
        """Run once and return the outcome."""
        ...


def index_checks(checks: Sequence[ScheduledCheck]) -> dict[str, ScheduledCheck]:
    """Index monitors by name. ``map`` plus a lambda is the higher-order step."""
    pairs = map(lambda check: (check.name, check), checks)
    return dict(pairs)


def select_due(
    checks: Sequence[ScheduledCheck],
    next_due: Mapping[str, float],
    now: float,
) -> list[ScheduledCheck]:
    """Return monitors whose next due time is at or before ``now``."""
    return [check for check in checks if now >= next_due.get(check.name, 0.0)]


def require_schedule_interval(value: float, name: str) -> float:
    """Refuse an interval below the check minimum so the loop cannot spin."""
    if value < MIN_INTERVAL_SECONDS:
        raise ConfigValidationError(
            f"{name} interval must be at least {MIN_INTERVAL_SECONDS:g} seconds"
        )
    return value
