"""DNS check. Hierarchical inheritance: ``DnsCheck(BaseCheck)`` beside HTTP."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from pulseguard.checks.base import (
    BaseCheck,
    is_disallowed_literal_host,
    optional_value,
    prepare_spec,
    require_name_target,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, timed
from pulseguard.exceptions import CheckError, CheckTimeoutError, ConfigValidationError

DNS_CONFIG_FIELDS = frozenset(
    {"kind", "name", "target", "timeout", "interval", "expected", "allow_private"}
)
_MAX_EXPECTED = 8
_MAX_NAME_LENGTH = 253
Lookup = Callable[[str, float], list[str]]


class DnsCheck(BaseCheck):
    """Resolve a hostname and require every expected address to be present.

    Extra answers are allowed, so a name that has both IPv4 and IPv6 records
    still passes when the config lists one of them. The system resolver is
    used unless a caller passes ``resolver``. That argument is not read from
    JSON. ``socket.setdefaulttimeout`` is saved and restored around the lookup;
    it is process-wide, which is acceptable until checks run on a thread pool.
    """

    kind: ClassVar[str] = "dns"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        expected: list[str],
        timeout: float | None = None,
        interval: float | None = None,
        allow_private: bool = True,
        resolver: Lookup | None = None,
    ) -> None:
        """Validate the name and the expected addresses. Nothing is resolved yet."""
        super().__init__(name, target, timeout=timeout, interval=interval)
        if not isinstance(allow_private, bool):
            raise ConfigValidationError("allow_private must be a boolean")
        self.allow_private = allow_private
        self.expected = _require_expected(expected, allow_private=allow_private)
        _validate_dns_name(self.target, allow_private=allow_private)
        self._resolver = resolver

    @log_call
    @timed
    def run(self) -> CheckResult:
        """Resolve the name once. Polymorphism: callers use ``check.run()``."""
        started = time.perf_counter()
        try:
            found = self._lookup()
        except CheckError as exc:
            return _result(
                self, ok=False, message=str(exc), severity=Severity.CRITICAL, started=started
            )
        missing = [addr for addr in self.expected if addr not in found]
        if missing:
            listed = ", ".join(missing)
            return _result(
                self,
                ok=False,
                message=f"missing expected address: {listed}",
                severity=Severity.CRITICAL,
                started=started,
            )
        return _result(
            self, ok=True, message="name resolved", severity=Severity.OK, started=started
        )

    def _lookup(self) -> list[str]:
        """Return the addresses for this name."""
        try:
            if self._resolver is not None:
                return self._resolver(self.target, self.timeout)
            return _system_lookup(self.target, self.timeout)
        except TimeoutError as exc:
            raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
        except socket.gaierror as exc:
            raise CheckError("name lookup failed") from exc

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> DnsCheck:
        """Build a ``DnsCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=DNS_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        try:
            expected = spec["expected"]
        except KeyError as exc:
            raise ConfigValidationError("missing expected") from exc
        if not isinstance(expected, list):
            raise ConfigValidationError("expected must be a list of addresses")
        return cls(
            name,
            target,
            expected=expected,
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            allow_private=optional_value(spec, "allow_private", True),
        )


def _validate_dns_name(host: str, *, allow_private: bool) -> None:
    """Reject IP literals, URL-shaped names, and disallowed localhost names."""
    if "://" in host or "/" in host or " " in host:
        raise ConfigValidationError("dns target must be a hostname")
    if len(host) > _MAX_NAME_LENGTH:
        raise ConfigValidationError(f"dns target must be at most {_MAX_NAME_LENGTH} characters")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ConfigValidationError("dns target must be a hostname, not an IP address")
    if not allow_private and is_disallowed_literal_host(host):
        raise ConfigValidationError("private and localhost targets are disabled for this monitor")


def _require_expected(expected: object, *, allow_private: bool) -> tuple[str, ...]:
    """Return a de-duplicated tuple of IP address strings."""
    if not isinstance(expected, list) or not expected or len(expected) > _MAX_EXPECTED:
        raise ConfigValidationError(f"expected must list 1..{_MAX_EXPECTED} addresses")
    cleaned: list[str] = []
    for item in expected:
        if not isinstance(item, str):
            raise ConfigValidationError("expected addresses must be strings")
        try:
            parsed = ipaddress.ip_address(item)
        except ValueError as exc:
            raise ConfigValidationError(f"expected address {item!r} is not an IP") from exc
        if not allow_private and is_disallowed_literal_host(item):
            raise ConfigValidationError("private expected addresses are disabled for this monitor")
        text = str(parsed)
        if text not in cleaned:
            cleaned.append(text)
    return tuple(cleaned)


def _system_lookup(host: str, timeout: float) -> list[str]:
    """Resolve ``host`` with the system resolver and a temporary default timeout."""
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    finally:
        socket.setdefaulttimeout(previous)
    found: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in found:
            found.append(address)
    return found


def _result(
    check: DnsCheck,
    *,
    ok: bool,
    message: str,
    severity: Severity,
    started: float,
) -> CheckResult:
    """Build a DNS result without repeating the looked-up name."""
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
