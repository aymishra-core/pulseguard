"""Repository port implemented by SQLite and MySQL."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import datetime

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError
from pulseguard.storage.records import DEFAULT_CHUNK_SIZE, MonitorRecord, require_chunk_size


class Repository(ABC):
    """Persistence port shared by the database backends.

    Polymorphism: a caller stores results through ``Repository`` and can be
    given either backend. ``transaction`` is the context manager for a write
    that must commit or roll back as one unit.
    """

    @abstractmethod
    def add_result(self, result: CheckResult) -> int:
        """Store one check result and return its id."""

    @abstractmethod
    def results_for(self, monitor: str) -> list[CheckResult]:
        """Return stored results for ``monitor``, oldest first."""

    def iter_results(
        self,
        monitor: str | None = None,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> Iterator[CheckResult]:
        """Yield history in pages. Demonstrates a generator.

        SQL backends override this and issue ``LIMIT``/``OFFSET`` queries.
        The default pages the list from ``results_for`` and requires a monitor.
        """
        if monitor is None:
            raise ConfigValidationError("monitor is required")
        size = require_chunk_size(chunk_size)
        rows = self.results_for(monitor)
        index = 0
        while index < len(rows):
            window = rows[index : index + size]
            index += size
            yield from window

    @abstractmethod
    def delete_result(self, result_id: int) -> bool:
        """Delete one result. Return False when the id is absent."""

    @abstractmethod
    def delete_older_than(self, cutoff: datetime) -> int:
        """Delete results strictly older than ``cutoff`` and return the count."""

    @abstractmethod
    def save_monitor(self, record: MonitorRecord) -> None:
        """Insert a catalog row or replace the row with the same name."""

    @abstractmethod
    def update_monitor(
        self,
        name: str,
        *,
        interval_seconds: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Change the interval, the enabled flag, or both."""

    @abstractmethod
    def monitors(self) -> list[MonitorRecord]:
        """Return catalog rows ordered by name."""

    @abstractmethod
    def delete_monitor(self, name: str) -> bool:
        """Delete a catalog row. Stored results are left in place."""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """Commit on success and roll back when the body raises."""
