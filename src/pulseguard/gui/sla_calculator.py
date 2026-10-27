"""Event-driven SLA numbers. The inputs are counts the operator types.

The target is a configuration value, not a measurement from this project.
"""

from __future__ import annotations

from pulseguard.checks.models import LatencyStats
from pulseguard.core.sla import SlaReport, error_budget_remaining
from pulseguard.exceptions import ConfigValidationError

# A form cap, not a measurement.
_MAX_SAMPLES = 100_000


def budget_from_counts(successes: object, total: object, target: object) -> float | None:
    """Return the remaining error budget for a typed sample window."""
    count = _require_count(total, "sample count")
    if count < 1 or count > _MAX_SAMPLES:
        raise ConfigValidationError(f"sample count must be from 1 to {_MAX_SAMPLES}")
    good = _require_count(successes, "success count")
    if good > count:
        raise ConfigValidationError("success count cannot exceed the sample count")
    report = SlaReport(
        count=count,
        successes=good,
        availability=good / count,
        latency=LatencyStats(0, 0.0, 0.0),
    )
    return error_budget_remaining(report, target=_as_target(target))


def format_budget(successes: object, total: object, target: object) -> str:
    """Return a readable budget, or the validation message.

    The remaining fraction still comes from ``error_budget_remaining``.
    """
    try:
        remaining = budget_from_counts(successes, total, target)
    except ConfigValidationError as exc:
        return str(exc)
    if remaining is None:
        return "no samples"
    count = _require_count(total, "sample count")
    good = _require_count(successes, "success count")
    goal = _as_target(target)
    status = "target met" if remaining >= 0 else "target missed"
    return (
        f"Achieved {_format_percent(good / count)}. "
        f"Allowed failures: {_format_number(count * (1 - goal))}. "
        f"Actual failures: {count - good}. "
        f"Error budget remaining: {_format_percent(remaining)} ({status})"
    )


def _require_count(value: object, field: str) -> int:
    """Reject a boolean or a non-integer count."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    if value < 0:
        raise ConfigValidationError(f"{field} must be an integer")
    return value


def _format_percent(fraction: float) -> str:
    """Render a fraction as a percent, dropping a trailing ``.00``."""
    return f"{_format_number(fraction * 100)}%"


def _format_number(value: float) -> str:
    """Render a display number. This does not change the budget math."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _as_target(value: object) -> float:
    """Pass a configured target through. The SLA helper bounds it."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError("sla target must be a number")
    return float(value)
