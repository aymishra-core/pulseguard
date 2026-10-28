"""The live check log shows completed checks and an honest badge."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_the_log_opens_with_the_last_twenty_checks_newest_first(tmp_path: Path) -> None:
    """A fresh page lists twenty completed checks and skips a monitoring gap."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("shop", "http", "http://127.0.0.1/", 30, True))
        repo.add_result(_result("shop", True, "monitoring gap", _NOW, 1))
        for index in range(25):
            message = "<script>alert(1)</script>" if index == 24 else f"check {index}"
            repo.add_result(
                _result(
                    "shop",
                    index % 2 == 0,
                    message,
                    _NOW + timedelta(seconds=index + 1),
                    10 + index,
                )
            )
    page = client.get("/dashboard").get_data(as_text=True)
    soup = BeautifulSoup(page, "html.parser")
    rows = soup.select("[data-log] [data-log-id]")
    assert len(rows) == 20
    assert rows[0].select_one("b").get_text(strip=True) == "shop"
    assert rows[0].select_one(".ts").get_text(strip=True) == "12:00:25"
    assert rows[0].select_one(".lv").get_text(strip=True) == "UP"
    assert "<script>alert(1)</script>" in rows[0].get_text(" ", strip=True)
    assert soup.select("[data-log] script") == []
    assert "34 ms" in rows[0].get_text(" ", strip=True)
    assert rows[1].select_one(".lv").get_text(strip=True) == "DOWN"
    assert "monitoring gap" not in soup.select_one("[data-log]").get_text()
    assert soup.select_one("[data-log-badge]").get_text(strip=True) == "streaming"
    body = client.get("/api/dashboard").get_json()
    assert [row["message"] for row in body["checks"][:2]] == [
        "<script>alert(1)</script>",
        "check 23",
    ]
    assert len(body["checks"]) == 25


def test_an_empty_log_waits_for_the_next_check(tmp_path: Path) -> None:
    """No completed check leaves the waiting line, not a blank panel."""
    client = _client(tmp_path / "history.db")
    page = client.get("/dashboard").get_data(as_text=True)
    soup = BeautifulSoup(page, "html.parser")
    assert (
        soup.select_one("[data-log-empty]").get_text(strip=True) == "Waiting for the next check..."
    )
    assert soup.select("[data-log] [data-log-id]") == []


def test_a_check_message_is_text_and_new_lines_follow_the_payload(tmp_path: Path) -> None:
    """Three new checks prepend in order, the fiftieth stays, and a script stays text."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    from threading import Thread

    from werkzeug.serving import make_server

    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )
    server = make_server("127.0.0.1", 0, app)
    Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    base = f"http://{host}:{port}"
    try:
        with playwright_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(base + "/setup")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/login")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/dashboard")
            first = {
                "checks": [
                    _line(3, "12:00:03", "api", "DOWN", "<script>alert(1)</script>", 9),
                    _line(2, "12:00:02", "shop", "UP", "HTTP 200", 12),
                    _line(1, "12:00:01", "web", "UP", "HTTP 200", 4),
                ],
                "generated_ms": 1,
            }
            added = page.evaluate(
                """(body) => {
                  applyState(body);
                  const lines = Array.from(document.querySelectorAll("[data-log-id]"));
                  return {
                    ids: lines.map((row) => row.getAttribute("data-log-id")),
                    text: lines.map((row) => row.textContent),
                    scripts: document.querySelectorAll("[data-log] script").length
                  };
                }""",
                first,
            )
            assert added["ids"] == ["3", "2", "1"]
            assert added["text"][0].startswith("12:00:03 api DOWN <script>alert(1)</script> 9 ms")
            assert added["scripts"] == 0
            assert "<script>" in added["text"][0]
            extra = {
                "checks": [
                    _line(index, "12:00:00", f"n{index}", "UP", "ok", 1)
                    for index in range(51, 3, -1)
                ],
                "generated_ms": 2,
            }
            capped = page.evaluate(
                """(body) => {
                  applyState(body);
                  const lines = Array.from(document.querySelectorAll("[data-log-id]"));
                  const attack = "<" + "script>alert(1)</" + "script>";
                  return {
                    count: lines.length,
                    first: lines[0].getAttribute("data-log-id"),
                    last: lines[lines.length - 1].getAttribute("data-log-id"),
                    script: lines.some((row) => row.textContent.includes(attack))
                  };
                }""",
                extra,
            )
            assert capped == {"count": 50, "first": "51", "last": "2", "script": True}
            badge = page.evaluate(
                """() => {
                  const now = Date.now();
                  const outage = pgLive.logBadge(now - 20000, now, false, 5000);
                  pgShowLogBadge(outage);
                  const reconnecting = document.querySelector("[data-log-badge]").textContent;
                  const reconnectingClass = document.querySelector("[data-log-live]").className;
                  pgShowLogBadge(pgLive.logBadge(now, now, true, 5000));
                  const paused = document.querySelector("[data-log-badge]").textContent;
                  pgShowLogBadge(pgLive.logBadge(now, now, false, 5000));
                  const streaming = document.querySelector("[data-log-badge]").textContent;
                  return {outage, reconnecting, reconnectingClass, paused, streaming};
                }"""
            )
            assert badge["outage"] == "reconnecting"
            assert badge["reconnecting"] == "reconnecting"
            assert "is-reconnecting" in badge["reconnectingClass"]
            assert badge["paused"] == "paused"
            assert badge["streaming"] == "streaming"
            browser.close()
    finally:
        server.shutdown()


def _line(
    row_id: int, time: str, name: str, result: str, message: str, latency: int
) -> dict[str, object]:
    """Return one live-log row for a browser payload."""
    return {
        "id": row_id,
        "time": time,
        "name": name,
        "result": result,
        "message": message,
        "latency": latency,
    }


def _client(database: Path):
    """Return a signed-in client."""
    client = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    ).test_client()
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    return client


def _result(monitor: str, ok: bool, message: str, moment: datetime, latency: float) -> CheckResult:
    """Return one stored result."""
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=latency,
        message=message,
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=moment,
    )


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
