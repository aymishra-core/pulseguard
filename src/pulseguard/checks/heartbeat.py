"""Heartbeat check. Hierarchical inheritance: ``HeartbeatCheck(BaseCheck)`` beside HTTP."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, ClassVar

from pulseguard.checks.base import (
    BaseCheck,
    optional_value,
    prepare_spec,
    require_name_target,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, timed
from pulseguard.exceptions import ConfigValidationError

HEARTBEAT_CONFIG_FIELDS = frozenset(
    {"kind", "name", "target", "timeout", "interval", "grace_seconds"}
)
# Grace is at least the product's minimum interval, and at most seven days.
MIN_GRACE_SECONDS = 30.0
MAX_GRACE_SECONDS = 604_800.0
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
Clock = Callable[[], datetime]


class HeartbeatCheck(BaseCheck):
    """Dead-man's switch. ``target`` is the unguessable token, not a URL.

    ``record_ping`` is what ``/ping/<token>`` calls. ``pulseguard web`` keeps
    this object in the same process as the scheduler. ``run`` compares that
    timestamp with ``grace_seconds``. The token is never copied into the result.
    """

    kind: ClassVar[str] = "heartbeat"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        grace_seconds: float = 120,
        timeout: float | None = None,
        interval: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Validate the token and the grace period. No ping is recorded yet."""
        super().__init__(name, target, timeout=timeout, interval=interval)
        self._validate_token(self.target)
        self.grace_seconds = grace_seconds
        self._clock = clock or utc_now
        self._last_seen: datetime | None = None
        self._persist_ping: Callable[[datetime], None] | None = None

    @staticmethod
    def new_token() -> str:
        """Return an unguessable token suitable for a ping URL."""
        return secrets.token_urlsafe(32)

    @property
    def token(self) -> str:
        """Return the ping token. This is the check target."""
        return self.target

    @property
    def grace_seconds(self) -> float:
        """Return how long a ping stays fresh."""
        return self._grace_seconds

    @grace_seconds.setter
    def grace_seconds(self, value: object) -> None:
        """Bound the grace period."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigValidationError("grace_seconds must be a number")
        number = float(value)
        if number < MIN_GRACE_SECONDS or number > MAX_GRACE_SECONDS:
            raise ConfigValidationError(
                f"grace_seconds must be between {MIN_GRACE_SECONDS:g} and {MAX_GRACE_SECONDS:g}"
            )
        self._grace_seconds = number

    def record_ping(self, when: datetime | None = None) -> None:
        """Remember one ping. Naive timestamps are rejected."""
        moment = self._clock() if when is None else when
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise ConfigValidationError("ping time must be timezone-aware")
        self._last_seen = moment
        if self._persist_ping is not None:
            self._persist_ping(moment)

    def restore_last_ping(self, when: datetime) -> None:
        """Restore a ping that was stored before this process started."""
        if not isinstance(when, datetime) or when.tzinfo is None:
            raise ConfigValidationError("ping time must be timezone-aware")
        self._last_seen = when

    @log_call
    @timed
    def run(self) -> CheckResult:
        """Report whether a ping arrived inside the grace period."""
        started = time.perf_counter()
        now = self._clock()
        seen = self._last_seen
        if seen is None:
            return _result(self, ok=False, message="no ping recorded", started=started)
        age = (now - seen).total_seconds()
        if age > self.grace_seconds:
            return _result(
                self,
                ok=False,
                message=f"last ping was {int(age)} seconds ago",
                started=started,
            )
        return _result(self, ok=True, message="ping is within the grace period", started=started)

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> HeartbeatCheck:
        """Build a ``HeartbeatCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=HEARTBEAT_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        return cls(
            name,
            target,
            grace_seconds=optional_value(spec, "grace_seconds", 120),
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
        )

    @staticmethod
    def _validate_token(token: str) -> None:
        """Require a URL-safe token long enough that it is not guessable by hand."""
        if not _TOKEN_RE.fullmatch(token):
            raise ConfigValidationError(
                "heartbeat target must be a 16..128 character token of letters, digits, '_' or '-'"
            )


def _result(check: HeartbeatCheck, *, ok: bool, message: str, started: float) -> CheckResult:
    """Build a heartbeat result. ``message`` must not contain the token."""
    assert check.token not in message, "heartbeat result leaked the token"
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert elapsed_ms >= 0, "latency cannot be negative"
    severity = Severity.OK if ok else Severity.CRITICAL
    return CheckResult(
        monitor=check.name,
        kind=check.kind,
        ok=ok,
        latency_ms=elapsed_ms,
        message=message,
        severity=severity,
        checked_at=check._clock(),
        status_code=None,
    )
