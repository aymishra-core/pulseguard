"""Latency scoring, including one injected slow sample and a process handoff."""

from __future__ import annotations

import pytest

from pulseguard.exceptions import ConfigValidationError
from pulseguard.ml.anomaly import detect, detect_in_process, time_modes


def test_an_injected_slow_sample_is_flagged() -> None:
    """A quiet series stays quiet. One large sample is the only flag."""
    quiet = [10.0, 11.0, 9.0, 10.0, 12.0, 10.0, 11.0, 9.0]
    assert detect(quiet) == ()
    series = [10.0] * 30
    series.append(400.0)
    assert detect(series) == (30,)
    assert detect_in_process(series) == (30,)


def test_short_series_is_rejected() -> None:
    """Fewer than three samples is a configuration error."""
    with pytest.raises(ConfigValidationError, match="anomaly samples"):
        detect([1.0, 2.0])


def test_time_modes_reports_three_positive_durations() -> None:
    """The comparison runs. It does not claim which mode is faster."""
    timings = time_modes([10.0, 10.0, 10.0, 400.0], tasks=1, workers=1, repeats=1)
    assert set(timings) == {"sequential_s", "threads_s", "processes_s"}
    assert all(value > 0 for value in timings.values())
