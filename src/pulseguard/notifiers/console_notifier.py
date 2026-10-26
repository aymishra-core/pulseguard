"""Write check outcomes as single lines. This does not send email."""

from __future__ import annotations

import sys
from typing import TextIO

from pulseguard.checks.models import CheckResult
from pulseguard.notifiers.base import Notifier


class ConsoleNotifier(Notifier):
    """Append one line per result. Pass a stream to also write it out."""

    def __init__(self, output: TextIO | None = None) -> None:
        """Store lines in memory and, when ``output`` is set, write them there."""
        self._output = output
        self._lines: list[str] = []

    @property
    def lines(self) -> tuple[str, ...]:
        """Return the lines sent so far."""
        return tuple(self._lines)

    def send(self, result: CheckResult) -> None:
        """Record ``result`` as one line. Arguments that are not on the result are omitted."""
        line = f"{result.monitor} ok={result.ok} severity={result.severity} {result.message}"
        self._lines.append(line)
        stream = self._output
        if stream is None:
            return
        stream.write(line + "\n")
        stream.flush()


def stderr_notifier() -> ConsoleNotifier:
    """Build a console notifier that writes to stderr."""
    return ConsoleNotifier(sys.stderr)
