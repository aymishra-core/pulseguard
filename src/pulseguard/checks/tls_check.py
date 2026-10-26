"""TLS expiry check. Multiple inheritance: ``TlsExpiryCheck(SerializableMixin, BaseCheck)``."""

from __future__ import annotations

import math
import socket
import ssl
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from pulseguard.checks.base import (
    BaseCheck,
    is_disallowed_literal_host,
    optional_value,
    prepare_spec,
    require_int,
    require_name_target,
)
from pulseguard.checks.mixins import SerializableMixin
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, timed
from pulseguard.exceptions import (
    CertificateExpiringError,
    CheckError,
    CheckTimeoutError,
    ConfigValidationError,
)

# Product alert windows: 30, 14, 7, 3, and 1 days.
ALERT_WINDOWS: tuple[tuple[int, Severity], ...] = (
    (1, Severity.CRITICAL),
    (3, Severity.CRITICAL),
    (7, Severity.CRITICAL),
    (14, Severity.WARNING),
    (30, Severity.WARNING),
)
TLS_CONFIG_FIELDS = frozenset(
    {
        "kind",
        "name",
        "target",
        "timeout",
        "interval",
        "port",
        "allow_private",
        "ca_file",
        "server_name",
    }
)
_MIN_PORT = 1
_MAX_PORT = 65535
_SECONDS_PER_DAY = 86400


class TlsExpiryCheck(SerializableMixin, BaseCheck):
    """Read a certificate ``notAfter`` and alert inside the 30/14/7/3/1 day windows.

    Multiple inheritance puts ``SerializableMixin`` first, matching the class
    declaration ``TlsExpiryCheck(SerializableMixin, BaseCheck)``. ``to_mapping``
    is the mixin behaviour.
    """

    kind: ClassVar[str] = "tls"
    serial_fields: ClassVar[tuple[str, ...]] = ("name", "kind", "target", "port", "allow_private")

    def __init__(
        self,
        name: str,
        target: str,
        *,
        port: int = 443,
        timeout: float | None = None,
        interval: float | None = None,
        allow_private: bool = True,
        ca_file: str | None = None,
        server_name: str | None = None,
    ) -> None:
        """Validate the host and trust file. The handshake happens in ``run``."""
        super().__init__(name, target, timeout=timeout, interval=interval)
        self.port = port
        if not isinstance(allow_private, bool):
            raise ConfigValidationError("allow_private must be a boolean")
        self.allow_private = allow_private
        self.server_name = _require_server_name(server_name, target)
        self.ca_file = _require_ca_file(ca_file)
        if "://" in self.target or "/" in self.target:
            raise ConfigValidationError(
                "tls target must be a host, with the port in the port field"
            )
        if not allow_private and is_disallowed_literal_host(self.target):
            raise ConfigValidationError(
                "private and localhost targets are disabled for this monitor"
            )

    @property
    def port(self) -> int:
        """Return the TLS port."""
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
        """Handshake and read ``notAfter``. Polymorphism: callers use ``check.run()``."""
        started = time.perf_counter()
        try:
            expires = self._expires_at()
            _raise_if_due(expires, utc_now())
        except CertificateExpiringError as exc:
            severity = exc.severity if isinstance(exc.severity, Severity) else Severity.CRITICAL
            return _result(
                self,
                ok=False,
                message=str(exc),
                severity=severity,
                started=started,
            )
        except CheckError as exc:
            return _result(
                self,
                ok=False,
                message=str(exc),
                severity=Severity.CRITICAL,
                started=started,
            )
        return _result(
            self,
            ok=True,
            message=f"certificate valid until {expires.isoformat()}",
            severity=Severity.OK,
            started=started,
        )

    def _expires_at(self) -> datetime:
        """Return the peer certificate's expiry as a timezone-aware datetime."""
        context = ssl.create_default_context()
        if self.ca_file is not None:
            context.load_verify_locations(cafile=self.ca_file)
        try:
            raw = socket.create_connection((self.target, self.port), timeout=self.timeout)
        except TimeoutError as exc:
            raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
        except OSError as exc:
            raise CheckError(f"connection failed: {type(exc).__name__}") from exc
        try:
            try:
                tls = context.wrap_socket(raw, server_hostname=self.server_name)
            except (TimeoutError, ssl.SSLError, OSError):
                raw.close()
                raise
            try:
                peer = tls.getpeercert()
            finally:
                tls.close()
        except TimeoutError as exc:
            raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
        except ssl.SSLError as exc:
            raise CheckError(f"tls handshake failed: {type(exc).__name__}") from exc
        except OSError as exc:
            raise CheckError(f"connection failed: {type(exc).__name__}") from exc
        if not peer:
            raise CheckError("peer certificate was not presented")
        not_after = peer.get("notAfter")
        if not isinstance(not_after, str):
            raise CheckError("peer certificate has no notAfter")
        try:
            epoch = ssl.cert_time_to_seconds(not_after)
        except ValueError as exc:
            raise CheckError("peer certificate notAfter could not be parsed") from exc
        return datetime.fromtimestamp(epoch, UTC)

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> TlsExpiryCheck:
        """Build a ``TlsExpiryCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=TLS_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        ca_file = optional_value(spec, "ca_file")
        server_name = optional_value(spec, "server_name")
        if ca_file is not None and not isinstance(ca_file, str):
            raise ConfigValidationError("ca_file must be a string")
        if server_name is not None and not isinstance(server_name, str):
            raise ConfigValidationError("server_name must be a string")
        return cls(
            name,
            target,
            port=optional_value(spec, "port", 443),
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            allow_private=optional_value(spec, "allow_private", True),
            ca_file=ca_file,
            server_name=server_name,
        )


def _require_server_name(server_name: str | None, target: str) -> str:
    """Return the name used for SNI and hostname verification."""
    chosen = target if server_name is None else server_name
    if not isinstance(chosen, str) or not chosen.strip() or chosen.strip() != chosen:
        raise ConfigValidationError("server_name must be a non-empty hostname")
    if "://" in chosen or "/" in chosen:
        raise ConfigValidationError("server_name must be a hostname")
    return chosen


def _require_ca_file(ca_file: str | None) -> str | None:
    """Return a CA bundle path when the operator supplied one."""
    if ca_file is None:
        return None
    if not isinstance(ca_file, str) or not ca_file.strip():
        raise ConfigValidationError("ca_file must be a path")
    path = Path(ca_file)
    if not path.is_file():
        raise ConfigValidationError("ca_file must be an existing file")
    return str(path)


def _raise_if_due(expires: datetime, now: datetime) -> None:
    """Raise when ``expires`` is inside an alert window or already past."""
    seconds = (expires - now).total_seconds()
    stamp = expires.isoformat()
    if seconds < 0:
        raise CertificateExpiringError(
            f"certificate expired (was valid until {stamp})",
            severity=Severity.CRITICAL,
        )
    days = math.floor(seconds / _SECONDS_PER_DAY)
    for limit, severity in ALERT_WINDOWS:
        if days <= limit:
            unit = "day" if limit == 1 else "days"
            raise CertificateExpiringError(
                f"certificate expires within {limit} {unit} (valid until {stamp})",
                severity=severity,
            )


def _result(
    check: TlsExpiryCheck,
    *,
    ok: bool,
    message: str,
    severity: Severity,
    started: float,
) -> CheckResult:
    """Build a TLS result. The host is left out of the message."""
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
