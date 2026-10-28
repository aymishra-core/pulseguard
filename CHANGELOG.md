# Changelog

## 1.3.9 - 2026-10-29

Every Agents row can be deleted, including a last-push name from before Add agent. Delete removes that record and the last-push time, and asks whether to keep or delete the monitor and its history. Open monitor appears only after a monitor with that name exists. Startup adopts those older names into the agent list. The monitor is still created on the next push.

## 1.3.8 - 2026-10-29

An accepted `pulseguard push` creates a passive monitor of kind agent. The dashboard, sidebar, monitors list, and detail page show it with the other monitors. Each push is a normal check result, so history, the 24 h bar, uptime, SLA, and the live check log include it. Three failures open an incident and send the email alert. One success resolves it, and Depends on still suppresses a failure. Silence longer than three expected intervals records "no report from agent" and opens an incident. The Agents page links to the monitor. Deleting an agent asks whether to keep that history.

## 1.3.7 - 2026-10-29

The Agents page asks for a name, generates a token, and shows the `pulseguard push` command that machine should run. The token stays masked until Reveal. Each agent shows Never seen, Seen N s ago, or Stale, plus its last result. Send a test result uses the collector. Rejected attempts stay on the page with the reason.

## 1.3.6 - 2026-10-29

A live refresh paints the status ring, the failing banner, the hero buttons, the subline, the filter counts, and the monitor pills from the same payload as the first page. The banner and the top-bar chip update on every signed-in page. A reload and a live update draw the same nodes.

## 1.3.5 - 2026-10-29

The live check log opens with the last 20 completed checks and prepends each new one from the dashboard payload: time, monitor, UP or DOWN, the check message, and the latency. It keeps 50 lines. The badge says streaming only while a refresh has succeeded inside two poll intervals, reconnecting after that, and paused while the tab is hidden.

## 1.3.4 - 2026-10-29

The add and edit form asks for a target the way the check uses it. HTTP, keyword, and defacement want a full URL. TCP, TLS, and DNS want a host, and a pasted URL offers that host (and the port, for TCP and TLS) in one click. The port field is only for TCP and TLS: TLS starts at 443, and TCP requires a port from 1 to 65535. Depends on is a single-line dropdown that adds removable chips, hides the monitor being edited and any choice that would cycle, and stops at 8.

## 1.3.3 - 2026-10-29

The dashboard reads one snapshot. The hero, the tiles, the top-bar chip, the sidebar badges, Needs attention, the monitor cards, the activity feed, and the live log all come from that object, and `/api/dashboard` returns it. A poll applies a response only when it is newer, pauses while the tab is hidden, and says the connection is lost after 15 seconds without a success. The PulseGuard mark links to the dashboard.

## 1.3.2 - 2026-10-28

A failing monitor is one click from the dashboard. The hero names the service state (nominal, degraded, outage, or awaiting the first sweep), the Needs attention list links each problem to its monitor and incident, and the sidebar, breadcrumbs, and command palette follow the same monitors.

## 1.3.1 - 2026-10-28

Deleting, pausing, or disabling a monitor closes its open incidents. Startup and a catalog reload close any that remain for a monitor that is gone or disabled, one log line each. The incident page can close an open incident. The dashboard hero, the top-bar chip, the banner, and the Incidents badge count only incidents whose monitor is still running.

## 1.3.0 - 2026-10-28

The console is the finished ui-pack, copied into the Jinja templates. The sidebar, top bar, incident banner, command palette, and page markup keep the pack's class names. Sample monitors, KPIs, incidents, certificates, and the preview strip are gone. The log reads `/api/activity`, Test this check posts `/monitors/probe`, and the chart uses stored latency. A grey slice is no data, not downtime. Times render in the viewer's zone. `ui-pack/` stays untracked.

## 1.2.2 - 2026-10-28

Links follow one rule. A name inherits the text colour at weight 600 and shows the accent underline only on hover or keyboard focus. A link in a sentence uses the accent colour with an underline offset. The hero ring is red when an incident is open or a monitor is down, amber for a gap or a warning, grey when every monitor is paused, and green only when every monitor is up.

- The six summary tiles share one row and the same height. Each sparkline sits on the same baseline.
- The activity feed is a timeline: a state dot, one line of text, and a relative time that stays on that line.

## 1.2.1 - 2026-10-28

Visible times use one server helper and one browser helper. A person sees "Today 11:05", "yesterday 7:28 pm", "12 s ago", or "21h 3m", with the exact local time on the tooltip. Machine-readable values stay on `<time datetime>`. Latency is a whole number of milliseconds.

- The dashboard hero shows a check, warning, or pause icon, and links the open incident with how long it has run.
- First steps is a slim progress card. It hides when the account, a monitor, alerts, and a heartbeat are done.
- The monitor chart has 1 h, 24 h, 7 d, and 30 d ranges, a hover readout, p50 and p95 guides, and failure ticks. KPI counts ease in, and the up, down, and uptime tiles draw a sparkline.
- Add monitor hides the later steps until Next. Tables with more than 12 rows offer Load more. The command palette can run every enabled check through `POST /monitors/run`.
- The logo is not a nav item. The sidebar collapses from an icon and remembers that choice. The theme control is an icon, including on login, setup, and the error pages.
- `home.html` is styled with the same system. No route renders it.

## 1.2.0 - 2026-10-27

The browser console uses one design system: semantic colours, a 4 px spacing scale, light and dark themes, and a sidebar shell. Times are shown in the browser's local zone. A gap longer than three intervals is stored as "no data", left out of uptime, and the first failure after it is not an outage trigger.

- `POST /monitors/probe` runs one unsaved check from the Add monitor form. It requires a login and CSRF, and it is rate limited.
- The public status page shows a 90-day uptime bar and incident titles and times. Unknown routes render a branded 404, 403, or 500 with a way back.
- The latency chart is still the stored polyline. It does not offer a 1 hour / 24 hour / 7 day / 30 day range, a hover crosshair, p50 and p95 lines, or failure ticks. KPI numbers do not count up. The command palette jumps to pages and monitors. It does not run every check at once.
- A nightly backup, a retention purge, a timed digest, an importlib plugin loader, `/health`, and `/metrics` are not included.

## 1.1.2 - 2026-10-27

- The test lab is a separate program. PulseGuard no longer creates demo monitors or mentions the lab.
- A defacement monitor can learn its SHA-256 from the first successful check, or the operator can type the hash.
- A new heartbeat monitor shows its ping URL and token with a copy button.

## 1.1.1 - 2026-10-27

`pulseguard serve` opens `/`. That page sends a new install to setup, a signed-out visitor to login, and a signed-in operator to the dashboard. The public status page stays at `/status`. `/admin` remains on `pulseguard web` only.

## 1.1.0 - 2026-10-27

`pulseguard serve` opens a browser UI so the operator does not need a terminal after the process starts.

- First-run setup, login, and a signed-in console for monitors, incidents, certificates, alerts, SLA, agents, export, and backup.
- The scheduler can run from the catalog and pick up an add, edit, pause, or delete without a restart. `run` and `web` still take a JSON file.
- Incidents, the alert log, heartbeat ping times, users, and settings are stored. An open incident survives a restart. Every alert is logged, including when SMTP is not configured.
- SMTP settings can be saved in the database. The password is write-only in the form, and `PULSEGUARD_SMTP_*` overrides it.
- `tools/lab.py` serves a local page and TCP port for the demo monitors.
- A nightly backup, a retention purge, a timed digest, an importlib plugin loader, `/health`, and `/metrics` are not included.

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
