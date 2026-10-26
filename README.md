# PulseGuard

PulseGuard is a self-hosted monitor for websites, APIs, TCP ports, TLS certificate expiry, and scheduled-job heartbeats. This repository is a student project built to production-grade standards. It is not a replacement for Uptime Kuma or any other monitoring product.

Phase 5 logs each incident open and resolve at INFO, sends one email for each of those events, searches visible HTML text, and writes an evidence pack with a 6-hour countdown. The agent, the web status page, and the desktop console are not built yet. Nothing runs a nightly backup, a retention purge, or a timed digest yet.

## What works now

- `pulseguard check` loads every kind from JSON through the check registry and runs it once. The result is a JSON array.
- HTTP status, keyword, defacement hash, TCP connect, DNS, TLS certificate expiry, and heartbeat grace.
- Keyword search uses BeautifulSoup on the page. `script` and `style` tags are removed before the search. The parser is the stdlib `html.parser`.
- Defacement matches that same visible text, then hashes the raw response bytes. Repeated mismatches stay at warning until `alert_after` (default 3), then become critical. The streak stays on the check instance. Incident state is separate and also stays in the process.
- TLS reads `notAfter` and alerts inside the 30, 14, 7, 3, and 1 day windows.
- A heartbeat target is an unguessable token. `record_ping` is what the later ping route will call. The token is not copied into the result.
- `pulseguard check --db history.db` stores that run in SQLite. Without `--db`, the command only prints JSON.
- `pulseguard run --config monitors.json --db history.db --duration 30` schedules those checks and shuts the threads down when the duration ends. Omit `--duration` to run until interrupted. A full job queue defers work instead of growing. The SQLite file is opened on the writer thread.
- An incident opens after 3 consecutive failures and resolves after 1 consecutive success. It cannot close before it opens. `depends_on` suppresses a failing monitor when a dependency is already down. Opening and resolving each write one INFO log that names the monitor. A failure while the incident is already open does not log or mail again.
- When `PULSEGUARD_SMTP_HOST` is set, `pulseguard run` sends one email for the open and one for the resolve. An empty host sends nothing. `EmailNotifier.send_digest()` sends the queued lines as one message; `run` does not call it on a timer.
- `pulseguard run --evidence-dir DIR` writes a CSV, JSON, and text timeline when an incident opens. The deadline is 6 hours after the open. Rows older than 180 days are left out. The pack says it is not legal advice.
- `pulseguard export` writes stored results or the monitor catalog as CSV or JSON. `import-monitors` loads a catalog CSV. `run` reads the JSON file, not the catalog table.
- `pulseguard backup` writes a SQLite snapshot with a SHA-256 header, and can verify or restore it.
- `ConsoleNotifier.send` and `EmailNotifier.send` are the notifier implementations.
- MySQL is the same repository interface. Install it with `pip install 'pulseguard[mysql]'` and set `PULSEGUARD_MYSQL_*` in `.env`. The CLI itself opens SQLite.
- Private and localhost targets are allowed by default, because the monitor runs on your machine. `--deny-private` rejects localhost names and literal non-public IPs for an ad-hoc URL. The same switch exists on TCP, DNS, and TLS configs.

## What it does not do

It does not expose a status page, and it does not send the digest or purge old rows on a schedule. It does not scan hosts you did not configure. `run` on the sample file probes that file's local placeholders. Tests that open a connection use `127.0.0.1`, `localhost` from the hosts file, a certificate generated in a temporary directory, or a local SMTP listener. The MySQL tests use a scripted connection unless `PULSEGUARD_MYSQL_HOST` is set.

Redirects are not followed. Hostnames are not resolved during validation, so turning private targets off does not stop a public name from pointing at a private address later. That gap stays until a later phase, and the public status page must never fetch a user-supplied URL.

Operator input is bounded. Intervals are 30–86400 seconds, timeouts at most 120 seconds, retries 1–5, backoff 0–30 seconds, names 1–128 characters, and URLs at most 2048 characters. Keyword text is 1–200 characters, a body read is at most 1048576 bytes, `alert_after` is 1–20, a defacement hash is 64 hex characters, DNS expects 1–8 addresses, TCP and TLS ports are 1–65535, heartbeat tokens are 16–128 characters, and heartbeat grace is 30–604800 seconds. Stored messages are at most 2000 characters, a catalog target is at most 2048 characters, history pages are 1–1000 rows, and retention is 1–3650 days (the helper defaults to 180). The engine allows 1–32 workers, a job queue of 1–1000, and a per-host cap of 1–32. `failures_before_open` and `successes_before_resolve` are 1–20. A monitor may list up to 8 dependencies, and groups may nest 8 levels. An evidence pack keeps at most 1000 events, and its countdown is 6 hours. A digest keeps at most 100 lines. SMTP settings allow a host of 1–255 characters, a port of 1–65535, and 1–8 recipients. Those caps are validation limits, not performance figures. There are no benchmark numbers in this repository yet.

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

## Checks

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
```

`pytest` prints coverage for `pulseguard`. The figure in that output is the one this revision actually measured.
