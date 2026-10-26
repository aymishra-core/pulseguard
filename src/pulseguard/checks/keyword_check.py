"""Keyword check. Multilevel inheritance: ``BaseCheck`` → ``HttpCheck`` → ``KeywordCheck``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import requests

from pulseguard.checks.base import optional_value, prepare_spec, require_int, require_name_target
from pulseguard.checks.http_check import HTTP_CONFIG_FIELDS, HttpCheck
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.exceptions import ConfigValidationError, ContentMismatchError

# Cap on the entity this check will hold. It is a memory bound, not a measurement.
MAX_BODY_BYTES = 1_048_576
MAX_KEYWORD_LENGTH = 200
KEYWORD_CONFIG_FIELDS = HTTP_CONFIG_FIELDS | {"keyword", "max_body_bytes"}


class KeywordCheck(HttpCheck):
    """HTTP check that also requires a raw-text keyword.

    The search looks at the response bytes decoded as UTF-8. It does not parse
    HTML. A later phase can switch this to BeautifulSoup so tags and scripts
    are not part of the searched text.
    """

    kind: ClassVar[str] = "keyword"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        keyword: str,
        timeout: float | None = None,
        interval: float | None = None,
        expected_status: int = 200,
        method: str = "GET",
        allow_private: bool = True,
        retries: int = 3,
        retry_backoff: float = 0.2,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        """Validate the keyword, then the HTTP target."""
        if not isinstance(method, str) or method.strip().upper() != "GET":
            raise ConfigValidationError("keyword checks require method GET")
        super().__init__(
            name,
            target,
            timeout=timeout,
            interval=interval,
            expected_status=expected_status,
            method="GET",
            allow_private=allow_private,
            retries=retries,
            retry_backoff=retry_backoff,
        )
        self.keyword = keyword
        self.max_body_bytes = max_body_bytes

    @property
    def keyword(self) -> str:
        """Return the required text."""
        return self._keyword

    @keyword.setter
    def keyword(self, value: object) -> None:
        """Reject an empty or oversized keyword."""
        if not isinstance(value, str):
            raise ConfigValidationError("keyword must be a string")
        cleaned = value.strip()
        if not cleaned or len(cleaned) > MAX_KEYWORD_LENGTH or cleaned != value:
            raise ConfigValidationError(
                f"keyword must be 1..{MAX_KEYWORD_LENGTH} characters without surrounding space"
            )
        self._keyword = cleaned

    @property
    def max_body_bytes(self) -> int:
        """Return how many response bytes this check will read."""
        return self._max_body_bytes

    @max_body_bytes.setter
    def max_body_bytes(self, value: object) -> None:
        """Bound the read so a huge page cannot be stored whole."""
        number = require_int(value, "max_body_bytes")
        if number < 1 or number > MAX_BODY_BYTES:
            raise ConfigValidationError(f"max_body_bytes must be from 1 to {MAX_BODY_BYTES}")
        self._max_body_bytes = number

    def _body(self, response: requests.Response) -> bytes:
        """Read at most ``max_body_bytes``. Demonstrates the body hook on HttpCheck."""
        return _read_limited(response, self.max_body_bytes)

    def _interpret(self, status: int, body: bytes, latency_ms: float) -> CheckResult:
        """Require the keyword after the status check has passed."""
        status_result = HttpCheck._interpret(self, status, body, latency_ms)
        if not status_result.ok:
            return status_result
        text = body.decode("utf-8", errors="replace")
        try:
            self._require_keyword(text)
        except ContentMismatchError as exc:
            return self._outcome(
                ok=False,
                status=status,
                latency_ms=latency_ms,
                message=str(exc),
                severity=Severity.CRITICAL,
            )
        return self._outcome(
            ok=True,
            status=status,
            latency_ms=latency_ms,
            message=f"HTTP {status} contains the keyword",
            severity=Severity.OK,
        )

    def _require_keyword(self, text: str) -> None:
        """Raise when the decoded page does not contain the keyword."""
        if self.keyword not in text:
            raise ContentMismatchError("keyword not found")

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> KeywordCheck:
        """Build a ``KeywordCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=KEYWORD_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        try:
            keyword = spec["keyword"]
        except KeyError as exc:
            raise ConfigValidationError("missing keyword") from exc
        return cls(
            name,
            target,
            keyword=keyword,
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            expected_status=optional_value(spec, "expected_status", 200),
            method=optional_value(spec, "method", "GET"),
            allow_private=optional_value(spec, "allow_private", True),
            retries=optional_value(spec, "retries", 3),
            retry_backoff=optional_value(spec, "retry_backoff", 0.2),
            max_body_bytes=optional_value(spec, "max_body_bytes", MAX_BODY_BYTES),
        )


def _read_limited(response: requests.Response, limit: int) -> bytes:
    """Read the entity, stopping once it grows past ``limit``."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ContentMismatchError(f"response body exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
