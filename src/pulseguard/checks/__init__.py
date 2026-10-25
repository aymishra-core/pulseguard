"""Built-in checks. Importing this package registers them with ``CheckMeta``."""

from pulseguard.checks.base import BaseCheck, CheckMeta, build_check
from pulseguard.checks.http_check import HttpCheck
from pulseguard.checks.models import CheckResult, IncidentLog, LatencyStats, Severity

__all__ = [
    "BaseCheck",
    "CheckMeta",
    "CheckResult",
    "HttpCheck",
    "IncidentLog",
    "LatencyStats",
    "Severity",
    "build_check",
]
