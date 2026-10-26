"""Mixins used by the TLS and defacement checks.

``SerializableMixin`` is the other base of ``TlsExpiryCheck`` (multiple
inheritance). ``AlertThresholdMixin`` is the other base of ``DefacementCheck``
(hybrid inheritance). Neither mixin is a check, so neither sets ``kind``.
"""

from __future__ import annotations

from typing import ClassVar

from pulseguard.checks.base import require_int
from pulseguard.checks.models import Severity
from pulseguard.exceptions import ConfigValidationError

# How many consecutive mismatches a defacement check may require.
MIN_ALERT_AFTER = 1
MAX_ALERT_AFTER = 20


class SerializableMixin:
    """Copy the declared fields of a check into a JSON-ready mapping.

    Multiple inheritance: ``TlsExpiryCheck(SerializableMixin, BaseCheck)``.
    """

    serial_fields: ClassVar[tuple[str, ...]] = ()

    def to_mapping(self) -> dict[str, object]:
        """Return the serial fields for this instance."""
        return {field: getattr(self, field) for field in type(self).serial_fields}


class AlertThresholdMixin:
    """Count consecutive mismatches before a result becomes critical.

    Hybrid inheritance: ``DefacementCheck(KeywordCheck, AlertThresholdMixin)``.
    The streak stays on the check instance. The engine incident state is separate.
    """

    def configure_threshold(self, alert_after: int = 3) -> None:
        """Store the mismatch count required before a critical result."""
        self._streak = 0
        self.alert_after = alert_after

    @property
    def alert_after(self) -> int:
        """Return how many consecutive mismatches escalate to critical."""
        return self.__alert_after

    @alert_after.setter
    def alert_after(self, value: object) -> None:
        """Validate and store the threshold behind a name-mangled attribute."""
        number = require_int(value, "alert_after")
        if number < MIN_ALERT_AFTER or number > MAX_ALERT_AFTER:
            raise ConfigValidationError(
                f"alert_after must be from {MIN_ALERT_AFTER} to {MAX_ALERT_AFTER}"
            )
        self.__alert_after = number

    def note_mismatch(self) -> int:
        """Record one mismatch and return the current streak."""
        self._streak += 1
        return self._streak

    def note_match(self) -> None:
        """Clear the streak after a matching observation."""
        self._streak = 0

    def severity_for_streak(self) -> Severity:
        """Return warning until the streak reaches ``alert_after``, then critical."""
        assert self._streak > 0, "severity_for_streak requires a mismatch"
        if self._streak >= self.alert_after:
            return Severity.CRITICAL
        return Severity.WARNING
