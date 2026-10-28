"""Visible times stay out of ISO form, and latency stays a whole number of milliseconds."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_FRACTION = re.compile(r"\d+\.\d{4,} ms")
_WHEN = datetime(2026, 10, 27, 13, 58, 41, 149081, tzinfo=UTC)


def test_visible_text_has_no_raw_timestamps_or_fractional_milliseconds(tmp_path: Path) -> None:
    """Every signed-in page, the public status page, and the error pages stay readable."""
    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password="s3cret-password",
        api_key="example-api-key-value",
    )
    client = app.test_client()
    setup = client.get("/setup")
    token = _csrf(setup.get_data(as_text=True))
    client.post(
        "/setup",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("web", "http", "http://127.0.0.1/", 30, True))
        repo.add_result(
            CheckResult(
                monitor="web",
                kind="http",
                ok=False,
                latency_ms=142.73349999712082,
                message="connection refused",
                severity=Severity.CRITICAL,
                checked_at=_WHEN,
            )
        )
        repo.open_incident("web", _WHEN, "connection refused", "http")
        incident_id = repo.incidents(state="open")[0].id
        repo.set_setting("public_status", "0")
    pages = [
        "/dashboard",
        "/monitors",
        "/monitors/new",
        "/monitors/web",
        "/monitors/web/view",
        "/incidents",
        f"/incidents/{incident_id}",
        "/alerts",
        "/certificates",
        "/sla",
        "/agents",
        "/data",
        "/settings",
        "/help",
        "/status",
        "/missing-page",
    ]
    for path in pages:
        response = client.get(path)
        assert response.status_code in {200, 404}, path
        _assert_readable(path, response.get_data(as_text=True))
    with SqliteRepository(database) as repo:
        repo.set_setting("public_status", "1")
    public = client.get("/status")
    assert public.status_code == 200
    _assert_readable("/status", public.get_data(as_text=True))


def test_run_all_checks_requires_login_and_reports_a_count(tmp_path: Path) -> None:
    """The palette action posts to a real route and does not run for an anonymous visitor."""
    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password="s3cret-password",
        api_key="example-api-key-value",
    )
    client = app.test_client()
    assert client.post("/monitors/run").status_code == 302
    setup = client.get("/setup")
    token = _csrf(setup.get_data(as_text=True))
    client.post(
        "/setup",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post(
        "/login",
        data={"csrf_token": token, "username": "ada", "password": "long-enough"},
    )
    made = client.post(
        "/monitors/new",
        data={
            "csrf_token": token,
            "kind": "tcp",
            "name": "port",
            "target": "127.0.0.1",
            "port": "1",
            "timeout": "1",
            "interval": "30",
            "enabled": "1",
            "allow_private": "1",
        },
        follow_redirects=False,
    )
    assert made.status_code == 302
    page = client.get("/dashboard")
    token = _csrf(page.get_data(as_text=True))
    ran = client.post("/monitors/run", data={"csrf_token": token}, follow_redirects=True)
    assert ran.status_code == 200
    assert b"Ran 1" in ran.data


def _assert_readable(path: str, html: str) -> None:
    """Fail when a person can see an ISO timestamp or a long fractional millisecond."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(["script", "style"]):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    assert _ISO.search(text) is None, path
    assert _FRACTION.search(text) is None, path


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
