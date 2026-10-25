"""Process-wide logging for the pulseguard logger.

Demonstrates the logging module and rotating text logs. Records are one-line
``key=value`` fields so they can be grepped without a third-party formatter.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "pulseguard"
_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
# Upper bounds for the rotating file. These are retention policy, not measurements.
_LOG_MAX_BYTES = 1_000_000
_LOG_BACKUP_COUNT = 3


class LogFormatter(logging.Formatter):
    """Format one log record as a single key=value line."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` without embedded newlines."""
        message = record.getMessage().replace("\r", " ").replace("\n", "\\n")
        timestamp = self.formatTime(record, self.datefmt)
        return f"ts={timestamp} level={record.levelname} logger={record.name} message={message}"


def setup_logging(*, level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    """Attach a stderr handler and, when ``log_dir`` is set, a rotating file.

    Propagation stops at this logger so a record is not also printed by the
    root logger's last-resort handler.
    """
    chosen = level.strip().upper()
    if chosen not in _VALID_LEVELS:
        known = ", ".join(sorted(_VALID_LEVELS))
        raise ValueError(f"log level must be one of {known}")

    logger = logging.getLogger(LOGGER_NAME)
    _close_handlers(logger)
    logger.setLevel(chosen)
    logger.propagate = False
    logger.addHandler(_stream_handler())
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.addHandler(_file_handler(log_dir / "pulseguard.log"))
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logger


def _stream_handler() -> logging.Handler:
    """Build the stderr handler."""
    handler = logging.StreamHandler()
    handler.setFormatter(LogFormatter())
    return handler


def _file_handler(path: Path) -> logging.Handler:
    """Build the rotating file handler. Demonstrates rotating text logs."""
    handler = RotatingFileHandler(
        path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(LogFormatter())
    return handler


def _close_handlers(logger: logging.Logger) -> None:
    """Drop handlers already attached to ``logger`` so setup is idempotent."""
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
