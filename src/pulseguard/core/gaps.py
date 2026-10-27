"""Monitoring gaps. A sleep or a stalled scheduler is not an outage.

A gap is more than three intervals with no stored result. The gap itself is
not a success or a failure. The first failed check after that gap is labelled
and does not count toward the incident streak.
"""

from __future__ import annotations

from datetime import datetime, timedelta

GAP_MESSAGE = "monitoring gap"
AFTER_GAP_MESSAGE = "possible local network issue after a gap"
GAP_INTERVALS = 3


def is_monitoring_gap(message: str) -> bool:
    """Return True for a stored gap row."""
    return message == GAP_MESSAGE


def exceeds_gap(previous: datetime, current: datetime, interval_seconds: float) -> bool:
    """Return True when ``current`` is more than three intervals after ``previous``."""
    if interval_seconds <= 0:
        return False
    return (current - previous).total_seconds() > GAP_INTERVALS * interval_seconds


def gap_moment(previous: datetime, interval_seconds: float) -> datetime:
    """Return a timestamp inside the quiet period, one interval after the last check."""
    return previous + timedelta(seconds=max(interval_seconds, 1))
