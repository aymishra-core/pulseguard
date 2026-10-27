"""Stored status rows for the public page and the desktop console.

These rows are read from the database. Nothing here contacts a monitor target.
"""

from __future__ import annotations

from dataclasses import dataclass

from pulseguard.checks.models import CheckResult
from pulseguard.storage.base import Repository
from pulseguard.storage.records import require_monitor_name


@dataclass(frozen=True)
class StatusRow:
    """One public monitor line. The target and any token are left out."""

    name: str
    kind: str
    ok: bool | None
    severity: str
    message: str
    checked_at: str

    def as_public(self) -> dict[str, object]:
        """Return the JSON fields a visitor may see."""
        return {
            "name": self.name,
            "kind": self.kind,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
            "checked_at": self.checked_at,
        }


def latest_status(repository: Repository, *, monitor: str | None = None) -> list[StatusRow]:
    """Return the newest stored result for each monitor, by name.

    Catalog rows with no results are included so the page can show them.
    ``monitor`` filters by name. It is not fetched.
    """
    chosen = None if monitor is None else require_monitor_name(monitor)
    latest: dict[str, CheckResult] = {}
    for result in repository.iter_results(None):
        latest[result.monitor] = result
    rows: dict[str, StatusRow] = {}
    for record in repository.monitors():
        stored: CheckResult | None = latest.pop(record.name) if record.name in latest else None
        rows[record.name] = _from_parts(record.name, record.kind, stored)
    for name, result in latest.items():
        rows[name] = _from_parts(name, result.kind, result)
    ordered = [rows[name] for name in sorted(rows)]
    if chosen is None:
        return ordered
    return [row for row in ordered if row.name == chosen]


def _from_parts(name: str, kind: str, result: CheckResult | None) -> StatusRow:
    """Build a public row. A missing result stays unknown."""
    if result is None:
        return StatusRow(name, kind, None, "", "no results yet", "")
    return StatusRow(
        name,
        kind,
        result.ok,
        str(result.severity),
        result.message,
        result.checked_at.isoformat(),
    )
