"""Tkinter window. Every widget call checks that it is on the main thread.

The toolbar uses ``pack``. The SLA form uses ``grid``. ``after`` polls the
feed queue. A loader thread never reaches these methods.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pulseguard.exceptions import ConfigValidationError
from pulseguard.gui.console import UI_POLL_MS, StatusFeed
from pulseguard.gui.sla_calculator import format_budget
from pulseguard.web.status import StatusRow

if TYPE_CHECKING:
    import tkinter as tk


class TkStatusView:
    """The ops console. Construct it on the main thread."""

    def __init__(self, root: tk.Tk, feed: StatusFeed) -> None:
        """Build the toolbar, the table, and the SLA form."""
        self._require_main()
        tk, ttk = _toolkit()
        self._root = root
        self._feed = feed
        toolbar = ttk.Frame(root)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(toolbar, text="PulseGuard").pack(side=tk.LEFT, padx=8, pady=4)
        self._tree = ttk.Treeview(
            root,
            columns=("name", "kind", "severity", "message"),
            show="headings",
            height=8,
        )
        for column in ("name", "kind", "severity", "message"):
            self._tree.heading(column, text=column)
        self._tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        form = ttk.Frame(root)
        form.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=8)
        self._successes = tk.StringVar()
        self._total = tk.StringVar()
        self._target = tk.StringVar(value="0.99")
        self._budget = tk.StringVar(value="")
        _labeled(form, 0, "successes", self._successes)
        _labeled(form, 1, "total", self._total)
        _labeled(form, 2, "target", self._target)
        ttk.Button(form, text="Calculate", command=self._calculate).grid(
            row=3, column=1, sticky="w"
        )
        ttk.Label(form, textvariable=self._budget).grid(row=3, column=2, sticky="w")
        self._after_id: str | None = root.after(UI_POLL_MS, self._tick)

    def replace_rows(self, rows: Sequence[StatusRow]) -> None:
        """Replace the table. The target column is not a column."""
        self._require_main()
        tk, _ttk = _toolkit()
        self._tree.delete(*self._tree.get_children())
        for row in rows:
            self._tree.insert(
                "",
                tk.END,
                values=(row.name, row.kind, row.severity, row.message),
            )

    def show_budget(self, text: str) -> None:
        """Show the calculator result on the form."""
        self._require_main()
        self._budget.set(text)

    def close(self) -> None:
        """Cancel the poll callback. A closed window has nothing left to cancel."""
        self._require_main()
        after_id = self._after_id
        self._after_id = None
        if after_id is None:
            return
        tk, _ttk = _toolkit()
        with contextlib.suppress(tk.TclError):
            self._root.after_cancel(after_id)

    def _tick(self) -> None:
        """Drain the queue on the main thread and schedule the next poll."""
        self._require_main()
        self._feed.poll(self)
        self._after_id = self._root.after(UI_POLL_MS, self._tick)

    def _calculate(self) -> None:
        """Read the form and show the budget. Tk calls this on the main thread."""
        self._require_main()
        text = format_budget(
            _parse_int(self._successes.get()),
            _parse_int(self._total.get()),
            _parse_float(self._target.get()),
        )
        self.show_budget(text)

    def _require_main(self) -> None:
        """Refuse a widget call from a loader thread."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("widgets are main-thread only")


def launch_console(database: Path) -> None:
    """Open the window and run the Tk event loop until it closes."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("widgets are main-thread only")
    try:
        tk, _ttk = _toolkit()
    except ImportError as exc:
        raise ConfigValidationError("desktop console needs tkinter") from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ConfigValidationError("desktop console needs a display") from exc
    root.title("PulseGuard")
    feed = StatusFeed(database)
    view = TkStatusView(root, feed)
    feed.start()
    try:
        root.mainloop()
    finally:
        feed.stop()
        view.close()
        with contextlib.suppress(tk.TclError):
            if root.winfo_exists():
                root.destroy()


def _labeled(form: Any, row: int, caption: str, variable: tk.StringVar) -> None:
    """Place one caption and one entry on the grid."""
    _tk, ttk = _toolkit()
    ttk.Label(form, text=caption).grid(row=row, column=0, sticky="w", padx=4, pady=2)
    ttk.Entry(form, textvariable=variable, width=12).grid(row=row, column=1, sticky="w")


def _toolkit() -> tuple[Any, Any]:
    """Import Tkinter when a window is built. Tests can import this module without it."""
    import tkinter as tk
    from tkinter import ttk

    return tk, ttk


def _parse_int(value: str) -> object:
    """Return an int, or the original text when it is not a base-10 integer."""
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    return value


def _parse_float(value: str) -> object:
    """Return a float, or the original text when it is not a number."""
    try:
        if value.lower() in {"true", "false"}:
            return value
        return float(value)
    except ValueError:
        return value
