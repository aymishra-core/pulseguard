"""CSV for stored results and for bulk monitor import."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from typing import TextIO

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError
from pulseguard.storage.records import (
    MONITOR_COLUMNS,
    RESULT_COLUMNS,
    MonitorRecord,
    require_interval,
    require_kind,
    require_monitor_name,
    require_target,
)


def write_results_csv(results: Iterable[CheckResult], output: TextIO) -> int:
    """Write result rows and return how many were written."""
    writer = csv.DictWriter(output, fieldnames=RESULT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for result in results:
        status = "" if result.status_code is None else str(result.status_code)
        writer.writerow(
            {
                "monitor": result.monitor,
                "kind": result.kind,
                "ok": "1" if result.ok else "0",
                "status_code": status,
                "latency_ms": result.latency_ms,
                "message": result.message,
                "severity": str(result.severity),
                "checked_at": result.checked_at.isoformat(),
            }
        )
        count += 1
    return count


def write_monitors_csv(records: Iterable[MonitorRecord], output: TextIO) -> int:
    """Write catalog rows and return how many were written."""
    writer = csv.DictWriter(output, fieldnames=MONITOR_COLUMNS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for record in records:
        writer.writerow(
            {
                "name": record.name,
                "kind": record.kind,
                "target": record.target,
                "interval_seconds": record.interval_seconds,
                "enabled": "1" if record.enabled else "0",
            }
        )
        count += 1
    return count


def read_monitors_csv(source: TextIO) -> list[MonitorRecord]:
    """Parse a monitor catalog. The header must match ``MONITOR_COLUMNS``."""
    reader = csv.DictReader(source)
    if reader.fieldnames is None:
        raise ConfigValidationError("monitor csv needs a header")
    if tuple(reader.fieldnames) != MONITOR_COLUMNS:
        expected = ", ".join(MONITOR_COLUMNS)
        raise ConfigValidationError(f"monitor csv header must be {expected}")
    records: list[MonitorRecord] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        record = _record_from_csv(row, line)
        if record.name in seen:
            raise ConfigValidationError(f"monitor csv line {line}: duplicate name {record.name!r}")
        seen.add(record.name)
        records.append(record)
    if not records:
        raise ConfigValidationError("monitor csv has no rows")
    return records


def _record_from_csv(row: dict[str, str | None], line: int) -> MonitorRecord:
    """Build one catalog row, prefixing errors with the file line."""
    try:
        name = require_monitor_name(_cell(row, "name"))
        kind = require_kind(_cell(row, "kind"))
        target = require_target(_cell(row, "target"))
        interval_text = _cell(row, "interval_seconds")
        if not interval_text.isdigit():
            raise ConfigValidationError("interval_seconds must be an integer")
        enabled_text = _cell(row, "enabled")
        if enabled_text == "1":
            enabled = True
        elif enabled_text == "0":
            enabled = False
        else:
            raise ConfigValidationError("enabled must be 0 or 1")
        return MonitorRecord(
            name=name,
            kind=kind,
            target=target,
            interval_seconds=require_interval(int(interval_text)),
            enabled=enabled,
        )
    except ConfigValidationError as exc:
        raise ConfigValidationError(f"monitor csv line {line}: {exc}") from exc


def _cell(row: dict[str, str | None], column: str) -> str:
    """Return one cell, rejecting a missing value."""
    value = row.get(column)
    if value is None:
        raise ConfigValidationError(f"{column} is missing")
    return value
