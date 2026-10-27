# Changelog

## 1.0.0 - 2026-10-27

Tag notes for v1.0.0. This is the first release cut.

- HTTP, keyword, defacement, TCP, DNS, TLS expiry, and heartbeat checks.
- SQLite storage, with MySQL behind the same repository interface.
- A threaded scheduler, incident open/resolve, email when SMTP is configured, and an evidence pack.
- A length-prefixed agent frame protocol.
- A status page, admin login, CSRF, a JSON write API, and `/ping/<token>`.
- A desktop console whose widgets are updated on the main thread.
- An optional latency anomaly scorer, including a process-pool path.
- A container that runs as a non-root user. `docker compose up --build` publishes the status page on `127.0.0.1:8080`.

A nightly backup, a retention purge, and a timed digest are not scheduled. The benchmark seconds are the output of `benchmarks/bench_threads_vs_processes.py` on the machine that ran it. This file does not copy those seconds.
