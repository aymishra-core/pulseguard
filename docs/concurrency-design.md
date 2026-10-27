# Concurrency

Checks are I/O. The engine keeps at most `workers` of them in flight and submits the next when one finishes (`FIRST_COMPLETED` in `src/pulseguard/core/engine.py`). A full job queue defers the check. It does not grow.

One writer thread is the only caller of `add_result` on the engine's SQLite connection. The status page and the desktop loader open their own connections.

Shutdown sets an `Event`, stops the agent collector first, then joins the scheduler, the dispatcher, and the writer. `Lock` guards the engine counters. The collector uses a `BoundedSemaphore` for its connection cap. Each monitored host has its own semaphore in `HostLimiter`.

The desktop loader is named `pulseguard-gui-load`. It only queues rows. Widget calls check `threading.main_thread()` before they touch Tk.

Anomaly scoring is CPU work. `detect_in_process` and `time_modes` use `ProcessPoolExecutor`. Threads and processes are timed by `benchmarks/bench_threads_vs_processes.py`. The printed seconds are that run's clock readings.
