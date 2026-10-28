"""Problem click-through: hero wording, attention rows, filters, badges, palette."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app
from pulseguard.web.board import clock_hms, short_span
from pulseguard.web.pages import (
    _attention,
    _filter_cards,
    _history_note,
    _nav_rows,
    _uptime_key,
)

_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


def test_hero_wording_for_each_state(tmp_path: Path) -> None:
    """Each board state has one eyebrow and one headline."""
    database = tmp_path / "history.db"
    client = _client(database)
    moment = utc_now()
    with SqliteRepository(database) as repo:
        _http(repo, "web")
        _http(repo, "shop")
    first = client.get("/dashboard").get_data(as_text=True)
    assert "AWAITING FIRST SWEEP" in first
    assert "Awaiting first sweep" in first
    assert "Needs attention" not in first

    with SqliteRepository(database) as repo:
        repo.add_result(_result("web", True, "HTTP 200", moment, 20))
        repo.add_result(_result("shop", True, "HTTP 200", moment, 40))
    nominal = client.get("/dashboard").get_data(as_text=True)
    assert "NOMINAL" in nominal
    assert "All 2 monitors healthy" in nominal
    assert "Needs attention" not in nominal
    assert "No incidents in 30 d" in nominal
    assert "2/2 healthy" in nominal
    assert "All up" not in nominal

    with SqliteRepository(database) as repo:
        repo.add_result(_result("shop", False, "connection refused", moment, 40))
        repo.open_incident("shop", moment, "connection refused", "http")
    degraded = client.get("/dashboard").get_data(as_text=True)
    assert "DEGRADED" in degraded
    assert "1 of 2 monitors failing" in degraded
    assert 'href="/monitors/shop/view"' in degraded
    assert ">Open monitor<" in degraded
    assert "Needs attention" in degraded
    assert "failing for" in degraded
    assert ">Incident<" in degraded
    assert "1 failing" in degraded
    assert 'class="chip down"' in degraded
    shop = re.search(r'class="chip down" href="([^"]+)"', degraded)
    assert shop is not None
    assert shop.group(1) == "/monitors/shop/view"

    with SqliteRepository(database) as repo:
        repo.add_result(_result("web", False, "connection refused", moment, 20))
        repo.open_incident("web", moment, "connection refused", "http")
    outage = client.get("/dashboard").get_data(as_text=True)
    assert "OUTAGE" in outage
    assert "2 of 2 monitors failing" in outage
    assert 'href="/monitors?status=down"' in outage
    assert "View 2 failing" in outage

    with SqliteRepository(database) as repo:
        repo.add_result(_result("shop", True, "HTTP 200", moment, 40))
        repo.add_result(_result("web", True, "HTTP 200", moment, 20))
    waiting = client.get("/dashboard").get_data(as_text=True)
    assert "Incident open, awaiting next check" in waiting
    assert "DEGRADED" in waiting
    assert "2 open" in waiting
    assert "2/2 healthy" not in waiting

    quiet = moment - timedelta(days=2, hours=1)
    with SqliteRepository(database) as repo:
        repo.resolve_incident("shop", quiet)
        repo.resolve_incident("web", quiet)
    settled = client.get("/dashboard").get_data(as_text=True)
    assert "All 2 monitors healthy" in settled
    assert "Last incident 2 days ago" in settled
    assert "Needs attention" not in settled


def test_attention_lists_a_paused_incident_and_an_expiring_certificate(tmp_path: Path) -> None:
    """A paused open incident and a certificate inside 30 days each get a row."""
    database = tmp_path / "history.db"
    client = _client(database)
    moment = utc_now()
    soon = (moment + timedelta(days=10)).date().isoformat()
    with SqliteRepository(database) as repo:
        _http(repo, "hold", enabled=False)
        repo.open_incident("hold", moment, "connection refused", "http")
        repo.save_monitor(MonitorRecord("cert", "tls", "example.com", 3600, True))
        repo.add_result(_result("cert", True, f"expires {soon}", moment, 8, kind="tls"))
        later = (moment + timedelta(days=90)).date().isoformat()
        repo.save_monitor(MonitorRecord("later", "tls", "example.com", 3600, True))
        repo.add_result(_result("later", True, f"expires {later}", moment, 8, kind="tls"))
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Needs attention" in page
    assert ">Paused<" in page
    assert ">Expiring<" in page
    assert "expires in" in page
    assert 'href="/monitors/hold/view"' in page
    assert 'href="/monitors/cert/view"' in page
    assert 'class="badge warn">1<' in page


def test_links_badges_and_filters_agree(tmp_path: Path) -> None:
    """A failing monitor is one click away, and the query selects the tab."""
    database = tmp_path / "history.db"
    client = _client(database)
    moment = utc_now()
    earlier = moment - timedelta(minutes=5)
    with SqliteRepository(database) as repo:
        _http(repo, "web")
        _http(repo, "shop")
        repo.add_result(_result("web", True, "HTTP 200", earlier, 10))
        repo.add_result(_result("shop", True, "HTTP 200", earlier, 10))
        repo.add_result(_result("web", True, "HTTP 200", moment, 12))
        repo.add_result(_result("shop", False, "page content changed", moment, 400))
        repo.open_incident("shop", moment, "page content changed", "http")
        ended = moment - timedelta(days=2, hours=2)
        repo.open_incident("web", ended - timedelta(hours=1), "connection refused", "http")
        repo.resolve_incident("web", ended)
    board = client.get("/dashboard").get_data(as_text=True)
    assert 'class="banner-name" href="/monitors/shop/view"' in board
    assert "failing: page content changed" in board
    assert ">Open monitor<" in board
    assert 'href="/monitors"' in board
    assert 'href="/incidents?status=open"' in board
    assert "sort=uptime" in board
    assert "sort=latency" in board
    assert 'href="/incidents/' in board
    assert 'class="badge">1<' in board
    assert board.count('class="badge"') == 2

    down = client.get("/monitors?status=down").get_data(as_text=True)
    again = client.get("/monitors?status=down").get_data(as_text=True)
    assert 'aria-selected="true">Down' in down
    assert again.split('id="mons"', 1)[1] == down.split('id="mons"', 1)[1]
    cards = down.split('id="mons"', 1)[1].split("</section>", 1)[0]
    assert "shop" in cards
    assert "web" not in cards

    ordered = client.get("/monitors?sort=latency").get_data(as_text=True)
    names = re.findall(r'class="mon-name" href="[^"]+">([^<]+)', ordered.split('id="mons"', 1)[1])
    assert names[:2] == ["shop", "web"]

    resolved = client.get("/incidents?status=resolved").get_data(as_text=True)
    assert 'aria-selected="true">Resolved' in resolved
    table = resolved.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert "web" in table
    assert "shop" not in table

    detail = client.get("/monitors/web/view").get_data(as_text=True)
    assert 'href="/monitors">Monitors</a>' in detail
    assert 'href="/monitors/web/view">web</a>' in detail
    assert 'aria-current="page"' in detail
    assert "Back to monitors" in detail
    assert 'id="monitor-switch"' in detail
    assert 'aria-label="Previous monitor"' in detail
    assert 'aria-label="Next monitor"' not in detail
    fresh = client.get("/monitors/new").get_data(as_text=True)
    assert 'aria-current="page"' in fresh
    incident_id = re.search(r'href="/incidents/(\d+)"', board)
    assert incident_id is not None
    incident = client.get(f"/incidents/{incident_id.group(1)}").get_data(as_text=True)
    assert 'href="/incidents">Incidents</a>' in incident
    assert f'href="/incidents/{incident_id.group(1)}"' in incident


def test_age_filter_and_history_helpers() -> None:
    """Quiet branches: ages, a bad timestamp, and the monitor sort keys."""
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    cards = [
        {"name": "b", "state": "up", "tone": "up", "uptime": "99%", "latency": 10},
        {"name": "a", "state": "down", "tone": "down", "uptime": "—", "latency": None},
        {"name": "c", "state": "up", "tone": "warn", "uptime": "80%", "latency": 50},
    ]
    attn, status, ordered = _filter_cards(cards, "attn", "name")
    assert (status, ordered) == ("attn", "name")
    assert [card["name"] for card in attn] == ["a", "c"]
    by_uptime, _, sort = _filter_cards(cards, "nope", "uptime")
    assert sort == "uptime"
    assert [card["name"] for card in by_uptime] == ["c", "b", "a"]
    assert _uptime_key({"uptime": "—"}) == 101.0
    assert short_span(now + timedelta(seconds=5), now) == "0 s"
    assert short_span(now - timedelta(days=2), now) == "2 d"
    assert short_span(now - timedelta(hours=3), now) == "3 h"
    assert short_span(now - timedelta(minutes=14), now) == "14 m"
    assert clock_hms("") == "—"
    assert clock_hms(now) == "12:00:00"

    shop = MonitorRecord("shop", "http", "http://127.0.0.1/", 30, True)
    hold = MonitorRecord("hold", "http", "http://127.0.0.1/", 30, False)
    cert = MonitorRecord("cert", "tls", "example.com", 3600, True)
    later = MonitorRecord("later", "tls", "example.com", 3600, True)
    blank = MonitorRecord("blank", "tls", "example.com", 3600, True)
    down = _result("shop", False, "connection refused", now, 12)
    expiring = _result("cert", True, "expires 2026-11-01", now, 8, kind="tls")
    distant = _result("later", True, "expires 2027-10-28", now, 8, kind="tls")
    rows = _attention(
        [shop, hold, cert, later, blank],
        {"shop": down, "cert": expiring, "later": distant},
        [_Row("hold", "not-a-time", 4), _Row("shop", "2026-10-28T10:00:00", 2)],
        now,
    )
    assert {row["name"] for row in rows} == {"shop", "hold", "cert"}
    assert _nav_rows([cert, later], {"cert": expiring, "later": distant}, now)[0]["dot"] == "warn"
    repo = _Repo(
        [
            _Row("old", "", 1, ended=""),
            _Row("bad", "", 2, ended="not-a-time"),
            _Row("day", "", 3, ended="2026-10-27T12:00:00"),
        ]
    )
    assert _history_note(_Repo([]), now) == "No incidents in 30 d"
    assert _history_note(repo, now) == "No incidents in 30 d"
    assert _history_note(_Repo([_Row("day", "", 3, ended="2026-10-27T12:00:00")]), now) == (
        "Last incident 1 day ago"
    )
    assert _history_note(_Repo([_Row("day", "", 3, ended="2026-10-28T01:00:00+00:00")]), now) == (
        "Last incident today"
    )
    assert _history_note(_Repo([_Row("old", "", 1, ended="2026-01-01T00:00:00+00:00")]), now) == (
        "No incidents in 30 d"
    )


def test_palette_returns_monitors_and_incidents(tmp_path: Path) -> None:
    """The palette endpoint lists stored monitors and incidents after login."""
    database = tmp_path / "history.db"
    app = create_app(
        database=database,
        secret_key="test-secret-key-value",
        admin_user="operator",
        admin_password=_PASSWORD,
        api_key=_API_KEY,
    )
    guest = app.test_client()
    denied = guest.get("/api/palette")
    assert denied.status_code == 401
    client = app.test_client()
    _sign_in(client)
    moment = utc_now()
    with SqliteRepository(database) as repo:
        _http(repo, "shop")
        repo.open_incident("shop", moment, "connection refused", "http")
    body = client.get("/api/palette").get_json()
    assert body["monitors"] == [{"name": "shop", "href": "/monitors/shop/view"}]
    assert body["incidents"][0]["label"] == "INC-0001"
    assert body["incidents"][0]["monitor"] == "shop"
    assert body["incidents"][0]["href"] == "/incidents/1"
    assert any(page["keys"] == "g d" for page in body["pages"])


class _Row:
    """One incident row for the history and attention helpers."""

    def __init__(self, monitor: str, started_at: str, row_id: int, ended: str = "") -> None:
        self.monitor = monitor
        self.started_at = started_at
        self.id = row_id
        self.ended_at = ended


class _Repo:
    """A stand-in whose ``incidents`` returns the rows it was given."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def incidents(self) -> list[_Row]:
        return self._rows


def _http(repo: SqliteRepository, name: str, *, enabled: bool = True) -> None:
    """Store one HTTP monitor. Nothing is contacted."""
    repo.save_monitor(MonitorRecord(name, "http", "http://127.0.0.1/", 30, enabled))


def _result(
    monitor: str,
    ok: bool,
    message: str,
    moment: datetime,
    latency: float,
    *,
    kind: str = "http",
) -> CheckResult:
    """Return one stored result."""
    return CheckResult(
        monitor=monitor,
        kind=kind,
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
    _sign_in(client)
    return client


def _sign_in(client) -> None:
    """Create the account and sign in."""
    page = client.get("/setup")
    token = _csrf(page.get_data(as_text=True))
    client.post("/setup", data={"csrf_token": token, "username": "ada", "password": "long-enough"})
    login = client.get("/login")
    token = _csrf(login.get_data(as_text=True))
    client.post("/login", data={"csrf_token": token, "username": "ada", "password": "long-enough"})


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
