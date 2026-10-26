"""Built-in checks. Importing this package registers them with ``CheckMeta``."""

from pulseguard.checks.base import BaseCheck, CheckMeta, build_check
from pulseguard.checks.defacement import DefacementCheck
from pulseguard.checks.dns_check import DnsCheck
from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.http_check import HttpCheck
from pulseguard.checks.keyword_check import KeywordCheck
from pulseguard.checks.mixins import AlertThresholdMixin, SerializableMixin
from pulseguard.checks.models import CheckResult, IncidentLog, LatencyStats, Severity
from pulseguard.checks.tcp_check import TcpCheck
from pulseguard.checks.tls_check import TlsExpiryCheck

__all__ = [
    "AlertThresholdMixin",
    "BaseCheck",
    "CheckMeta",
    "CheckResult",
    "DefacementCheck",
    "DnsCheck",
    "HeartbeatCheck",
    "HttpCheck",
    "IncidentLog",
    "KeywordCheck",
    "LatencyStats",
    "SerializableMixin",
    "Severity",
    "TcpCheck",
    "TlsExpiryCheck",
    "build_check",
]
