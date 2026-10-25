"""Restore loggers that tests reconfigure."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def restore_loggers() -> Iterator[None]:
    """Put the process loggers back after each test."""
    names = ("pulseguard", "urllib3")
    saved = []
    for name in names:
        logger = logging.getLogger(name)
        saved.append((logger, list(logger.handlers), logger.level, logger.propagate))
    yield
    for logger, handlers, level, propagate in saved:
        current = list(logger.handlers)
        logger.handlers.clear()
        for handler in current:
            if handler not in handlers:
                handler.close()
        for handler in handlers:
            logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate
