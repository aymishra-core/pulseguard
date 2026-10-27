-- SQLite schema. Version 2 adds incidents, alerts, users, settings, and pings.
-- CREATE IF NOT EXISTS keeps a re-run safe. A version-1 file picks up the new tables.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor TEXT NOT NULL,
    kind TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
    message TEXT NOT NULL,
    severity TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status_code INTEGER
);

CREATE INDEX IF NOT EXISTS idx_results_monitor_time
    ON results (monitor, checked_at);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    reason TEXT NOT NULL,
    cause TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    monitor TEXT NOT NULL,
    kind TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    error_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    salt_hex TEXT NOT NULL,
    digest_hex TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    setting_key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_options (
    name TEXT PRIMARY KEY,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeat_pings (
    monitor TEXT PRIMARY KEY,
    last_ping TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitors (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
);
