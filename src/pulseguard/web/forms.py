"""Form and JSON validation. Values are bounded. Nothing is contacted."""

from __future__ import annotations

from pulseguard.exceptions import ConfigValidationError
from pulseguard.storage.records import MonitorRecord, require_monitor_name


def monitor_from_payload(payload: object) -> MonitorRecord:
    """Build a catalog row from a JSON object. Unknown fields are refused."""
    if not isinstance(payload, dict):
        raise ConfigValidationError("JSON object required")
    extra = set(payload) - {"name", "kind", "target", "interval_seconds", "enabled"}
    if extra:
        raise ConfigValidationError("unknown monitor field")
    try:
        name = payload["name"]
        kind = payload["kind"]
        target = payload["target"]
        interval = payload["interval_seconds"]
        enabled = payload["enabled"]
    except KeyError as exc:
        raise ConfigValidationError(f"missing {exc.args[0]}") from exc
    return MonitorRecord(
        name=name,
        kind=kind,
        target=target,
        interval_seconds=interval,
        enabled=enabled,
    )


def parse_enabled(value: object) -> bool:
    """Accept the form words ``true`` and ``false``."""
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigValidationError("enabled must be true or false")


def parse_monitor_name(value: object) -> str:
    """Return a catalog name from a form field."""
    return require_monitor_name(value)
