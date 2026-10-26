"""One open alert and one resolved alert, plus the evidence pack. Mail stays on localhost."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.engine import Engine
from pulseguard.exceptions import AlertError, ConfigValidationError
from pulseguard.exporters.evidence_pack import NOT_LEGAL_ADVICE, write_evidence_pack
from pulseguard.notifiers.email_notifier import EmailNotifier, smtp_settings_from_env
from pulseguard.storage.records import DEFAULT_RETENTION_DAYS
from tests.fake_smtp import FakeSmtp
from tests.mock_servers import MockHttpServer


class _Idle:
    """A scheduled check the outage test never runs. The writer path is driven directly."""

    kind = "http"

    def __init__(self, name: str = "web") -> None:
        """Store the schedule fields the engine validates."""
        self.name = name
        self.target = f"http://127.0.0.1/{name}"
        self.interval = 30.0

    def run(self) -> CheckResult:
        """The outage test does not schedule this check."""
        raise AssertionError("not scheduled")


class _Recording:
    """Counts alert deliveries without opening a socket."""

    def __init__(self) -> None:
        """Start with an empty list."""
        self.sent: list[str] = []

    def send(self, result: CheckResult) -> None:
        """Record the alert message."""
        self.sent.append(result.message)


def test_one_outage_sends_one_open_and_one_resolved_mail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Five failures and two successes produce two messages and two INFO lines."""
    caplog.set_level(logging.INFO, logger="pulseguard.core.incidents")
    with FakeSmtp() as smtp:
        settings = smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                "PULSEGUARD_SMTP_PORT": str(smtp.port),
                "PULSEGUARD_SMTP_USERNAME": "mailer",
                "PULSEGUARD_SMTP_PASSWORD": "s3cret-smtp",
                "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
                "PULSEGUARD_SMTP_STARTTLS": "false",
            }
        )
        assert "s3cret-smtp" not in repr(settings)
        notifier = EmailNotifier(settings)
        engine = Engine(
            [_Idle()],
            notifier=notifier,
            failures_before_open=3,
            successes_before_resolve=1,
        )
        for _ in range(5):
            engine._observe(_result(ok=False), None)
        for _ in range(2):
            engine._observe(_result(ok=True), None)
    assert len(smtp.messages) == 2
    opened, resolved = (message.decode("utf-8", errors="replace") for message in smtp.messages)
    assert "incident opened" in opened
    assert "monitor=web" in opened
    assert "incident resolved" in resolved
    assert "monitor=web" in resolved
    assert "s3cret-smtp" not in opened
    assert "s3cret-smtp" not in resolved
    assert "127.0.0.1" not in opened
    logs = [
        record.getMessage()
        for record in caplog.records
        if record.name == "pulseguard.core.incidents"
    ]
    assert logs.count("incident opened monitor=web") == 1
    assert logs.count("incident resolved monitor=web") == 1


def test_a_suppressed_failure_does_not_send() -> None:
    """A down dependency keeps the child off the alert path."""
    box = _Recording()
    engine = Engine(
        [_Idle("root"), _Idle("child")],
        notifier=box,
        dependencies={"child": ["root"]},
        failures_before_open=1,
        successes_before_resolve=1,
    )
    engine._observe(_result(ok=False, monitor="root"), None)
    engine._observe(_result(ok=False, monitor="child"), None)
    assert box.sent == ["incident opened: HTTP 503"]


def test_digest_is_one_message_and_a_closed_port_does_not_leak_the_password() -> None:
    """The digest sends once. A refused connection raises a message without the password."""
    when = datetime(2026, 10, 26, tzinfo=UTC)
    with FakeSmtp() as smtp:
        notifier = EmailNotifier(
            smtp_settings_from_env(
                {
                    "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                    "PULSEGUARD_SMTP_PORT": str(smtp.port),
                    "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                    "PULSEGUARD_SMTP_TO": "a@example.invalid,b@example.invalid",
                }
            )
        )
        notifier.note(_result(ok=False, when=when))
        notifier.note(_result(ok=True, monitor="api", when=when))
        assert notifier.send_digest() == 2
        assert notifier.send_digest() == 0
    assert len(smtp.messages) == 1
    body = smtp.messages[0].decode("utf-8", errors="replace")
    assert "monitor=web" in body
    assert "monitor=api" in body
    assert "[pulseguard] digest" in body or "digest" in body
    refused = EmailNotifier(
        smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                "PULSEGUARD_SMTP_PORT": "1",
                "PULSEGUARD_SMTP_PASSWORD": "s3cret-smtp",
                "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
            }
        )
    )
    with pytest.raises(AlertError, match="email delivery failed") as caught:
        refused.send(_result(ok=False, when=when))
    assert "s3cret-smtp" not in str(caught.value)


def test_smtp_settings_reject_a_bad_shape() -> None:
    """Host, port, and addresses are bounded before any socket opens."""
    with pytest.raises(ConfigValidationError, match="smtp host"):
        smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "",
                "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
            }
        )
    with pytest.raises(ConfigValidationError, match="smtp port"):
        smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                "PULSEGUARD_SMTP_PORT": "0",
                "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
            }
        )
    with pytest.raises(ConfigValidationError, match="email address"):
        smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                "PULSEGUARD_SMTP_FROM": "not-an-email",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
            }
        )


def test_starttls_is_requested_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The STARTTLS flag calls starttls before login. No real TLS handshake is opened."""
    calls: list[str] = []

    class _Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Record the session."""

        def __enter__(self) -> _Client:
            """Return the same client."""
            return self

        def __exit__(self, *_args: object) -> None:
            """Close nothing."""

        def starttls(self) -> None:
            """Record the upgrade."""
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            """Record login without keeping the password."""
            calls.append(f"login:{user}")
            assert password == "s3cret-smtp"

        def send_message(self, message: object) -> None:
            """Record the send."""
            calls.append("send")
            assert "s3cret-smtp" not in str(message)

    monkeypatch.setattr("pulseguard.notifiers.email_notifier.smtplib.SMTP", _Client)
    notifier = EmailNotifier(
        smtp_settings_from_env(
            {
                "PULSEGUARD_SMTP_HOST": "127.0.0.1",
                "PULSEGUARD_SMTP_PORT": "587",
                "PULSEGUARD_SMTP_USERNAME": "mailer",
                "PULSEGUARD_SMTP_PASSWORD": "s3cret-smtp",
                "PULSEGUARD_SMTP_FROM": "ops@example.invalid",
                "PULSEGUARD_SMTP_TO": "oncall@example.invalid",
                "PULSEGUARD_SMTP_STARTTLS": "true",
            }
        )
    )
    notifier.send(_result(ok=False))
    assert calls == ["starttls", "login:mailer", "send"]


def test_evidence_pack_counts_down_six_hours_and_drops_old_rows(tmp_path: Path) -> None:
    """The deadline is six hours after open. Rows older than 180 days stay out."""
    opened = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    now = opened + timedelta(hours=1)
    old = _result(ok=False, when=now - timedelta(days=DEFAULT_RETENTION_DAYS + 1))
    fresh = _result(ok=False, when=now - timedelta(hours=1))
    write_evidence_pack(
        tmp_path,
        monitor="web",
        opened_at=opened,
        results=[old, fresh],
        now=now,
    )
    payload = json.loads((tmp_path / "web-timeline.json").read_text(encoding="utf-8"))
    assert payload["remaining_seconds"] == 5 * 3600
    assert payload["retention_days"] == DEFAULT_RETENTION_DAYS
    assert payload["not_legal_advice"] == NOT_LEGAL_ADVICE
    assert len(payload["events"]) == 1
    text = (tmp_path / "web-timeline.txt").read_text(encoding="utf-8")
    assert "remaining_seconds=18000" in text
    assert NOT_LEGAL_ADVICE in text
    csv_text = (tmp_path / "web-timeline.csv").read_text(encoding="utf-8")
    assert csv_text.count("HTTP 503") == 1
    later = opened + timedelta(hours=7)
    write_evidence_pack(
        tmp_path,
        monitor="web",
        opened_at=opened,
        results=[fresh],
        now=later,
    )
    elapsed = json.loads((tmp_path / "web-timeline.json").read_text(encoding="utf-8"))
    assert elapsed["remaining_seconds"] == -3600


def test_opening_an_incident_writes_the_pack_once(tmp_path: Path) -> None:
    """Further failures while the incident is open do not write the pack again."""
    engine = Engine(
        [_Idle()],
        evidence_dir=tmp_path,
        failures_before_open=3,
        successes_before_resolve=1,
    )
    for _ in range(5):
        engine._observe(_result(ok=False), None)
    payload = json.loads((tmp_path / "web-timeline.json").read_text(encoding="utf-8"))
    assert len(payload["events"]) == 3
    assert NOT_LEGAL_ADVICE in payload["not_legal_advice"]


def test_run_does_not_email_a_passing_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured SMTP server receives nothing when the check stays up."""
    from pulseguard.cli import main

    database = tmp_path / "history.db"
    config = tmp_path / "monitors.json"
    with FakeSmtp() as smtp, MockHttpServer() as server:
        monkeypatch.setenv("PULSEGUARD_SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("PULSEGUARD_SMTP_PORT", str(smtp.port))
        monkeypatch.setenv("PULSEGUARD_SMTP_FROM", "ops@example.invalid")
        monkeypatch.setenv("PULSEGUARD_SMTP_TO", "oncall@example.invalid")
        monkeypatch.setenv("PULSEGUARD_SMTP_STARTTLS", "false")
        monkeypatch.delenv("PULSEGUARD_SMTP_USERNAME", raising=False)
        monkeypatch.delenv("PULSEGUARD_SMTP_PASSWORD", raising=False)
        config.write_text(
            json.dumps(
                {
                    "monitors": [
                        {
                            "kind": "http",
                            "name": "demo",
                            "target": f"{server.base_url}/ok",
                            "interval": 30,
                            "timeout": 2,
                            "retries": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code = main(
            [
                "run",
                "--config",
                str(config),
                "--db",
                str(database),
                "--duration",
                "1",
                "--evidence-dir",
                str(tmp_path / "evidence"),
            ]
        )
    assert code == 0
    assert smtp.messages == []
    assert not (tmp_path / "evidence").exists()


def _result(
    *,
    ok: bool,
    monitor: str = "web",
    when: datetime | None = None,
) -> CheckResult:
    """Build one result. Nothing is contacted."""
    return CheckResult(
        monitor=monitor,
        kind="http",
        ok=ok,
        latency_ms=1,
        message="HTTP 200" if ok else "HTTP 503",
        severity=Severity.OK if ok else Severity.CRITICAL,
        checked_at=when if when is not None else utc_now(),
        status_code=200 if ok else 503,
    )
