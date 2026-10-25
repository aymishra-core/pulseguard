"""Registry, validation, and HTTP check tests. Targets are the local mock only."""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from pulseguard.checks import BaseCheck, CheckMeta, HttpCheck, build_check
from pulseguard.checks.http_check import is_disallowed_literal_host
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.exceptions import (
    CertificateExpiringError,
    CheckError,
    CheckTimeoutError,
    ConfigValidationError,
    ContentMismatchError,
    PulseGuardError,
)
from tests.mock_servers import MockHttpServer

ROOT = Path(__file__).resolve().parents[1]


def test_exception_hierarchy() -> None:
    """The syllabus exception tree is real and importable."""
    assert issubclass(CheckTimeoutError, CheckError)
    assert issubclass(CertificateExpiringError, CheckError)
    assert issubclass(ContentMismatchError, CheckError)
    assert issubclass(CheckError, PulseGuardError)
    assert issubclass(ConfigValidationError, PulseGuardError)
    assert str(CheckTimeoutError("slow")) == "slow"


def test_http_kind_is_registered_and_base_is_not() -> None:
    """Importing the check package registers ``http`` and not the abstract base."""
    assert CheckMeta.registry["http"] is HttpCheck
    assert "" not in CheckMeta.registry
    assert BaseCheck in HttpCheck.__mro__
    with pytest.raises(TypeError):
        BaseCheck("web", "http://127.0.0.1/health")  # type: ignore[abstract]


def test_missing_or_duplicate_kind_cannot_be_created() -> None:
    """``CheckMeta`` refuses a class with no kind, blank kind, or a taken kind."""
    with pytest.raises(TypeError, match="kind"):

        class MissingKind(BaseCheck):
            """No kind attribute."""

            def run(self) -> CheckResult:
                """Unused."""
                raise NotImplementedError

            @classmethod
            def from_config(cls, spec: Mapping[str, Any]) -> BaseCheck:
                """Unused."""
                raise NotImplementedError

    with pytest.raises(TypeError, match="whitespace"):

        class SpacedKind(BaseCheck):
            """Kind with padding."""

            kind = " spaced "

            def run(self) -> CheckResult:
                """Unused."""
                raise NotImplementedError

            @classmethod
            def from_config(cls, spec: Mapping[str, Any]) -> BaseCheck:
                """Unused."""
                raise NotImplementedError

    with pytest.raises(TypeError, match="already registered"):

        class SecondHttp(BaseCheck):
            """Tries to take the http kind."""

            kind = "http"

            def run(self) -> CheckResult:
                """Unused."""
                raise NotImplementedError

            @classmethod
            def from_config(cls, spec: Mapping[str, Any]) -> BaseCheck:
                """Unused."""
                raise NotImplementedError

    registered_kind = f"phase1-{uuid.uuid4().hex}"

    class Custom(BaseCheck):
        """A unique kind registered only by this test."""

        kind = registered_kind

        def run(self) -> CheckResult:
            """Unused."""
            raise NotImplementedError

        @classmethod
        def from_config(cls, spec: Mapping[str, Any]) -> BaseCheck:
            """Unused."""
            raise NotImplementedError

    assert CheckMeta.registry[registered_kind] is Custom


def test_class_and_instance_variables_stay_separate() -> None:
    """The class default is copied onto the instance at construction."""
    original = HttpCheck.default_timeout
    try:
        HttpCheck.default_timeout = 8.0
        first = HttpCheck("one", "http://127.0.0.1/health")
        assert first.timeout == 8.0
        assert "kind" not in first.__dict__
        assert first.kind == HttpCheck.kind == "http"
        first.timeout = 9.0
        assert HttpCheck.default_timeout == 8.0
        second = HttpCheck("two", "http://127.0.0.1/health")
        assert second.timeout == 8.0
        assert first.timeout == 9.0
    finally:
        HttpCheck.default_timeout = original


def test_interval_is_name_mangled_and_bounded() -> None:
    """``interval`` validates input and stores it as ``_BaseCheck__interval``."""
    check = HttpCheck("web", "http://127.0.0.1/health", interval=30)
    assert check.interval == 30
    assert check._BaseCheck__interval == 30  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        _ = check.__interval  # type: ignore[attr-defined]
    check.interval = 60
    assert check._BaseCheck__interval == 60  # type: ignore[attr-defined]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", interval=29.9)
    with pytest.raises(ConfigValidationError):
        check.timeout = True  # type: ignore[assignment]
    with pytest.raises(ConfigValidationError):
        check.timeout = 0
    with pytest.raises(ConfigValidationError):
        check.timeout = 121
    check.timeout = 120
    assert check.timeout == 120


def test_target_validation_rejects_unsafe_urls() -> None:
    """Scheme, userinfo, fragment, and literal private hosts are checked up front."""
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "ftp://127.0.0.1/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://user:secret@127.0.0.1/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health#frag")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1:0/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", allow_private=False)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://10.1.2.3/health", allow_private=False)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://localhost/health", allow_private=False)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://[::1]/health", allow_private=False)
    allowed = HttpCheck("web", "http://monitor.invalid/health", allow_private=False)
    assert allowed.target == "http://monitor.invalid/health"
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", method="POST")
    with pytest.raises(ConfigValidationError):
        HttpCheck(" ", "http://127.0.0.1/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("a" * 129, "http://127.0.0.1/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", " http://127.0.0.1/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck(1, "http://127.0.0.1/health")  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", 1)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/" + ("a" * 3000))
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1:99999/health")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http:///missing-host")
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", expected_status=99)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", expected_status=600)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", expected_status=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", method=1)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", retries=0)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", retries=9)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", retry_backoff=-1)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", retry_backoff=31)
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", retry_backoff=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", allow_private="yes")  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError):
        HttpCheck("web", "http://127.0.0.1/health", interval=86_401)
    with pytest.raises(ConfigValidationError, match="kind"):
        HttpCheck.from_config({"kind": "tcp", "name": "web", "target": "http://127.0.0.1/"})
    with pytest.raises(ConfigValidationError, match="missing"):
        HttpCheck.from_config({"kind": "http", "target": "http://127.0.0.1/"})
    with pytest.raises(ConfigValidationError):
        build_check(["http"])  # type: ignore[arg-type]
    with pytest.raises(ConfigValidationError, match="kind"):
        build_check({"kind": "  ", "name": "web", "target": "http://127.0.0.1/"})


def test_literal_host_policy_does_not_resolve_names() -> None:
    """Host policy is a pure string/IP check. It does not open a socket."""
    assert is_disallowed_literal_host("localhost")
    assert is_disallowed_literal_host("127.0.0.1")
    assert is_disallowed_literal_host("10.0.0.1")
    assert is_disallowed_literal_host("100.64.1.1")
    assert is_disallowed_literal_host("169.254.169.254")
    assert is_disallowed_literal_host("::1")
    assert is_disallowed_literal_host("::ffff:127.0.0.1")
    assert not is_disallowed_literal_host("1.2.3.4")
    assert not is_disallowed_literal_host("monitor.invalid")


def test_build_check_reads_http_config() -> None:
    """JSON objects become checks through the metaclass registry."""
    check = build_check(
        {
            "kind": "http",
            "name": "web",
            "target": "http://127.0.0.1/health",
            "expected_status": 204,
            "method": "head",
        }
    )
    assert isinstance(check, HttpCheck)
    assert check.method == "HEAD"
    assert check.expected_status == 204
    with pytest.raises(ConfigValidationError, match="unknown check kind"):
        build_check({"kind": "tcp", "name": "x", "target": "127.0.0.1:9"})
    with pytest.raises(ConfigValidationError, match="unknown fields"):
        build_check({"kind": "http", "name": "web", "target": "http://127.0.0.1/", "nope": 1})
    with pytest.raises(ConfigValidationError, match="name and target"):
        build_check({"kind": "http", "name": 1, "target": "http://127.0.0.1/"})
    example = build_check_from_example()
    assert example.kind == "http"
    assert example.allow_private is True


def build_check_from_example() -> HttpCheck:
    """Load the shipped example without probing it."""
    import json

    payload = json.loads((ROOT / "config" / "monitors.example.json").read_text(encoding="utf-8"))
    built = build_check(payload["monitors"][0])
    assert isinstance(built, HttpCheck)
    return built


def test_http_check_success_failure_redirect_and_head() -> None:
    """A local server proves status matching, redirects, and HEAD."""
    with MockHttpServer() as server:
        ok = HttpCheck("web", f"{server.base_url}/ok", retries=1)
        success = ok.run()
        assert success.ok is True
        assert success.status_code == 200
        assert success.severity == Severity.OK
        assert success.latency_ms >= 0
        assert ok.last_elapsed >= 0
        assert server.state.last_user_agent.startswith("pulseguard/")

        missing = HttpCheck("web", f"{server.base_url}/missing", retries=1)
        failed = missing.run()
        assert failed.ok is False
        assert failed.status_code == 404
        assert failed.severity == Severity.CRITICAL

        redirected = HttpCheck("web", f"{server.base_url}/redirect", retries=1)
        bounce = redirected.run()
        assert bounce.ok is False
        assert bounce.status_code == 302

        head = HttpCheck("web", f"{server.base_url}/ok", method="HEAD", retries=1)
        headed = head.run()
        assert headed.ok is True
        assert server.state.last_path == "/ok"


def test_http_check_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """A slow handler trips the timeout and is not reported as a status code."""
    caplog.set_level(logging.WARNING, logger="pulseguard.core.decorators")
    with MockHttpServer() as server:
        server.state.delay_seconds = 1.0
        check = HttpCheck(
            "web",
            f"{server.base_url}/ok",
            timeout=0.2,
            retries=1,
            retry_backoff=0,
        )
        result = check.run()
    assert result.ok is False
    assert result.status_code is None
    assert "timed out" in result.message
    assert "super-secret" not in result.message


def test_http_check_retries_connection_refused(caplog: pytest.LogCaptureFixture) -> None:
    """Transport failures retry and the result does not include the URL."""
    caplog.set_level(logging.WARNING, logger="pulseguard.core.decorators")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    target = f"http://127.0.0.1:{port}/health?token=super-secret-value"
    check = HttpCheck("web", target, timeout=1, retries=2, retry_backoff=0)
    result = check.run()
    assert result.ok is False
    assert result.message == "request failed: ConnectionError"
    assert "super-secret-value" not in result.message
    assert sum("retrying" in record.message for record in caplog.records) == 1


def test_run_is_polymorphic() -> None:
    """Callers hold a ``BaseCheck`` and still execute ``run``."""
    with MockHttpServer() as server:
        check: BaseCheck = HttpCheck(
            "web", f"{server.base_url}/ok?token=super-secret-value", retries=1
        )
        result = check.run()
    assert result.ok is True
    assert "super-secret-value" not in result.message
