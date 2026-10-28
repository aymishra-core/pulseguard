"""A live payload paints the same status nodes as a fresh page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_NAMES = ("web", "shop", "api")
_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_a_live_payload_matches_the_server_page(tmp_path: Path) -> None:
    """Failing, healthy, and one-of-three payloads leave no stale status nodes."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    from threading import Thread

    from werkzeug.serving import make_server

    database = tmp_path / "state.db"
    _shift(database, set(_NAMES), _NOW)
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
            page.add_init_script("window.__pgHold = true")
            page.goto(base + "/setup")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/login")
            page.fill('input[name="username"]', "ada")
            page.fill('input[name="password"]', "long-enough")
            page.click('button[type="submit"]')
            page.wait_for_url("**/dashboard")
            page.wait_for_timeout(1000)
            _shift(database, set(), _NOW + timedelta(minutes=1))
            _same(page, "/dashboard")
            _shift(database, set(_NAMES), _NOW + timedelta(minutes=2))
            _same(page, "/dashboard")
            _shift(database, {"shop"}, _NOW + timedelta(minutes=3))
            _same(page, "/dashboard")
            page.goto(base + "/alerts")
            page.wait_for_url("**/alerts")
            _shift(database, set(), _NOW + timedelta(minutes=4))
            _same(page, "/alerts")
            browser.close()
    finally:
        server.shutdown()


def _same(page: object, path: str) -> None:
    """Apply the current payload and require the status DOM to match ``path``."""
    fresh = page.evaluate(  # type: ignore[attr-defined]
        """async (path) => {
          const payload = await (await fetch("/api/dashboard?offset=0")).json();
          const html = await (await fetch(path)).text();
          applyState(payload);
          return {html: html, live: document.documentElement.outerHTML};
        }""",
        path,
    )
    assert _status(fresh["live"]) == _status(fresh["html"])


def _status(html: str) -> dict[str, object]:
    """Return the status nodes a live update and a reload must share."""
    soup = BeautifulSoup(html, "html.parser")
    banner = soup.select_one("[data-state=banner]")
    hero = soup.select_one(".hero")
    icon = soup.select_one("#hero-ring use")
    subline = soup.select_one("[data-state=subline]")
    chip = soup.select_one("[data-snap=chip]")
    opened = soup.select_one("[data-opened]")
    return {
        "banner": None if banner is None else _words(banner.get_text(" ", strip=True)),
        "banner_hrefs": [] if banner is None else [a.get("href") for a in banner.select("a")],
        "hero_class": "" if hero is None else " ".join(hero.get("class") or []),
        "ring": "" if hero is None else hero.get("data-ring") or "",
        "icon": "" if icon is None else icon.get("href") or "",
        "title": _words(_text(soup, "[data-snap=hero-title]")),
        "eye": _words(_text(soup, "[data-snap=hero-eye]")),
        "subline": "" if subline is None else _words(subline.get_text(" ", strip=True)),
        "sub_href": ""
        if subline is None or subline.select_one("a") is None
        else subline.select_one("a").get("href") or "",
        "actions": [
            {
                "label": a.get_text(strip=True),
                "href": a.get("href"),
                "class": " ".join(a.get("class") or []),
            }
            for a in soup.select("[data-state=actions] a")
        ],
        "chip": _words(_text(soup, "[data-snap=chip-label]")),
        "chip_href": "" if chip is None else chip.get("href") or "",
        "chip_class": "" if chip is None else " ".join(chip.get("class") or []),
        "badges": {
            "fail": _text(soup, "[data-snap=badge-fail]"),
            "open": _text(soup, "[data-snap=badge-open]"),
            "cert": _text(soup, "[data-snap=badge-cert]"),
        },
        "dots": [item for item in (_dot(link) for link in soup.select(".nav-mon")) if item],
        "tabs": [_words(node.get_text(" ", strip=True)) for node in soup.select("[data-tab]")],
        "cards": [
            {
                "name": card.select_one(".mon-name").get_text(strip=True),
                "st": card.get("data-st") or "",
                "pill": card.select_one(".pill").get_text(strip=True),
                "pill_class": " ".join(card.select_one(".pill").get("class") or []),
                "msg": _words(card.select_one(".mon-msg").get_text(" ", strip=True)),
            }
            for card in soup.select("#mons .mon")
        ],
        "mix": _text(soup, "[data-snap=mix]"),
        "open": _text(soup, "[data-snap=open]"),
        "sweep": _text(soup, "[data-snap=sweep]"),
        "checks": _text(soup, "[data-kpi=checks]"),
        "latency": _words(_text(soup, "[data-kpi=latency]")),
        "total": _text(soup, "[data-snap=total]"),
        "uptime": _text(soup, "[data-snap=uptime]"),
        "cert": _text(soup, "[data-snap=cert]"),
        "opened": "" if opened is None else opened.get("data-opened") or "",
        "history": _text(soup, "[data-snap=history]"),
        "attention": [
            _words(row.get_text(" ", strip=True)) for row in soup.select("[data-attention] li")
        ],
    }


def _dot(link: Any) -> str:
    """Return one sidebar monitor as ``name:dot classes``, or an empty string."""
    mark = link.select_one(".dot-s")
    if mark is None:
        return ""
    classes = " ".join(mark.get("class") or [])
    return f"{link.get_text(strip=True)}:{classes}"


def _text(soup: BeautifulSoup, selector: str) -> str:
    """Return one node's text, or an empty string when the node is gone."""
    node = soup.select_one(selector)
    if node is None:
        return ""
    return node.get_text(strip=True)


def _words(text: str) -> str:
    """Collapse whitespace so a reload and a live paint compare the same."""
    return " ".join(text.split())


def _shift(database: Path, failing: set[str], moment: datetime) -> None:
    """Store one new result per monitor and open or close its incident."""
    with SqliteRepository(database) as repo:
        saved = {row.name for row in repo.monitors()}
        for name in _NAMES:
            if name not in saved:
                repo.save_monitor(MonitorRecord(name, "http", "http://127.0.0.1/", 30, True))
        open_names = set(repo.open_incident_names())
        for name in _NAMES:
            down = name in failing
            repo.add_result(
                CheckResult(
                    monitor=name,
                    kind="http",
                    ok=not down,
                    latency_ms=20,
                    message="connection refused" if down else "HTTP 200",
                    severity=Severity.CRITICAL if down else Severity.OK,
                    checked_at=moment,
                )
            )
            if down and name not in open_names:
                repo.open_incident(name, moment, "connection refused", "check")
            if not down and name in open_names:
                repo.resolve_incident(name, moment)
