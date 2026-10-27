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
    """Return the success fraction, or None when the window is empty.

    A monitoring gap is neither up nor down, so it is left out of the fraction.
    """
    counted = [result for result in results if result.message != "monitoring gap"]
    if not counted:
        return None
    good = sum(1 for result in counted if result.ok)
    return good / len(counted)


def plain_message(message: str) -> str:
    """Turn a stored result line into a sentence an operator can act on."""
    text = " ".join(message.split())
    if text == "monitoring gap":
        return "No data. PulseGuard was not checking."
    if text == "possible local network issue after a gap":
        return "The check failed after a gap. This may be your network, not the site."
    if "connection refused" in text.lower():
        return "The site did not answer (connection refused)."
    if text.startswith("check failed:"):
        kind = text.split(":", 1)[1].strip()
        if "Timeout" in kind:
            return "The site took too long to answer."
        if kind in {"ConnectionError", "ConnectTimeout", "NewConnectionError"}:
            return "The site did not answer (connection refused)."
        return f"The check could not finish ({kind})."
    return text


def segments(
    results: list[CheckResult], now: datetime, *, hours: int = 24, parts: int = 90
) -> list[str]:
    """Return up, down, gap, or empty for each slice of the window."""
    width = timedelta(hours=hours) / parts
    start = now - timedelta(hours=hours)
    labels: list[str] = []
    for index in range(parts):
        begin = start + width * index
        end = begin + width
        bucket = [item for item in results if begin <= item.checked_at < end]
        useful = [item for item in bucket if item.message != "monitoring gap"]
        if any(item.message == "monitoring gap" for item in bucket) and not useful:
            labels.append("gap")
        elif any(not item.ok for item in useful):
            labels.append("down")
        elif useful:
            labels.append("up")
        else:
            labels.append("empty")
    return labels


def activity_lines(results: list[CheckResult], *, limit: int = 12) -> list[dict[str, str]]:
    """Return recent state changes, newest first."""
    grouped: dict[str, list[CheckResult]] = {}
    for result in results:
        grouped.setdefault(result.monitor, []).append(result)
    events: list[tuple[datetime, str, str]] = []
    for name, rows in grouped.items():
        previous: bool | None = None
        for result in rows:
            if result.message == "monitoring gap":
                events.append((result.checked_at, "unknown", f"{name} had no data"))
                previous = None
                continue
            if previous is None:
                previous = result.ok
                continue
            if previous and not result.ok:
                if result.message == "possible local network issue after a gap":
                    text = f"{name} failed after a gap. Check the local network."
                else:
                    text = f"{name} went down"
                events.append((result.checked_at, "down", text))
            elif (not previous) and result.ok:
                events.append((result.checked_at, "up", f"{name} recovered"))
            previous = result.ok
    events.sort(key=lambda item: item[0], reverse=True)
    return [
        {"at": moment.isoformat(), "state": state, "text": text}
        for moment, state, text in events[:limit]
    ]


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
