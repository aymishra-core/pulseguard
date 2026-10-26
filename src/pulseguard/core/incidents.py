"""Incident state machine.

An incident opens after K consecutive failures and resolves after M consecutive
successes. It cannot close before it opens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError

# Same bounds as a defacement alert_after. They are limits, not measurements.
MIN_STREAK = 1
MAX_STREAK = 20
DEFAULT_FAILURES_BEFORE_OPEN = 3
DEFAULT_SUCCESSES_BEFORE_RESOLVE = 1


@dataclass(frozen=True)
class IncidentTransition:
    """One open or resolved edge for a monitor."""

    monitor: str
    action: str
    at: datetime


@dataclass
class _Slot:
    """Streaks for one monitor. ``state`` is ``clear`` or ``open``."""

    failures: int = 0
    successes: int = 0
    state: str = "clear"


class IncidentTracker:
    """Count consecutive results and open or resolve incidents."""

    def __init__(
        self,
        *,
        failures_before_open: int = DEFAULT_FAILURES_BEFORE_OPEN,
        successes_before_resolve: int = DEFAULT_SUCCESSES_BEFORE_RESOLVE,
    ) -> None:
        """Store K and M."""
        self._failures_before_open = require_streak(failures_before_open, "failures_before_open")
        self._successes_before_resolve = require_streak(
            successes_before_resolve, "successes_before_resolve"
        )
        self._slots: dict[str, _Slot] = {}
        self.transitions: list[IncidentTransition] = []

    def observe(self, result: CheckResult, *, suppressed: bool) -> str | None:
        """Apply one result. Suppressed failures do not move the streak.

        Returns ``open``, ``resolved``, or None when the state did not change.
        """
        if suppressed:
            return None
        slot = self._slots.setdefault(result.monitor, _Slot())
        if result.ok:
            slot.failures = 0
            slot.successes += 1
            if slot.state == "open" and slot.successes >= self._successes_before_resolve:
                self.mark_resolved(result.monitor, result.checked_at)
                return "resolved"
            return None
        slot.successes = 0
        slot.failures += 1
        if slot.state != "open" and slot.failures >= self._failures_before_open:
            slot.state = "open"
            slot.successes = 0
            self.transitions.append(IncidentTransition(result.monitor, "open", result.checked_at))
            return "open"
        return None

    def mark_resolved(self, monitor: str, when: datetime) -> None:
        """Close an open incident. Closing a clear monitor violates the invariant."""
        slot = self._slots.get(monitor)
        assert slot is not None and slot.state == "open", "an incident cannot close before it opens"
        slot.state = "clear"
        slot.failures = 0
        slot.successes = 0
        self.transitions.append(IncidentTransition(monitor, "resolved", when))

    def is_open(self, monitor: str) -> bool:
        """Return True when ``monitor`` has an open incident."""
        slot = self._slots.get(monitor)
        return slot is not None and slot.state == "open"


def require_streak(value: object, field: str) -> int:
    """Return a streak length inside the allowed range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    if value < MIN_STREAK or value > MAX_STREAK:
        raise ConfigValidationError(f"{field} must be from {MIN_STREAK} to {MAX_STREAK}")
    return value
