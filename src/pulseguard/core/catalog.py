"""Build scheduler checks from the monitor catalog.

``run`` and ``web`` still load a JSON file. ``serve`` uses this module so a
catalog edit is what the scheduler runs next.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime

from pulseguard.checks.base import build_check
from pulseguard.checks.defacement import DefacementCheck
from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.core.scheduler import ScheduledCheck
from pulseguard.exceptions import ConfigValidationError
from pulseguard.logging_setup import LOGGER_NAME
from pulseguard.storage.base import Repository
from pulseguard.storage.records import MonitorRecord

LOGGER = logging.getLogger(f"{LOGGER_NAME}.core.catalog")


def checks_from_catalog(
    repository: Repository,
    *,
    on_ping: Callable[[str, datetime], None] | None = None,
    on_baseline: Callable[[str, str], None] | None = None,
    cache: dict[str, tuple[str, ScheduledCheck]] | None = None,
) -> tuple[tuple[ScheduledCheck, ...], dict[str, tuple[str, ...]]]:
    """Return enabled checks and their dependency edges.

    ``cache`` keeps the same check object while its spec is unchanged, so a
    defacement streak and a fresh ping survive the next reload.
    """
    options = repository.option_map() if hasattr(repository, "option_map") else {}
    kept = cache if cache is not None else {}
    checks: list[ScheduledCheck] = []
    edges: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for record in repository.monitors():
        if not record.enabled:
            continue
        extra = options.get(record.name, {})
        depends = extra.get("depends_on", ())
        if isinstance(depends, list):
            edges[record.name] = tuple(str(item) for item in depends if isinstance(item, str))
        spec = _spec(record, extra)
        signature = json.dumps(spec, sort_keys=True, default=str)
        previous = kept.get(record.name)
        if previous is not None and previous[0] == signature:
            check = previous[1]
        else:
            try:
                check = build_check(spec)
            except ConfigValidationError:
                LOGGER.warning("catalog monitor skipped name=%s", record.name)
                continue
            if isinstance(check, HeartbeatCheck):
                _restore_ping(repository, check)
                if on_ping is not None:
                    monitor_name = check.name

                    def _save(moment: datetime, name: str = monitor_name) -> None:
                        if on_ping is not None:
                            on_ping(name, moment)

                    check._persist_ping = _save
            if isinstance(check, DefacementCheck) and on_baseline is not None:
                learned_name = check.name

                def _save_baseline(digest: str, name: str = learned_name) -> None:
                    if on_baseline is not None:
                        on_baseline(name, digest)

                check._persist_baseline = _save_baseline
            kept[record.name] = (signature, check)
        seen.add(record.name)
        checks.append(check)
    for name in list(kept):
        if name not in seen:
            del kept[name]
    return tuple(checks), edges


def _spec(record: MonitorRecord, extra: dict[str, object]) -> dict[str, object]:
    """Merge the catalog row with stored options. ``depends_on`` is not a check field."""
    spec: dict[str, object] = {
        "kind": record.kind,
        "name": record.name,
        "target": record.target,
        "interval": record.interval_seconds,
    }
    for key, value in extra.items():
        if key != "depends_on":
            spec[key] = value
    return spec


def _restore_ping(repository: Repository, check: HeartbeatCheck) -> None:
    """Copy a stored ping onto the live check."""
    last = getattr(repository, "last_ping", None)
    if not callable(last):
        return
    stamp = last(check.name)
    if not isinstance(stamp, str) or not stamp:
        return
    check.restore_last_ping(datetime.fromisoformat(stamp))


def default_thresholds(repository: Repository) -> tuple[int, int]:
    """Read K and M from settings. Missing keys stay at 3 and 1."""
    from pulseguard.core.incidents import (
        DEFAULT_FAILURES_BEFORE_OPEN,
        DEFAULT_SUCCESSES_BEFORE_RESOLVE,
    )

    failures = DEFAULT_FAILURES_BEFORE_OPEN
    successes = DEFAULT_SUCCESSES_BEFORE_RESOLVE
    getter = getattr(repository, "get_setting", None)
    if not callable(getter):
        return failures, successes
    raw_failures = getter("failures_before_open")
    raw_successes = getter("successes_before_resolve")
    if isinstance(raw_failures, str) and raw_failures.isdigit():
        candidate = int(raw_failures)
        if 1 <= candidate <= 20:
            failures = candidate
    if isinstance(raw_successes, str) and raw_successes.isdigit():
        candidate = int(raw_successes)
        if 1 <= candidate <= 20:
            successes = candidate
    return failures, successes
