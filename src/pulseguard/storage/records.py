"""Rows shared by the SQLite and MySQL repositories.

Timestamps are stored as fixed-width UTC text so a string comparison matches
time order. ``iter_results`` pages with the chunk-size bounds below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pulseguard.checks.base import MAX_INTERVAL_SECONDS, MAX_NAME_LENGTH, MIN_INTERVAL_SECONDS
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.exceptions import ConfigValidationError, StorageError

# Validation limits, not measurements.
SCHEMA_VERSION = 2
DEFAULT_RETENTION_DAYS = 180
MAX_RETENTION_DAYS = 3650
DEFAULT_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 1000
MAX_TARGET_LENGTH = 2048
MAX_STORED_MESSAGE_LENGTH = 2000
MAX_KIND_LENGTH = 32
MONITOR_COLUMNS = ("name", "kind", "target", "interval_seconds", "enabled")
RESULT_COLUMNS = (
    "monitor",
    "kind",
    "ok",
    "status_code",
    "latency_ms",
    "message",
    "severity",
    "checked_at",
)
_MIN_INTERVAL = int(MIN_INTERVAL_SECONDS)
_MAX_INTERVAL = int(MAX_INTERVAL_SECONDS)


@dataclass(frozen=True)
class MonitorRecord:
    """One catalog row. The check runner still reads JSON until the scheduler exists."""

    name: str
    kind: str
    target: str
    interval_seconds: int
    enabled: bool

    def __post_init__(self) -> None:
        """Reject a row the catalog cannot store."""
        require_monitor_name(self.name)
        require_kind(self.kind)
        require_target(self.target)
        require_interval(self.interval_seconds)
        require_enabled(self.enabled)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready mapping."""
        return {
            "name": self.name,
            "kind": self.kind,
            "target": self.target,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class IncidentRow:
    """One stored incident. Times stay as the text the database holds."""

    id: int
    monitor: str
    state: str
    started_at: str
    ended_at: str
    reason: str
    cause: str


@dataclass(frozen=True)
class AlertRow:
    """One alert-log row. The SMTP password is not stored here."""

    id: int
    created_at: str
    monitor: str
    kind: str
    recipient: str
    status: str
    error_text: str


def require_monitor_name(name: object) -> str:
    """Return a monitor name within the same length cap as a check name."""
    if not isinstance(name, str):
        raise ConfigValidationError("name must be a string")
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_NAME_LENGTH or cleaned != name:
        raise ConfigValidationError(
            f"name must be 1..{MAX_NAME_LENGTH} characters without surrounding space"
        )
    return cleaned


def require_kind(kind: object) -> str:
    """Return a kind that the check registry knows."""
    if not isinstance(kind, str):
        raise ConfigValidationError("kind must be a string")
    if not kind.strip() or kind.strip() != kind or len(kind) > MAX_KIND_LENGTH:
        raise ConfigValidationError("kind must be a non-empty string without surrounding space")
    from pulseguard.checks import CheckMeta

    if kind not in CheckMeta.registry:
        raise ConfigValidationError(f"unknown check kind {kind!r}")
    return kind


def require_target(target: object) -> str:
    """Return a target string. This does not connect to it."""
    if not isinstance(target, str) or not target.strip() or target.strip() != target:
        raise ConfigValidationError("target must be a non-empty string without surrounding space")
    if len(target) > MAX_TARGET_LENGTH:
        raise ConfigValidationError(f"target must be at most {MAX_TARGET_LENGTH} characters")
    return target


def require_interval(value: object) -> int:
    """Return a whole-second interval inside the check bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError("interval_seconds must be an integer")
    if value < _MIN_INTERVAL or value > _MAX_INTERVAL:
        raise ConfigValidationError(
            f"interval_seconds must be from {_MIN_INTERVAL} to {_MAX_INTERVAL}"
        )
    return value


def require_enabled(value: object) -> bool:
    """Return a real boolean. Integers are not accepted."""
    if not isinstance(value, bool):
        raise ConfigValidationError("enabled must be a boolean")
    return value


def require_chunk_size(value: object) -> int:
    """Return a page size for history streaming."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError("chunk_size must be an integer")
    if value < 1 or value > MAX_CHUNK_SIZE:
        raise ConfigValidationError(f"chunk_size must be from 1 to {MAX_CHUNK_SIZE}")
    return value


def require_row_id(value: object) -> int:
    """Return a positive result id."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigValidationError("result id must be a positive integer")
    return value


def require_aware(moment: object, field: str) -> datetime:
    """Reject a naive timestamp."""
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ConfigValidationError(f"{field} must be timezone-aware")
    return moment.astimezone(UTC)


def format_timestamp(moment: datetime) -> str:
    """Format ``moment`` so lexicographic order matches time order."""
    utc = require_aware(moment, "timestamp")
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def retention_cutoff(now: datetime, *, days: int = DEFAULT_RETENTION_DAYS) -> datetime:
    """Return the instant before which stored results are due for deletion.

    The default is 180 days. Nothing calls this on a schedule yet.
    """
    if isinstance(days, bool) or not isinstance(days, int):
        raise ConfigValidationError("retention days must be an integer")
    if days < 1 or days > MAX_RETENTION_DAYS:
        raise ConfigValidationError(f"retention days must be from 1 to {MAX_RETENTION_DAYS}")
    start = require_aware(now, "retention time")
    return start - timedelta(days=days)


def result_params(result: CheckResult) -> tuple[object, ...]:
    """Return the bound parameters for an INSERT of ``result``."""
    if len(result.message) > MAX_STORED_MESSAGE_LENGTH:
        raise StorageError(f"message must be at most {MAX_STORED_MESSAGE_LENGTH} characters")
    return (
        result.monitor,
        result.kind,
        1 if result.ok else 0,
        float(result.latency_ms),
        result.message,
        str(result.severity),
        format_timestamp(result.checked_at),
        result.status_code,
    )


def result_from_row(row: tuple[object, ...]) -> CheckResult:
    """Build a ``CheckResult`` from a stored row."""
    if len(row) != 8:
        raise StorageError("stored result has the wrong shape")
    monitor, kind, ok, latency, message, severity, checked_at, status = row
    if not isinstance(monitor, str) or not isinstance(kind, str) or not isinstance(message, str):
        raise StorageError("stored result has the wrong shape")
    if not isinstance(severity, str) or not isinstance(checked_at, str):
        raise StorageError("stored result has the wrong shape")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise StorageError("stored latency is invalid")
    passed = _stored_flag(ok, "ok")
    status_code = _stored_status(status)
    try:
        moment = datetime.fromisoformat(checked_at)
    except ValueError as exc:
        raise StorageError("stored checked_at is invalid") from exc
    if moment.tzinfo is None:
        raise StorageError("stored checked_at is invalid")
    return CheckResult(
        monitor=monitor,
        kind=kind,
        ok=passed,
        latency_ms=float(latency),
        message=message,
        severity=Severity(severity),
        checked_at=moment,
        status_code=status_code,
    )


def monitor_from_row(row: tuple[object, ...]) -> MonitorRecord:
    """Build a ``MonitorRecord`` from a stored row."""
    if len(row) != 5:
        raise StorageError("stored monitor has the wrong shape")
    name, kind, target, interval, enabled = row
    if not isinstance(name, str) or not isinstance(kind, str) or not isinstance(target, str):
        raise StorageError("stored monitor has the wrong shape")
    if isinstance(interval, bool) or not isinstance(interval, int):
        raise StorageError("stored interval is invalid")
    return MonitorRecord(
        name=name,
        kind=kind,
        target=target,
        interval_seconds=interval,
        enabled=_stored_flag(enabled, "enabled"),
    )


def _stored_flag(value: object, field: str) -> bool:
    """Map a stored 0 or 1 onto a boolean."""
    if value in (1, True):
        return True
    if value in (0, False):
        return False
    raise StorageError(f"stored {field} flag is invalid")


def _stored_status(value: object) -> int | None:
    """Map a stored status code, keeping NULL as None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise StorageError("stored status_code is invalid")
    return value
