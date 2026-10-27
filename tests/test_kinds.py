"""Behaviour of the checks added in phase 2. Targets stay on the loopback interface."""

from __future__ import annotations

import hashlib
import socket
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest

from pulseguard.checks import (
    DefacementCheck,
    DnsCheck,
    HeartbeatCheck,
    KeywordCheck,
    TcpCheck,
    TlsExpiryCheck,
    build_check,
)
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.exceptions import ConfigValidationError
from pulseguard.notifiers import ConsoleNotifier
from pulseguard.notifiers.console_notifier import stderr_notifier
from tests.local_tls import LocalTlsServer
from tests.mock_servers import PAGE_BODY, MockHttpServer

_OK_HASH = hashlib.sha256(b"ok").hexdigest()


def test_keyword_match_miss_and_size_limit() -> None:
    """A local page proves the keyword, a miss, and the body cap."""
    with MockHttpServer() as server:
        found = KeywordCheck("page", f"{server.base_url}/ok", keyword="ok", retries=1)
        success = found.run()
        assert success.ok is True
        assert success.kind == "keyword"
        assert success.message == "HTTP 200 contains the keyword"
        assert "ok" not in success.message

        missing = KeywordCheck("page", f"{server.base_url}/ok", keyword="canary-phrase", retries=1)
        failed = missing.run()
        assert failed.ok is False
        assert failed.message == "keyword not found"
        assert "canary-phrase" not in failed.message

        down = KeywordCheck("page", f"{server.base_url}/missing", keyword="ok", retries=1)
        status = down.run()
        assert status.status_code == 404
        assert status.message == "expected HTTP 200 but got 404"

        tiny = KeywordCheck(
            "page",
            f"{server.base_url}/ok",
            keyword="ok",
            retries=1,
            max_body_bytes=1,
        )
        oversized = tiny.run()
        assert oversized.ok is False
        assert "exceeds 1 bytes" in oversized.message


def test_keyword_and_defacement_use_visible_text() -> None:
    """Script, style, and attributes are not the keyword. The hash stays on the raw bytes."""
    digest = hashlib.sha256(PAGE_BODY).hexdigest()
    with MockHttpServer() as server:
        found = KeywordCheck("page", f"{server.base_url}/page", keyword="ok", retries=1)
        assert found.run().ok is True
        hidden = KeywordCheck("page", f"{server.base_url}/page", keyword="secretword", retries=1)
        missed = hidden.run()
        assert missed.ok is False
        assert missed.message == "keyword not found"
        styled = KeywordCheck("page", f"{server.base_url}/page", keyword="color", retries=1)
        assert styled.run().message == "keyword not found"
        matched = DefacementCheck(
            "page",
            f"{server.base_url}/page",
            keyword="ok",
            expected_sha256=digest,
            alert_after=1,
            retries=1,
        )
        assert matched.run().message == "content matches"
        keyword_only = DefacementCheck(
            "page",
            f"{server.base_url}/page",
            keyword="secretword",
            expected_sha256=digest,
            alert_after=1,
            retries=1,
        )
        assert keyword_only.run().message == "keyword missing (1 of 1)"


def test_defacement_streak_uses_the_mixin() -> None:
    """Hash mismatches warn until ``alert_after``, then become critical."""
    with MockHttpServer() as server:
        check = DefacementCheck(
            "page",
            f"{server.base_url}/ok",
            keyword="ok",
            expected_sha256="a" * 64,
            alert_after=2,
            retries=1,
        )
        first = check.run()
        second = check.run()
        assert first.severity == Severity.WARNING
        assert first.message == "body hash differs (1 of 2)"
        assert second.severity == Severity.CRITICAL
        assert second.message == "body hash differs (2 of 2)"
        check.expected_sha256 = _OK_HASH
        restored = check.run()
        assert restored.ok is True
        assert restored.message == "content matches"
        assert check._streak == 0


def test_tcp_connects_locally_and_reports_refusal_without_the_host() -> None:
    """The mock server accepts TCP; a closed port does not."""
    with MockHttpServer() as server:
        host, port_text = server.base_url.removeprefix("http://").split(":")
        opened = TcpCheck("port", host, port=int(port_text), timeout=2)
        assert opened.run().ok is True
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    closed_port = int(sock.getsockname()[1])
    sock.close()
    refused = TcpCheck("port", "127.0.0.1", port=closed_port, timeout=2).run()
    assert refused.ok is False
    assert refused.message == "connection failed: ConnectionRefusedError"
    assert "127.0.0.1" not in refused.message
    with pytest.raises(ConfigValidationError):
        TcpCheck("port", "127.0.0.1", port=9, allow_private=False)


def test_dns_uses_localhost_and_an_injected_resolver() -> None:
    """localhost is resolved locally. Failures use an injected resolver."""
    previous = socket.getdefaulttimeout()
    answers = {
        item[4][0] for item in socket.getaddrinfo("localhost", None, type=socket.SOCK_STREAM)
    }
    expected = "127.0.0.1" if "127.0.0.1" in answers else "::1"
    check = DnsCheck("name", "localhost", expected=[expected], timeout=2)
    assert check.run().ok is True
    assert socket.getdefaulttimeout() == previous

    def explode(_host: str, _timeout: float) -> list[str]:
        raise TimeoutError("slow")

    timed_out = DnsCheck("name", "localhost", expected=[expected], resolver=explode, timeout=2)
    assert "timed out" in timed_out.run().message

    def fail(_host: str, _timeout: float) -> list[str]:
        raise socket.gaierror("no")

    failed = DnsCheck("name", "localhost", expected=[expected], resolver=fail, timeout=2)
    assert failed.run().message == "name lookup failed"

    def elsewhere(_host: str, _timeout: float) -> list[str]:
        return ["192.0.2.1"]

    mismatched = DnsCheck("name", "localhost", expected=[expected], resolver=elsewhere, timeout=2)
    assert mismatched.run().ok is False
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "localhost", expected=["127.0.0.1"], allow_private=False)


def test_tls_expiry_windows_against_a_local_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A locally generated certificate walks the healthy, warning, and expired paths."""
    with LocalTlsServer() as server:
        check = TlsExpiryCheck(
            "cert",
            "127.0.0.1",
            port=server.port,
            server_name="127.0.0.1",
            ca_file=str(server.certificate),
            timeout=5,
        )
        healthy = check.run()
        assert healthy.ok is True
        assert healthy.message.startswith("certificate valid until ")
        expires = datetime.fromisoformat(healthy.message.removeprefix("certificate valid until "))

        def at(moment: datetime) -> datetime:
            return moment

        monkeypatch.setattr(
            "pulseguard.checks.tls_check.utc_now",
            lambda: at(expires - timedelta(days=10)),
        )
        warning = check.run()
        assert warning.ok is False
        assert warning.severity == Severity.WARNING
        assert "within 14 days" in warning.message

        monkeypatch.setattr(
            "pulseguard.checks.tls_check.utc_now",
            lambda: at(expires - timedelta(hours=12)),
        )
        critical = check.run()
        assert critical.severity == Severity.CRITICAL
        assert "within 1 day" in critical.message

        monkeypatch.setattr(
            "pulseguard.checks.tls_check.utc_now",
            lambda: at(expires + timedelta(days=1)),
        )
        expired = check.run()
        assert expired.ok is False
        assert expired.message.startswith("certificate expired")
        assert "127.0.0.1" not in expired.message


def test_heartbeat_grace_hides_the_token() -> None:
    """No ping is a failure. A fresh ping passes. A late ping fails. The token stays out."""
    now = datetime(2026, 10, 26, tzinfo=UTC)
    current = {"at": now}
    token = HeartbeatCheck.new_token()
    other = HeartbeatCheck.new_token()
    assert token != other
    check = HeartbeatCheck(
        "job",
        token,
        grace_seconds=30,
        clock=lambda: current["at"],
    )
    missed = check.run()
    assert missed.ok is False
    assert missed.message == "no ping recorded"
    assert token not in missed.message
    check.record_ping()
    assert check.run().ok is True
    current["at"] = now + timedelta(seconds=31)
    late = check.run()
    assert late.ok is False
    assert token not in late.message
    with pytest.raises(ConfigValidationError):
        HeartbeatCheck("job", "short-token", grace_seconds=30)
    loaded = build_check(
        {
            "kind": "heartbeat",
            "name": "job",
            "target": token,
            "grace_seconds": 30,
            "interval": 60,
        }
    )
    assert isinstance(loaded, HeartbeatCheck)
    assert loaded.token == token
    with pytest.raises(ConfigValidationError):
        check.record_ping(datetime(2026, 10, 26))
    with pytest.raises(ConfigValidationError):
        HeartbeatCheck("job", token, grace_seconds=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HeartbeatCheck("job", token, grace_seconds=10)


def test_bad_config_is_rejected_before_a_connection() -> None:
    """Constructors reject malformed monitors without opening a socket."""
    with pytest.raises(ConfigValidationError):
        KeywordCheck("page", "http://127.0.0.1/health", keyword="ok", method="HEAD")
    with pytest.raises(ConfigValidationError):
        KeywordCheck("page", "http://127.0.0.1/health", keyword="  ok")
    with pytest.raises(ConfigValidationError):
        KeywordCheck("page", "http://127.0.0.1/health", keyword=1)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        KeywordCheck("page", "http://127.0.0.1/health", keyword="ok", max_body_bytes=0)
    with pytest.raises(ConfigValidationError):
        DefacementCheck(
            "page",
            "http://127.0.0.1/health",
            keyword="ok",
            expected_sha256="zz",
            alert_after=3,
        )
    with pytest.raises(ConfigValidationError):
        DefacementCheck(
            "page",
            "http://127.0.0.1/health",
            keyword="ok",
            expected_sha256=_OK_HASH,
            alert_after=0,
        )
    with pytest.raises(ConfigValidationError):
        DefacementCheck(
            "page",
            "http://127.0.0.1/health",
            keyword="ok",
            expected_sha256=_OK_HASH,
            alert_after=21,
        )
    with pytest.raises(ConfigValidationError):
        DefacementCheck(
            "page",
            "http://127.0.0.1/health",
            keyword="ok",
            expected_sha256=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigValidationError, match="missing"):
        build_check({"kind": "defacement", "name": "page", "target": "http://127.0.0.1/"})
    with pytest.raises(ConfigValidationError):
        TcpCheck("port", "http://127.0.0.1", port=9)
    with pytest.raises(ConfigValidationError):
        TcpCheck("port", "127.0.0.1", port=9, allow_private="yes")  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        TcpCheck("port", "127.0.0.1", port=0)
    with pytest.raises(ConfigValidationError, match="missing port"):
        build_check({"kind": "tcp", "name": "port", "target": "127.0.0.1"})
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "127.0.0.1", expected=["127.0.0.1"])
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "localhost", expected=["not-an-ip"])
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "localhost", expected=[])
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "localhost", expected=[1])  # type: ignore[list-item]
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "http://localhost", expected=["127.0.0.1"])
    with pytest.raises(ConfigValidationError):
        DnsCheck("name", "localhost", expected=["10.0.0.1"], allow_private=False)
    duplicate = DnsCheck("name", "localhost", expected=["127.0.0.1", "127.0.0.1"])
    assert duplicate.expected == ("127.0.0.1",)
    with pytest.raises(ConfigValidationError):
        build_check({"kind": "dns", "name": "name", "target": "localhost", "expected": "127.0.0.1"})
    with pytest.raises(ConfigValidationError):
        TlsExpiryCheck("cert", "127.0.0.1", port=0)
    with pytest.raises(ConfigValidationError):
        TlsExpiryCheck("cert", "http://127.0.0.1", port=443)
    with pytest.raises(ConfigValidationError):
        TlsExpiryCheck("cert", "127.0.0.1", allow_private=False)
    with pytest.raises(ConfigValidationError):
        TlsExpiryCheck("cert", "127.0.0.1", ca_file="/no/such/ca.pem")
    with pytest.raises(ConfigValidationError):
        TlsExpiryCheck("cert", "127.0.0.1", server_name="http://localhost")
    with pytest.raises(ConfigValidationError):
        build_check({"kind": "tls", "name": "cert", "target": "127.0.0.1", "ca_file": 1})


def test_tls_reports_a_refused_port_and_a_non_tls_peer() -> None:
    """A closed port and a plain HTTP port both become failed results."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    closed = int(sock.getsockname()[1])
    sock.close()
    refused = TlsExpiryCheck("cert", "127.0.0.1", port=closed, timeout=2).run()
    assert refused.ok is False
    assert refused.message == "connection failed: ConnectionRefusedError"
    with MockHttpServer() as server:
        port = int(server.base_url.rsplit(":", maxsplit=1)[1])
        plain = TlsExpiryCheck("cert", "127.0.0.1", port=port, timeout=2).run()
    assert plain.ok is False
    assert plain.message.startswith("tls handshake failed")


def test_defacement_reports_a_status_miss_and_both_content_problems() -> None:
    """A 404 stays a status failure. A bad page names both mismatches."""
    with MockHttpServer() as server:
        missing = DefacementCheck(
            "page",
            f"{server.base_url}/missing",
            keyword="ok",
            expected_sha256=_OK_HASH,
            alert_after=1,
            retries=1,
        )
        status = missing.run()
        assert status.status_code == 404
        both = DefacementCheck(
            "page",
            f"{server.base_url}/ok",
            keyword="absent",
            expected_sha256="b" * 64,
            alert_after=1,
            retries=1,
        )
        failed = both.run()
        assert failed.severity == Severity.CRITICAL
        assert failed.message == "keyword missing, body hash differs (1 of 1)"


def test_defacement_learns_the_first_successful_body() -> None:
    """A learn baseline stores the hash of the first good body and then compares."""
    body = b"<h1>Acme Shop</h1><p>Welcome. Status: all systems normal.</p>"
    learned: list[str] = []
    check = build_check(
        {
            "kind": "defacement",
            "name": "page",
            "target": "http://127.0.0.1/",
            "keyword": "Acme Shop",
            "baseline": "learn",
            "interval": 30,
        }
    )
    assert isinstance(check, DefacementCheck)
    check._persist_baseline = learned.append
    first = check._interpret(200, body, 1.0)
    assert first.ok is True
    assert first.message == "baseline learned"
    assert learned == [hashlib.sha256(body).hexdigest()]
    changed = check._interpret(200, b"<h1>HACKED by anon</h1>", 1.0)
    assert changed.ok is False
    assert "body hash differs" in changed.message
    fresh = build_check(
        {
            "kind": "defacement",
            "name": "page",
            "target": "http://127.0.0.1/",
            "keyword": "missing",
            "baseline": "learn",
            "interval": 30,
        }
    )
    assert isinstance(fresh, DefacementCheck)
    missed = fresh._interpret(200, body, 1.0)
    assert missed.ok is False
    assert fresh.expected_sha256 == ""


def test_console_notifier_writes_the_stream_it_was_given() -> None:
    """A stream receives the same line the notifier keeps."""
    result = CheckResult(
        monitor="web",
        kind="http",
        ok=True,
        latency_ms=0,
        message="HTTP 200",
        severity=Severity.OK,
        checked_at=utc_now(),
        status_code=200,
    )
    buffer = StringIO()
    ConsoleNotifier(buffer).send(result)
    assert buffer.getvalue().startswith("web ok=True severity=ok HTTP 200")
    assert isinstance(stderr_notifier(), ConsoleNotifier)
