"""SMTP alerts and a digest. Credentials come from the environment."""

from __future__ import annotations

import os
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import AlertError, ConfigValidationError
from pulseguard.notifiers.base import Notifier

# Validation limits, not measurements.
_MAX_HOST_LENGTH = 255
_MAX_ADDRESS_LENGTH = 254
_MAX_SECRET_LENGTH = 256
_MAX_RECIPIENTS = 8
_MAX_DIGEST_LINES = 100
_SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class SmtpSettings:
    """Where to deliver mail. The password is omitted from ``repr``."""

    host: str
    port: int
    username: str
    password: str
    from_addr: str
    to_addrs: tuple[str, ...]
    starttls: bool = False

    def __repr__(self) -> str:
        """Return the settings without the password."""
        return (
            f"SmtpSettings(host={self.host!r}, port={self.port}, username={self.username!r}, "
            f"from_addr={self.from_addr!r}, to_addrs={self.to_addrs!r}, starttls={self.starttls})"
        )


class EmailNotifier(Notifier):
    """Send one message per ``send`` call, and queue lines for a later digest.

    ``note`` does not send mail. ``send_digest`` sends the queued lines once
    and then clears them. Nothing here sends on a timer.
    """

    def __init__(self, settings: SmtpSettings) -> None:
        """Store the SMTP settings. Nothing is connected yet."""
        self._settings = settings
        self._notes: list[str] = []

    def send(self, result: CheckResult) -> None:
        """Deliver one result as its own message."""
        subject = f"[pulseguard] {result.message} {result.monitor}"
        body = f"monitor={result.monitor} severity={result.severity} {result.message}"
        self._deliver(subject, body)

    def note(self, result: CheckResult) -> None:
        """Remember one line for the next digest. Extra lines past the cap are dropped."""
        if len(self._notes) >= _MAX_DIGEST_LINES:
            return
        self._notes.append(
            f"monitor={result.monitor} ok={str(result.ok).lower()} "
            f"severity={result.severity} {result.message}"
        )

    def send_digest(self) -> int:
        """Send the queued lines as one message. Returns how many lines were sent."""
        if not self._notes:
            return 0
        count = len(self._notes)
        body = "\n".join(self._notes)
        self._deliver("[pulseguard] digest", body)
        self._notes.clear()
        return count

    def _deliver(self, subject: str, body: str) -> None:
        """Open a short SMTP session and send one message."""
        message = EmailMessage()
        message["From"] = self._settings.from_addr
        message["To"] = ", ".join(self._settings.to_addrs)
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(
                self._settings.host,
                self._settings.port,
                timeout=_SMTP_TIMEOUT_SECONDS,
            ) as client:
                if self._settings.starttls:
                    client.starttls()
                if self._settings.username:
                    client.login(self._settings.username, self._settings.password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise AlertError("email delivery failed") from exc


def smtp_settings_from_env(environ: Mapping[str, str] | None = None) -> SmtpSettings:
    """Read SMTP settings from ``environ`` or the process environment."""
    env = os.environ if environ is None else environ
    port_text = env.get("PULSEGUARD_SMTP_PORT", "587").strip()
    if not port_text.isdigit():
        raise ConfigValidationError("smtp port must be an integer")
    starttls = env.get("PULSEGUARD_SMTP_STARTTLS", "").strip().lower() in {"1", "true", "yes"}
    recipients = tuple(
        _require_address(part, "smtp recipient")
        for part in env.get("PULSEGUARD_SMTP_TO", "").split(",")
        if part.strip()
    )
    if not recipients or len(recipients) > _MAX_RECIPIENTS:
        raise ConfigValidationError(f"smtp recipients must be 1..{_MAX_RECIPIENTS} addresses")
    return SmtpSettings(
        host=_require_host(env.get("PULSEGUARD_SMTP_HOST", "")),
        port=_require_port(int(port_text)),
        username=_require_secret(env.get("PULSEGUARD_SMTP_USERNAME", ""), "smtp username"),
        password=_require_secret(env.get("PULSEGUARD_SMTP_PASSWORD", ""), "smtp password"),
        from_addr=_require_address(env.get("PULSEGUARD_SMTP_FROM", ""), "smtp from"),
        to_addrs=recipients,
        starttls=starttls,
    )


def _require_host(value: str) -> str:
    """Reject a blank or oversized SMTP host. The name is not resolved."""
    host = value.strip()
    if not host or len(host) > _MAX_HOST_LENGTH or any(char.isspace() for char in host):
        raise ConfigValidationError(f"smtp host must be 1..{_MAX_HOST_LENGTH} characters")
    return host


def _require_port(value: int) -> int:
    """Reject a port outside 1..65535."""
    if value < 1 or value > 65535:
        raise ConfigValidationError("smtp port must be from 1 to 65535")
    return value


def _require_address(value: str, field: str) -> str:
    """Require one ``@`` and no spaces. This is a shape check, not a mailbox lookup."""
    address = value.strip()
    if (
        len(address) < 3
        or len(address) > _MAX_ADDRESS_LENGTH
        or address.count("@") != 1
        or any(char.isspace() for char in address)
    ):
        raise ConfigValidationError(f"{field} must be an email address")
    return address


def _require_secret(value: str, field: str) -> str:
    """Allow an empty username or password, and cap the length."""
    if len(value) > _MAX_SECRET_LENGTH:
        raise ConfigValidationError(f"{field} must be at most {_MAX_SECRET_LENGTH} characters")
    return value
