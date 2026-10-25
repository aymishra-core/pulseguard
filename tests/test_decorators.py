"""Decorator tests: retry closure, timing, and call logging."""

from __future__ import annotations

import logging
import time

import pytest

from pulseguard.core.decorators import log_call, retry, timed
from pulseguard.exceptions import CheckError


def test_retry_uses_attempts_then_returns() -> None:
    """The nonlocal counter allows exactly ``attempts`` calls."""
    calls = {"n": 0}

    @retry(attempts=3, backoff_seconds=0, retry_on=(CheckError,))
    def flaky() -> str:
        """Fail twice, then succeed."""
        calls["n"] += 1
        if calls["n"] < 3:
            raise CheckError("not yet")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_raises_the_last_error(caplog: pytest.LogCaptureFixture) -> None:
    """A spent attempt budget re-raises, after logging one line per retry."""
    calls = {"n": 0}

    @retry(attempts=2, backoff_seconds=0, retry_on=(CheckError,))
    def always() -> None:
        """Always fail."""
        calls["n"] += 1
        raise CheckError("down")

    with (
        caplog.at_level(logging.WARNING, logger="pulseguard.core.decorators"),
        pytest.raises(CheckError, match="down"),
    ):
        always()
    assert calls["n"] == 2
    assert sum("retrying" in record.message for record in caplog.records) == 1
    assert all("down" not in record.message for record in caplog.records)


def test_retry_sleeps_for_a_positive_backoff() -> None:
    """A non-zero backoff sleeps between attempts."""
    calls = {"n": 0}

    @retry(attempts=2, backoff_seconds=0.01, retry_on=(CheckError,))
    def flaky() -> str:
        """Fail once, then succeed."""
        calls["n"] += 1
        if calls["n"] == 1:
            raise CheckError("once")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_retry_requires_an_exception_type() -> None:
    """An empty ``retry_on`` tuple is rejected."""
    with pytest.raises(ValueError, match="retry_on"):
        retry(retry_on=())


def test_retry_ignores_unlisted_errors() -> None:
    """Errors outside ``retry_on`` propagate on the first attempt."""
    calls = {"n": 0}

    @retry(attempts=4, backoff_seconds=0, retry_on=(CheckError,))
    def boom() -> None:
        """Raise a programming error."""
        calls["n"] += 1
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        boom()
    assert calls["n"] == 1


@pytest.mark.parametrize(
    ("attempts", "backoff"),
    [(0, 0.0), (True, 0.0), (1, -0.1), (1, True)],
)
def test_retry_rejects_bad_configuration(attempts: object, backoff: object) -> None:
    """Retry configuration is validated when the decorator is applied."""
    with pytest.raises(ValueError):

        @retry(attempts=attempts, backoff_seconds=backoff)  # type: ignore[arg-type]
        def _unused() -> None:
            """Not called."""
            return None


def test_timed_records_elapsed_on_the_instance() -> None:
    """``@timed`` stores a non-negative duration on ``last_elapsed``."""

    class Probe:
        """Tiny stand-in for a check instance."""

        def __init__(self) -> None:
            """Start from a sentinel so the test can see the write."""
            self.last_elapsed = -1.0

        @timed
        def go(self) -> str:
            """Sleep briefly so the clock moves."""
            time.sleep(0.05)
            return "done"

    probe = Probe()
    assert probe.go() == "done"
    assert probe.last_elapsed >= 0.03


def test_log_call_omits_arguments(caplog: pytest.LogCaptureFixture) -> None:
    """``@log_call`` records the function name and not its arguments."""

    @log_call
    def add(left: int, right: int) -> int:
        """Add two numbers. The decorator must not log them."""
        return left + right

    @log_call
    def fail() -> None:
        """Raise so the failure line is logged."""
        raise CheckError("nope")

    with caplog.at_level(logging.INFO, logger="pulseguard.core.decorators"):
        assert add(20, 22) == 42
        with pytest.raises(CheckError):
            fail()
    messages = [record.message for record in caplog.records]
    assert any(message.startswith("calling ") and message.endswith(".add") for message in messages)
    assert any(message.startswith("finished ") and message.endswith(".add") for message in messages)
    assert any(message.startswith("failed ") and message.endswith(".fail") for message in messages)
    assert all("20" not in message and "22" not in message for message in messages)
