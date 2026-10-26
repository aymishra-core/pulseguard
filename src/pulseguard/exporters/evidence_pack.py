"""Incident evidence pack: a timeline, a 6-hour countdown, and 180-day retention.

The countdown and the retention window are policy limits, not measurements.
This pack is an operator worksheet. It is not legal advice and it does not
claim to satisfy CERT-In or DPDP directions.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import AlertError
from pulseguard.storage.records import DEFAULT_RETENTION_DAYS, require_aware

# Policy limits, not measurements.
EVIDENCE_COUNTDOWN = timedelta(hours=6)
MAX_PACK_EVENTS = 1000
NOT_LEGAL_ADVICE = (
    "This pack is an operator worksheet. It is not legal advice and it does not "
    "claim to satisfy CERT-In or DPDP directions."
)


def write_evidence_pack(
    directory: Path,
    *,
    monitor: str,
    opened_at: datetime,
    results: list[CheckResult],
    now: datetime,
) -> Path:
    """Write CSV, JSON, and text timelines for one open incident.

    Rows older than the retention window are left out. At most
    ``MAX_PACK_EVENTS`` of the newest remaining rows are written.
    """
    opened = require_aware(opened_at, "opened_at")
    moment = require_aware(now, "now")
    cutoff = moment - timedelta(days=DEFAULT_RETENTION_DAYS)
    kept = [row for row in results if row.checked_at >= cutoff]
    kept.sort(key=lambda row: row.checked_at)
    if len(kept) > MAX_PACK_EVENTS:
        kept = kept[-MAX_PACK_EVENTS:]
    deadline = opened + EVIDENCE_COUNTDOWN
    remaining_seconds = int((deadline - moment).total_seconds())
    retain_until = moment + timedelta(days=DEFAULT_RETENTION_DAYS)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AlertError("evidence pack could not be written") from exc
    stem = directory / _file_stem(monitor)
    payload = {
        "monitor": monitor,
        "opened_at": opened.isoformat(),
        "deadline": deadline.isoformat(),
        "remaining_seconds": remaining_seconds,
        "retain_until": retain_until.isoformat(),
        "retention_days": DEFAULT_RETENTION_DAYS,
        "not_legal_advice": NOT_LEGAL_ADVICE,
        "events": [row.as_dict() for row in kept],
    }
    try:
        _write_csv(Path(f"{stem}-timeline.csv"), kept)
        Path(f"{stem}-timeline.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        Path(f"{stem}-timeline.txt").write_text(_text(payload, kept), encoding="utf-8")
    except OSError as exc:
        raise AlertError("evidence pack could not be written") from exc
    return directory


def _write_csv(path: Path, rows: list[CheckResult]) -> None:
    """Write the timeline columns. The file has no comment row."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("checked_at", "ok", "severity", "message"))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "checked_at": row.checked_at.isoformat(),
                    "ok": str(row.ok).lower(),
                    "severity": str(row.severity),
                    "message": row.message,
                }
            )


def _text(payload: dict[str, object], rows: list[CheckResult]) -> str:
    """Render the same facts as a short text timeline."""
    lines = [
        NOT_LEGAL_ADVICE,
        f"monitor={payload['monitor']}",
        f"opened_at={payload['opened_at']}",
        f"deadline={payload['deadline']}",
        f"remaining_seconds={payload['remaining_seconds']}",
        f"retain_until={payload['retain_until']}",
        f"retention_days={payload['retention_days']}",
        "timeline:",
    ]
    for row in rows:
        lines.append(
            f"{row.checked_at.isoformat()} ok={str(row.ok).lower()} "
            f"severity={row.severity} {row.message}"
        )
    return "\n".join(lines) + "\n"


def _file_stem(monitor: str) -> str:
    """Turn a monitor name into a single path segment."""
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in monitor)
    cleaned = cleaned.strip("._") or "monitor"
    return cleaned[:80]
