"""One dashboard snapshot, a quiet poll, and the brand link."""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

from pulseguard.checks.models import CheckResult, Severity
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app
from pulseguard.web.board import clock_span, local_day_start, mean_successful_ms
from pulseguard.web.pages import build_snapshot

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"
_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
_LIVE = Path(__file__).resolve().parents[1] / "src" / "pulseguard" / "web" / "static" / "live.js"
_PAGES = (
    "/dashboard",
    "/monitors",
    "/monitors/new",
    "/monitors/shop/view",
    "/incidents",
    "/certificates",
    "/alerts",
    "/sla",
    "/agents",
    "/data",
    "/settings",
    "/help",
)


def test_one_snapshot_fills_the_hero_tiles_chip_and_badge(tmp_path: Path) -> None:
    """The same fixture prints one count in every dashboard widget."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "shop")
        _monitor(repo, "hold", enabled=False)
        repo.add_result(_result("shop", True, "HTTP 200", _NOW - timedelta(hours=2), 100))
        repo.add_result(_result("shop", True, "monitoring gap", _NOW - timedelta(minutes=6), 50))
        repo.add_result(
            _result("shop", False, "connection refused", _NOW - timedelta(minutes=5), 9000)
        )
        repo.add_result(_result("hold", True, "HTTP 200", _NOW, 20))
        repo.open_incident("shop", _NOW - timedelta(minutes=14), "connection refused", "http")
        repo.open_incident("hold", _NOW - timedelta(hours=1), "paused", "http")
        snap = build_snapshot(repo, _NOW)
    assert snap["checks_today"] == 4
    assert snap["mean_latency_ms"] == 60
    assert snap["open_incident_count"] == 1
    assert len(snap["open_incidents"]) == 1
    assert snap["counts"]["down"] == 1
    assert snap["counts"]["up"] == 0
    assert snap["counts"]["paused"] == 1
    assert snap["last_sweep_at"].startswith("2026-10-29T11:55")
    assert snap["log"][0]["mark"] in {"PASS", "FAIL", "GAP"}
    assert "placeholder" not in str(snap["log"])

    page = client.get("/dashboard").get_data(as_text=True)
    body = client.get("/api/dashboard").get_json()
    soup = BeautifulSoup(page, "html.parser")
    assert _values(soup, "checks") == {str(body["checks_today"])}
    assert _values(soup, "latency") == {str(body["mean_latency_ms"])}
    assert _values(soup, "open") == {str(len(body["open_incidents"]))}
    assert soup.select_one("[data-snap=chip-label]").get_text(strip=True) == "1 failing"
    assert soup.select_one("[data-snap=badge-fail]").get_text(strip=True) == "1"
    assert soup.select_one("[data-snap=badge-open]").get_text(strip=True) == "1"
    assert soup.select_one("[data-snap=mix]").get_text(strip=True) == "0 up · 1 down"
    assert "None open" not in soup.select_one("[data-snap=incident-sub]").get_text()
    assert "since" in soup.select_one("[data-snap=incident-sub]").get_text()
    assert "Open for" in soup.select_one("[data-snap=aside]").get_text()


def test_a_resolved_incident_does_not_count_as_open(tmp_path: Path) -> None:
    """Zero open incidents and a resolved incident today stay in agreement."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "shop")
        repo.add_result(_result("shop", True, "HTTP 200", _NOW, 80))
        repo.open_incident("shop", _NOW - timedelta(hours=3), "connection refused", "http")
        repo.resolve_incident("shop", _NOW - timedelta(hours=1))
    page = client.get("/dashboard").get_data(as_text=True)
    soup = BeautifulSoup(page, "html.parser")
    assert _values(soup, "open") == {"0"}
    assert soup.select_one("[data-snap=incident-sub]").get_text(strip=True) == "None open"
    note = soup.select_one("[data-snap=aside]").get_text(" ", strip=True)
    assert "ago, resolved" in note
    assert "Open for" not in note
    assert soup.select_one("[data-snap=badge-open]") is None
    assert "None open" in page
    assert page.count('data-snap="open"') == 2


def test_generated_at_moves_forward_and_an_unchanged_etag_is_304(tmp_path: Path) -> None:
    """A later clock is newer, and a second read of the same rows costs a 304."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "shop")
        repo.add_result(_result("shop", True, "HTTP 200", _NOW, 40))
        first = build_snapshot(repo, _NOW)
        second = build_snapshot(repo, _NOW + timedelta(milliseconds=5))
    assert second["generated_ms"] > first["generated_ms"]
    assert second["generated_at"] > first["generated_at"]
    opened = client.get("/api/dashboard")
    tag = opened.headers["ETag"]
    again = client.get("/api/dashboard", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert again.headers["ETag"] == tag
    assert again.data == b""
    quiet = client.get("/api/dashboard?offset=nope")
    far = client.get("/api/dashboard?offset=9999")
    assert quiet.get_json()["checks_today"] == opened.get_json()["checks_today"]
    assert far.get_json()["checks_today"] == opened.get_json()["checks_today"]


def test_activity_since_returns_newer_rows_in_event_order(tmp_path: Path) -> None:
    """A later insert with an earlier time stays behind the newer event."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "shop")
        _monitor(repo, "web")
        repo.add_result(_result("web", False, "connection refused", _NOW + timedelta(seconds=2), 9))
        repo.add_result(_result("shop", True, "HTTP 200", _NOW, 11))
        repo.add_result(_result("web", True, "HTTP 200", _NOW + timedelta(seconds=1), 12))
    payload = client.get("/api/activity?since=1").get_json()
    assert [row["name"] for row in payload["events"]] == ["web", "web"]
    assert [row["at"] for row in payload["events"]] == sorted(
        row["at"] for row in payload["events"]
    )
    assert payload["events"][0]["id"] == 2
    assert client.get("/api/activity?since=3").get_json()["events"] == []
    assert client.get("/api/activity?since=nope").get_json()["events"][0]["name"] == "shop"
    guest = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    ).test_client()
    assert guest.get("/api/activity?since=1").status_code == 401


def test_brand_links_to_the_dashboard_on_every_shell_page(tmp_path: Path) -> None:
    """The mark is a real link, including the pages behind the collapsed sidebar."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        _monitor(repo, "shop")
    for path in _PAGES:
        page = client.get(path)
        assert page.status_code == 200, path
        soup = BeautifulSoup(page.get_data(as_text=True), "html.parser")
        brand = soup.select_one("a.brand")
        assert brand is not None, path
        assert brand.get("href") == "/dashboard"
        assert brand.get("aria-label") == "PulseGuard dashboard"


def test_check_all_now_reports_progress_as_json(tmp_path: Path) -> None:
    """A dashboard button can ask for JSON. A normal form still redirects."""
    database = tmp_path / "history.db"
    client = _client(database)
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("port", "tcp", "127.0.0.1", 30, True))
        repo.save_options("port", {"port": 1, "timeout": 1, "allow_private": True})
    token = _csrf(client.get("/dashboard").get_data(as_text=True))
    progress = client.post(
        "/monitors/run",
        data={"csrf_token": token},
        headers={"Accept": "application/json"},
    )
    assert progress.status_code == 200
    assert progress.get_json() == {"ran": 1, "total": 1}
    redirected = client.post("/monitors/run", data={"csrf_token": token}, follow_redirects=True)
    assert b"Ran 1" in redirected.data


def test_live_decisions_ignore_an_older_snapshot_and_mark_staleness() -> None:
    """The browser applies only a newer snapshot and goes stale at 15 seconds."""
    script = r"""
const live = require(process.argv[1]);
function assert(cond, msg) { if (!cond) { console.error(msg); process.exit(1); } }
assert(live.shouldApply(10, 11) === true, "newer");
assert(live.shouldApply(11, 10) === false, "older");
assert(live.shouldApply(11, 11) === false, "same");
assert(live.isStale(14999) === false, "fresh");
assert(live.isStale(15000) === true, "stale");
assert(live.backoffMs(0) === 5000 && live.backoffMs(1) === 10000, "backoff");
assert(live.backoffMs(2) === 20000 && live.backoffMs(9) === 20000, "cap");
assert(live.pollMs(0, 0) === 5000, "calm");
assert(live.pollMs(1, 0) === 3000 && live.pollMs(0, 2) === 3000, "urgent");
assert(live.liveLabel(1000, 1000, true) === "Connection lost, retrying", "lost");
assert(live.liveLabel(1000, 1500, false) === "Live · updated just now", "now");
assert(live.liveLabel(1000, 4000, false) === "Live · updated 3s ago", "ago");
assert(live.dueLabel(null, 1000) === "due now", "missing");
assert(live.dueLabel(500, 1000) === "due now", "late");
assert(live.dueLabel(5000, 1000) === "4s", "seconds");
const rows = live.newerRows([{id:1,at:"b"},{id:3,at:"a"},{id:2,at:"c"}], 1);
assert(rows.map(function (row) { return row.id; }).join() === "3,2", "order");
const added = live.mergeChecks([{id:1},{id:2}], [{id:5},{id:4},{id:3},{id:2}], 50);
assert(added.map(function (row) { return row.id; }).join() === "5,4,3,1,2", "prepend");
const held = [];
for (let id = 48; id >= 1; id -= 1) held.push({id: id});
const capped = live.mergeChecks(held, [{id: 51}, {id: 50}, {id: 49}], 50);
assert(capped.length === 50 && capped[0].id === 51 && capped[49].id === 2, "cap");
assert(live.logBadge(1000, 1000, false, 5000) === "streaming", "live");
assert(live.logBadge(1000, 11000, false, 5000) === "streaming", "edge");
assert(live.logBadge(1000, 11001, false, 5000) === "reconnecting", "outage");
assert(live.logBadge(1000, 1000, true, 5000) === "paused", "hidden");
"""
    result = subprocess.run(
        ["node", "-e", script, str(_LIVE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_clock_and_mean_helpers_follow_one_definition() -> None:
    """Local midnight, a clamped duration, and the successful-check mean."""
    assert clock_span(_NOW - timedelta(seconds=14 * 60 + 7), _NOW) == "00:14:07"
    assert clock_span(_NOW + timedelta(seconds=5), _NOW) == "00:00:00"
    start = local_day_start(_NOW, 180)
    assert start.isoformat().startswith("2026-10-29T00:00:00")
    assert mean_successful_ms([]) is None
    rows = [
        _result("shop", True, "HTTP 200", _NOW, 100),
        _result("shop", False, "connection refused", _NOW, 9000),
        _result("shop", True, "monitoring gap", _NOW, 50),
    ]
    assert mean_successful_ms(rows) == 100


def _values(soup: BeautifulSoup, name: str) -> set[str]:
    """Return the visible text of one snapshot field."""
    return {node.get_text(strip=True) for node in soup.select(f'[data-snap="{name}"]')}


def _monitor(repo: SqliteRepository, name: str, *, enabled: bool = True) -> None:
    """Store one HTTP monitor. Nothing is contacted."""
    repo.save_monitor(MonitorRecord(name, "http", "http://127.0.0.1/", 30, enabled))


def _result(
    monitor: str,
    ok: bool,
    message: str,
    moment: datetime,
    latency: float,
) -> CheckResult:
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


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
