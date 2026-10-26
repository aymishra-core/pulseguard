"""TCP connect check. Hierarchical inheritance: ``TcpCheck(BaseCheck)`` beside HTTP."""

from __future__ import annotations

import socket
import time
from collections.abc import Mapping
from typing import Any, ClassVar

from pulseguard.checks.base import (
    BaseCheck,
    is_disallowed_literal_host,
    optional_value,
    prepare_spec,
    require_int,
    require_name_target,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, timed
from pulseguard.exceptions import CheckError, CheckTimeoutError, ConfigValidationError

TCP_CONFIG_FIELDS = frozenset(
    {"kind", "name", "target", "timeout", "interval", "port", "allow_private"}
)
_MIN_PORT = 1
_MAX_PORT = 65535


class TcpCheck(BaseCheck):
    """Open a TCP connection and close it.

    This check only answers whether the configured host accepts a connection.
    Agent frames are a separate protocol and are not spoken here.
    """

    kind: ClassVar[str] = "tcp"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        port: int,
        timeout: float | None = None,
        interval: float | None = None,
        allow_private: bool = True,
    ) -> None:
        """Validate the host and port. Nothing is contacted yet."""
        super().__init__(name, target, timeout=timeout, interval=interval)
        self.port = port
        if not isinstance(allow_private, bool):
            raise ConfigValidationError("allow_private must be a boolean")
        self.allow_private = allow_private
        _validate_host(self.target, allow_private=allow_private)

    @property
    def port(self) -> int:
        """Return the TCP port."""
        return self._port

    @port.setter
    def port(self, value: object) -> None:
        """Reject ports outside the TCP range."""
        number = require_int(value, "port")
        if number < _MIN_PORT or number > _MAX_PORT:
            raise ConfigValidationError(f"port must be from {_MIN_PORT} to {_MAX_PORT}")
        self._port = number

    @log_call
    @timed
    def run(self) -> CheckResult:
        """Try one TCP connection. Polymorphism: callers use ``check.run()``."""
        started = time.perf_counter()
        try:
            self._connect()
        except CheckError as exc:
            return _result(
                self, ok=False, message=str(exc), severity=Severity.CRITICAL, started=started
            )
        return _result(
            self,
            ok=True,
            message="connection accepted",
            severity=Severity.OK,
            started=started,
        )

    def _connect(self) -> None:
        """Open and close a TCP socket. Raises ``CheckError`` on failure."""
        try:
            with socket.create_connection((self.target, self.port), timeout=self.timeout):
                return
        except TimeoutError as exc:
            raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
        except OSError as exc:
            raise CheckError(f"connection failed: {type(exc).__name__}") from exc

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> TcpCheck:
        """Build a ``TcpCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=TCP_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        try:
            port = spec["port"]
        except KeyError as exc:
            raise ConfigValidationError("missing port") from exc
        return cls(
            name,
            target,
            port=port,
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            allow_private=optional_value(spec, "allow_private", True),
        )


def _validate_host(host: str, *, allow_private: bool) -> None:
    """Reject URL-shaped targets and, when asked, literal private hosts."""
    if "://" in host or "/" in host:
        raise ConfigValidationError("tcp target must be a host, with the port in the port field")
    if not allow_private and is_disallowed_literal_host(host):
        raise ConfigValidationError("private and localhost targets are disabled for this monitor")


def _result(
    check: TcpCheck,
    *,
    ok: bool,
    message: str,
    severity: Severity,
    started: float,
) -> CheckResult:
    """Build a TCP result. The host is omitted so the line is safe to log."""
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert elapsed_ms >= 0, "latency cannot be negative"
    return CheckResult(
        monitor=check.name,
        kind=check.kind,
        ok=ok,
        latency_ms=elapsed_ms,
        message=message,
        severity=severity,
        checked_at=utc_now(),
        status_code=None,
    )
