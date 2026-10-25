"""HTTP status check. Single inheritance: ``HttpCheck(BaseCheck)``."""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlparse

import requests

from pulseguard import __version__
from pulseguard.checks.base import BaseCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, retry, timed
from pulseguard.exceptions import CheckError, CheckTimeoutError, ConfigValidationError

MAX_URL_LENGTH = 2048
MAX_RETRIES = 5
MAX_BACKOFF_SECONDS = 30.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_METHODS = frozenset({"GET", "HEAD"})
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_CONFIG_FIELDS = frozenset(
    {
        "kind",
        "name",
        "target",
        "timeout",
        "interval",
        "expected_status",
        "method",
        "allow_private",
        "retries",
        "retry_backoff",
    }
)


class HttpCheck(BaseCheck):
    """Fetch a URL and compare the status code.

    Redirects are not followed, so a 3xx response cannot bounce the probe onto
    another host. Private and localhost targets are allowed by default because
    this process is the operator's own monitor. Set ``allow_private`` false to
    reject localhost names and literal non-public IPs. Other hostnames are not
    resolved here, so this check does not block on DNS and does not defend
    against a hostname that later resolves to a private address.
    """

    kind: ClassVar[str] = "http"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        timeout: float | None = None,
        interval: float | None = None,
        expected_status: int = 200,
        method: str = "GET",
        allow_private: bool = True,
        retries: int = 3,
        retry_backoff: float = 0.2,
    ) -> None:
        """Validate the URL, then store the HTTP options."""
        super().__init__(name, target, timeout=timeout, interval=interval)
        self.expected_status = _require_status(expected_status)
        self.method = _require_method(method)
        if not isinstance(allow_private, bool):
            raise ConfigValidationError("allow_private must be a boolean")
        self.allow_private = allow_private
        self.retries = _require_retries(retries)
        self.retry_backoff = _require_backoff(retry_backoff)
        _validate_http_target(self.target, allow_private=allow_private)

    @log_call
    @timed
    def run(self) -> CheckResult:
        """Run the HTTP check. Polymorphism: callers use ``check.run()``."""
        clock = _Clock()
        try:
            return self._run_with_retry(clock)
        except CheckError as exc:
            return CheckResult(
                monitor=self.name,
                kind=self.kind,
                ok=False,
                latency_ms=clock.elapsed_ms(),
                message=str(exc),
                severity=Severity.CRITICAL,
                checked_at=utc_now(),
                status_code=None,
            )

    def _run_with_retry(self, clock: _Clock) -> CheckResult:
        """Apply ``@retry`` with this monitor's own attempt count.

        The decorator is bound here, not on the class, so each monitor can set
        ``retries`` and ``retry_backoff`` in its config. Demonstrates ``@retry``.
        """

        @retry(
            attempts=self.retries,
            backoff_seconds=self.retry_backoff,
            retry_on=(CheckError,),
        )
        def attempt() -> CheckResult:
            """One HTTP attempt. Transport failures raise and are retried."""
            return self._execute(clock)

        return attempt()

    def _execute(self, clock: _Clock) -> CheckResult:
        """Perform one request. Raises ``CheckError`` on transport failure."""
        attempt_started = clock.elapsed_ms()
        try:
            response = requests.request(
                self.method,
                self.target,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": f"pulseguard/{__version__}"},
            )
        except requests.Timeout as exc:
            raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
        except requests.RequestException as exc:
            raise CheckError(f"request failed: {type(exc).__name__}") from exc
        try:
            status = response.status_code
        finally:
            response.close()
        latency_ms = clock.elapsed_ms() - attempt_started
        assert latency_ms >= 0, "request latency cannot be negative"
        ok = status == self.expected_status
        if ok:
            message = f"HTTP {status}"
            severity = Severity.OK
        else:
            message = f"expected HTTP {self.expected_status} but got {status}"
            severity = Severity.CRITICAL
        return CheckResult(
            monitor=self.name,
            kind=self.kind,
            ok=ok,
            latency_ms=latency_ms,
            message=message,
            severity=severity,
            checked_at=utc_now(),
            status_code=status,
        )

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> HttpCheck:
        """Build an ``HttpCheck`` from a JSON object."""
        _reject_unknown(spec)
        kind = spec.get("kind", cls.kind)
        if kind != cls.kind:
            raise ConfigValidationError(f"HttpCheck requires kind {cls.kind!r}")
        try:
            name = spec["name"]
            target = spec["target"]
        except KeyError as exc:
            raise ConfigValidationError(f"missing {exc.args[0]}") from exc
        if not isinstance(name, str) or not isinstance(target, str):
            raise ConfigValidationError("name and target must be strings")
        return cls(
            name,
            target,
            timeout=_optional(spec, "timeout"),
            interval=_optional(spec, "interval"),
            expected_status=_optional(spec, "expected_status", 200),
            method=_optional(spec, "method", "GET"),
            allow_private=_optional(spec, "allow_private", True),
            retries=_optional(spec, "retries", 3),
            retry_backoff=_optional(spec, "retry_backoff", 0.2),
        )


class _Clock:
    """Monotonic clock for one check, so a failure still has a latency."""

    def __init__(self) -> None:
        """Start the clock."""
        self._time = time.perf_counter
        self._started = self._time()

    def elapsed_ms(self) -> float:
        """Return milliseconds since the clock started."""
        return (self._time() - self._started) * 1000.0


def is_disallowed_literal_host(host: str) -> bool:
    """Return True for localhost names and literal non-public IP addresses.

    Non-IP hostnames other than localhost return False. They are not resolved.
    """
    cleaned = host.lower().rstrip(".")
    if cleaned in _LOCAL_NAMES:
        return True
    try:
        parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    mapped = getattr(parsed, "ipv4_mapped", None)
    if isinstance(mapped, ipaddress.IPv4Address):
        parsed = mapped
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    ):
        return True
    return isinstance(parsed, ipaddress.IPv4Address) and parsed in _CGNAT


def _validate_http_target(target: str, *, allow_private: bool) -> None:
    """Reject targets this check will not probe."""
    if len(target) > MAX_URL_LENGTH:
        raise ConfigValidationError(f"target must be at most {MAX_URL_LENGTH} characters")
    try:
        parsed = urlparse(target)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigValidationError("target is not a valid URL") from exc
    if port == 0:
        raise ConfigValidationError("target port must not be 0")
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ConfigValidationError("target scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigValidationError("target must not include a username or password")
    if parsed.fragment:
        raise ConfigValidationError("target must not include a fragment")
    if not host:
        raise ConfigValidationError("target must include a host")
    if not allow_private and is_disallowed_literal_host(host):
        raise ConfigValidationError("private and localhost targets are disabled for this monitor")


def _require_status(status: object) -> int:
    """Return an HTTP status code in the range this client can compare."""
    number = _require_int(status, "expected_status")
    if number < 100 or number > 599:
        raise ConfigValidationError("expected_status must be from 100 to 599")
    return number


def _require_method(method: object) -> str:
    """Return GET or HEAD."""
    if not isinstance(method, str):
        raise ConfigValidationError("method must be a string")
    cleaned = method.strip().upper()
    if cleaned not in _ALLOWED_METHODS:
        raise ConfigValidationError("method must be GET or HEAD")
    return cleaned


def _require_retries(retries: object) -> int:
    """Return a retry count inside the accepted bound."""
    number = _require_int(retries, "retries")
    if number < 1 or number > MAX_RETRIES:
        raise ConfigValidationError(f"retries must be from 1 to {MAX_RETRIES}")
    return number


def _require_backoff(backoff: object) -> float:
    """Return a non-negative backoff inside the accepted bound."""
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)):
        raise ConfigValidationError("retry_backoff must be a number")
    number = float(backoff)
    if number < 0 or number > MAX_BACKOFF_SECONDS:
        raise ConfigValidationError(
            f"retry_backoff must be from 0 to {MAX_BACKOFF_SECONDS:g} seconds"
        )
    return number


def _require_int(value: object, field: str) -> int:
    """Return an int. Booleans are rejected because they are ints."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    return value


def _reject_unknown(spec: Mapping[str, Any]) -> None:
    """Reject config keys this check does not understand."""
    unknown = sorted(set(spec) - _CONFIG_FIELDS)
    if unknown:
        raise ConfigValidationError(f"unknown fields: {', '.join(unknown)}")


def _optional(spec: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Return ``spec[key]`` when present, otherwise ``default``."""
    if key not in spec:
        return default
    return spec[key]
