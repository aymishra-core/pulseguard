"""Value types shared by checks.

Operator overloading lives here: ``Severity.__lt__``, ``LatencyStats.__add__``,
``CheckResult.__eq__`` / ``__hash__``, and ``IncidentLog`` len / iter / contains.
``IncidentLog`` also provides the custom iterator ``IncidentCursor``.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from pulseguard.exceptions import ConfigValidationError


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@functools.total_ordering
class Severity:
    """Ordered alert level from ok through critical.

    ``__lt__`` is the comparison operator. ``total_ordering`` fills in the rest
    from ``__lt__`` and ``__eq__``.
    """

    _RANK: ClassVar[dict[str, int]] = {
        "ok": 0,
        "info": 1,
        "warning": 2,
        "critical": 3,
    }
    OK: ClassVar[Severity]
    INFO: ClassVar[Severity]
    WARNING: ClassVar[Severity]
    CRITICAL: ClassVar[Severity]

    def __init__(self, name: str) -> None:
        """Build a severity from its name."""
        if not isinstance(name, str):
            raise ConfigValidationError("severity must be a string")
        key = name.strip().lower()
        try:
            rank = self._RANK[key]
        except KeyError as exc:
            known = ", ".join(sorted(self._RANK))
            raise ConfigValidationError(f"unknown severity {name!r}; expected {known}") from exc
        self._name = key
        self._rank = rank

    def __lt__(self, other: object) -> bool:
        """Return True when this severity is less severe than ``other``."""
        if not isinstance(other, Severity):
            return NotImplemented
        return self._rank < other._rank

    def __eq__(self, other: object) -> bool:
        """Return True when both sides name the same severity."""
        if not isinstance(other, Severity):
            return NotImplemented
        return self._name == other._name

    def __hash__(self) -> int:
        """Hash by severity name so equal values can share a set."""
        return hash(self._name)

    def __str__(self) -> str:
        """Return the severity name."""
        return self._name

    def __repr__(self) -> str:
        """Return a constructor-style representation."""
        return f"Severity({self._name!r})"


Severity.OK = Severity("ok")
Severity.INFO = Severity("info")
Severity.WARNING = Severity("warning")
Severity.CRITICAL = Severity("critical")


@dataclass(eq=False)
class CheckResult:
    """Outcome of one check run.

    Equality and hashing ignore ``checked_at`` and ``latency_ms`` so two runs
    with the same outcome can be deduplicated. ``__post_init__`` asserts the
    invariants that a result must always satisfy.
    """

    monitor: str
    kind: str
    ok: bool
    latency_ms: float
    message: str
    severity: Severity
    checked_at: datetime
    status_code: int | None = None

    def __post_init__(self) -> None:
        """Assert result invariants."""
        assert self.monitor, "monitor name is required"
        assert self.kind, "kind is required"
        assert self.message, "message is required"
        assert self.latency_ms >= 0, "latency_ms must be >= 0"
        assert self.checked_at.tzinfo is not None, "checked_at must be timezone-aware"
        if self.ok:
            assert self.severity < Severity.CRITICAL, "a passing check cannot be critical"
        else:
            assert self.severity > Severity.OK, "a failing check must outrank ok"

    def __eq__(self, other: object) -> bool:
        """Compare outcome fields, not the clock or the sample latency."""
        if not isinstance(other, CheckResult):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        """Hash the same fields ``__eq__`` compares."""
        return hash(self._identity())

    def _identity(self) -> tuple[object, ...]:
        """Return the fields that define an equal outcome."""
        return (
            self.monitor,
            self.kind,
            self.ok,
            self.status_code,
            self.message,
            self.severity,
        )

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready mapping of this result."""
        return {
            "monitor": self.monitor,
            "kind": self.kind,
            "ok": self.ok,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "severity": str(self.severity),
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class LatencyStats:
    """A latency window that can be added to another window.

    ``__add__`` combines two samples. An empty window has ``count == 0``.
    """

    count: int
    total_ms: float
    max_ms: float

    def __post_init__(self) -> None:
        """Assert the window is internally consistent."""
        assert self.count >= 0, "count must be >= 0"
        assert self.total_ms >= 0, "total_ms must be >= 0"
        assert self.max_ms >= 0, "max_ms must be >= 0"
        if self.count == 0:
            assert self.total_ms == 0, "an empty window cannot carry a total"
            assert self.max_ms == 0, "an empty window cannot carry a max"

    def __add__(self, other: object) -> LatencyStats:
        """Combine two latency windows."""
        if not isinstance(other, LatencyStats):
            return NotImplemented
        if self.count == 0:
            return other
        if other.count == 0:
            return self
        return LatencyStats(
            count=self.count + other.count,
            total_ms=self.total_ms + other.total_ms,
            max_ms=max(self.max_ms, other.max_ms),
        )

    @property
    def mean_ms(self) -> float | None:
        """Return the mean latency, or None when the window is empty."""
        if self.count == 0:
            return None
        return self.total_ms / self.count


class IncidentCursor:
    """Iterator over incident notes. Demonstrates a custom iterator."""

    def __init__(self, events: Sequence[str]) -> None:
        """Start at the first note."""
        self._events = events
        self._index = 0

    def __iter__(self) -> IncidentCursor:
        """Return this iterator."""
        return self

    def __next__(self) -> str:
        """Return the next note or raise ``StopIteration``."""
        if self._index >= len(self._events):
            raise StopIteration
        event = self._events[self._index]
        self._index += 1
        return event


class IncidentLog:
    """Ordered incident notes with length, membership, and iteration.

    ``__iter__`` returns an ``IncidentCursor`` rather than yielding directly,
    so this type stays a custom iterator and not a generator.
    """

    def __init__(self, events: Iterable[str] | None = None) -> None:
        """Copy ``events`` into a new log."""
        self._events: list[str] = []
        for event in events or ():
            self.append(event)

    def append(self, event: str) -> None:
        """Append a non-empty note."""
        if not isinstance(event, str):
            raise TypeError("incident note must be a string")
        text = event.strip()
        if not text:
            raise ValueError("incident note cannot be empty")
        self._events.append(text)

    def __len__(self) -> int:
        """Return how many notes are stored."""
        return len(self._events)

    def __iter__(self) -> Iterator[str]:
        """Return a new cursor so the log can be walked more than once."""
        return IncidentCursor(self._events)

    def __contains__(self, item: object) -> bool:
        """Return True when ``item`` is a stored note."""
        return isinstance(item, str) and item in self._events
