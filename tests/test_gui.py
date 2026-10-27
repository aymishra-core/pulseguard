"""The desktop console queues snapshots and only touches widgets on the main thread."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.cli import main
from pulseguard.gui.console import StatusFeed, read_status
from pulseguard.gui.sla_calculator import format_budget
from pulseguard.storage import SqliteRepository
from pulseguard.web.status import StatusRow


class _View:
    """Stand-in for the Tk widgets. It records which thread called it."""

    def __init__(self) -> None:
        """Start empty."""
        self.calls: list[str] = []
        self.rows: tuple[StatusRow, ...] = ()

    def replace_rows(self, rows: tuple[StatusRow, ...]) -> None:
        """Refuse a loader thread, then remember the snapshot."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("widgets are main-thread only")
        self.calls.append(threading.current_thread().name)
        self.rows = rows


def test_budget_uses_the_sla_helper() -> None:
    """One success out of two at target 0.5 leaves no budget."""
    assert format_budget(1, 2, 0.5) == (
        "Achieved 50%. Allowed failures: 1. Actual failures: 1. "
        "Error budget remaining: 0% (target met)"
    )
    assert "between 0 and 1" in format_budget(1, 2, 1)


def test_budget_reports_met_and_missed_windows() -> None:
    """A 0.99 target over 1000 samples shows the budget in words."""
    assert format_budget(990, 1000, 0.99) == (
        "Achieved 99%. Allowed failures: 10. Actual failures: 10. "
        "Error budget remaining: 0% (target met)"
    )
    assert format_budget(980, 1000, 0.99) == (
        "Achieved 98%. Allowed failures: 10. Actual failures: 20. "
        "Error budget remaining: -100% (target missed)"
    )
    assert format_budget(995, 1000, 0.99) == (
        "Achieved 99.5%. Allowed failures: 10. Actual failures: 5. "
        "Error budget remaining: 50% (target met)"
    )


def test_loader_thread_does_not_touch_the_view(tmp_path: Path) -> None:
    """A worker may publish. Only poll, on the main thread, calls the view."""
    feed = StatusFeed(tmp_path / "history.db")
    view = _View()
    row = StatusRow("web", "http", True, "ok", "HTTP 200", "2026-10-27T00:00:00+00:00")
    errors: list[str] = []

    def work() -> None:
        feed.publish((row,))
        try:
            view.replace_rows(())
        except RuntimeError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=work, name="pulseguard-gui-load")
    worker.start()
    worker.join()
    assert errors == ["widgets are main-thread only"]
    assert view.calls == []
    feed.poll(view)
    assert view.rows == (row,)
    assert view.calls == [threading.current_thread().name]
    assert threading.current_thread() is threading.main_thread()


def test_feed_reads_the_database_and_stops(tmp_path: Path) -> None:
    """The loader thread exits, and the snapshot has no target field."""
    database = tmp_path / "history.db"
    with SqliteRepository(database) as repo:
        repo.add_result(
            CheckResult(
                monitor="web",
                kind="http",
                ok=True,
                latency_ms=1,
                message="HTTP 200",
                severity=Severity.OK,
                checked_at=datetime(2026, 10, 27, tzinfo=UTC),
                status_code=200,
            )
        )
    rows = read_status(database)
    assert rows[0].message == "HTTP 200"
    assert not hasattr(rows[0], "target")
    feed = StatusFeed(database)
    feed.start()
    thread = feed.thread
    assert thread is not None
    assert thread.name == "pulseguard-gui-load"
    feed.stop()
    assert thread.is_alive() is False


def test_console_command_does_not_open_a_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command calls the launcher. This test replaces the launcher."""
    import pulseguard.gui.widgets as widgets

    seen: list[Path] = []
    monkeypatch.setattr(widgets, "launch_console", lambda path: seen.append(path))
    database = tmp_path / "history.db"
    code, _text, err = _run(["console", "--db", str(database)])
    assert code == 0, err
    assert seen == [database]


def test_closing_the_console_exits_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the window returns control with no traceback."""
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    import pulseguard.gui.widgets as widgets

    real_init = widgets.TkStatusView.__init__

    def close_soon(self: widgets.TkStatusView, root: tkinter.Tk, feed: StatusFeed) -> None:
        real_init(self, root, feed)
        root.withdraw()
        root.after(0, root.destroy)

    monkeypatch.setattr(widgets.TkStatusView, "__init__", close_soon)
    database = tmp_path / "history.db"
    code, _text, err = _run(["console", "--db", str(database)])
    assert code == 0, err
    assert "Traceback" not in err
    assert "pulseguard-gui-load" not in [thread.name for thread in threading.enumerate()]


def test_tk_widgets_stay_on_the_main_thread(tmp_path: Path) -> None:
    """When Tk is available, a worker cannot update the table or the form."""
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    root.withdraw()
    from pulseguard.gui.widgets import TkStatusView

    feed = StatusFeed(tmp_path / "history.db")
    view = TkStatusView(root, feed)
    try:
        assert view._tree["columns"] == ("name", "kind", "severity", "message")
        assert view._after_id is not None
        errors: list[str] = []

        def touch() -> None:
            try:
                view.replace_rows(())
            except RuntimeError as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=touch)
        worker.start()
        worker.join()
        assert errors == ["widgets are main-thread only"]
        view._successes.set("1")
        view._total.set("2")
        view._target.set("0.5")
        view._calculate()
        assert view._budget.get() == (
            "Achieved 50%. Allowed failures: 1. Actual failures: 1. "
            "Error budget remaining: 0% (target met)"
        )
    finally:
        view.close()
        root.destroy()


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    import contextlib
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()
