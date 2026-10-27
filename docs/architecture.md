# Architecture

```
scheduler -> bounded queue -> worker pool -> result queue -> one SQLite writer
                                      |-> incidents -> notifiers
                                      |-> status page (reads its own connection)
                                      |-> desktop console (reads its own connection)
remote agent -- TCP frames --> collector -> same result queue
```

`pulseguard run` and `pulseguard web` start that scheduler from a JSON file. `pulseguard serve` starts the same writer and builds the schedule from the monitors table. About once a second the scheduler reloads that table, so an add, edit, pause, or delete applies without a restart. `run` and `web` do not reload the catalog.

The status page reads stored rows. It does not contact monitor targets. `/ping/<token>` calls `record_ping` on the heartbeat object the scheduler already holds. `serve` stores that ping time, and it stores open incidents, the alert log, users, and settings in the same database. An alert row is written even when SMTP is not configured. A restart does not drop an open incident.

The signed-in pages are server-rendered Jinja in `web/pages.py`, with one CSS file and one script. GET pages do not fetch a monitor URL. Run check now is a POST the operator submits. `POST /monitors/probe` runs one unsaved check from the Add monitor form. It requires a login and a CSRF token, and it refuses a second probe inside two seconds. `pulseguard serve` opens `/` and sends that request to setup, login, or the dashboard. The public status page stays at `/status`, and it can be turned off. It draws a 90-day uptime bar from stored results and lists incident titles and times. It does not show targets. `pulseguard web` still serves the public page at `/` and keeps `/admin`.

A gap of more than three intervals is stored as `monitoring gap`. That row is grey on the uptime bar and is left out of the uptime percentage. The first failure after a gap is labelled `possible local network issue after a gap` and does not count toward opening an incident. The three-failure rule is unchanged for later failures.

PulseGuard does not ship sample monitors. Every address, port, keyword, and token comes from the Add monitor form. A defacement monitor stores the SHA-256 of the first successful body when its baseline is learn, or it uses the hash the operator typed. `tools/lab.py` is a separate program. The application does not import it and does not start it.

The desktop console is a separate process. It reads the same SQLite file. A loader thread queues one snapshot, and the main thread applies it.

`docs/concurrency-design.md` covers the threads. `docs/security.md` covers the bind address, login, and the status page. `docs/syllabus-coverage.md` maps the course topics to files.
