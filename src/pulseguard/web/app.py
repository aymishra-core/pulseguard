"""Flask application. The public pages read stored rows and do not fetch URLs."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response as RedirectResponse

from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.exceptions import ConfigValidationError, PulseGuardError
from pulseguard.storage import SqliteRepository
from pulseguard.web.auth import (
    clear_login_failures,
    csrf_ok,
    ensure_csrf,
    hash_password,
    login_is_limited,
    login_ok,
    login_required,
    record_login_failure,
    require_admin_password,
    require_admin_user,
    require_api_key,
    require_api_key_value,
    require_secret_key,
)
from pulseguard.web.forms import monitor_from_payload, parse_enabled, parse_monitor_name
from pulseguard.web.status import latest_status

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# Request-body caps. They are limits, not measurements.
_MAX_BODY_BYTES = 16_384
_MAX_UPLOAD_BYTES = 21 * 1024 * 1024
LOGGER = logging.getLogger("pulseguard.web")


def create_app(
    *,
    database: Path,
    secret_key: str,
    admin_user: str,
    admin_password: str,
    api_key: str,
    heartbeats: Sequence[HeartbeatCheck] = (),
    serve: bool = False,
) -> Flask:
    """Build the app. The password is hashed before it is discarded."""
    key = require_secret_key(secret_key)
    user = require_admin_user(admin_user) if not serve else "operator"
    if serve:
        token = _serve_api_key(database)
        salt, digest = b"", b""
    else:
        password = require_admin_password(admin_password)
        token = require_api_key_value(api_key)
        salt, digest = hash_password(password)
        del password
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).with_name("templates")),
        static_folder=str(Path(__file__).with_name("static")),
    )
    app.config["SECRET_KEY"] = key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES
    app.config["DATABASE"] = str(database)
    app.config["DATA_DIR"] = str(database.parent)
    app.config["ADMIN_USER"] = user
    app.config["ADMIN_SALT"] = salt
    app.config["ADMIN_DIGEST"] = digest
    app.config["API_KEY"] = token
    app.config["HEARTBEATS"] = _index_heartbeats(heartbeats)
    app.config["SERVE"] = serve
    app.config["LOGIN_FAILURES"] = {}
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    _register(app)
    return app


def _register(app: Flask) -> None:
    """Attach routes. Access logs stay off so a ping token is not written out."""

    @app.after_request
    def _headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.errorhandler(PulseGuardError)
    def _failure(exc: PulseGuardError) -> tuple[Response | str, int]:
        if request.path.startswith("/api/"):
            return jsonify({"error": str(exc)}), 400
        return str(exc), 400

    @app.get("/")
    def status_page() -> str | RedirectResponse | tuple[str, int]:
        if app.config.get("SERVE"):
            return _serve_home()
        return _public_board()

    @app.get("/status")
    def public_status() -> str | tuple[str, int]:
        return _public_board()

    def _public_board() -> str | tuple[str, int]:
        monitor = request.args.get("monitor")
        with _repository() as repo:
            if repo.get_setting("public_status") == "0":
                return render_template("status_off.html"), 404
            rows = latest_status(repo, monitor=monitor)
        return render_template("status.html", rows=rows, serve=bool(app.config.get("SERVE")))

    @app.get("/api/status")
    def status_json() -> Response:
        monitor = request.args.get("monitor")
        with _repository() as repo:
            rows = latest_status(repo, monitor=monitor)
        body = {
            "note": "stored results only",
            "monitors": [row.as_public() for row in rows],
        }
        return jsonify(body)

    @app.post("/api/monitors")
    @require_api_key
    def create_monitor() -> tuple[Response, int]:
        if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
            return jsonify({"error": "body too large"}), 413
        record = monitor_from_payload(request.get_json(silent=True))
        with _repository() as repo:
            repo.save_monitor(record)
        return jsonify({"name": record.name, "enabled": record.enabled}), 201

    @app.route("/ping/<token>", methods=["GET", "POST"])
    def ping(token: str) -> tuple[str, int]:
        book: dict[str, HeartbeatCheck] = app.config["HEARTBEATS"]
        check = _live_heartbeat(token, book)
        if check is None:
            LOGGER.info("heartbeat ping rejected")
            return "not found", 404
        check.record_ping()
        if check.name == check.token:
            LOGGER.info("heartbeat ping accepted")
        else:
            LOGGER.info("heartbeat ping accepted monitor=%s", check.name)
        return "ok", 200

    if not app.config.get("SERVE"):

        @app.get("/admin/login")
        def login_form() -> str:
            return render_template("login.html", csrf_token=ensure_csrf(), failed=False)

        @app.post("/admin/login")
        def login_submit() -> RedirectResponse | tuple[str, int]:
            if not csrf_ok():
                return "csrf token rejected", 400
            username = request.form.get("username")
            if login_is_limited(username):
                LOGGER.info("admin login locked")
                return "try again later", 429
            if not login_ok(username, request.form.get("password")):
                record_login_failure(username)
                LOGGER.info("admin login failed")
                return render_template("login.html", csrf_token=ensure_csrf(), failed=True), 401
            clear_login_failures(username)
            session["user"] = app.config["ADMIN_USER"]
            LOGGER.info("admin login succeeded")
            return redirect(url_for("admin_page"))

        @app.post("/admin/logout")
        def logout() -> RedirectResponse | tuple[str, int]:
            if not csrf_ok():
                return "csrf token rejected", 400
            session.clear()
            return redirect(url_for("login_form"))

        @app.get("/admin")
        @login_required
        def admin_page() -> str:
            with _repository() as repo:
                records = repo.monitors()
            return render_template("admin.html", monitors=records, csrf_token=ensure_csrf())

        @app.post("/admin/monitors")
        @login_required
        def admin_update() -> RedirectResponse | tuple[str, int]:
            if not csrf_ok():
                return "csrf token rejected", 400
            name = parse_monitor_name(request.form.get("name"))
            enabled = parse_enabled(request.form.get("enabled"))
            with _repository() as repo:
                repo.update_monitor(name, enabled=enabled)
            return redirect(url_for("admin_page"))

    from pulseguard.web.console import register_console

    register_console(app)


def _repository() -> SqliteRepository:
    """Open a connection for this request. The caller closes it."""
    return SqliteRepository(Path(str(current_app.config["DATABASE"])))


def _serve_home() -> RedirectResponse:
    """Send a serve visitor to setup, login, or the dashboard."""
    from pulseguard.web.console import _signed_in

    if _signed_in() is not None:
        return redirect(url_for("dashboard"))
    with _repository() as repo:
        needs_setup = repo.user_count() == 0
    if needs_setup:
        return redirect(url_for("setup_form"))
    return redirect(url_for("console_login"))


def _serve_api_key(database: Path) -> str:
    """Return the stored API key, creating one on the first serve."""
    import secrets

    from pulseguard.storage import SqliteRepository

    with SqliteRepository(database) as repo:
        stored = repo.get_setting("api_key")
        if isinstance(stored, str) and stored:
            return require_api_key_value(stored)
        generated = secrets.token_urlsafe(18).replace("-", "a").replace("_", "b")
        if len(generated) < 16:
            generated = f"{generated}examplekey"
        repo.set_setting("api_key", generated[:128])
        return require_api_key_value(generated[:128])


def _live_heartbeat(token: str, book: dict[str, HeartbeatCheck]) -> HeartbeatCheck | None:
    """Prefer the scheduler's heartbeat so a catalog edit is the one that is pinged."""
    if _TOKEN_RE.fullmatch(token) is None:
        return None
    engine = current_app.config.get("ENGINE")
    if engine is not None:
        found = engine.heartbeat_for(token)
        if isinstance(found, HeartbeatCheck):
            return found
    return book.get(token)


def _index_heartbeats(checks: Sequence[HeartbeatCheck]) -> dict[str, HeartbeatCheck]:
    """Index ping targets by token. The token is the dict key, not a log field."""
    found: dict[str, HeartbeatCheck] = {}
    for check in checks:
        if not isinstance(check, HeartbeatCheck):
            raise ConfigValidationError("ping book only accepts heartbeat checks")
        if check.token in found:
            raise ConfigValidationError("heartbeat tokens must be unique")
        found[check.token] = check
    return found
