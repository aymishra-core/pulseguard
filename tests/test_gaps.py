"""A long quiet period is a monitoring gap, not an outage."""

from __future__ import annotations

from datetime import timedelta

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.engine import Engine
from pulseguard.core.gaps import AFTER_GAP_MESSAGE, GAP_MESSAGE, exceeds_gap
from pulseguard.core.incidents import IncidentTracker
from pulseguard.web.app import create_app
from pulseguard.web.board import activity_lines, plain_message, uptime


class _Quiet:
    """A check the engine can hold without running."""

    name = "web"
    kind = "http"
    interval = 30.0

    @property
    def target(self) -> str:
        """Return a loopback target. The test never connects."""
        return "http://127.0.0.1/"

    def run(self) -> CheckResult:
        """The gap test does not run the check."""
        raise AssertionError("not run")


class _Book:
    """In-memory results for the gap annotation."""

    def __init__(self) -> None:
        """Start empty."""
        self.rows: list[CheckResult] = []

    def results_for(self, name: str) -> list[CheckResult]:
        """Return rows for one monitor."""
        return [row for row in self.rows if row.monitor == name]

    def add_result(self, result: CheckResult) -> None:
        """Append one row."""
        self.rows.append(result)


def _result(ok: bool, message: str, *, age: int = 0) -> CheckResult:
    """Build one result at a known age in seconds."""
    return CheckResult(
        monitor="web",
        kind="http",
        ok=ok,
        latency_ms=1,
        message=message,
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=utc_now() - timedelta(seconds=age),
    )


def test_three_intervals_of_silence_is_a_gap() -> None:
    """More than three intervals is a gap. Three intervals is still a normal wait."""
    start = utc_now()
    assert exceeds_gap(start, start + timedelta(seconds=91), 30)
    assert not exceeds_gap(start, start + timedelta(seconds=90), 30)


def test_gap_is_excluded_from_uptime_and_does_not_open_an_incident() -> None:
    """The gap row is not a success, and the first failure after it does not count."""
    gap = _result(True, GAP_MESSAGE)
    good = _result(True, "HTTP 200")
    bad = _result(False, "down")
    assert uptime([gap, good, bad]) == 0.5
    tracker = IncidentTracker(failures_before_open=3)
    assert tracker.observe(bad, suppressed=False, counts=False) is None
    assert tracker.observe(bad, suppressed=False) is None
    assert tracker.observe(bad, suppressed=False) is None
    assert tracker.observe(bad, suppressed=False) == "open"


def test_engine_records_a_gap_and_relabels_the_next_failure() -> None:
    """The writer stores the gap, then keeps that failure off the streak."""
    engine = Engine([_Quiet()], repository_factory=lambda: _Book())
    book = _Book()
    book.add_result(_result(True, "HTTP 200", age=200))
    failed = _result(False, "connection refused")
    annotated, counts = engine._annotate_gap(failed, book)  # type: ignore[arg-type]
    assert counts is False
    assert annotated.message == AFTER_GAP_MESSAGE
    assert book.rows[-1].message == GAP_MESSAGE


def test_plain_language_and_the_activity_feed() -> None:
    """A connection error and a recovery read as sentences, newest first."""
    assert "connection refused" in plain_message("check failed: ConnectionError")
    assert "too long" in plain_message("check failed: ReadTimeout")
    assert plain_message("HTTP 200") == "HTTP 200"
    old = _result(True, "HTTP 200", age=20)
    failed = _result(False, "connection refused", age=10)
    back = _result(True, "HTTP 200", age=0)
    lines = activity_lines([old, failed, back])
    assert lines[0]["text"] == "web recovered"
    assert lines[1]["state"] == "down"


def test_activity_feed_requires_a_login(tmp_path) -> None:
    """The activity feed is not on the public status page."""
    app = create_app(
        database=tmp_path / "history.db",
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password="s3cret-password",
        api_key="example-api-key-value",
    )
    assert app.test_client().get("/api/activity").status_code == 401
