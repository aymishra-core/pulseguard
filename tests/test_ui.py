"""Signed-in pages. No test contacts a monitor URL."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.engine import Engine
from pulseguard.notifiers.settings_notifier import DatabaseNotifier
from pulseguard.storage import SqliteRepository
from pulseguard.web.app import create_app
from pulseguard.web.demo import load_demo

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


def test_pages_require_login_and_csrf(tmp_path: Path) -> None:
    """Anonymous visitors are sent to login, and a POST without CSRF is refused."""
    client, _database = _login(tmp_path)
    anonymous = _app(tmp_path / "other.db").test_client()
    assert anonymous.get("/monitors").status_code == 302
    assert anonymous.get("/api/dashboard").status_code == 401
    assert client.post("/demo", data={"name": "x"}).status_code == 400
    page = client.get("/dashboard")
    assert b"Add your first monitor" in page.data
    assert b"Load demo monitors" in page.data


def test_monitor_form_validates_and_a_pause_reaches_the_scheduler(tmp_path: Path) -> None:
    """A bad target is rejected. Pause and delete change the catalog within 5 seconds."""
    client, database = _login(tmp_path)
    token = _csrf(client.get("/monitors/new").get_data(as_text=True))
    rejected = client.post(
        "/monitors/new",
        data={
            "csrf_token": token,
            "kind": "http",
            "name": "web",
            "target": "not a url",
            "interval": "30",
        },
    )
    assert b'role="alert"' in rejected.data
    saved = client.post(
        "/monitors/new",
        data={
            "csrf_token": token,
            "kind": "heartbeat",
            "name": "job",
            "interval": "30",
            "grace_seconds": "60",
            "enabled": "1",
        },
    )
    assert saved.status_code == 302
    engine = Engine(
        (),
        repository_factory=lambda: SqliteRepository(database),
        catalog=True,
        tick_seconds=0.05,
    )
    engine.start()
    try:
        _wait(lambda: "job" in engine.catalog_names())
        token = _csrf(client.get("/dashboard").get_data(as_text=True))
        assert client.post("/monitors/job/pause", data={"csrf_token": token}).status_code == 302
        _wait(lambda: "job" not in engine.catalog_names())
        assert client.post("/monitors/job/pause", data={"csrf_token": token}).status_code == 302
        _wait(lambda: "job" in engine.catalog_names())
        client.application.config["ENGINE"] = engine
        ran = client.post("/monitors/job/check", data={"csrf_token": token}, follow_redirects=True)
        assert b"ping" in ran.data
        copied = client.post("/monitors/job/duplicate", data={"csrf_token": token})
        assert copied.status_code == 302
        assert (
            client.post("/monitors/job-copy/delete", data={"csrf_token": token}).status_code == 302
        )
        _wait(lambda: "job-copy" not in engine.catalog_names())
    finally:
        engine.stop()
    detail = client.get("/monitors/job/view")
    assert detail.status_code == 200
    assert b"job" in detail.data
    edited = client.post(
        "/monitors/job",
        data={"csrf_token": token, "kind": "heartbeat", "interval": "45", "enabled": "1"},
    )
    assert edited.status_code == 302
    with SqliteRepository(database) as repo:
        assert repo.monitors()[0].interval_seconds == 45


def test_dashboard_demo_alerts_sla_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Demo monitors, the calculator, and a test alert do not need a mail server."""
    for name in (
        "PULSEGUARD_SMTP_HOST",
        "PULSEGUARD_SMTP_PORT",
        "PULSEGUARD_SMTP_USERNAME",
        "PULSEGUARD_SMTP_PASSWORD",
        "PULSEGUARD_SMTP_FROM",
        "PULSEGUARD_SMTP_TO",
        "PULSEGUARD_SMTP_STARTTLS",
    ):
        monkeypatch.delenv(name, raising=False)
    client, database = _login(tmp_path)
    with SqliteRepository(database) as repo:
        repo.set_setting("smtp_password", "super-secret-mail")
    token = _csrf(client.get("/dashboard").get_data(as_text=True))
    assert client.post("/demo", data={"csrf_token": token}).status_code == 302
    board = client.get("/dashboard")
    assert b"shop-keyword" in board.data
    assert b"nightly-job" in board.data
    payload = client.get("/api/dashboard").get_json()
    assert "shop-depends" in {row["name"] for row in payload["monitors"]}
    alerts = client.get("/alerts")
    assert b"super-secret-mail" not in alerts.data
    assert b"password is saved" in alerts.data
    saved = client.post(
        "/alerts",
        data={
            "csrf_token": token,
            "failures_before_open": "4",
            "successes_before_resolve": "2",
            "smtp_host": "",
            "smtp_password": "",
        },
    )
    assert saved.status_code == 302
    tested = client.post("/alerts/test", data={"csrf_token": token}, follow_redirects=True)
    assert b"not configured" in tested.data
    sla = client.post(
        "/sla",
        data={
            "csrf_token": token,
            "source": "typed",
            "successes": "1",
            "total": "2",
            "target": "0.5",
        },
        follow_redirects=True,
    )
    assert b"Achieved 50%" in sla.data
    data_sla = client.post(
        "/sla",
        data={
            "csrf_token": token,
            "source": "data",
            "monitor": "web",
            "hours": "24",
            "target": "0.99",
        },
        follow_redirects=True,
    )
    assert data_sla.status_code == 200
    assert b"How PulseGuard works" in client.get("/help").data
    assert (
        b"No TLS monitors yet" in client.get("/certificates").data
        or b"cert" in client.get("/certificates").data
    )
    settings = client.get("/settings")
    assert _API_KEY.encode() in settings.data
    client.post(
        "/settings",
        data={
            "csrf_token": token,
            "new_password": "newer-password",
            "current_password": "long-enough",
        },
    )
    client.post("/settings", data={"csrf_token": token})
    hidden = client.get("/")
    assert hidden.status_code == 404
    client.post("/logout", data={"csrf_token": token})
    login = client.get("/login")
    fresh = _csrf(login.get_data(as_text=True))
    signed = client.post(
        "/login",
        data={"csrf_token": fresh, "username": "ada", "password": "newer-password"},
    )
    assert signed.status_code == 302


def test_exports_backups_evidence_and_agents(tmp_path: Path) -> None:
    """Downloads stay inside the data directory and a bad row is reported."""
    client, database = _login(tmp_path)
    token = _csrf(client.get("/data").get_data(as_text=True))
    exported = client.post(
        "/data/export",
        data={"csrf_token": token, "table": "monitors", "format": "csv"},
    )
    assert "attachment" in exported.headers["Content-Disposition"]
    assert b"name,kind,target" in exported.data
    good = b"name,kind,target,interval_seconds,enabled\nimported,http,http://127.0.0.1/,30,1\n"
    imported = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(good), "../../nope.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Imported 1" in imported.data
    duplicate = (
        b"name,kind,target,interval_seconds,enabled\n"
        b"web,http,http://127.0.0.1/,30,1\n"
        b"web,http,http://127.0.0.1/,30,1\n"
    )
    failed = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(duplicate), "m.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"line 3" in failed.data
    huge = b"x" * (_CSV + 1)
    too_big = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(huge), "big.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"too large" in too_big.data
    backup = client.post("/data/backup", data={"csrf_token": token})
    assert backup.data.startswith(b"PGDB")
    verified = client.post(
        "/data/verify",
        data={"csrf_token": token, "file": (io.BytesIO(backup.data), "../../evil.pgdb")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"checksum OK" in verified.data
    root = Path(str(client.application.config["DATA_DIR"]))
    uploads = list((root / "uploads").glob("*"))
    assert uploads
    assert all(path.parent == root / "uploads" for path in uploads)
    assert all("evil" not in path.name and "nope" not in path.name for path in uploads)
    moment = utc_now()
    with SqliteRepository(database) as repo:
        repo.open_incident("imported", moment, "down", "imported")
        repo.add_result(
            CheckResult(
                monitor="imported",
                kind="http",
                ok=False,
                latency_ms=1,
                message="down",
                severity=Severity.CRITICAL,
                checked_at=moment,
            )
        )
        incident_id = repo.incidents(state="open")[0].id
    pack = client.get(f"/incidents/{incident_id}/evidence")
    assert pack.status_code == 200
    with zipfile.ZipFile(io.BytesIO(pack.data)) as archive:
        assert len(archive.namelist()) > 1
    page = client.get("/incidents?tab=open")
    assert b"imported" in page.data
    assert b"not legal advice" in page.data
    timeline = client.get(f"/incidents/{incident_id}")
    assert b"down" in timeline.data
    refused = client.post(
        "/data/restore",
        data={"csrf_token": token, "confirm": "no", "file": (io.BytesIO(backup.data), "b.pgdb")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Type RESTORE" in refused.data
    client.application.config["DATA_DIR"] = str(tmp_path / "elsewhere")
    unsafe = client.post(
        "/data/restore",
        data={
            "csrf_token": token,
            "confirm": "RESTORE",
            "file": (io.BytesIO(backup.data), "b.pgdb"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"not allowed" in unsafe.data
    agents = client.post(
        "/agents",
        data={"csrf_token": token, "host": "0.0.0.0", "port": "8791", "enabled": "1"},
        follow_redirects=True,
    )
    assert agents.status_code == 200
    assert b"0.0.0.0" in agents.data or b"unspecified" in agents.data or b"private" in agents.data
    turned = client.post("/agents/token", data={"csrf_token": token}, follow_redirects=True)
    assert b"Copy token" in turned.data
    match = re.search(r"<code>([^<]+)</code>", turned.get_data(as_text=True))
    assert match is not None
    again = client.get("/agents")
    assert match.group(1) not in again.get_data(as_text=True)
    assert b"pulseguard push" in again.data


def test_remaining_forms_downloads_and_restore(tmp_path: Path) -> None:
    """Cover the other kinds, a real restore, and the certificate bands."""
    client, database = _login(tmp_path)
    token = _csrf(client.get("/monitors").get_data(as_text=True))
    assert b"No monitors yet" in client.get("/monitors").data
    specs = (
        _fields(kind="http", name="web", target="http://127.0.0.1/", retries="1", method="GET"),
        _fields(kind="keyword", name="words", target="http://127.0.0.1/", keyword="Acme"),
        _fields(
            kind="defacement",
            name="hash",
            target="http://127.0.0.1/",
            keyword="Acme",
            expected_sha256="ab" * 32,
            alert_after="3",
        ),
        _fields(kind="tcp", name="port", target="127.0.0.1", port="9"),
        _fields(kind="dns", name="names", target="example.com", expected="127.0.0.1"),
        _fields(kind="tls", name="cert", target="example.com", port="443"),
    )
    for spec in specs:
        spec["csrf_token"] = token
        assert client.post("/monitors/new", data=spec).status_code == 302
    missing_port = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="tcp", name="bare", target="127.0.0.1"),
    )
    assert b"port" in missing_port.data
    duplicate = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="http", name="web", target="http://127.0.0.1/"),
    )
    assert b"already exists" in duplicate.data
    moment = utc_now()
    with SqliteRepository(database) as repo:
        repo.add_result(
            CheckResult(
                monitor="cert",
                kind="tls",
                ok=True,
                latency_ms=2,
                message="certificate valid until 2026-12-01T00:00:00+00:00",
                severity=Severity.OK,
                checked_at=moment,
            )
        )
        repo.add_result(
            CheckResult(
                monitor="cert",
                kind="tls",
                ok=False,
                latency_ms=3,
                message="certificate valid until 2026-10-30T00:00:00+00:00",
                severity=Severity.CRITICAL,
                checked_at=moment,
            )
        )
    certs = client.get("/certificates")
    assert b"critical" in certs.data
    detail = client.get("/monitors/cert/view")
    assert b"polyline" in detail.data or b"ms" in detail.data
    sla = client.post(
        "/sla",
        data=_fields(
            csrf_token=token,
            source="typed",
            successes="5",
            total="2",
            target="0.99",
        ),
        follow_redirects=True,
    )
    assert b"cannot exceed" in sla.data or b"success" in sla.data
    smtp = client.post(
        "/alerts",
        data=_fields(
            csrf_token=token,
            failures_before_open="3",
            smtp_host="mail.example",
            smtp_port="587",
        ),
        follow_redirects=True,
    )
    assert smtp.status_code == 200
    client.post("/settings/api-key", data={"csrf_token": token})
    assert _API_KEY.encode() not in client.get("/settings").data
    client.post(
        "/agents",
        data={"csrf_token": token, "host": "127.0.0.1", "port": "8791"},
    )
    assert b"127.0.0.1" in client.get("/agents").data
    backup = client.post("/data/backup", data={"csrf_token": token})
    restored = client.post(
        "/data/restore",
        data={
            "csrf_token": token,
            "confirm": "RESTORE",
            "file": (io.BytesIO(backup.data), "b.pgdb"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Restart" in restored.data
    assert client.get("/monitors/missing/view").status_code == 404
    assert client.get("/incidents/999").status_code == 404
    exported = client.post(
        "/data/export",
        data={"csrf_token": token, "table": "results", "format": "json"},
    )
    assert exported.status_code == 200


def test_error_paths_and_the_collector_switch(tmp_path: Path) -> None:
    """Unknown monitors, a bad file, and the saved collector switch."""
    from pulseguard.checks.heartbeat import HeartbeatCheck
    from pulseguard.web.board import certificate_band, days_remaining, sparkline, uptime

    client, database = _login(tmp_path)
    token = _csrf(client.get("/dashboard").get_data(as_text=True))
    for action in ("pause", "check", "duplicate", "delete"):
        status = client.post(f"/monitors/missing/{action}", data={"csrf_token": token})
        assert status.status_code == 404
    refused = client.post(
        "/data/export",
        data={"csrf_token": token, "table": "nope", "format": "csv"},
    )
    assert refused.status_code == 400
    empty = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(b""), "m.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"empty" in empty.data
    bad = client.post(
        "/data/verify",
        data={"csrf_token": token, "file": (io.BytesIO(b"nope"), "b.pgdb")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"backup" in bad.data
    resolved = client.get("/incidents?tab=resolved")
    assert b"No resolved incidents" in resolved.data
    assert uptime([]) is None
    assert certificate_band(None) == "unknown"
    assert certificate_band(40) == "ok"
    assert certificate_band(10) == "warn"
    assert days_remaining("no date here", utc_now()) is None
    assert sparkline([1.0]) == ""
    with SqliteRepository(database) as repo:
        repo.set_setting("agent_enabled", "1")
        repo.set_setting("agent_token", HeartbeatCheck.new_token())
        repo.set_setting("agent_host", "127.0.0.1")
        repo.set_setting("agent_port", "0")
    engine = Engine(
        (),
        repository_factory=lambda: SqliteRepository(database),
        catalog=True,
        tick_seconds=0.05,
    )
    engine.start()
    try:
        _wait(lambda: (engine.agent_port or 0) > 0)
        engine._note_agent(
            CheckResult(
                monitor="pushed",
                kind="http",
                ok=True,
                latency_ms=1,
                message="HTTP 200",
                severity=Severity.OK,
                checked_at=utc_now(),
            )
        )
        with SqliteRepository(database) as repo:
            assert "pushed" in (repo.get_setting("agent_seen") or "")
            repo.set_setting("agent_enabled", "0")
        _wait(lambda: engine._collector is None)
    finally:
        engine.stop()


def test_anonymous_routes_form_errors_and_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every page refuses an anonymous visitor, and the form helpers reject bad input."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    from pulseguard.exceptions import ConfigValidationError
    from pulseguard.storage.records import MonitorRecord
    from pulseguard.web.board import days_remaining, monitor_state
    from pulseguard.web.forms import monitor_from_payload, parse_enabled
    from pulseguard.web.pages import (
        _agent_seen,
        _copy_name,
        _duration,
        _in_incident,
        _inside,
        _pct,
        _safe_name,
    )

    anonymous = _app(tmp_path / "other.db").test_client()
    gets = (
        "/dashboard",
        "/monitors",
        "/monitors/new",
        "/monitors/web",
        "/monitors/web/view",
        "/incidents",
        "/incidents/1",
        "/incidents/1/evidence",
        "/certificates",
        "/alerts",
        "/sla",
        "/agents",
        "/data",
        "/settings",
        "/help",
    )
    for path in gets:
        assert anonymous.get(path).status_code == 302
    posts = (
        "/demo",
        "/monitors/new",
        "/monitors/web",
        "/monitors/web/pause",
        "/monitors/web/check",
        "/monitors/web/duplicate",
        "/monitors/web/delete",
        "/alerts",
        "/alerts/test",
        "/sla",
        "/agents",
        "/agents/token",
        "/data/export",
        "/data/import",
        "/data/backup",
        "/data/verify",
        "/data/restore",
        "/settings",
        "/settings/api-key",
    )
    for path in posts:
        assert anonymous.post(path, data={"name": "x"}).status_code in {302, 400}

    client, database = _login(tmp_path)
    for path in ("/sla", "/alerts", "/agents", "/settings", "/monitors/new"):
        assert client.post(path, data={"name": "x"}).status_code == 400
    token = _csrf(client.get("/monitors/new").get_data(as_text=True))
    long_name = "a" * 129
    assert client.get(f"/monitors/{long_name}").status_code == 404
    assert client.get("/monitors/%20web").status_code == 404
    for action in ("pause", "check", "duplicate", "delete"):
        refused = client.post(f"/monitors/{long_name}/{action}", data={"csrf_token": token})
        assert refused.status_code == 404
    for payload in (
        _fields(csrf_token=token, kind="icmp", name="bad", target="http://127.0.0.1/"),
        _fields(
            csrf_token=token, kind="http", name="bad", target="http://127.0.0.1/", interval="no"
        ),
        _fields(
            csrf_token=token,
            kind="http",
            name="bad",
            target="http://127.0.0.1/",
            expected_status="x",
        ),
        _fields(
            csrf_token=token,
            kind="http",
            name="bad",
            target="http://127.0.0.1/",
            retry_backoff="nope",
        ),
    ):
        page = client.post("/monitors/new", data=payload)
        assert page.status_code == 200
        assert b'role="alert"' in page.data
    too_many = _fields(csrf_token=token, kind="http", name="bad", target="http://127.0.0.1/")
    too_many["depends_on"] = [f"m{i}" for i in range(9)]
    assert b"at most 8" in client.post("/monitors/new", data=too_many).data
    saved = client.post(
        "/monitors/new",
        data=_fields(
            csrf_token=token,
            kind="http",
            name="web",
            target="http://127.0.0.1:9/",
            timeout="5",
            method="GET",
            expected_status="200",
            retries="1",
            retry_backoff="0.5",
        ),
    )
    assert saved.status_code == 302
    dns = client.post(
        "/monitors/new",
        data=_fields(
            csrf_token=token,
            kind="dns",
            name="names",
            target="example.com",
            expected="10.0.0.1",
        ),
    )
    assert dns.status_code == 302
    edited = client.get("/monitors/names")
    assert b"10.0.0.1" in edited.data
    copied = client.post("/monitors/web/duplicate", data={"csrf_token": token})
    assert copied.status_code == 302

    class _Ready:
        def run_now(self, name: str) -> str:
            return "ran " + name

    class _Full:
        def run_now(self, name: str) -> str:
            raise ConfigValidationError("check queue is full")

    client.application.config["ENGINE"] = _Ready()
    ran = client.post("/monitors/web/check", data={"csrf_token": token}, follow_redirects=True)
    assert b"ran web" in ran.data
    client.application.config["ENGINE"] = _Full()
    queued = client.post("/monitors/web/check", data={"csrf_token": token}, follow_redirects=True)
    assert b"check queue is full" in queued.data
    client.application.config["ENGINE"] = None

    resting = client.post(
        "/monitors/new",
        data={
            "csrf_token": token,
            "kind": "http",
            "name": "resting",
            "target": "http://127.0.0.1:9/",
            "interval": "30",
            "allow_private": "1",
        },
    )
    assert resting.status_code == 302
    assert b"paused" in client.get("/dashboard").data
    now = utc_now()
    with SqliteRepository(database) as repo:
        repo.save_monitor(
            MonitorRecord(
                name="shop",
                kind="tls",
                target="127.0.0.1",
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.save_options("shop", {"port": 443, "allow_private": True})
        repo.add_result(
            CheckResult(
                monitor="shop",
                kind="tls",
                ok=False,
                latency_ms=12,
                message="certificate valid until 2026-11-10",
                severity=Severity.CRITICAL,
                checked_at=now,
            )
        )
        repo.add_result(
            CheckResult(
                monitor="shop",
                kind="tls",
                ok=False,
                latency_ms=18,
                message="certificate valid until 2026-11-10",
                severity=Severity.CRITICAL,
                checked_at=now,
            )
        )
        repo.open_incident("web", now - timedelta(hours=3), "down", "http")
        repo.resolve_incident("web", now - timedelta(hours=1))
    assert b"down" in client.get("/dashboard").data
    body = client.get("/api/dashboard").get_json()
    assert body["certs"] >= 1
    certificates = client.get("/certificates").data
    assert b"warn" in certificates or b"critical" in certificates
    resolved = client.get("/incidents?tab=resolved")
    assert b"2h" in resolved.data
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE incidents SET started_at = 'not-a-date'")
    assert client.get("/incidents/1/evidence").status_code == 400

    exported = client.post(
        "/data/export",
        data={"csrf_token": token, "table": "monitors", "format": "json"},
    )
    assert exported.status_code == 200
    assert exported.mimetype == "application/json"
    sheet = client.post(
        "/data/export",
        data={"csrf_token": token, "table": "monitors", "format": "csv"},
    )
    assert b"name,kind,target" in sheet.data
    csv_text = "name,kind,target,interval_seconds,enabled\nextra,http,http://127.0.0.1:9/,30,1\n"
    imported = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(csv_text.encode()), "m.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Imported 1" in imported.data
    broken = client.post(
        "/data/import",
        data={"csrf_token": token, "file": (io.BytesIO(b"\xff"), "m.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"utf-8" in broken.data
    missing_file = client.post(
        "/data/verify",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert b"choose a file" in missing_file.data
    sample = client.post(
        "/sla",
        data={
            "csrf_token": token,
            "source": "data",
            "monitor": "resting",
            "hours": "24",
            "target": "0.99",
        },
        follow_redirects=True,
    )
    assert b"no samples" in sample.data
    blank = client.post(
        "/sla",
        data={"csrf_token": token, "successes": "", "total": "", "target": "0.99"},
    )
    assert b"integer" in blank.data
    mailed = client.post(
        "/alerts",
        data={
            "csrf_token": token,
            "failures_before_open": "3",
            "successes_before_resolve": "1",
            "smtp_password": "secret-mail",
        },
        follow_redirects=True,
    )
    assert b"secret-mail" not in mailed.data
    locked = client.post(
        "/settings",
        data={
            "csrf_token": token,
            "current_password": "wrong-password",
            "new_password": "another-password",
        },
    )
    assert b"login failed" in locked.data
    with SqliteRepository(database) as repo:
        repo.set_setting("agent_seen", "not-json")
    assert client.get("/agents").status_code == 200
    with SqliteRepository(database) as repo:
        repo.set_setting("agent_seen", "[]")
    assert client.get("/agents").status_code == 200
    with SqliteRepository(database) as repo:
        repo.set_setting("agent_seen", '{"lab":"2026-10-27 12:00:00"}')
    assert b"lab" in client.get("/agents").data

    start = datetime(2026, 10, 27, tzinfo=UTC)
    assert _duration("not-a-date", "") == ""
    assert _duration("2026-10-27T02:00:00+00:00", "2026-10-27T01:00:00+00:00") == ""
    assert _duration("2026-10-27T00:00:00+00:00", "2026-10-27T02:05:00+00:00") == "2h 5m"
    assert _duration("2026-10-27T00:00:00+00:00", "2026-10-27T00:03:04+00:00") == "3m 4s"
    assert _in_incident(start, "not-a-date", "") is False
    later = datetime(2026, 10, 27, 1, tzinfo=UTC)
    assert _in_incident(start, "2026-10-27T01:00:00+00:00", "") is False
    assert _in_incident(later, "2026-10-27T00:00:00+00:00", "") is True
    assert _in_incident(later, "2026-10-27T00:00:00+00:00", "bad") is True
    assert _in_incident(later, "2026-10-27T00:00:00+00:00", "2026-10-28T00:00:00+00:00") is True
    assert _agent_seen(None) == []
    assert _agent_seen("{") == []
    assert _agent_seen("[]") == []
    assert _pct(None) == "—"
    assert _pct(0.5) == "50%"
    assert _safe_name(" web") is None
    with pytest.raises(ConfigValidationError):
        _copy_name("a" * 124)
    assert _inside(Path("/etc/passwd"), tmp_path) is False
    paused = MonitorRecord("resting", "http", "http://127.0.0.1/", 30, False)
    assert monitor_state(paused, None) == "paused"
    assert days_remaining("until 2026-99-99", now) is None
    with pytest.raises(ConfigValidationError):
        monitor_from_payload([])
    with pytest.raises(ConfigValidationError):
        monitor_from_payload({"name": "a", "extra": 1})
    with pytest.raises(ConfigValidationError):
        monitor_from_payload({"name": "a"})
    assert parse_enabled("true") is True
    assert parse_enabled("false") is False
    with pytest.raises(ConfigValidationError):
        parse_enabled("yes")

    class _Bare:
        def close(self) -> None:
            return None

    monkeypatch.setenv("PULSEGUARD_SMTP_HOST", "smtp.example")
    note = CheckResult(
        monitor="smtp-test",
        kind="http",
        ok=True,
        latency_ms=0,
        message="test email",
        severity=Severity.INFO,
        checked_at=now,
    )
    notifier = DatabaseNotifier(lambda: _Bare())
    notifier.note(note)
    mapping = notifier.mapping()
    assert mapping["PULSEGUARD_SMTP_HOST"] == "smtp.example"
    assert mapping["PULSEGUARD_SMTP_PORT"] == ""


def test_notifier_reports_missing_smtp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A database with no host records the alert as not configured."""
    monkeypatch.delenv("PULSEGUARD_SMTP_HOST", raising=False)
    database = tmp_path / "history.db"
    notifier = DatabaseNotifier(lambda: SqliteRepository(database))
    assert notifier.mapping()["PULSEGUARD_SMTP_HOST"] == ""
    with pytest.raises(Exception, match="not configured"):
        notifier.send(
            CheckResult(
                monitor="smtp-test",
                kind="http",
                ok=True,
                latency_ms=0,
                message="test email",
                severity=Severity.INFO,
                checked_at=utc_now(),
            )
        )


def test_load_demo_builds_the_lab_set(tmp_path: Path) -> None:
    """The demo helper stores the six local monitors."""
    database = tmp_path / "history.db"
    with SqliteRepository(database) as repo:
        load_demo(repo)
        names = {record.name for record in repo.monitors()}
    assert names == {
        "web",
        "shop-keyword",
        "shop-defacement",
        "lab-port",
        "nightly-job",
        "shop-depends",
    }


def _login(tmp_path: Path):
    """Create the admin account and return a signed-in client."""
    database = tmp_path / "history.db"
    client = _app(database).test_client()
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    return client, database


def _app(database: Path):
    """Build an app around ``database``."""
    return create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )


def _fields(**values: str) -> dict[str, str]:
    """Return form fields with the usual interval and private-target switch."""
    payload = {"interval": "30", "enabled": "1", "allow_private": "1"}
    payload.update(values)
    return payload


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _wait(ready: object) -> None:
    """Poll for up to five seconds."""
    import time

    if not callable(ready):
        raise AssertionError("ready must be callable")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.05)
    raise AssertionError("change was not visible within 5 seconds")


_CSV = 1_000_000
