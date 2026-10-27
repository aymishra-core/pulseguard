# Architecture

```
scheduler -> bounded queue -> worker pool -> result queue -> one SQLite writer
                                      |-> incidents -> notifiers
                                      |-> status page (reads its own connection)
                                      |-> desktop console (reads its own connection)
remote agent -- TCP frames --> collector -> same result queue
```

`pulseguard run` and `pulseguard web` start that scheduler in the current process. The status page reads stored rows. It does not contact monitor targets. `/ping/<token>` calls `record_ping` on the heartbeat object the scheduler already holds.

The desktop console is a separate process. It reads the same SQLite file. A loader thread queues one snapshot, and the main thread applies it.

`docs/concurrency-design.md` covers the threads. `docs/security.md` covers the bind address, login, and the status page. `docs/syllabus-coverage.md` maps the course topics to files.
