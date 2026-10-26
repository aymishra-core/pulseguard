"""MySQL repository behind the same ``Repository`` port.

The CLI stores history in SQLite. This class is for an operator who points
the process at MySQL. Credentials come from the environment and are not logged.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pulseguard.exceptions import ConfigValidationError, StorageError
from pulseguard.storage.sql_repo import SqlRepository

# Column widths match the validation caps in records.py. They are limits.
_MYSQL_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INT PRIMARY KEY,
        applied_at VARCHAR(40) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS results (
        id BIGINT NOT NULL AUTO_INCREMENT,
        monitor VARCHAR(128) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        ok TINYINT NOT NULL,
        latency_ms DOUBLE NOT NULL,
        message VARCHAR(2000) NOT NULL,
        severity VARCHAR(16) NOT NULL,
        checked_at VARCHAR(40) NOT NULL,
        status_code INT NULL,
        PRIMARY KEY (id),
        KEY idx_results_monitor_time (monitor, checked_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS monitors (
        name VARCHAR(128) PRIMARY KEY,
        kind VARCHAR(32) NOT NULL,
        target VARCHAR(2048) NOT NULL,
        interval_seconds INT NOT NULL,
        enabled TINYINT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.:-]{1,255}$")
_CONNECT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class MysqlSettings:
    """Connection settings. ``__repr__`` leaves the password out."""

    host: str
    port: int
    user: str
    password: str
    database: str

    def __post_init__(self) -> None:
        """Reject a host, account, or database name that cannot be a setting."""
        if not isinstance(self.host, str) or _HOST_RE.fullmatch(self.host) is None:
            raise ConfigValidationError("MySQL host must be a hostname or address")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigValidationError("MySQL port must be an integer")
        if self.port < 1 or self.port > 65535:
            raise ConfigValidationError("MySQL port must be from 1 to 65535")
        if not isinstance(self.user, str) or _NAME_RE.fullmatch(self.user) is None:
            raise ConfigValidationError("MySQL user must be 1..64 letters, digits, '_' or '-'")
        if not isinstance(self.password, str):
            raise ConfigValidationError("MySQL password must be a string")
        if not isinstance(self.database, str) or _NAME_RE.fullmatch(self.database) is None:
            raise ConfigValidationError("MySQL database must be 1..64 letters, digits, '_' or '-'")

    def __repr__(self) -> str:
        """Return the settings without the password."""
        return (
            "MysqlSettings("
            f"host={self.host!r}, port={self.port}, user={self.user!r}, "
            f"database={self.database!r})"
        )


class MysqlRepository(SqlRepository):
    """MySQL CRUD using ``%s`` placeholders. Pass ``connection`` only from tests."""

    def __init__(
        self,
        settings: MysqlSettings | None = None,
        *,
        connection: Any | None = None,
    ) -> None:
        """Open MySQL, or use ``connection`` when a test supplies one."""
        if connection is None:
            if settings is None:
                raise ConfigValidationError("MySQL settings are required")
            connection = _connect(settings)
        super().__init__(
            connection,
            mark="%s",
            apply_ddl=_apply_mysql_ddl,
            missing_table=_mysql_missing_table,
            query_error="MySQL query failed",
        )


def mysql_settings_from_env(environ: Mapping[str, str] | None = None) -> MysqlSettings:
    """Read MySQL settings from ``PULSEGUARD_MYSQL_*``. The password may be empty."""
    env = os.environ if environ is None else environ
    host = env.get("PULSEGUARD_MYSQL_HOST", "").strip()
    user = env.get("PULSEGUARD_MYSQL_USER", "").strip()
    database = env.get("PULSEGUARD_MYSQL_DATABASE", "").strip()
    if not host or not user or not database:
        raise ConfigValidationError(
            "MySQL requires PULSEGUARD_MYSQL_HOST, PULSEGUARD_MYSQL_USER, "
            "and PULSEGUARD_MYSQL_DATABASE"
        )
    port_text = env.get("PULSEGUARD_MYSQL_PORT", "").strip() or "3306"
    if not port_text.isdigit():
        raise ConfigValidationError("PULSEGUARD_MYSQL_PORT must be an integer")
    return MysqlSettings(
        host=host,
        port=int(port_text),
        user=user,
        password=env.get("PULSEGUARD_MYSQL_PASSWORD", ""),
        database=database,
    )


def _connect(settings: MysqlSettings) -> Any:
    """Open a MySQL connection. The driver is imported only here."""
    try:
        import mysql.connector as mysql_connector
    except ImportError as exc:
        raise StorageError(
            "MySQL support needs the mysql extra: pip install 'pulseguard[mysql]'"
        ) from exc
    try:
        return mysql_connector.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=settings.database,
            connection_timeout=_CONNECT_TIMEOUT_SECONDS,
            autocommit=False,
        )
    except Exception as exc:
        raise StorageError("could not connect to MySQL") from exc


def _apply_mysql_ddl(connection: Any) -> None:
    """Create the version, result, and monitor tables when they are absent."""
    cursor = connection.cursor()
    try:
        for statement in _MYSQL_SCHEMA:
            cursor.execute(statement)
    except Exception as exc:
        raise StorageError("could not apply MySQL schema") from exc
    finally:
        cursor.close()


def _mysql_missing_table(exc: BaseException) -> bool:
    """Return True when ``schema_migrations`` has not been created yet."""
    if getattr(exc, "errno", None) == 1146:
        return True
    text = str(exc).lower()
    return "schema_migrations" in text and "doesn't exist" in text
