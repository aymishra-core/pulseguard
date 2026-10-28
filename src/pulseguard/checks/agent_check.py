"""Passive agent monitor. A push stores the result. Nothing here contacts a host."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pulseguard.checks.base import BaseCheck, optional_value, prepare_spec, require_name_target
from pulseguard.checks.models import CheckResult, Severity, utc_now

AGENT_CONFIG_FIELDS = frozenset({"kind", "name", "target", "timeout", "interval"})
STALE_MESSAGE = "no report from agent"


class AgentCheck(BaseCheck):
    """A monitor filled by ``pulseguard push``. It has no target and no schedule.

    The catalog keeps the row so the dashboard can draw it. The scheduler does
    not run this class. ``run`` only exists so the registry can build one.
    """

    kind: ClassVar[str] = "agent"

    def __init__(
        self,
        name: str,
        target: str = "",
        *,
        timeout: float | None = None,
        interval: float | None = None,
    ) -> None:
        """Store the name. An empty target stays empty."""
        super().__init__(name, target or "agent", timeout=timeout, interval=interval)
        if target == "":
            self._target = ""

    def run(self) -> CheckResult:
        """Report silence. The collector, not this method, records real pushes."""
        return CheckResult(
            monitor=self.name,
            kind=self.kind,
            ok=False,
            latency_ms=0,
            message=STALE_MESSAGE,
            severity=Severity.CRITICAL,
            checked_at=utc_now(),
        )

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> AgentCheck:
        """Build one agent monitor. ``target`` may be omitted."""
        prepare_spec(spec, kind=cls.kind, allowed=AGENT_CONFIG_FIELDS)
        raw = dict(spec)
        if "target" not in raw:
            raw["target"] = ""
        name, target = require_name_target(raw)
        timeout = optional_value(raw, "timeout")
        interval = optional_value(raw, "interval")
        return cls(
            name,
            target,
            timeout=timeout
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
            else None,
            interval=interval
            if isinstance(interval, (int, float)) and not isinstance(interval, bool)
            else None,
        )
