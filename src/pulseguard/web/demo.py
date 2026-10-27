"""Demo monitors that point at the local lab. Nothing here opens a socket."""

from __future__ import annotations

import hashlib
from typing import Any

from pulseguard.checks.base import build_check
from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.storage.records import MonitorRecord

LAB_SITE = "http://127.0.0.1:8799/"
LAB_BODY = b"Acme Shop\n"
LAB_HASH = hashlib.sha256(LAB_BODY).hexdigest()


def load_demo(repository: Any) -> None:
    """Insert the six lab monitors. A second click updates the same names."""
    token = HeartbeatCheck.new_token()
    rows: tuple[tuple[str, str, str, dict[str, object]], ...] = (
        ("web", "http", LAB_SITE, {}),
        ("shop-keyword", "keyword", LAB_SITE, {"keyword": "Acme Shop"}),
        (
            "shop-defacement",
            "defacement",
            LAB_SITE,
            {"keyword": "Acme Shop", "expected_sha256": LAB_HASH},
        ),
        ("lab-port", "tcp", "127.0.0.1", {"port": 8798}),
        ("nightly-job", "heartbeat", token, {"grace_seconds": 60}),
        ("shop-depends", "http", LAB_SITE, {"depends_on": ["web"]}),
    )
    for name, kind, target, options in rows:
        spec: dict[str, object] = {"kind": kind, "name": name, "target": target, "interval": 30}
        for key, value in options.items():
            if key != "depends_on":
                spec[key] = value
        check = build_check(spec)
        repository.save_monitor(
            MonitorRecord(
                name=check.name,
                kind=check.kind,
                target=check.target,
                interval_seconds=int(check.interval),
                enabled=True,
            )
        )
        repository.save_options(name, options)
