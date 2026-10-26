"""HTTP status check. Single inheritance: ``HttpCheck(BaseCheck)``."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlparse

import requests

from pulseguard import __version__
from pulseguard.checks.base import (
    BaseCheck,
    is_disallowed_literal_host,
    optional_value,
    prepare_spec,
    require_int,
    require_name_target,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.decorators import log_call, retry, timed
from pulseguard.exceptions import (
    CheckError,
    CheckTimeoutError,
    ConfigValidationError,
    ContentMismatchError,
)

MAX_URL_LENGTH = 2048
MAX_RETRIES = 5
MAX_BACKOFF_SECONDS = 30.0
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_METHODS = frozenset({"GET", "HEAD"})
HTTP_CONFIG_FIELDS = frozenset(
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
        response = self._open_response()
        try:
            status = response.status_code
            body = b""
            if status == self.expected_status:
                try:
                    body = self._body(response)
                except ContentMismatchError as exc:
                    return self._outcome(
                        ok=False,
                        status=status,
                        latency_ms=_latency_since(clock, attempt_started),
                        message=str(exc),
                        severity=Severity.CRITICAL,
                    )
                except requests.Timeout as exc:
                    raise CheckTimeoutError(f"timed out after {self.timeout:g} seconds") from exc
                except requests.RequestException as exc:
                    raise CheckError(f"request failed: {type(exc).__name__}") from exc
            latency_ms = _latency_since(clock, attempt_started)
        finally:
            response.close()
        return self._interpret(status, body, latency_ms)

    def _open_response(self) -> requests.Response:
        """Open the HTTP response. The caller closes it."""
        try:
            return requests.request(
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

    def _body(self, response: requests.Response) -> bytes:
        """Return the entity a subclass wants to inspect. Status checks skip it."""
        del response
        return b""

    def _interpret(self, status: int, body: bytes, latency_ms: float) -> CheckResult:
        """Turn a finished response into a result. Subclasses inspect ``body``."""
        del body
        ok = status == self.expected_status
        if ok:
            message = f"HTTP {status}"
            severity = Severity.OK
        else:
            message = f"expected HTTP {self.expected_status} but got {status}"
            severity = Severity.CRITICAL
        return self._outcome(
            ok=ok,
            status=status,
            latency_ms=latency_ms,
            message=message,
            severity=severity,
        )

    def _outcome(
        self,
        *,
        ok: bool,
        status: int | None,
        latency_ms: float,
        message: str,
        severity: Severity,
    ) -> CheckResult:
        """Build a result for this monitor."""
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
        prepare_spec(spec, kind=cls.kind, allowed=HTTP_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        return cls(
            name,
            target,
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            expected_status=optional_value(spec, "expected_status", 200),
            method=optional_value(spec, "method", "GET"),
            allow_private=optional_value(spec, "allow_private", True),
            retries=optional_value(spec, "retries", 3),
            retry_backoff=optional_value(spec, "retry_backoff", 0.2),
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


def _latency_since(clock: _Clock, started_ms: float) -> float:
    """Return milliseconds since ``started_ms`` on ``clock``."""
    latency_ms = clock.elapsed_ms() - started_ms
    assert latency_ms >= 0, "request latency cannot be negative"
    return latency_ms


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
    number = require_int(status, "expected_status")
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
    number = require_int(retries, "retries")
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
