"""Numbers for the dashboard, certificates, and SLA pages.

These functions only read rows that are already stored. They do not open sockets.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from markupsafe import Markup

from pulseguard.checks.models import CheckResult
from pulseguard.storage.records import MonitorRecord

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def latest_by_name(results: list[CheckResult]) -> dict[str, CheckResult]:
    """Return the newest result for each monitor. ``results`` is oldest first."""
    found: dict[str, CheckResult] = {}
    for result in results:
        found[result.monitor] = result
    return found


def window(results: list[CheckResult], now: datetime, hours: int) -> list[CheckResult]:
    """Return results checked inside the last ``hours`` hours."""
    cutoff = now - timedelta(hours=hours)
    return [result for result in results if result.checked_at >= cutoff]


def uptime(results: list[CheckResult]) -> float | None:
    """Return the success fraction, or None when the window is empty."""
    if not results:
        return None
    good = sum(1 for result in results if result.ok)
    return good / len(results)


def monitor_state(record: MonitorRecord, result: CheckResult | None) -> str:
    """Return up, down, unknown, or paused. The word is the signal, not only a colour."""
    if not record.enabled:
        return "paused"
    if result is None:
        return "unknown"
    return "up" if result.ok else "down"


def days_remaining(message: str, now: datetime) -> int | None:
    """Read a certificate date out of a stored TLS message."""
    match = _DATE.search(message)
    if match is None:
        return None
    try:
        day = datetime.fromisoformat(match.group(1))
    except ValueError:
        return None
    return (day.date() - now.date()).days


def certificate_band(days: int | None) -> str:
    """Return ok, warn, critical, or unknown for a days-left count."""
    if days is None:
        return "unknown"
    if days > 30:
        return "ok"
    if days >= 8:
        return "warn"
    return "critical"


def sparkline(values: list[float]) -> Markup:
    """Return an inline SVG of the last 20 latency samples. Values are numbers only."""
    return Markup(_polyline(values[-20:], 120, 36, "latency"))


def latency_chart(values: list[float]) -> Markup:
    """Return a wider inline SVG for the monitor detail page."""
    return Markup(_polyline(values[-100:], 640, 160, "latency over the last 100 checks"))


def _polyline(values: list[float], width: int, height: int, label: str) -> str:
    """Build one polyline. Non-finite numbers are dropped before drawing."""
    clean = [value for value in values if math.isfinite(value)]
    if len(clean) < 2:
        return ""
    peak = max(clean) or 1.0
    step = width / (len(clean) - 1)
    points: list[str] = []
    for index, value in enumerate(clean):
        x = round(index * step, 1)
        y = round(height - (min(value, peak) / peak) * (height - 8) - 4, 1)
        points.append(f"{x},{y}")
    joined = " ".join(points)
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{label}">'
        f'<polyline fill="none" points="{joined}" /></svg>'
    )
