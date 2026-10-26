# PulseGuard

PulseGuard is a self-hosted monitor for websites, APIs, TCP ports, TLS certificate expiry, and scheduled-job heartbeats. This repository is a student project built to production-grade standards. It is not a replacement for Uptime Kuma or any other monitoring product.

Phase 3 stores check history in SQLite and can export it or take a checksummed backup. MySQL implements the same repository port. The scheduler, email, agent, web status page, and desktop console are not built yet. Nothing runs a nightly backup or a retention purge yet.

## What works now

- `pulseguard check` loads every kind from JSON through the check registry and runs it once. The result is a JSON array.
- HTTP status, raw-text keyword, raw-body defacement hash, TCP connect, DNS, TLS certificate expiry, and heartbeat grace.
- Keyword search reads the response bytes. It does not parse HTML yet.
- Defacement hashes those same raw bytes. Repeated mismatches stay at warning until `alert_after` (default 3), then become critical. The streak lives in the process until the incident engine stores it.
- TLS reads `notAfter` and alerts inside the 30, 14, 7, 3, and 1 day windows.
- A heartbeat target is an unguessable token. `record_ping` is what the later ping route will call. The token is not copied into the result.
- `pulseguard check --db history.db` stores that run in SQLite. Without `--db`, the command only prints JSON.
- `pulseguard export` writes stored results or the monitor catalog as CSV or JSON. `import-monitors` loads a catalog CSV. The scheduler does not read the catalog yet.
- `pulseguard backup` writes a SQLite snapshot with a SHA-256 header, and can verify or restore it.
- `ConsoleNotifier.send` is the notifier extension point. Email is later.
- MySQL is the same repository interface. Install it with `pip install 'pulseguard[mysql]'` and set `PULSEGUARD_MYSQL_*` in `.env`. The CLI itself opens SQLite.
- Private and localhost targets are allowed by default, because the monitor runs on your machine. `--deny-private` rejects localhost names and literal non-public IPs for an ad-hoc URL. The same switch exists on TCP, DNS, and TLS configs.

## What it does not do

It does not schedule checks, send email, or expose a status page. It does not scan hosts you did not configure. History is stored only when a command is given a SQLite file. Tests that open a connection use `127.0.0.1`, `localhost` from the hosts file, or a certificate generated in a temporary directory. The MySQL tests use a scripted connection unless `PULSEGUARD_MYSQL_HOST` is set.

Redirects are not followed. Hostnames are not resolved during validation, so turning private targets off does not stop a public name from pointing at a private address later. That gap stays until a later phase, and the public status page must never fetch a user-supplied URL.

Operator input is bounded. Intervals are 30–86400 seconds, timeouts at most 120 seconds, retries 1–5, backoff 0–30 seconds, names 1–128 characters, and URLs at most 2048 characters. Keyword text is 1–200 characters, a body read is at most 1048576 bytes, `alert_after` is 1–20, a defacement hash is 64 hex characters, DNS expects 1–8 addresses, TCP and TLS ports are 1–65535, heartbeat tokens are 16–128 characters, and heartbeat grace is 30–604800 seconds. Stored messages are at most 2000 characters, a catalog target is at most 2048 characters, history pages are 1–1000 rows, and retention is 1–3650 days (the helper defaults to 180). Those caps are validation limits, not performance figures. There are no benchmark numbers in this repository yet.

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
```

Store one local run, then export it and take a backup:

```bash
pulseguard check --name demo --url http://127.0.0.1:<port>/ok --db history.db
pulseguard export --db history.db --format json --output results.json
pulseguard backup --db history.db --output history.pgdb
pulseguard backup --verify history.pgdb
```

Copy `.env.example` to `.env` if you want `PULSEGUARD_LOG_LEVEL`. Do not commit `.env`.

## Checks

```bash
ruff check src tests
ruff format --check src tests
mypy
pytest
```

`pytest` prints coverage for `pulseguard`. The figure in that output is the one this revision actually measured.
