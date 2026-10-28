"""Sidebar scroll, form spacing, countdown rings, and the response chart."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from pulseguard.checks.agent_check import STALE_MESSAGE
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


def test_an_agent_without_latency_omits_zero_milliseconds(tmp_path: Path) -> None:
    """A push that did not measure latency does not render 0 ms, and the chart payload exists."""
    database = tmp_path / "quiet.db"
    client = _client(database)
    now = utc_now()
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("quiet", "agent", "", 60, True))
        repo.add_result(
            CheckResult(
                monitor="quiet",
                kind="agent",
                ok=False,
                latency_ms=0,
                message=STALE_MESSAGE,
                severity=Severity.CRITICAL,
                checked_at=now,
            )
        )
    page = client.get("/monitors/quiet/view").get_data(as_text=True)
    assert "0 ms" not in page
    assert "no report" in page
    assert "strip-data" in page
    payload = client.get("/api/dashboard?chart=quiet").get_json()
    assert payload["chart_series"]
    assert payload["chart_series"][0][1] == 0
    agents = client.get("/agents").get_data()
    assert b"collector-grid" in agents
    assert b"form-foot" in agents
    assert b'class="add-agent"' in agents


def test_layout_rings_and_chart_tooltip(tmp_path: Path) -> None:
    """Settings stays put, Save has room, rings fit, and the chart tooltip follows the pointer."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    playwright = None
    browser = None
    try:
        playwright = playwright_api.sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
    except Exception as exc:
        if playwright is not None:
            playwright.stop()
        pytest.skip(f"playwright browser is missing: {exc}")
    database = tmp_path / "polish.db"
    _seed(database)
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
        serve=True,
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    shot = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    try:
        page = browser.new_page()
        page.set_default_timeout(15_000)
        _sign_in(page, base)
        for width, height in ((1440, 700), (1440, 500)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto(base + "/dashboard")
            page.wait_for_selector(".cd-label")
            _pinned(page, height)
            _wheel_stays(page)
            _rings_fit(page)
        page.set_viewport_size({"width": 390, "height": 700})
        page.goto(base + "/dashboard")
        page.click("[data-menu]")
        page.wait_for_selector("body.nav-lock")
        assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"
        _pinned(page, 700)
        _wheel_stays(page)
        page.set_viewport_size({"width": 1440, "height": 700})
        page.goto(base + "/agents")
        page.locator(".collector-card").scroll_into_view_if_needed()
        gap = page.evaluate(
            """() => {
              const host = document.querySelector('#host').getBoundingClientRect();
              const port = document.querySelector('#agent-port').getBoundingClientRect();
              const nodes = [...document.querySelectorAll('button')];
              const save = nodes.find((node) => node.textContent.trim() === 'Save');
              const button = save.getBoundingClientRect();
              return {columns: port.left - host.right, above: button.top - port.bottom};
            }"""
        )
        assert gap["columns"] >= 16
        assert gap["above"] >= 16
        _shoot(page, shot, "agents")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(base + "/monitors")
        page.wait_for_selector(".cd.is-quiet")
        _rings_fit(page)
        _shoot(page, shot, "rings")
        page.goto(base + "/monitors/speed/view")
        page.wait_for_selector(".chart .line")
        _chart(page)
        page.locator(".chart").scroll_into_view_if_needed()
        box = page.locator(".chart").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] - 12, box["y"] + box["height"] * 0.45)
        page.wait_for_selector(".chart-wrap .tip.on")
        _shoot(page, shot, "chart-tooltip")
    finally:
        browser.close()
        playwright.stop()
        server.shutdown()


def _pinned(page: object, height: int) -> None:
    """Settings and Help sit inside the viewport while the document is unscrolled."""
    assert page.evaluate("document.scrollingElement.scrollTop") == 0
    for word in ("Settings", "Help"):
        box = page.locator(".side-foot a", has_text=word).bounding_box()
        assert box is not None
        assert box["y"] >= 0
        assert box["y"] + box["height"] <= height + 1


def _wheel_stays(page: object) -> None:
    """A wheel over the sidebar leaves the main scroll position alone."""
    page.evaluate("document.scrollingElement.scrollTop = 180")
    before = page.evaluate("document.scrollingElement.scrollTop")
    assert before > 0
    box = page.locator(".side-scroll").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + 24, box["y"] + min(80, box["height"] / 2))
    page.mouse.wheel(0, 500)
    assert page.evaluate("document.scrollingElement.scrollTop") == before
    page.evaluate("document.scrollingElement.scrollTop = 0")


def _rings_fit(page: object) -> None:
    """Each countdown label stays inside its circle."""
    fitted = page.evaluate(
        """() => [...document.querySelectorAll('.cd')].every((ring) => {
          const label = ring.querySelector('.cd-label');
          const outer = ring.getBoundingClientRect();
          const inner = label.getBoundingClientRect();
          if (outer.width < 44 || outer.height < 44) return false;
          if (!label.textContent) return ring.classList.contains('is-quiet');
          return inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5
            && inner.top >= outer.top - 0.5 && inner.bottom <= outer.bottom + 0.5
            && label.scrollWidth <= inner.width + 1;
        })"""
    )
    assert fitted
    labels = page.locator(".cd-label").all_inner_texts()
    assert any(text.endswith("s") and text != "now" for text in labels)
    assert any(text.endswith("m") for text in labels)
    assert "now" in labels
    assert page.locator(".cd.is-quiet").get_attribute("aria-label") == "no report"


def _chart(page: object) -> None:
    """Hover, the right edge, and the arrow keys move one tooltip."""
    box = page.locator(".chart").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.08, box["y"] + box["height"] * 0.45)
    page.wait_for_selector(".chart-wrap .tip.on")
    assert "No data" in page.locator(".chart-wrap .tip").inner_text()
    page.mouse.move(box["x"] + box["width"] - 12, box["y"] + box["height"] * 0.45)
    tip = page.locator(".chart-wrap .tip")
    text = tip.inner_text()
    assert "33 ms" in text
    assert "Up" in text
    expected = page.evaluate(
        """() => {
          const rows = JSON.parse(document.getElementById('chart-data').textContent);
          return pgLive.clockLabel(rows[rows.length - 1][0]);
        }"""
    )
    assert expected in text
    wrap = page.locator(".chart-wrap").bounding_box()
    tip_box = tip.bounding_box()
    assert wrap is not None and tip_box is not None
    assert tip_box["x"] >= wrap["x"] - 1
    assert tip_box["x"] + tip_box["width"] <= wrap["x"] + wrap["width"] + 1
    page.locator(".chart").focus()
    before = page.locator(".chart").get_attribute("data-index")
    page.keyboard.press("ArrowLeft")
    after = page.locator(".chart").get_attribute("data-index")
    assert before is not None and after is not None
    assert int(after) == int(before) - 1
    assert "22 ms" in page.locator(".chart-wrap .tip").inner_text()
    page.keyboard.press("Escape")
    assert page.locator(".chart-wrap .tip.on").count() == 0
    line = page.locator(".chart .p50").bounding_box()
    assert line is not None
    page.mouse.move(line["x"] + line["width"] * 0.7, line["y"] + line["height"] / 2)
    assert "p50" in page.locator(".chart-wrap .tip").inner_text()
    cell = page.locator(".bar i").first
    cell.hover()
    assert "No data" in page.locator(".strip-tip").inner_text()


def _shoot(page: object, folder: Path, name: str, *, full: bool = False) -> None:
    """Save the current view in both themes, after a tooltip fade has finished."""
    folder.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    for theme in ("dark", "light"):
        page.evaluate(
            "(theme) => document.documentElement.setAttribute('data-theme', theme)", theme
        )
        page.wait_for_timeout(50)
        page.screenshot(path=str(folder / f"{name}-{theme}.png"), full_page=full)


def _sign_in(page: object, base: str) -> None:
    """Create the first account and land on the dashboard."""
    page.goto(base + "/setup")
    page.fill("#username", "ada")
    page.fill("#password", "long-enough")
    page.click("button[type=submit]")
    page.wait_for_url("**/login")
    page.fill("#username", "ada")
    page.fill("#password", "long-enough")
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard")


def _seed(database: Path) -> None:
    """Monitors for a long sidebar, each ring state, and a chart with a gap."""
    now = utc_now()
    with SqliteRepository(database) as repo:
        for index in range(12):
            name = f"m{index:02d}"
            repo.save_monitor(MonitorRecord(name, "tcp", "127.0.0.1", 60, True))
            repo.add_result(_result(name, "up", 5, now - timedelta(seconds=30)))
        repo.save_monitor(MonitorRecord("aa-sec", "tcp", "127.0.0.1", 120, True))
        repo.add_result(_result("aa-sec", "up", 8, now - timedelta(seconds=100)))
        repo.save_monitor(MonitorRecord("bb-min", "tcp", "127.0.0.1", 900, True))
        repo.add_result(_result("bb-min", "up", 8, now - timedelta(seconds=120)))
        repo.save_monitor(MonitorRecord("cc-now", "tcp", "127.0.0.1", 30, True))
        repo.add_result(_result("cc-now", "down", 8, now - timedelta(minutes=10), ok=False))
        repo.save_monitor(MonitorRecord("dd-quiet", "agent", "", 60, True))
        repo.add_result(
            CheckResult(
                monitor="dd-quiet",
                kind="agent",
                ok=False,
                latency_ms=0,
                message=STALE_MESSAGE,
                severity=Severity.CRITICAL,
                checked_at=now - timedelta(seconds=10),
            )
        )
        repo.save_monitor(MonitorRecord("speed", "tcp", "127.0.0.1", 60, True))
        repo.add_result(
            CheckResult(
                monitor="speed",
                kind="tcp",
                ok=True,
                latency_ms=0,
                message="monitoring gap",
                severity=Severity.INFO,
                checked_at=now - timedelta(minutes=40),
            )
        )
        repo.add_result(_result("speed", "first", 11, now - timedelta(minutes=20)))
        repo.add_result(_result("speed", "second", 22, now - timedelta(minutes=10)))
        repo.add_result(_result("speed", "third", 33, now - timedelta(minutes=1)))


def _result(
    name: str, message: str, latency: float, when: datetime, *, ok: bool = True
) -> CheckResult:
    """One stored check."""
    return CheckResult(
        monitor=name,
        kind="tcp",
        ok=ok,
        latency_ms=latency,
        message=message,
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=when,
    )


def _client(database: Path) -> object:
    """Return a signed-in test client."""
    import re

    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )
    client = app.test_client()
    setup = client.get("/setup")
    token = re.search(r'name="csrf_token" value="([^"]+)"', setup.get_data(as_text=True))
    assert token is not None
    client.post(
        "/setup",
        data={"csrf_token": token.group(1), "username": "ada", "password": "long-enough"},
    )
    login = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', login.get_data(as_text=True))
    assert token is not None
    client.post(
        "/login",
        data={"csrf_token": token.group(1), "username": "ada", "password": "long-enough"},
    )
    return client
