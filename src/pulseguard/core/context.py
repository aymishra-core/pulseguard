"""Context managers used by the engine.

``Timer`` records a duration. ``HostLimiter`` is the per-host concurrency cap.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

from pulseguard.exceptions import ConfigValidationError

# Validation limits, not measurements.
MIN_HOST_LIMIT = 1
MAX_HOST_LIMIT = 32


class Timer:
    """Measure the body of a ``with`` block."""

    def __init__(self) -> None:
        """Start with a zero duration."""
        self.elapsed = 0.0
        self._started = 0.0

    def __enter__(self) -> Timer:
        """Mark the start."""
        self._started = time.perf_counter()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Store the elapsed seconds."""
        self.elapsed = time.perf_counter() - self._started


class HostLimiter:
    """Allow a fixed number of checks per target at once.

    The slot is a context manager. It yields False instead of waiting when the
    host is already at the cap, so the caller can defer the check.
    """

    def __init__(self, limit: int) -> None:
        """Store the per-host cap."""
        self._limit = require_host_limit(limit)
        self._guard = Lock()
        self._slots: dict[str, BoundedSemaphore] = {}

    @contextmanager
    def slot(self, host: str) -> Iterator[bool]:
        """Yield True when this host has room. Release the slot on the way out."""
        semaphore = self._semaphore(host)
        acquired = semaphore.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                semaphore.release()

    def _semaphore(self, host: str) -> BoundedSemaphore:
        """Return the semaphore for ``host``, creating it once."""
        with self._guard:
            semaphore = self._slots.get(host)
            if semaphore is None:
                semaphore = BoundedSemaphore(self._limit)
                self._slots[host] = semaphore
            return semaphore


def require_host_limit(value: object) -> int:
    """Return a per-host cap inside the allowed range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError("host_limit must be an integer")
    if value < MIN_HOST_LIMIT or value > MAX_HOST_LIMIT:
        raise ConfigValidationError(f"host_limit must be from {MIN_HOST_LIMIT} to {MAX_HOST_LIMIT}")
    return value
