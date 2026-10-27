"""Defacement check. Hybrid inheritance: ``DefacementCheck(KeywordCheck, AlertThresholdMixin)``."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from pulseguard.checks.base import optional_value, prepare_spec, require_name_target
from pulseguard.checks.keyword_check import (
    KEYWORD_CONFIG_FIELDS,
    MAX_BODY_BYTES,
    KeywordCheck,
    visible_text,
)
from pulseguard.checks.mixins import AlertThresholdMixin
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.exceptions import ConfigValidationError

DEFACEMENT_CONFIG_FIELDS = KEYWORD_CONFIG_FIELDS | {"expected_sha256", "alert_after", "baseline"}
_SHA256_HEX_LENGTH = 64


class DefacementCheck(KeywordCheck, AlertThresholdMixin):
    """Keyword check that also requires the raw body to hash to a known digest.

    The keyword uses the same visible text as ``KeywordCheck``. The hash is
    still SHA-256 of the raw bytes. The mismatch streak stays on this instance
    and is separate from the engine's incident state.
    """

    kind: ClassVar[str] = "defacement"

    def __init__(
        self,
        name: str,
        target: str,
        *,
        keyword: str,
        expected_sha256: str = "",
        alert_after: int = 3,
        learn: bool = False,
        timeout: float | None = None,
        interval: float | None = None,
        expected_status: int = 200,
        method: str = "GET",
        allow_private: bool = True,
        retries: int = 3,
        retry_backoff: float = 0.2,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        """Build the keyword check, then the mismatch threshold."""
        super().__init__(
            name,
            target,
            keyword=keyword,
            timeout=timeout,
            interval=interval,
            expected_status=expected_status,
            method=method,
            allow_private=allow_private,
            retries=retries,
            retry_backoff=retry_backoff,
            max_body_bytes=max_body_bytes,
        )
        self._persist_baseline: Callable[[str], None] | None = None
        self._learn = learn
        self._expected_sha256 = ""
        if expected_sha256 or not learn:
            self.expected_sha256 = expected_sha256
        self.configure_threshold(alert_after)

    @property
    def expected_sha256(self) -> str:
        """Return the expected hex digest of the raw body."""
        return self._expected_sha256

    @expected_sha256.setter
    def expected_sha256(self, value: object) -> None:
        """Store a lowercase SHA-256 hex digest."""
        if not isinstance(value, str):
            raise ConfigValidationError("expected_sha256 must be a string")
        cleaned = value.strip().lower()
        if len(cleaned) != _SHA256_HEX_LENGTH or any(
            char not in "0123456789abcdef" for char in cleaned
        ):
            raise ConfigValidationError("expected_sha256 must be 64 hex characters")
        self._expected_sha256 = cleaned

    def _interpret(self, status: int, body: bytes, latency_ms: float) -> CheckResult:
        """Compare the keyword and the body hash, then apply the streak."""
        if status != self.expected_status:
            return KeywordCheck._interpret(self, status, body, latency_ms)
        keyword_ok = self.keyword in visible_text(body)
        digest = hashlib.sha256(body).hexdigest()
        if self._learn and not self._expected_sha256:
            if not keyword_ok:
                streak = self.note_mismatch()
                return self._outcome(
                    ok=False,
                    status=status,
                    latency_ms=latency_ms,
                    message=f"keyword missing ({streak} of {self.alert_after})",
                    severity=self.severity_for_streak(),
                )
            self.expected_sha256 = digest
            if self._persist_baseline is not None:
                self._persist_baseline(digest)
            self.note_match()
            return self._outcome(
                ok=True,
                status=status,
                latency_ms=latency_ms,
                message="baseline learned",
                severity=Severity.OK,
            )
        hash_ok = hmac.compare_digest(digest, self.expected_sha256)
        if keyword_ok and hash_ok:
            self.note_match()
            return self._outcome(
                ok=True,
                status=status,
                latency_ms=latency_ms,
                message="content matches",
                severity=Severity.OK,
            )
        streak = self.note_mismatch()
        reasons: list[str] = []
        if not keyword_ok:
            reasons.append("keyword missing")
        if not hash_ok:
            reasons.append("body hash differs")
        return self._outcome(
            ok=False,
            status=status,
            latency_ms=latency_ms,
            message=f"{', '.join(reasons)} ({streak} of {self.alert_after})",
            severity=self.severity_for_streak(),
        )

    @classmethod
    def from_config(cls, spec: Mapping[str, Any]) -> DefacementCheck:
        """Build a ``DefacementCheck`` from a JSON object."""
        prepare_spec(spec, kind=cls.kind, allowed=DEFACEMENT_CONFIG_FIELDS)
        name, target = require_name_target(spec)
        try:
            keyword = spec["keyword"]
        except KeyError as exc:
            raise ConfigValidationError(f"missing {exc.args[0]}") from exc
        baseline = optional_value(spec, "baseline", "manual")
        if baseline not in {"learn", "manual"}:
            raise ConfigValidationError("baseline must be learn or manual")
        raw_hash = spec.get("expected_sha256", "")
        if not isinstance(raw_hash, str):
            raise ConfigValidationError("expected_sha256 must be a string")
        if baseline != "learn" and not raw_hash.strip():
            raise ConfigValidationError("missing expected_sha256")
        return cls(
            name,
            target,
            keyword=keyword,
            expected_sha256=raw_hash.strip(),
            learn=baseline == "learn",
            alert_after=optional_value(spec, "alert_after", 3),
            timeout=optional_value(spec, "timeout"),
            interval=optional_value(spec, "interval"),
            expected_status=optional_value(spec, "expected_status", 200),
            method=optional_value(spec, "method", "GET"),
            allow_private=optional_value(spec, "allow_private", True),
            retries=optional_value(spec, "retries", 3),
            retry_backoff=optional_value(spec, "retry_backoff", 0.2),
            max_body_bytes=optional_value(spec, "max_body_bytes", MAX_BODY_BYTES),
        )
