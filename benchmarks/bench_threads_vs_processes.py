"""Time the anomaly scorer sequentially, with threads, and with processes.

The printed seconds are the clock readings from this run. Nothing in this file
is a stored result from another machine.
"""

from __future__ import annotations

from pulseguard.ml.anomaly import time_modes


def main() -> None:
    """Print one timing line per mode. The series is generated here."""
    series = [10.0] * 32
    series.append(400.0)
    tasks = 4
    workers = 2
    repeats = 20000
    timings = time_modes(series, tasks=tasks, workers=workers, repeats=repeats)
    print(f"tasks={tasks}")
    print(f"workers={workers}")
    print(f"repeats={repeats}")
    print(f"samples={len(series)}")
    for name in ("sequential_s", "threads_s", "processes_s"):
        print(f"{name}={timings[name]:.6f}")


if __name__ == "__main__":
    main()
