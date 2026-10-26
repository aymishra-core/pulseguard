"""JSON export of stored results and the monitor catalog."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TextIO

from pulseguard.checks.models import CheckResult
from pulseguard.storage.records import MonitorRecord


def write_results_json(results: Iterable[CheckResult], output: TextIO) -> int:
    """Write a JSON array of results and return how many were written."""
    payload = [result.as_dict() for result in results]
    json.dump(payload, output, indent=2)
    output.write("\n")
    return len(payload)


def write_monitors_json(records: Iterable[MonitorRecord], output: TextIO) -> int:
    """Write a JSON array of catalog rows and return how many were written."""
    payload = [record.as_dict() for record in records]
    json.dump(payload, output, indent=2)
    output.write("\n")
    return len(payload)
