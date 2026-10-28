"""Numbers for the dashboard, certificates, and SLA pages.

These functions only read rows that are already stored. They do not open sockets.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from markupsafe import Markup

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.storage.records import IncidentRow, MonitorRecord

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


def hint_for(message: str) -> str:
    """Return one next step for a stored cause."""
    text = message.lower()
    if "gap" in text:
        return "This followed a quiet period. Confirm your network, then watch the next results."
    if "connection refused" in text or "connectionerror" in text:
        return "Check that the program is running and that the port is open."
    if "timeout" in text:
        return "Check the path to the site. Raise the timeout only if the site is slow on purpose."
    if "expected http" in text or "status" in text:
        return "Open the address and compare the status code with the one you typed."
    if "keyword" in text:
        return "The visible text changed. If you meant to change the page, update the keyword."
    if "hash" in text:
        return "The page bytes changed. If that was intentional, learn a new baseline."
    if "certificate" in text or text.startswith("tls"):
        return "Renew the certificate before the date in the message."
    if "ping" in text or "heartbeat" in text or "grace" in text:
        return "Confirm the job is running and that it can reach this PulseGuard address."
    return "Compare this message with the last check that succeeded."


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


def ring_state(
    records: list[MonitorRecord],
    latest: dict[str, CheckResult],
    open_incidents: int,
) -> str:
    """Return the hero ring: down, warn, paused, or up.

    Red when a monitor is down. Amber when an incident is still open between
    checks, for a gap, or for a warning that is still up. Grey only when every
    monitor is paused. Green only when every monitor is up.
    """
    states = [monitor_state(record, latest.get(record.name)) for record in records]
    if "down" in states:
        return "down"
    if open_incidents:
        return "warn"
    for record in records:
        result = latest.get(record.name)
        if result is None:
            continue
        if result.message == "monitoring gap":
            return "warn"
        if result.ok and result.severity >= Severity.WARNING:
            return "warn"
    if "unknown" in states or not states:
        return "warn"
    if all(item == "paused" for item in states):
        return "paused"
    if all(item == "up" for item in states):
        return "up"
    return "warn"


def dashboard_hero(
    records: Sequence[MonitorRecord],
    latest: Mapping[str, CheckResult],
    active: Sequence[IncidentRow],
    *,
    results_exist: bool,
) -> dict[str, object]:
    """Return the dashboard hero. Only incidents on enabled monitors count."""
    catalog = list(records)
    found = dict(latest)
    down = [record for record in catalog if monitor_state(record, found.get(record.name)) == "down"]
    if down:
        return _down_hero(down, found, active)
    if active:
        return _waiting_hero(catalog, found, active)
    if catalog and all(not record.enabled for record in catalog):
        return _hero("paused", "", "#pause", "All monitors are paused")
    if not catalog:
        return _hero("neutral", "", "#plus", "Add your first monitor")
    if not results_exist:
        return _hero("neutral", "", "#clock", "Waiting for the first checks")
    ring = ring_state(catalog, found, 0)
    if ring == "warn":
        return _hero("warn", "hero--warn", "#alert", "All systems operational")
    return _hero("up", "hero--ok", "#check", "All systems operational")


def _down_hero(
    down: list[MonitorRecord],
    latest: dict[str, CheckResult],
    active: Sequence[IncidentRow],
) -> dict[str, object]:
    """Danger hero for the monitors that are down right now."""
    first = down[0]
    result = latest.get(first.name)
    plain = "" if result is None else plain_message(result.message).rstrip(".")
    title = "1 monitor is down" if len(down) == 1 else f"{len(down)} monitors are down"
    match = next((row for row in active if row.monitor == first.name), None)
    if match is None and active:
        match = min(active, key=lambda row: row.started_at)
    view = _hero("down", "hero--danger", "#x", title, name=first.name, plain=plain)
    if match is not None:
        view["incident_id"] = match.id
        view["incident_since"] = match.started_at
    return view


def _waiting_hero(
    records: list[MonitorRecord],
    latest: dict[str, CheckResult],
    active: Sequence[IncidentRow],
) -> dict[str, object]:
    """Warn hero when an enabled monitor still has an incident between checks."""
    ring = ring_state(records, latest, len(active))
    earliest = min(active, key=lambda row: row.started_at)
    view = _hero(
        ring,
        "hero--warn",
        "#alert",
        "Incident open, waiting for the next check",
        name=earliest.monitor,
    )
    view["incident_id"] = earliest.id
    view["incident_since"] = earliest.started_at
    return view


def _hero(
    ring: str,
    hero_class: str,
    icon: str,
    title: str,
    *,
    name: str = "",
    plain: str = "",
) -> dict[str, object]:
    """Return one hero description. ``incident_id`` stays 0 until a row is attached."""
    return {
        "ring": ring,
        "hero_class": hero_class,
        "hero_icon": icon,
        "hero_title": title,
        "hero_name": name,
        "hero_plain": plain,
        "incident_id": 0,
        "incident_since": "",
        "incident_name": name,
        "incident_plain": plain,
    }


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


def clock(value: object) -> str:
    """Return a visible time with no ISO timestamp and no microseconds.

    The browser replaces this with the local zone. The fallback stays in UTC.
    """
    moment = _moment(value)
    if moment is None:
        return "—"
    moment = moment.astimezone(UTC)
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    month = moment.strftime("%b")
    return f"{moment.day} {month} {moment.year}, {hour}:{moment.minute:02d} {suffix} UTC"


def stamp(value: object) -> str:
    """Return an ISO timestamp for a machine attribute, or an empty string."""
    moment = _moment(value)
    if moment is None:
        return ""
    return moment.isoformat()


def _moment(value: object) -> datetime | None:
    """Parse a stored time. Blank and unreadable values stay empty."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def hourly_counts(results: list[CheckResult], now: datetime, *, ok: bool) -> list[float]:
    """Return eight counts across the last day, for a KPI sparkline."""
    parts = 8
    width = timedelta(hours=24) / parts
    start = now - timedelta(hours=24)
    values: list[float] = []
    for index in range(parts):
        begin = start + width * index
        end = begin + width
        bucket = [
            item
            for item in results
            if begin <= item.checked_at < end and item.message != "monitoring gap"
        ]
        values.append(float(sum(1 for item in bucket if item.ok is ok)))
    return values


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
