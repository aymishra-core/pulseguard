"""Browser setup and login for ``pulseguard serve``.

``GET /`` goes to setup, login, or the dashboard. The public page is ``/status``.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response as RedirectResponse

from pulseguard.exceptions import ConfigValidationError
from pulseguard.storage import SqliteRepository
from pulseguard.web.auth import (
    clear_login_failures,
    csrf_ok,
    ensure_csrf,
    hash_password,
    login_is_limited,
    passwords_match,
    record_login_failure,
    require_admin_password,
    require_admin_user,
)


def register_console(app: Flask) -> None:
    """Attach setup, login, and the signed-in home page."""

    @app.get("/setup")
    def setup_form() -> str | RedirectResponse:
        with _repository() as repo:
            if repo.user_count():
                return redirect(url_for("console_login"))
        return render_template("setup.html", csrf_token=ensure_csrf(), error="")

    @app.post("/setup")
    def setup_submit() -> str | RedirectResponse | tuple[str, int]:
        if not csrf_ok():
            return "csrf token rejected", 400
        with _repository() as repo:
            if repo.user_count():
                return redirect(url_for("console_login"))
            try:
                username = require_admin_user(str(request.form.get("username", "")))
                password = require_admin_password(str(request.form.get("password", "")))
            except ConfigValidationError as exc:
                return render_template("setup.html", csrf_token=ensure_csrf(), error=str(exc)), 400
            salt, digest = hash_password(password)
            del password
            repo.save_user(username, salt.hex(), digest.hex())
        return redirect(url_for("console_login"))

    @app.get("/login")
    def console_login() -> str | RedirectResponse:
        with _repository() as repo:
            if repo.user_count() == 0:
                return redirect(url_for("setup_form"))
        return render_template("account_login.html", csrf_token=ensure_csrf(), error="")

    @app.post("/login")
    def console_login_submit() -> str | RedirectResponse | tuple[str, int]:
        if not csrf_ok():
            return "csrf token rejected", 400
        username = request.form.get("username", "")
        if login_is_limited(username):
            return render_template(
                "account_login.html",
                csrf_token=ensure_csrf(),
                error="try again later",
            ), 429
        with _repository() as repo:
            if not _password_matches(repo, username, request.form.get("password")):
                record_login_failure(username)
                return render_template(
                    "account_login.html",
                    csrf_token=ensure_csrf(),
                    error="login failed",
                ), 401
        clear_login_failures(username)
        session["user"] = username if isinstance(username, str) else ""
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def console_logout() -> str | RedirectResponse | tuple[str, int]:
        if not csrf_ok():
            return "csrf token rejected", 400
        session.clear()
        return redirect(url_for("console_login"))

    from pulseguard.web.pages import register_pages

    register_pages(app)


def _repository() -> SqliteRepository:
    """Open the request's database. The caller closes it."""
    from flask import current_app

    return SqliteRepository(Path(str(current_app.config["DATABASE"])))


def _signed_in() -> str | None:
    """Return the signed-in username when that account still exists."""
    name = session.get("user")
    if not isinstance(name, str) or not name:
        return None
    with _repository() as repo:
        if repo.user_hash(name) is None:
            return None
    return name


def _password_matches(repo: SqliteRepository, username: object, password: object) -> bool:
    """Return True when the stored hash matches. A missing user is just a failure."""
    if not isinstance(username, str) or not username:
        return False
    found = repo.user_hash(username)
    if found is None:
        return False
    try:
        salt = bytes.fromhex(found[0])
        digest = bytes.fromhex(found[1])
    except ValueError:
        return False
    return passwords_match(password, salt, digest)
