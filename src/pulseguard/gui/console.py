"""A queue between a loader thread and the main-thread view.

The loader never receives a view. ``poll`` is what applies a snapshot, and the
view is responsible for refusing that call from any other thread.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread

from pulseguard.storage import SqliteRepository
from pulseguard.web.status import StatusRow, latest_status

# UI poll limits, not measurements.
LOAD_POLL_SECONDS = 1.0
UI_POLL_MS = 200


class StatusFeed:
    """Publish status snapshots. Only the latest one is kept."""

    def __init__(self, database: Path) -> None:
        """Store the database path. The loader starts in ``start``."""
        self._database = database
        self._queue: Queue[tuple[StatusRow, ...]] = Queue(maxsize=1)
        self._stop = Event()
        self._thread: Thread | None = None

    @property
    def thread(self) -> Thread | None:
        """Return the loader thread after ``start``."""
        return self._thread

    def start(self) -> None:
        """Start the loader. It only reads the database and queues rows."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="pulseguard-gui-load")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the loader and join it."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)

    def publish(self, rows: Sequence[StatusRow]) -> None:
        """Queue one snapshot from any thread. This does not touch a widget."""
        snapshot = tuple(rows)
        with contextlib.suppress(Empty):
            self._queue.get_nowait()
        try:
            self._queue.put_nowait(snapshot)
        except Full:
            return

    def poll(self, view: object) -> None:
        """Apply the newest snapshot. ``view.replace_rows`` enforces the thread."""
        try:
            rows = self._queue.get_nowait()
        except Empty:
            return
        replace = getattr(view, "replace_rows", None)
        if not callable(replace):
            raise TypeError("view must provide replace_rows")
        replace(rows)

    def _loop(self) -> None:
        """Read until stopped. The view is not in scope here."""
        while not self._stop.is_set():
            self.publish(read_status(self._database))
            self._stop.wait(LOAD_POLL_SECONDS)


def read_status(database: Path) -> tuple[StatusRow, ...]:
    """Open the database, read the public rows, and close it."""
    with SqliteRepository(database) as repo:
        return tuple(latest_status(repo))
