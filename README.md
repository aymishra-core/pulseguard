# PulseGuard

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -e .
pulseguard serve
```

Open the browser page, create the admin account, and add monitors from there. `./start.sh` does the same steps.

## Try it in 2 minutes

The test lab is a separate program. It is not part of PulseGuard.

```sh
python tools/lab.py
```

In another terminal, run `pulseguard serve` and create the admin account. Click **Add monitor** and type each of these:

- HTTP, name `web`, address `http://127.0.0.1:8799/`
- keyword, address `http://127.0.0.1:8799/`, keyword `Acme Shop`
- TCP, address `127.0.0.1`, port `8798`
- heartbeat, then copy its address and token into the boxes on the lab page

On the lab page, use **Make site DOWN**, **Bring site UP**, and **Deface page**. For a defacement monitor, leave the baseline on **Learn from the first successful check** before you deface the page.

PulseGuard is a self-hosted monitor for websites, APIs, TCP ports, TLS certificate expiry, and scheduled-job heartbeats. This repository is a student project built to production-grade standards. It is not a replacement for Uptime Kuma or any other monitoring product.

Version 1.3.0 is the telemetry console: a grid background, teal accent, HUD corners, and a status ring that follows an open incident, a gap, a pause, or a clean bill of health. The sidebar shows the installed version. The type is a system sans and a system monospace. Light and dark follow the system until you toggle them, and a quiet period longer than three intervals is shown as no data instead of a false outage. Add monitor can test the check before you save it. The public status page stays at `/status`. Nothing runs a nightly backup, a retention purge, or a timed digest.

![Dashboard](docs/screenshots/dashboard.png)

![Dashboard, dark](docs/screenshots/dashboard-dark.png)

![Monitor detail](docs/screenshots/monitor-detail.png)

![Monitor detail, dark](docs/screenshots/monitor-detail-dark.png)

![Add a monitor, kind](docs/screenshots/add-monitor.png)

![Add a monitor, details](docs/screenshots/add-monitor-details.png)

![Add a monitor, review](docs/screenshots/add-monitor-review.png)

![Incidents](docs/screenshots/incidents.png)

![Alerts](docs/screenshots/alerts.png)

![Certificates](docs/screenshots/certificates.png)

![SLA calculator](docs/screenshots/sla.png)

![Public status](docs/screenshots/status.png)

![Public status, dark](docs/screenshots/status-dark.png)

![Sign in](docs/screenshots/login.png)

![Sign in, dark](docs/screenshots/login-dark.png)

![Add a monitor, kind, dark](docs/screenshots/add-monitor-dark.png)

![Incidents, dark](docs/screenshots/incidents-dark.png)

![Alerts, dark](docs/screenshots/alerts-dark.png)

![Certificates, dark](docs/screenshots/certificates-dark.png)

![SLA calculator, dark](docs/screenshots/sla-dark.png)

Live dashboard frames: [1](docs/screenshots/dashboard-live-1.png), [2](docs/screenshots/dashboard-live-2.png), [3](docs/screenshots/dashboard-live-3.png).

## What works now

- `pulseguard check` loads every kind from JSON through the check registry and runs it once. The result is a JSON array.
- HTTP status, keyword, defacement hash, TCP connect, DNS, TLS certificate expiry, and heartbeat grace.
- Keyword search uses BeautifulSoup on the page. `script` and `style` tags are removed before the search. The parser is the stdlib `html.parser`.
- Defacement matches that same visible text, then hashes the raw response bytes. The baseline is either the hash you type or the hash of the first successful response. Repeated mismatches stay at warning until `alert_after` (default 3), then become critical. The streak stays on the check instance. An open incident is stored in the database and is still open after a restart.
- TLS reads `notAfter` and alerts inside the 30, 14, 7, 3, and 1 day windows.
- A heartbeat target is an unguessable token. `pulseguard web` calls `record_ping` from `/ping/<token>` on the same objects the scheduler runs. The token is not copied into the result or the access log. `pulseguard serve` also stores the last ping time, so a restart does not forget it.
- `pulseguard check --db history.db` stores that run in SQLite. Without `--db`, the command only prints JSON.
- `pulseguard run --config monitors.json --db history.db --duration 30` schedules those checks and shuts the threads down when the duration ends. Omit `--duration` to run until interrupted. A full job queue defers work instead of growing. The SQLite file is opened on the writer thread.
- An incident opens after 3 consecutive failures and resolves after 1 consecutive success. It cannot close before it opens. `depends_on` suppresses a failing monitor when a dependency is already down. Opening and resolving each write one INFO log that names the monitor. A failure while the incident is already open does not log or mail again.
- When `PULSEGUARD_SMTP_HOST` is set, `pulseguard run` sends one email for the open and one for the resolve. An empty host sends nothing. `EmailNotifier.send_digest()` sends the queued lines as one message; `run` does not call it on a timer.
- `pulseguard run --evidence-dir DIR` writes a CSV, JSON, and text timeline when an incident opens. The deadline is 6 hours after the open. Rows older than 180 days are left out. The pack says it is not legal advice.
- `pulseguard export` writes stored results or the monitor catalog as CSV or JSON. `import-monitors` loads a catalog CSV. `pulseguard run` and `pulseguard web` still read the JSON file. `pulseguard serve` schedules the catalog table and reloads it about once a second, so an add, edit, pause, or delete applies without a restart.
- `pulseguard backup` writes a SQLite snapshot with a SHA-256 header, and can verify or restore it. That header is `PGDB`. Agent frames use a different header, `PGAG`.
- `pulseguard run --agent-listen 127.0.0.1:9700` starts a collector on a loopback or private IPv4 address. It needs `PULSEGUARD_AGENT_TOKEN`. Each connection sends one result. A bad frame closes that connection, and the collector keeps listening. Accepted results go through the same writer as local checks. `pulseguard push` sends one result and does not probe a URL. Close the client with a context manager; `__del__` only covers a socket that was left open.
- `pulseguard web --config monitors.json --db history.db` serves the status page on `127.0.0.1:8080` and runs the checks in the same process. The public page and `GET /api/status` read stored rows. They do not contact monitor targets, and they do not show catalog targets. `POST /api/monitors` requires `X-API-Key`. Admin pages use a session login and a CSRF token. The password is hashed with PBKDF2-HMAC-SHA256 for 100000 iterations.
- `pulseguard serve` creates `./data`, the SQLite file, and a secret file (mode 0600) on first run. It opens the browser unless `--no-browser` is set, and it refuses `0.0.0.0`. The signed-in pages cover monitors, incidents, certificates, alerts, SLA, agents, export, and backup. Run check now is an explicit POST. No other page fetches a monitor URL.
- `pulseguard console --db history.db` opens the desktop window. A loader thread queues snapshots. `after()` applies them, and widget calls raise if they are not on the main thread. The SLA form uses the same error-budget helper as the stored rollups.
- `ConsoleNotifier.send` and `EmailNotifier.send` are the notifier implementations.
- `pulseguard.ml.anomaly.detect` flags a latency sample that sits above a fixed multiple of the median absolute deviation. `detect_in_process` runs that score in a worker process. The series is data you already have. The function does not contact a host.
- MySQL is the same repository interface. Install it with `pip install 'pulseguard[mysql]'` and set `PULSEGUARD_MYSQL_*` in `.env`. The CLI itself opens SQLite.
- Private and localhost targets are allowed by default, because the monitor runs on your machine. `--deny-private` rejects localhost names and literal non-public IPs for an ad-hoc URL. The same switch exists on TCP, DNS, and TLS configs.

## What it does not do

It does not send the digest or purge old rows on a schedule. It does not scan hosts you did not configure. `run` and `web` on the sample file probe that file's local placeholders. The collector and the web server do not listen on a public address or on `0.0.0.0`. The public status page does not fetch a user-supplied URL. Tests that open a connection use `127.0.0.1`, `localhost` from the hosts file, a certificate generated in a temporary directory, a local SMTP listener, or a local agent collector. The MySQL tests use a scripted connection unless `PULSEGUARD_MYSQL_HOST` is set. The desktop test skips the Tk window when there is no display.

Redirects are not followed. Hostnames are not resolved during validation, so turning private targets off does not stop a public name from pointing at a private address later. That gap remains. The public status page does not fetch a user-supplied URL.

Operator input is bounded. Intervals are 30–86400 seconds, timeouts at most 120 seconds, retries 1–5, backoff 0–30 seconds, names 1–128 characters, and URLs at most 2048 characters. Keyword text is 1–200 characters, a body read is at most 1048576 bytes, `alert_after` is 1–20, a defacement hash is 64 hex characters, DNS expects 1–8 addresses, TCP and TLS ports are 1–65535, heartbeat tokens are 16–128 characters, and heartbeat grace is 30–604800 seconds. Stored messages are at most 2000 characters, a catalog target is at most 2048 characters, history pages are 1–1000 rows, and retention is 1–3650 days (the helper defaults to 180). The engine allows 1–32 workers, a job queue of 1–1000, and a per-host cap of 1–32. `failures_before_open` and `successes_before_resolve` are 1–20. A monitor may list up to 8 dependencies, and groups may nest 8 levels. An evidence pack keeps at most 1000 events, and its countdown is 6 hours. A digest keeps at most 100 lines. SMTP settings allow a host of 1–255 characters, a port of 1–65535, and 1–8 recipients. An agent frame payload is at most 8192 bytes, a collector handles at most 8 connections at once, and a frame read waits at most 5 seconds. The agent token is 16–128 characters. The web session key is 16–128 characters, the admin password is 8–128, the API key is 16–128, and a web request body is at most 16384 bytes. The status window polls every 200 milliseconds and the loader waits 1 second. The SLA form accepts 1–100000 samples. The web server uses 4 worker threads. The anomaly scorer accepts 3–100000 samples and repeats a timing run at most 100000 times. Those caps are validation limits, not performance figures.

## Run a check

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Terminal 1: local page. It prints the loopback URL it bound.
python tests/mock_servers.py

# Terminal 2, using that URL with /ok appended to the path:
pulseguard check --name demo --url http://127.0.0.1:<port>/ok
```

Exit code 0 means the check passed. Exit code 1 means the check ran and failed. Exit code 2 means the configuration was rejected. Results are a JSON array on stdout. Logs go to stderr, and to a rotating file if you pass `--log-dir`.

`config/monitors.example.json` shows one object of each kind. The targets are placeholders on your own machine. The tests construct that file and do not run it. Running it yourself will probe those local addresses.

```bash
pulseguard check --config config/monitors.example.json

# Schedule the same file for a few seconds. This probes the placeholder targets.
pulseguard run --config config/monitors.example.json --db history.db --duration 5
```

Store one local run, then export it and take a backup:

```bash
pulseguard check --name demo --url http://127.0.0.1:<port>/ok --db history.db
pulseguard export --db history.db --format json --output results.json
pulseguard backup --db history.db --output history.pgdb
pulseguard backup --verify history.pgdb
```

Copy `.env.example` to `.env` if you want `PULSEGUARD_LOG_LEVEL` or SMTP delivery. Leave `PULSEGUARD_SMTP_HOST` empty to keep mail off. Do not commit `.env`.

This also probes the placeholder targets, and writes a pack only if an incident opens:

```bash
pulseguard run --config config/monitors.example.json --db history.db --duration 5 --evidence-dir evidence
```

The token below is a placeholder. Put a real one in `.env`, not in the command line. `push` does not probe a URL. `run` still probes the file's targets.

```bash
export PULSEGUARD_AGENT_TOKEN=example-agent-token1
pulseguard run --config config/monitors.example.json --db history.db --duration 30 --agent-listen 127.0.0.1:9700
pulseguard push --port 9700 --name web --message "HTTP 200"
```

## Checks

```bash
ruff check src tests benchmarks
ruff format --check src tests benchmarks
mypy
pytest
```

`pytest` prints coverage for `pulseguard`. The figure in that output is the one that command measured.

## Layout

```mermaid
flowchart LR
  scheduler[Scheduler] --> jobs[Bounded job queue]
  jobs --> workers[Thread pool]
  workers --> results[Result queue]
  results --> writer[SQLite writer]
  writer --> incidents[Incidents and notifiers]
  agents[Agent frames] --> results
  writer --> page[Status page]
  writer --> console[Desktop console]
```

`docs/architecture.md`, `docs/concurrency-design.md`, `docs/security.md`, `docs/design-system.md`, and `docs/syllabus-coverage.md` expand that picture. `CHANGELOG.md` holds the v1.2.2 tag notes.

## Container

```bash
docker compose up --build
```

The image runs as the user `pulseguard`. Compose publishes the status page on `127.0.0.1:8080`. The process listens on the container's own address. The compose file sets placeholder values for the session key, the admin password, and the API key so a clean checkout can start. Replace them before anyone else can open the port. The sample monitor file still probes its local placeholders. The desktop window is not in the image.

## Benchmark

`benchmarks/bench_threads_vs_processes.py` times the anomaly scorer on a series built in the script. It does not open a socket. This is the output from the run that accompanied the 1.0.0 notes:

```
tasks=4
workers=2
repeats=20000
samples=33
sequential_s=0.746322
threads_s=0.741287
processes_s=0.433625
```

Run the script again for a new measurement. Those seconds belong to that run.

## Security and what is still ahead

Admin writes need a session and a CSRF token, or `X-API-Key` for `POST /api/monitors`. The public page reads stored rows. Details are in `docs/security.md`.

Still ahead: a nightly verified backup, a retention purge, a timed digest, an importlib plugin loader, `/health`, and `/metrics`. Hostnames are still not resolved during validation.

