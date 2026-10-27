"""Decorators used by checks.

``retry`` demonstrates a decorator and a closure that updates ``remaining``
with ``nonlocal``. ``timed`` and ``log_call`` decorate the check entrypoint.
``require_api_key`` for the write API lives in ``pulseguard.web.auth``.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import ParamSpec, Protocol, TypeVar, runtime_checkable

from pulseguard.exceptions import CheckError

LOGGER = logging.getLogger("pulseguard.core.decorators")
P = ParamSpec("P")
R = TypeVar("R")


@runtime_checkable
class _Elapsed(Protocol):
    """Duck-typed object that records how long its last call took."""

    last_elapsed: float


def retry(
    *,
    attempts: int = 3,
    backoff_seconds: float = 0.2,
    retry_on: tuple[type[BaseException], ...] = (CheckError,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Retry ``attempts`` times with linear backoff.

    The nested ``consume_attempt`` closure decrements ``remaining`` via ``nonlocal``.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be an integer >= 1")
    if isinstance(backoff_seconds, bool) or not isinstance(backoff_seconds, (int, float)):
        raise ValueError("backoff_seconds must be a number")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be >= 0")
    if not retry_on:
        raise ValueError("retry_on must list at least one exception type")

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        """Return ``fn`` wrapped with the retry loop."""

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            """Call ``fn`` until it returns or the attempts are spent."""
            remaining = attempts

            def consume_attempt() -> int:
                """Record one attempt. Closure with ``nonlocal``."""
                nonlocal remaining
                remaining -= 1
                assert remaining >= 0, "retry counter went negative"
                return remaining

            while True:
                left_after = consume_attempt()
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    if left_after <= 0:
                        raise
                    delay = float(backoff_seconds) * (attempts - left_after)
                    LOGGER.warning(
                        "retrying %s attempts_left=%s error=%s",
                        fn.__qualname__,
                        left_after,
                        type(exc).__name__,
                    )
                    if delay > 0:
                        time.sleep(delay)

        return wrapper

    return decorator


def timed(fn: Callable[P, R]) -> Callable[P, R]:
    """Store wall-clock seconds on ``self.last_elapsed`` when that attribute exists."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """Run ``fn`` and record the elapsed time on the instance."""
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            if args:
                owner = args[0]
                if isinstance(owner, _Elapsed):
                    owner.last_elapsed = elapsed

    return wrapper


def log_call(fn: Callable[P, R]) -> Callable[P, R]:
    """Log the qualified name around a call. Arguments are omitted on purpose."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        """Log start and finish without the call arguments."""
        LOGGER.info("calling %s", fn.__qualname__)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            LOGGER.warning("failed %s", fn.__qualname__)
            raise
        LOGGER.info("finished %s", fn.__qualname__)
        return result

    return wrapper
