"""Notifier port. Email delivery is a later phase."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pulseguard.checks.models import CheckResult


class Notifier(ABC):
    """Something that can deliver one check outcome.

    Polymorphism: callers use ``notifier.send`` and do not care which class it is.
    """

    @abstractmethod
    def send(self, result: CheckResult) -> None:
        """Deliver ``result``."""
