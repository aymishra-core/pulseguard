"""Kind-specific monitor form rules, the URL fix, and the dependency picker."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import pulseguard.checks.models  # noqa: F401  # import checks before storage
from pulseguard.storage import SqliteRepository
from pulseguard.storage.records import MonitorRecord
from pulseguard.web.app import create_app
from pulseguard.web.field_rules import (
    DNS_MESSAGE,
    PORT_MESSAGE,
    TCP_MESSAGE,
    TLS_MESSAGE,
    URL_MESSAGE,
    blocked_monitors,
    port_problem,
    target_message,
    target_problem,
    url_fix,
)

_FORM = Path(__file__).resolve().parents[1] / "src" / "pulseguard" / "web" / "static" / "form.js"
_PASSWORD = "s3cret-password"
_API_KEY = "example-api-key-value"


def test_each_kind_uses_one_sentence_on_the_client_and_the_server(tmp_path: Path) -> None:
    """Helper text, the client rule, and a bypassed post all use the same sentence."""
    client = _client(tmp_path / "history.db")
    fresh = client.get("/monitors/new").get_data(as_text=True)
    assert URL_MESSAGE in fresh
    assert TCP_MESSAGE not in fresh
    for kind, sentence in (
        ("http", URL_MESSAGE),
        ("keyword", URL_MESSAGE),
        ("defacement", URL_MESSAGE),
        ("tcp", TCP_MESSAGE),
        ("tls", TLS_MESSAGE),
        ("dns", DNS_MESSAGE),
    ):
        assert target_message(kind) == sentence
        assert target_problem(kind, "not a target") == sentence
        assert _js(
            "step", {"kind": kind, "name": "web", "target": "not a target", "port": "80"}
        ) == [{"field": "target", "message": sentence}]
    token = _csrf(fresh)
    refused = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="http", name="web", target="example.com"),
    )
    assert URL_MESSAGE in refused.get_data(as_text=True)
    assert refused.get_data(as_text=True).count('value="example.com"') >= 1
    tcp = client.post(
        "/monitors/new",
        data=_fields(
            csrf_token=token,
            kind="tcp",
            name="shop",
            target="https://shop.example.com/health",
            port="",
        ),
    )
    body = tcp.get_data(as_text=True)
    assert tcp.status_code == 200
    assert TCP_MESSAGE in body
    assert PORT_MESSAGE in body
    assert URL_MESSAGE not in body
    soup = BeautifulSoup(body, "html.parser")
    assert soup.select_one("#f-name")["value"] == "shop"
    assert soup.select_one("#f-target")["value"] == "https://shop.example.com/health"
    assert soup.select_one("[data-target-hint]").get_text(strip=True) == TCP_MESSAGE
    tls = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="tls", name="cert", target="https://www.example.com"),
    )
    assert TLS_MESSAGE in tls.get_data(as_text=True)
    assert URL_MESSAGE not in tls.get_data(as_text=True)
    dns = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="dns", name="names", target="https://www.example.com"),
    )
    assert DNS_MESSAGE in dns.get_data(as_text=True)
    assert URL_MESSAGE not in dns.get_data(as_text=True)
    saved = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="tcp", name="port", target="127.0.0.1", port="9"),
    )
    assert saved.status_code == 302
    page = client.get("/monitors/port").get_data(as_text=True)
    assert TCP_MESSAGE in page
    assert 'placeholder="e.g. 8798"' in page
    heart = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="heartbeat", name="job", grace_seconds="60"),
    )
    assert heart.status_code == 302
    beat = BeautifulSoup(client.get("/monitors/job").get_data(as_text=True), "html.parser")
    assert beat.select_one("[data-target-field]").has_attr("hidden")
    assert beat.select_one("#grace_seconds") is not None


def test_a_pasted_url_offers_the_host_and_port_from_that_value() -> None:
    """tcp and tls keep an explicit port, and default 80 or 443 when it is omitted."""
    cases = (
        (
            "tcp",
            "https://Example.COM/health",
            "Example.COM",
            443,
            "Use Example.COM and port 443 instead",
        ),
        (
            "tls",
            "http://127.0.0.1:8798/status",
            "127.0.0.1",
            8798,
            "Use 127.0.0.1 and port 8798 instead",
        ),
        ("dns", "https://www.example.com/a", "www.example.com", 443, "Use www.example.com instead"),
        (
            "tcp",
            "http://shop.example.com",
            "shop.example.com",
            80,
            "Use shop.example.com and port 80 instead",
        ),
    )
    for kind, raw, host, port, label in cases:
        found = url_fix(kind, raw)
        assert found == {"host": host, "port": port, "label": label}
        assert _js("fix", {"kind": kind, "value": raw}) == found
    assert url_fix("http", "https://example.com") is None
    assert url_fix("tcp", "127.0.0.1") is None
    assert _js("fix", {"kind": "tcp", "value": "127.0.0.1"}) is None


def test_tcp_port_is_required_and_tls_defaults_to_443(tmp_path: Path) -> None:
    """An empty or out-of-range tcp port is refused. A blank tls port is 443."""
    assert port_problem("tcp", "") == PORT_MESSAGE
    assert port_problem("tcp", "0") == PORT_MESSAGE
    assert port_problem("tcp", "65536") == PORT_MESSAGE
    assert port_problem("tcp", "nope") == PORT_MESSAGE
    assert port_problem("tcp", "65535") is None
    assert port_problem("tls", "") is None
    client = _client(tmp_path / "history.db")
    token = _csrf(client.get("/monitors/new").get_data(as_text=True))
    for raw in ("", "0", "65536"):
        page = client.post(
            "/monitors/new",
            data=_fields(csrf_token=token, kind="tcp", name="bare", target="127.0.0.1", port=raw),
        )
        assert PORT_MESSAGE in page.get_data(as_text=True)
        assert page.get_data(as_text=True).count('value="127.0.0.1"') >= 1
    saved = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="tls", name="cert", target="example.com"),
    )
    assert saved.status_code == 302
    with SqliteRepository(tmp_path / "history.db") as repo:
        assert repo.monitor_options("cert")["port"] == 443
    edit = client.get("/monitors/cert").get_data(as_text=True)
    assert TLS_MESSAGE in edit
    assert 'value="443"' in edit


def test_dependencies_hide_cycles_and_keep_the_posted_chips(tmp_path: Path) -> None:
    """The list omits this monitor and anything that already depends on it."""
    database = tmp_path / "history.db"
    client = _client(database)
    token = _csrf(client.get("/monitors/new").get_data(as_text=True))
    for name in ("root", "mid", "leaf", "free"):
        made = client.post(
            "/monitors/new",
            data=_fields(
                csrf_token=token,
                kind="http",
                name=name,
                target=f"http://127.0.0.1/{name}",
            ),
        )
        assert made.status_code == 302
    with SqliteRepository(database) as repo:
        repo.save_options("mid", {"depends_on": ["root"]})
        repo.save_options("leaf", {"depends_on": ["mid"]})
    edges = {"root": [], "mid": ["root"], "leaf": ["mid"], "free": []}
    assert set(blocked_monitors(edges, "root")) == {"mid", "leaf"}
    assert _js("blocked", {"edges": edges, "name": "root"}) == blocked_monitors(edges, "root")
    page = BeautifulSoup(client.get("/monitors/root").get_data(as_text=True), "html.parser")
    listed = {item.get_text(strip=True) for item in page.select("#dep-list [role=option]")}
    assert listed == {"free"}
    assert page.select_one("[data-self]")["data-self"] == "root"
    picked = client.post(
        "/monitors/new",
        data=_fields(
            csrf_token=token,
            kind="tcp",
            name="again",
            target="https://example.com",
            port="80",
            depends_on=["free", "root"],
        ),
    )
    kept = BeautifulSoup(picked.get_data(as_text=True), "html.parser")
    assert [item["value"] for item in kept.select("[data-dep-fields] input")] == ["free", "root"]
    assert [item.get("aria-label") for item in kept.select(".dep-chip")] == [
        "Remove free",
        "Remove root",
    ]
    assert kept.select_one("#f-target")["value"] == "https://example.com"
    names = [f"m{i}" for i in range(9)]
    for name in names:
        made = client.post(
            "/monitors/new",
            data=_fields(
                csrf_token=token,
                kind="http",
                name=name,
                target=f"http://127.0.0.1/{name}",
            ),
        )
        assert made.status_code == 302
    with SqliteRepository(database) as repo:
        repo.save_options("m0", {"depends_on": names[1:]})
    full = client.get("/monitors/m0").get_data(as_text=True)
    assert "8 of 8" in full
    assert 'id="depends_on"' in full and "disabled" in full
    assert _js("add", {"chosen": names[1:], "name": "extra"}) == names[1:]
    assert _js("remove", {"chosen": ["free", "root"], "name": "free"}) == ["root"]
    shown = _js(
        "choices",
        {"names": ["root", "mid", "free"], "chosen": ["free"], "blocked": ["mid"], "self": "root"},
    )
    assert shown == []


def test_live_dependency_chips_submit_the_names_they_show(tmp_path: Path) -> None:
    """Adding and removing a chip changes the hidden fields the form will post."""
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
    with SqliteRepository(database) as repo:
        repo.save_monitor(MonitorRecord("web", "http", "http://127.0.0.1/", 30, True))
        repo.save_monitor(MonitorRecord("shop", "http", "http://127.0.0.1/", 30, True))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    playwright = None
    browser = None
    try:
        playwright = playwright_api.sync_playwright().start()
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
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
        page.locator(".sect.on [data-next]").click()
        page.locator("#depends_on").click()
        page.locator("#dep-list [data-name=web]").click()
        assert page.locator(".dep-chip").inner_text().startswith("web")
        assert page.locator("[data-dep-fields] input").input_value() == "web"
        page.locator("#depends_on").focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        values = page.locator("[data-dep-fields] input").evaluate_all(
            "els => els.map(el => el.value)"
        )
        assert values == ["web", "shop"]
        page.locator("[aria-label='Remove web']").click()
        values = page.locator("[data-dep-fields] input").evaluate_all(
            "els => els.map(el => el.value)"
        )
        assert values == ["shop"]
        assert page.locator("#depends_on").inner_text() == "Add another dependency"
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        server.shutdown()


def test_pasted_urls_keep_the_host_and_a_dependency_is_stored(tmp_path: Path) -> None:
    """A pasted URL yields that host, and a valid post stores the chosen names."""
    assert target_problem("heartbeat", "ignored") is None
    assert target_problem("mystery", "ignored") is None
    assert target_problem("http", "https://example.com") is None
    assert target_problem("tcp", None) == TCP_MESSAGE
    assert port_problem("dns", "53") is None
    assert port_problem("tcp", 80) == PORT_MESSAGE
    assert blocked_monitors({}, None) == []
    edges = {"mid": ["root"], "leaf": ["mid", "root"]}
    assert blocked_monitors(edges, "root") == ["mid", "leaf"]
    pasted = url_fix("tcp", "https://user:secret@Example.COM:8443/health?q=1#x")
    assert pasted == {
        "host": "Example.COM",
        "port": 8443,
        "label": "Use Example.COM and port 8443 instead",
    }
    assert url_fix("tcp", "https://") is None
    assert url_fix("tcp", "http://:80/") is None
    assert url_fix("tcp", "http://[::1") is None
    assert url_fix("tcp", None) is None
    bare = url_fix("tls", "https://[::1]/status")
    numbered = url_fix("tcp", "http://[::1]:8798/")
    odd = url_fix("tcp", "http://[::1]:abc")
    assert bare == {"host": "::1", "port": 443, "label": "Use ::1 and port 443 instead"}
    assert numbered == {"host": "::1", "port": 8798, "label": "Use ::1 and port 8798 instead"}
    assert odd == {"host": "::1", "port": 80, "label": "Use ::1 and port 80 instead"}
    assert url_fix("dns", "http://[]/") is None
    client = _client(tmp_path / "history.db")
    token = _csrf(client.get("/monitors/new").get_data(as_text=True))
    for kind in ("keyword", "defacement"):
        page = client.post(
            "/monitors/new",
            data=_fields(
                csrf_token=token,
                kind=kind,
                name="page",
                target="example.com",
                keyword="ok",
            ),
        )
        body = page.get_data(as_text=True)
        assert URL_MESSAGE in body
        assert TCP_MESSAGE not in body
    made = client.post(
        "/monitors/new",
        data=_fields(csrf_token=token, kind="http", name="web", target="http://127.0.0.1/web"),
    )
    assert made.status_code == 302
    linked = client.post(
        "/monitors/new",
        data=_fields(
            csrf_token=token,
            kind="http",
            name="shop",
            target="http://127.0.0.1/shop",
            depends_on=["web"],
        ),
    )
    assert linked.status_code == 302
    with SqliteRepository(tmp_path / "history.db") as repo:
        assert repo.monitor_options("shop")["depends_on"] == ["web"]


def _js(kind: str, payload: object) -> object:
    """Run one form.js decision and return its JSON value."""
    script = r"""
const form = require(process.argv[1]);
const body = JSON.parse(process.argv[2]);
const kind = process.argv[3];
let value;
if (kind === "step") value = form.stepProblems(body);
else if (kind === "fix") value = form.urlFix(body.kind, body.value);
else if (kind === "blocked") value = form.blockedNames(body.edges, body.name);
else if (kind === "add") value = form.addChoice(body.chosen, body.name, 8);
else if (kind === "remove") value = form.removeChoice(body.chosen, body.name);
else if (kind === "choices") value = form.choices(body.names, body.chosen, body.blocked, body.self);
process.stdout.write(JSON.stringify(value));
"""
    result = subprocess.run(
        ["node", "-e", script, str(_FORM), json.dumps(payload), kind],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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


def _fields(**values: object) -> dict[str, object]:
    """Return form fields with the usual interval and private-target switch."""
    payload: dict[str, object] = {"interval": "30", "enabled": "1", "allow_private": "1"}
    payload.update(values)
    return payload


def _csrf(html: str) -> str:
    """Read the hidden token."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)
