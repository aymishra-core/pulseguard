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
    """A TLS certificate is inside an alert window or already expired."""

    def __init__(self, message: str, *, severity: object = None) -> None:
        """Store the message and the severity the check should report."""
        super().__init__(message)
        self.severity = severity


class ContentMismatchError(CheckError):
    """Page content did not match the expected keyword or hash."""


class ConfigValidationError(PulseGuardError):
    """Operator configuration failed validation."""


class StorageError(PulseGuardError):
    """A database or backup operation failed."""


class EngineError(PulseGuardError):
    """The scheduler could not start or shut down cleanly."""


class AlertError(PulseGuardError):
    """An alert could not be delivered or an evidence pack could not be written."""


class ProtocolError(PulseGuardError):
    """An agent frame was malformed, refused, or the socket could not be used."""
