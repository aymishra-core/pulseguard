"""SMTP delivery that reads the database, then lets the environment override it.

The password is not logged. An empty host means mail is not configured.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import AlertError
from pulseguard.notifiers.base import Notifier
from pulseguard.notifiers.email_notifier import smtp_settings_from_env
from pulseguard.storage.base import Repository

_KEYS = (
    ("PULSEGUARD_SMTP_HOST", "smtp_host"),
    ("PULSEGUARD_SMTP_PORT", "smtp_port"),
    ("PULSEGUARD_SMTP_USERNAME", "smtp_user"),
    ("PULSEGUARD_SMTP_PASSWORD", "smtp_password"),
    ("PULSEGUARD_SMTP_FROM", "smtp_from"),
    ("PULSEGUARD_SMTP_TO", "smtp_to"),
    ("PULSEGUARD_SMTP_STARTTLS", "smtp_starttls"),
)


class DatabaseNotifier(Notifier):
    """Build SMTP settings at send time so a form save applies to the next alert."""

    def __init__(self, repository_factory: Callable[[], Repository]) -> None:
        """Keep the factory. A connection is opened only inside ``send``."""
        self._factory = repository_factory

    def send(self, result: CheckResult) -> None:
        """Deliver one result, or raise ``not configured`` when no host is set."""
        from pulseguard.notifiers.email_notifier import EmailNotifier

        mapping = self.mapping()
        if not mapping.get("PULSEGUARD_SMTP_HOST", "").strip():
            raise AlertError("not configured")
        EmailNotifier(smtp_settings_from_env(mapping)).send(result)

    def note(self, result: CheckResult) -> None:
        """Digest lines are not queued by the browser UI."""
        del result

    def mapping(self) -> dict[str, str]:
        """Return SMTP environment keys. A set environment variable wins."""
        repo = self._factory()
        try:
            getter = getattr(repo, "get_setting", None)
            found: dict[str, str] = {}
            for env_name, setting in _KEYS:
                override = os.environ.get(env_name, "").strip()
                if override:
                    found[env_name] = override
                    continue
                if not callable(getter):
                    found[env_name] = ""
                    continue
                stored = getter(setting)
                found[env_name] = stored if isinstance(stored, str) else ""
            return found
        finally:
            repo.close()
