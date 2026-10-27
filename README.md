# PulseGuard

PulseGuard is a self-hosted monitor for websites, APIs, TCP ports, TLS certificate expiry, and scheduled-job heartbeats. This repository is a student project built to production-grade standards. It is not a replacement for Uptime Kuma or any other monitoring product.

Phase 7 serves a status page, an admin login, a JSON API, and a desktop window. Nothing runs a nightly backup, a retention purge, or a timed digest yet.

## What works now

- `pulseguard check` loads every kind from JSON through the check registry and runs it once. The result is a JSON array.
- HTTP status, keyword, defacement hash, TCP connect, DNS, TLS certificate expiry, and heartbeat grace.
- Keyword search uses BeautifulSoup on the page. `script` and `style` tags are removed before the search. The parser is the stdlib `html.parser`.
- Defacement matches that same visible text, then hashes the raw response bytes. Repeated mismatches stay at warning until `alert_after` (default 3), then become critical. The streak stays on the check instance. Incident state is separate and also stays in the process.
- TLS reads `notAfter` and alerts inside the 30, 14, 7, 3, and 1 day windows.
- A heartbeat target is an unguessable token. `pulseguard web` calls `record_ping` from `/ping/<token>` on the same objects the scheduler runs. The token is not copied into the result or the access log. A ping is remembered until that process exits.
- `pulseguard check --db history.db` stores that run in SQLite. Without `--db`, the command only prints JSON.
- `pulseguard run --config monitors.json --db history.db --duration 30` schedules those checks and shuts the threads down when the duration ends. Omit `--duration` to run until interrupted. A full job queue defers work instead of growing. The SQLite file is opened on the writer thread.
- An incident opens after 3 consecutive failures and resolves after 1 consecutive success. It cannot close before it opens. `depends_on` suppresses a failing monitor when a dependency is already down. Opening and resolving each write one INFO log that names the monitor. A failure while the incident is already open does not log or mail again.
- When `PULSEGUARD_SMTP_HOST` is set, `pulseguard run` sends one email for the open and one for the resolve. An empty host sends nothing. `EmailNotifier.send_digest()` sends the queued lines as one message; `run` does not call it on a timer.
- `pulseguard run --evidence-dir DIR` writes a CSV, JSON, and text timeline when an incident opens. The deadline is 6 hours after the open. Rows older than 180 days are left out. The pack says it is not legal advice.
- `pulseguard export` writes stored results or the monitor catalog as CSV or JSON. `import-monitors` loads a catalog CSV. `run` reads the JSON file, not the catalog table.
- `pulseguard backup` writes a SQLite snapshot with a SHA-256 header, and can verify or restore it. That header is `PGDB`. Agent frames use a different header, `PGAG`.
- `pulseguard run --agent-listen 127.0.0.1:9700` starts a collector on a loopback or private IPv4 address. It needs `PULSEGUARD_AGENT_TOKEN`. Each connection sends one result. A bad frame closes that connection, and the collector keeps listening. Accepted results go through the same writer as local checks. `pulseguard push` sends one result and does not probe a URL. Close the client with a context manager; `__del__` only covers a socket that was left open.
- `pulseguard web --config monitors.json --db history.db` serves the status page on `127.0.0.1:8080` and runs the checks in the same process. The public page and `GET /api/status` read stored rows. They do not contact monitor targets, and they do not show catalog targets. `POST /api/monitors` requires `X-API-Key`. Admin pages use a session login and a CSRF token. The password is hashed with PBKDF2-HMAC-SHA256 for 100000 iterations.
- `pulseguard console --db history.db` opens the desktop window. A loader thread queues snapshots. `after()` applies them, and widget calls raise if they are not on the main thread. The SLA form uses the same error-budget helper as the stored rollups.
- `ConsoleNotifier.send` and `EmailNotifier.send` are the notifier implementations.
- MySQL is the same repository interface. Install it with `pip install 'pulseguard[mysql]'` and set `PULSEGUARD_MYSQL_*` in `.env`. The CLI itself opens SQLite.
- Private and localhost targets are allowed by default, because the monitor runs on your machine. `--deny-private` rejects localhost names and literal non-public IPs for an ad-hoc URL. The same switch exists on TCP, DNS, and TLS configs.

## What it does not do

It does not send the digest or purge old rows on a schedule. It does not scan hosts you did not configure. `run` and `web` on the sample file probe that file's local placeholders. The collector and the web server do not listen on a public address or on `0.0.0.0`. The public status page does not fetch a user-supplied URL. Tests that open a connection use `127.0.0.1`, `localhost` from the hosts file, a certificate generated in a temporary directory, a local SMTP listener, or a local agent collector. The MySQL tests use a scripted connection unless `PULSEGUARD_MYSQL_HOST` is set. The desktop test skips the Tk window when there is no display.

Redirects are not followed. Hostnames are not resolved during validation, so turning private targets off does not stop a public name from pointing at a private address later. That gap stays until a later phase, and the public status page must never fetch a user-supplied URL.

Operator input is bounded. Intervals are 30–86400 seconds, timeouts at most 120 seconds, retries 1–5, backoff 0–30 seconds, names 1–128 characters, and URLs at most 2048 characters. Keyword text is 1–200 characters, a body read is at most 1048576 bytes, `alert_after` is 1–20, a defacement hash is 64 hex characters, DNS expects 1–8 addresses, TCP and TLS ports are 1–65535, heartbeat tokens are 16–128 characters, and heartbeat grace is 30–604800 seconds. Stored messages are at most 2000 characters, a catalog target is at most 2048 characters, history pages are 1–1000 rows, and retention is 1–3650 days (the helper defaults to 180). The engine allows 1–32 workers, a job queue of 1–1000, and a per-host cap of 1–32. `failures_before_open` and `successes_before_resolve` are 1–20. A monitor may list up to 8 dependencies, and groups may nest 8 levels. An evidence pack keeps at most 1000 events, and its countdown is 6 hours. A digest keeps at most 100 lines. SMTP settings allow a host of 1–255 characters, a port of 1–65535, and 1–8 recipients. An agent frame payload is at most 8192 bytes, a collector handles at most 8 connections at once, and a frame read waits at most 5 seconds. The agent token is 16–128 characters. The web session key is 16–128 characters, the admin password is 8–128, the API key is 16–128, and a web request body is at most 16384 bytes. The status window polls every 200 milliseconds and the loader waits 1 second. The SLA form accepts 1–100000 samples. The web server uses 4 worker threads. Those caps are validation limits, not performance figures. There are no benchmark numbers in this repository yet.

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
ruff check src tests
ruff format --check src tests
mypy
pytest
```

`pytest` prints coverage for `pulseguard`. The figure in that output is the one this revision actually measured.
