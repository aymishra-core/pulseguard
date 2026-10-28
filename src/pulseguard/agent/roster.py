"""Named agents stored in settings. Tokens stay out of logs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from pulseguard.agent.protocol import require_token
from pulseguard.checks.base import MAX_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS
from pulseguard.exceptions import ConfigValidationError

DEFAULT_INTERVAL = 60
STALE_FACTOR = 3
_MAX_AGENTS = 8
_MAX_REJECTED = 12
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MASK = "••••••••"
_MONITOR_LOCK = Lock()


def agent_name(value: object) -> str:
    """Return an agent name that can sit unquoted in the push command."""
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ConfigValidationError("Use letters, digits, _ or - (no spaces).")
    return value


def push_command(host: str, port: str, name: str, token: str) -> str:
    """Return the command the operator runs on the agent machine."""
    return (
        f'export PULSEGUARD_AGENT_TOKEN="{token}"\n'
        f"pulseguard push --host {host} --port {port} --name {name} "
        f'--kind http --message "ok"'
    )


def masked_command(host: str, port: str, name: str) -> str:
    """Return the same command with the token hidden."""
    return push_command(host, port, name, _MASK)


def load_agents(raw: str | None) -> list[dict[str, Any]]:
    """Return stored agents. A broken value is an empty list."""
    parsed = _object_list(raw)
    rows: list[dict[str, Any]] = []
    for item in parsed:
        name = item.get("name")
        token = item.get("token")
        if not isinstance(name, str) or not isinstance(token, str):
            continue
        interval = item.get("interval")
        seconds = interval if isinstance(interval, int) and interval > 0 else DEFAULT_INTERVAL
        rows.append({"name": name, "token": token, "interval": seconds})
    return rows


def expected_interval(repo: Any, name: str) -> int:
    """Return the stored interval for this agent, or the default."""
    seconds = DEFAULT_INTERVAL
    raw = repo.get_setting("agents") if hasattr(repo, "get_setting") else None
    for row in load_agents(raw if isinstance(raw, str) else None):
        if row["name"] == name:
            seconds = int(row["interval"])
            break
    low = int(MIN_INTERVAL_SECONDS)
    high = int(MAX_INTERVAL_SECONDS)
    if seconds < low:
        return low
    if seconds > high:
        return high
    return seconds


def ensure_agent_monitor(repo: Any, name: str) -> None:
    """Create or refresh the passive monitor for one accepted push."""
    from pulseguard.storage.records import require_monitor_name

    chosen = require_monitor_name(name)
    with _MONITOR_LOCK:
        _save_agent_monitor(repo, chosen)


def _save_agent_monitor(repo: Any, chosen: str) -> None:
    """Insert or refresh one agent monitor. Callers hold ``_MONITOR_LOCK``."""
    from pulseguard.storage.records import MonitorRecord

    seconds = expected_interval(repo, chosen)
    current = next((item for item in repo.monitors() if item.name == chosen), None)
    if (
        current is not None
        and current.kind == "agent"
        and current.target == ""
        and current.interval_seconds == seconds
    ):
        return
    enabled = True if current is None else bool(current.enabled)
    repo.save_monitor(MonitorRecord(chosen, "agent", "", seconds, enabled))


def migrate_legacy_agents(repo: Any) -> None:
    """Give each last-push name an agent record. The monitor waits for the next push."""
    from pulseguard.checks.heartbeat import HeartbeatCheck

    seen = _seen_map(repo.get_setting("agent_seen"))
    rows = load_agents(repo.get_setting("agents"))
    known = {str(row["name"]) for row in rows}
    added = False
    for name in sorted(seen):
        if name in known:
            continue
        try:
            chosen = agent_name(name)
        except ConfigValidationError:
            continue
        secret = require_token(HeartbeatCheck.new_token())
        trial = [*rows, {"name": chosen, "token": secret, "interval": DEFAULT_INTERVAL}]
        if len(json.dumps(trial, sort_keys=True)) > 2048:
            continue
        rows = trial
        known.add(chosen)
        added = True
    if not added:
        return
    repo.set_setting("agents", json.dumps(rows, sort_keys=True))
    if not repo.get_setting("agent_token"):
        repo.set_setting("agent_token", str(rows[0]["token"]))


def remove_agent(repo: Any, name: str) -> None:
    """Drop one agent and its seen time. A matching legacy token is replaced."""
    chosen = agent_name(name)
    rows = load_agents(repo.get_setting("agents"))
    removed = next((row for row in rows if row["name"] == chosen), None)
    kept = [row for row in rows if row["name"] != chosen]
    repo.set_setting("agents", json.dumps(kept, sort_keys=True))
    if removed is not None and repo.get_setting("agent_token") == removed["token"]:
        repo.set_setting("agent_token", str(kept[0]["token"]) if kept else "")
    seen = _seen_map(repo.get_setting("agent_seen"))
    seen.pop(chosen, None)
    repo.set_setting("agent_seen", json.dumps(seen, sort_keys=True)[:2048])


def add_agent(repo: Any, name: str, token: str) -> None:
    """Store one agent. The token is a setting value, not a log field."""
    chosen = agent_name(name)
    secret = require_token(token)
    rows = load_agents(repo.get_setting("agents"))
    if any(row["name"] == chosen for row in rows):
        raise ConfigValidationError("an agent with that name already exists")
    if len(rows) >= _MAX_AGENTS:
        raise ConfigValidationError("at most 8 agents")
    rows.append({"name": chosen, "token": secret, "interval": DEFAULT_INTERVAL})
    repo.set_setting("agents", json.dumps(rows, sort_keys=True))
    if not repo.get_setting("agent_token"):
        repo.set_setting("agent_token", secret)


def collector_tokens(getter: Callable[[str], str | None]) -> str:
    """Return every saved token, one per line, for the collector."""
    found: list[str] = []

    def add(value: object) -> None:
        if not isinstance(value, str) or not value or value in found:
            return
        try:
            found.append(require_token(value))
        except ConfigValidationError:
            return

    add(getter("agent_token"))
    for row in load_agents(getter("agents")):
        add(row.get("token"))
    return "\n".join(found)


def touch_seen(repo: Any, name: str, moment: datetime) -> None:
    """Remember when this agent name last sent a result."""
    seen = _seen_map(repo.get_setting("agent_seen"))
    seen[name] = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    repo.set_setting("agent_seen", json.dumps(seen, sort_keys=True)[:2048])


def remember_rejection(repo: Any, reason: str, moment: datetime) -> None:
    """Keep a short list of refused attempts. The reason is not a secret."""
    text = " ".join(reason.split())[:180]
    if not text:
        return
    rows = _object_list(repo.get_setting("agent_rejected"))
    rows.insert(
        0,
        {
            "reason": text,
            "at": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        },
    )
    del rows[_MAX_REJECTED:]
    while rows:
        payload = json.dumps(rows, sort_keys=True)
        if len(payload) <= 2048:
            repo.set_setting("agent_rejected", payload)
            return
        rows.pop()


def describe_agents(
    repo: Any,
    now: datetime,
    latest: Callable[[str], str],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return agent rows and rejected rows for the page."""
    stored = load_agents(repo.get_setting("agents"))
    seen = _seen_map(repo.get_setting("agent_seen"))
    listing = getattr(repo, "monitors", None)
    present = {record.name for record in listing()} if callable(listing) else set()
    by_name = {str(row["name"]): row for row in stored}
    names = [str(row["name"]) for row in stored]
    for name in sorted(seen):
        if name not in by_name:
            names.append(name)
    rows: list[dict[str, str]] = []
    for name in names:
        record = by_name.get(name)
        interval = int(record["interval"]) if record else DEFAULT_INTERVAL
        tone, label = _status(seen.get(name, ""), interval, now)
        message = latest(name) or "No result yet"
        rows.append(
            {
                "name": name,
                "tone": tone,
                "label": label,
                "message": message,
                "interval": str(interval),
                "registered": "1" if record else "",
                "monitor": "1" if name in present else "",
            }
        )
    rejected = [
        {"reason": str(item.get("reason") or ""), "at": str(item.get("at") or "")}
        for item in _object_list(repo.get_setting("agent_rejected"))
        if item.get("reason")
    ]
    return rows, rejected


def _status(raw: str, interval: int, now: datetime) -> tuple[str, str]:
    """Return the pill tone and the words Never seen, Seen, or Stale."""
    moment = _parse(raw)
    if moment is None:
        return "unknown", "Never seen"
    age = max(0, int((now - moment).total_seconds()))
    if age > interval * STALE_FACTOR:
        return "warn", "Stale"
    return "up", f"Seen {age} s ago"


def _seen_map(raw: str | None) -> dict[str, str]:
    """Return the name to timestamp map. Anything else is empty."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def _object_list(raw: str | None) -> list[dict[str, Any]]:
    """Return a list of objects. Anything else is empty."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _parse(value: str) -> datetime | None:
    """Parse a stored timestamp. Blank and broken values stay empty."""
    if not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
