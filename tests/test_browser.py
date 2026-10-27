"""One browser pass through setup, login, and a new monitor.

The test is skipped when Playwright is not installed or its browser cannot start.
It is not part of the default install, so CI stays on the packages in ``.[dev]``.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

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
        page.fill("#name", "web")
        page.fill("#target", "http://127.0.0.1:9/")
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
