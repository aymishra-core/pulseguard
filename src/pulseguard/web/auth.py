"""Session login, CSRF checks, and the API-key decorator.

Passwords are hashed at startup and the plaintext is not stored. The API key
and the session secret are compared or signed, and they are not logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from typing import TypeVar

from flask import current_app, jsonify, redirect, request, session, url_for

from pulseguard.exceptions import ConfigValidationError

# A work factor, not a measurement.
_PASSWORD_ITERATIONS = 100_000
_MIN_SECRET_LENGTH = 16
_MAX_SECRET_LENGTH = 128
_MIN_PASSWORD_LENGTH = 8
_MAX_PASSWORD_LENGTH = 128
_USER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
F = TypeVar("F", bound=Callable[..., object])


def require_secret_key(value: str) -> str:
    """Return a session secret of 16..128 characters."""
    if not isinstance(value, str) or any(char in value for char in "\r\n"):
        raise ConfigValidationError("secret key must be 16..128 characters on one line")
    if len(value) < _MIN_SECRET_LENGTH or len(value) > _MAX_SECRET_LENGTH:
        raise ConfigValidationError("secret key must be 16..128 characters on one line")
    return value


def require_admin_user(value: str) -> str:
    """Return an admin username."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(char not in _USER_CHARS for char in value)
    ):
        raise ConfigValidationError("admin user must be 1..64 letters, digits, _ or -")
    return value


def require_admin_password(value: str) -> str:
    """Return a password long enough to hash. The caller must not log it."""
    if (
        not isinstance(value, str)
        or len(value) < _MIN_PASSWORD_LENGTH
        or len(value) > _MAX_PASSWORD_LENGTH
        or any(char in value for char in "\r\n")
    ):
        raise ConfigValidationError("admin password must be 8..128 characters on one line")
    return value


def require_api_key_value(value: str) -> str:
    """Return an API key in the same shape as a heartbeat token."""
    if not isinstance(value, str) or len(value) < 16 or len(value) > 128:
        raise ConfigValidationError("api key must be 16..128 letters, digits, _ or -")
    if any(char not in _USER_CHARS for char in value):
        raise ConfigValidationError("api key must be 16..128 letters, digits, _ or -")
    return value


def hash_password(password: str) -> tuple[bytes, bytes]:
    """Return ``(salt, digest)``. The password itself is not retained."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PASSWORD_ITERATIONS,
    )
    return salt, digest


def passwords_match(attempt: object, salt: bytes, digest: bytes) -> bool:
    """Hash ``attempt`` and compare the digest. A bad type does not match."""
    if not isinstance(attempt, str) or len(attempt) > _MAX_PASSWORD_LENGTH:
        return False
    check = hashlib.pbkdf2_hmac("sha256", attempt.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return hmac.compare_digest(check, digest)


def login_ok(username: object, password: object) -> bool:
    """Return True when both the username and the password match."""
    expected = current_app.config["ADMIN_USER"]
    user_ok = isinstance(username, str) and _same_text(username, expected)
    pass_ok = passwords_match(
        password,
        current_app.config["ADMIN_SALT"],
        current_app.config["ADMIN_DIGEST"],
    )
    return user_ok and pass_ok


def ensure_csrf() -> str:
    """Return the session CSRF token, creating it on first use."""
    token = session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 16:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def csrf_ok() -> bool:
    """Return True when the form token matches the session token."""
    expected = session.get("csrf_token")
    supplied = request.form.get("csrf_token", "")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        return False
    return _same_text(supplied, expected)


def require_api_key(fn: F) -> F:
    """Reject a write when ``X-API-Key`` does not match. The key is not logged."""

    def wrapper(*args: object, **kwargs: object) -> object:
        expected = current_app.config["API_KEY"]
        supplied = request.headers.get("X-API-Key", "")
        if not _same_text(supplied, expected):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper  # type: ignore[return-value]


def login_required(fn: F) -> F:
    """Redirect an anonymous visitor to the login form."""

    def wrapper(*args: object, **kwargs: object) -> object:
        if session.get("user") != current_app.config["ADMIN_USER"]:
            return redirect(url_for("login_form"))
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper  # type: ignore[return-value]


def _same_text(supplied: str, expected: str) -> bool:
    """Compare two strings without branching on a shared prefix."""
    if len(supplied) != len(expected):
        return False
    return hmac.compare_digest(supplied, expected)
