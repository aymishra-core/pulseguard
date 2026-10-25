# PulseGuard

PulseGuard is a self-hosted monitor for websites, APIs, TCP ports, TLS certificate expiry, and scheduled-job heartbeats. This repository is a student project built to production-grade standards. It is not a replacement for Uptime Kuma or any other monitoring product.

Phase 1 is the foundation: the exception hierarchy, logging, the check registry, the HTTP check, and a one-shot CLI. The scheduler, database, alerts, agent, web status page, and desktop console are not built yet.

## What works now

- `HttpCheck` requests a URL you configure and compares the status code.
- Check classes register themselves by a `kind` string. `http` loads from JSON.
- `pulseguard check` runs those checks once and prints a JSON array.
- Private and localhost targets are allowed by default, because the monitor runs on your machine. `--deny-private` rejects localhost names and literal non-public IPs.

## What it does not do

It does not schedule checks, store history, send email, or expose a status page. It does not scan hosts you did not configure. Tests and the sample config talk only to `127.0.0.1` or to a hostname under `.invalid`, which does not resolve.

Redirects are not followed. Hostnames are not resolved during validation, so turning private targets off does not stop a public name from pointing at a private address later. That gap stays until a later phase, and the public status page must never fetch a user-supplied URL.

Operator input is bounded (interval, timeout, retries, URL length). Those caps are constants in the check package. They are validation limits, not performance figures. There are no benchmark numbers in this repository yet.

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

`config/monitors.example.json` is a shape example. Its target is a placeholder on your own loopback address. The tests construct that check and do not connect to it.

```bash
pulseguard check --config config/monitors.example.json
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
