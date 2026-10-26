-- SQLite schema version 1.
-- A later change adds a new version. This file stays the version-1 definition.

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

CREATE TABLE IF NOT EXISTS monitors (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
);
