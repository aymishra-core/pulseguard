"""Flag a slow sample in a latency series.

The cutoff is a fixed multiple of the median absolute deviation. It is a
limit, not a measurement from this project. Fitting can run in a process so
the comparison with threads has CPU work to time.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from pulseguard.exceptions import ConfigValidationError

# Validation limits, not measurements.
_MIN_SAMPLES = 3
_MAX_SAMPLES = 100_000
_MAX_TASKS = 32
_MAX_WORKERS = 8
_MAX_REPEATS = 100_000
_DEVIATIONS = 6


def detect(samples: Sequence[float]) -> tuple[int, ...]:
    """Return indexes whose latency is above the configured cutoff."""
    values = _require_samples(samples)
    center, scale = _center_and_scale(values)
    limit = center + _DEVIATIONS * scale
    return tuple(index for index, value in enumerate(values) if value > limit)


def detect_in_process(samples: Sequence[float]) -> tuple[int, ...]:
    """Score one series in a worker process. ``ProcessPoolExecutor`` does the handoff."""
    values = _require_samples(samples)
    with ProcessPoolExecutor(max_workers=1) as pool:
        return pool.submit(detect, values).result()


def score_repeated(samples: list[float], repeats: int) -> int:
    """Repeat ``detect`` so a timing run has CPU work. The return value is unused."""
    _require_bound(repeats, "repeat count", _MAX_REPEATS)
    found = 0
    for _ in range(repeats):
        found += len(detect(samples))
    return found


def time_modes(
    samples: Sequence[float],
    *,
    tasks: int,
    workers: int,
    repeats: int,
) -> dict[str, float]:
    """Time the same scoring work sequentially, in threads, and in processes."""
    payload = _require_samples(samples)
    _require_bound(tasks, "task count", _MAX_TASKS)
    _require_bound(workers, "worker count", _MAX_WORKERS)
    _require_bound(repeats, "repeat count", _MAX_REPEATS)
    jobs = [payload] * tasks
    repeat_args = [repeats] * tasks
    return {
        "sequential_s": _elapsed(lambda: _run_sequential(payload, tasks, repeats)),
        "threads_s": _elapsed(lambda: _run_pool(ThreadPoolExecutor, workers, jobs, repeat_args)),
        "processes_s": _elapsed(lambda: _run_pool(ProcessPoolExecutor, workers, jobs, repeat_args)),
    }


def _run_sequential(payload: list[float], tasks: int, repeats: int) -> None:
    """Score on this thread."""
    for _ in range(tasks):
        score_repeated(payload, repeats)


def _run_pool(
    pool_type: type[ThreadPoolExecutor] | type[ProcessPoolExecutor],
    workers: int,
    jobs: list[list[float]],
    repeats: list[int],
) -> None:
    """Score with the given executor. The caller times the call."""
    with pool_type(max_workers=workers) as pool:
        list(pool.map(score_repeated, jobs, repeats))


def _elapsed(action: Callable[[], None]) -> float:
    """Return seconds for one action. The figure is whatever the clock reports."""
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def _center_and_scale(values: list[float]) -> tuple[float, float]:
    """Return the median and a positive deviation scale."""
    center = _median(values)
    deviations = [abs(value - center) for value in values]
    scale = _median(deviations)
    if scale == 0:
        scale = 1.0
    return center, scale


def _median(values: list[float]) -> float:
    """Return the middle value of a copy. The input order stays as it was."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _require_samples(samples: Sequence[float]) -> list[float]:
    """Return a finite numeric series inside the length cap."""
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise ConfigValidationError("anomaly samples must be a list of numbers")
    if len(samples) < _MIN_SAMPLES or len(samples) > _MAX_SAMPLES:
        raise ConfigValidationError(
            f"anomaly samples must be from {_MIN_SAMPLES} to {_MAX_SAMPLES}"
        )
    values: list[float] = []
    for value in samples:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigValidationError("anomaly samples must be numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ConfigValidationError("anomaly samples must be finite")
        values.append(number)
    return values


def _require_bound(value: int, field: str, upper: int) -> int:
    """Return a positive count inside a cap."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > upper:
        raise ConfigValidationError(f"{field} must be from 1 to {upper}")
    return value
