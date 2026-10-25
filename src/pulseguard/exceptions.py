"""Error hierarchy for PulseGuard.

``PulseGuardError`` → ``CheckError`` → timeout, certificate, and content errors,
plus ``ConfigValidationError`` for startup and operator input.
"""

from __future__ import annotations


class PulseGuardError(Exception):
    """Base error for every PulseGuard failure."""


class CheckError(PulseGuardError):
    """A check could not complete."""


class CheckTimeoutError(CheckError):
    """A check exceeded its configured timeout."""


class CertificateExpiringError(CheckError):
    """A TLS certificate is inside an alert window."""


class ContentMismatchError(CheckError):
    """Page content did not match the expected keyword or hash."""


class ConfigValidationError(PulseGuardError):
    """Operator configuration failed validation."""
