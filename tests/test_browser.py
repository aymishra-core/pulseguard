"""Browser passes through setup, login, and the signed-in console.

The test is skipped when Playwright is not installed or its browser cannot start.
It is not part of the default install, so CI stays on the packages in ``.[dev]``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.storage import SqliteRepository
from pulseguard.web.app import create_app


def test_browser_can_set_up_sign_in_and_add_a_monitor(tmp_path: Path) -> None:
    """Setup, login, add a monitor, and see that name on the dashboard."""
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
    app = create_app(
        database=tmp_path / "history.db",
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password="s3cret-password",
        api_key="example-api-key-value",
        serve=True,
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = browser.new_page()
        page.set_default_timeout(15_000)
        page.goto(base + "/setup")
        page.fill("#username", "ada")
        page.fill("#password", "long-enough")
        page.click("button[type=submit]")
        page.wait_for_url("**/login")
        page.fill("#username", "ada")
        page.fill("#password", "long-enough")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard")
        page.goto(base + "/monitors/new")
        page.click("#step-next")
        page.fill("#name", "web")
        page.fill("#target", "http://127.0.0.1:9/")
        page.click("#step-next")
        page.get_by_role("button", name="Save").click()
        try:
            page.wait_for_url("**/monitors/web/view")
        except Exception as exc:
            text = page.locator("body").inner_text()[:400]
            raise AssertionError(f"{page.url} {text}") from exc
        page.goto(base + "/dashboard")
        assert "web" in page.content()
    finally:
        browser.close()
        playwright.stop()
        server.shutdown()


def test_browser_walks_theme_palette_probe_and_incident(tmp_path: Path) -> None:
    """Onboarding, theme, the command palette, a test check, a live card, and a timeline."""
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
    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password="s3cret-password",
        api_key="example-api-key-value",
        serve=True,
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = browser.new_page()
        page.set_default_timeout(15_000)
        page.goto(base + "/setup")
        page.fill("#username", "ada")
        page.fill("#password", "long-enough")
        page.click("button[type=submit]")
        page.wait_for_url("**/login")
        page.fill("#username", "ada")
        page.fill("#password", "long-enough")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard")
        assert "First steps" in page.content()
        page.click("#theme-toggle")
        assert page.locator("html").get_attribute("data-theme") in {"light", "dark"}
        page.keyboard.press("Control+k")
        page.wait_for_selector("#palette:not([hidden])")
        assert "Incidents" in page.locator("#palette-list").inner_text()
        page.keyboard.press("Escape")
        page.goto(base + "/monitors/new")
        page.select_option("#kind", "tcp")
        page.click("#step-next")
        page.fill("#name", "web")
        page.fill("#target", "127.0.0.1")
        page.fill("#port", "1")
        page.fill("#timeout", "1")
        page.get_by_role("button", name="Test this check now").click()
        page.wait_for_selector("#probe-result:not([hidden])")
        assert "ms" in page.locator("#probe-result").inner_text()
        page.click("#step-next")
        page.get_by_role("button", name="Save").click()
        page.wait_for_url("**/monitors/web/view")
        page.goto(base + "/dashboard")
        with SqliteRepository(database) as repo:
            repo.add_result(
                CheckResult(
                    monitor="web",
                    kind="tcp",
                    ok=False,
                    latency_ms=1,
                    message="connection refused",
                    severity=Severity.CRITICAL,
                    checked_at=utc_now(),
                )
            )
        page.wait_for_selector("text=connection refused", timeout=12_000)
        with SqliteRepository(database) as repo:
            repo.open_incident("web", utc_now(), "connection refused", "tcp")
        page.goto(base + "/incidents")
        page.locator("table a").click()
        assert "Detected" in page.content()
        assert "Check that the program is running" in page.content()
    finally:
        browser.close()
        playwright.stop()
        server.shutdown()
