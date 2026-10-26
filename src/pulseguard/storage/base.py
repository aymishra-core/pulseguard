"""Repository port. SQLite and MySQL implement this in a later phase."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pulseguard.checks.models import CheckResult


class Repository(ABC):
    """Persistence port shared by later database backends.

    Polymorphism: a caller stores results through ``Repository`` and can be
    given any implementation.
    """

    @abstractmethod
    def add_result(self, result: CheckResult) -> None:
        """Store one check result."""

    @abstractmethod
    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return stored results for ``monitor``, oldest first."""
