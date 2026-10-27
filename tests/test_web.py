"""Status page, admin login, the write API, and ping. No monitor URL is fetched."""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.cli import main
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_TOKEN = "example-heartbeat-token"
_TARGET = "http://secret.example/has<tag>"


def test_public_status_reads_storage_and_hides_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page and the JSON API show the stored result and not the catalog target."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("status page fetched a URL")

    monkeypatch.setattr(requests.sessions.Session, "request", boom)
    database = tmp_path / "history.db"
    _seed(database)
    client = _app(database).test_client()
    page = client.get("/")
    payload = client.get("/api/status")
    assert page.status_code == 200
    assert "HTTP 200" in page.get_data(as_text=True)
    assert "secret.example" not in page.get_data(as_text=True)
    assert "has<tag>" not in page.get_data(as_text=True)
    assert page.headers["Content-Security-Policy"] == "default-src 'self'"
    body = payload.get_json()
    assert body["note"] == "stored results only"
    assert body["monitors"][0]["message"] == "HTTP 200"
    assert "target" not in body["monitors"][0]
    assert "secret.example" not in payload.get_data(as_text=True)


def test_write_api_requires_the_key_and_omits_the_target(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing key is rejected. A valid write stores the row and does not echo the target."""
    caplog.set_level(logging.INFO, logger="pulseguard")
    database = tmp_path / "history.db"
    client = _app(database).test_client()
    payload = {
        "name": "web",
        "kind": "http",
        "target": _TARGET,
        "interval_seconds": 30,
        "enabled": True,
    }
    refused = client.post(
        "/api/monitors", json=payload, headers={"X-API-Key": "wrong-api-key-value"}
    )
    assert refused.status_code == 401
    assert "target" not in refused.get_data(as_text=True)
    assert _API_KEY not in caplog.text
    created = client.post("/api/monitors", json=payload, headers={"X-API-Key": _API_KEY})
    assert created.status_code == 201
    assert created.get_json() == {"name": "web", "enabled": True}
    assert "secret.example" not in created.get_data(as_text=True)
    with SqliteRepository(database) as repo:
        assert repo.monitors()[0].target == _TARGET
    rejected = client.post(
        "/api/monitors",
        json={**payload, "kind": "icmp"},
        headers={"X-API-Key": _API_KEY},
    )
    assert rejected.status_code == 400
    assert "icmp" in rejected.get_json()["error"]


def test_ping_updates_the_live_check_and_login_uses_csrf(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One ping makes the heartbeat pass. Admin can see the target; the public page cannot."""
    caplog.set_level(logging.INFO, logger="pulseguard.web")
    when = datetime(2026, 10, 27, tzinfo=UTC)
    check = HeartbeatCheck("job", _TOKEN, grace_seconds=120, clock=lambda: when)
    database = tmp_path / "history.db"
    _seed(database)
    app = _app(database, heartbeats=[check])
    assert _PASSWORD not in repr(app.config)
    client = app.test_client()
    missing = client.get("/ping/example-heartbeat-other")
    assert missing.status_code == 404
    assert missing.get_data(as_text=True) == "not found"
    assert "example-heartbeat-other" not in caplog.text
    assert client.get(f"/ping/{_TOKEN}").get_data(as_text=True) == "ok"
    assert check.run().ok is True
    assert _TOKEN not in caplog.text
    assert "heartbeat ping accepted monitor=job" in caplog.text

    denied = client.post(
        "/admin/login",
        data={"username": "operator", "password": _PASSWORD, "csrf_token": "not-the-token"},
    )
    assert denied.status_code == 400
    page = client.get("/admin/login")
    token = _csrf(page.get_data(as_text=True))
    failed = client.post(
        "/admin/login",
        data={"username": "operator", "password": "wrong-password", "csrf_token": token},
    )
    assert failed.status_code == 401
    assert "wrong-password" not in failed.get_data(as_text=True)
    assert _PASSWORD not in caplog.text
    token = _csrf(failed.get_data(as_text=True))
    logged_in = client.post(
        "/admin/login",
        data={"username": "operator", "password": _PASSWORD, "csrf_token": token},
        follow_redirects=True,
    )
    admin = logged_in.get_data(as_text=True)
    assert "secret.example" in admin
    assert "has&lt;tag&gt;" in admin
    assert client.get("/").get_data(as_text=True).find("secret.example") == -1
    csrf = _csrf(admin)
    saved = client.post(
        "/admin/monitors",
        data={"csrf_token": csrf, "name": "web", "enabled": "false"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    with SqliteRepository(database) as repo:
        assert repo.monitors()[0].enabled is False
    logout = _csrf(saved.get_data(as_text=True))
    client.post("/admin/logout", data={"csrf_token": logout})
    assert client.get("/admin").status_code == 302


def test_web_command_rejects_a_missing_secret_and_a_public_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process does not listen when the secret or the address is refused."""
    config = tmp_path / "monitors.json"
    config.write_text(
        '{"monitors":[{"kind":"http","name":"demo","target":"http://127.0.0.1/ok","interval":30}]}',
        encoding="utf-8",
    )
    database = tmp_path / "history.db"
    monkeypatch.delenv("PULSEGUARD_SECRET_KEY", raising=False)
    before = _thread_names()
    code, _text, err = _run(
        ["web", "--config", str(config), "--db", str(database), "--host", "1.2.3.4"]
    )
    assert code == 2
    assert "private" in err or "literal" in err
    _set_web_env(monkeypatch)
    code, _text, err = _run(
        ["web", "--config", str(config), "--db", str(database), "--host", "0.0.0.0"]
    )
    assert code == 2
    monkeypatch.delenv("PULSEGUARD_SECRET_KEY", raising=False)
    code, _text, err = _run(["web", "--config", str(config), "--db", str(database)])
    assert code == 2
    assert "secret key" in err
    assert _thread_names() == before


def _app(database: Path, heartbeats: list[HeartbeatCheck] | None = None) -> object:
    """Build an app. The return is a Flask app; tests use the client."""
    return create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
        heartbeats=heartbeats or [],
    )


def _seed(database: Path) -> None:
    """Store one catalog row and one result. The target must not leak later."""
    with SqliteRepository(database) as repo:
        repo.save_monitor(
            MonitorRecord(
                name="web",
                kind="http",
                target=_TARGET,
                interval_seconds=30,
                enabled=True,
            )
        )
        repo.add_result(
            CheckResult(
                monitor="web",
                kind="http",
                ok=True,
                latency_ms=1,
                message="HTTP 200",
                severity=Severity.OK,
                checked_at=datetime(2026, 10, 27, tzinfo=UTC),
                status_code=200,
            )
        )


def _csrf(html: str) -> str:
    """Read the hidden token from a rendered form."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _set_web_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the four web secrets to test placeholders."""
    monkeypatch.setenv("PULSEGUARD_SECRET_KEY", "test-secret-key-value")
    monkeypatch.setenv("PULSEGUARD_ADMIN_USER", "operator")
    monkeypatch.setenv("PULSEGUARD_ADMIN_PASSWORD", _PASSWORD)
    monkeypatch.setenv("PULSEGUARD_API_KEY", _API_KEY)


def _thread_names() -> list[str]:
    """Return live thread names so a refused command cannot leave an engine running."""
    return sorted(thread.name for thread in threading.enumerate())


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process."""
    import contextlib
    from io import StringIO

    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    return code, stdout.getvalue(), stderr.getvalue()
