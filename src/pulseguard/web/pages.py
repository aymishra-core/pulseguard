"""Signed-in pages. Every change needs a login and a CSRF token.

Rendered pages do not fetch monitor URLs. Run-check is an explicit POST.
"""

from __future__ import annotations

import io
import json
import secrets
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.wrappers import Response as RedirectResponse

from pulseguard.agent.protocol import require_agent_host, require_agent_port, require_token
from pulseguard.checks.base import build_check
from pulseguard.checks.heartbeat import HeartbeatCheck
from pulseguard.checks.models import CheckResult, Severity, utc_now
from pulseguard.core.incidents import require_streak
from pulseguard.exceptions import AlertError, ConfigValidationError, PulseGuardError, StorageError
from pulseguard.exporters.backup import verify_backup, write_verified_backup
from pulseguard.exporters.csv_export import (
    read_monitors_csv,
    write_monitors_csv,
    write_results_csv,
)
from pulseguard.exporters.evidence_pack import NOT_LEGAL_ADVICE, write_evidence_pack
from pulseguard.exporters.json_export import write_monitors_json, write_results_json
from pulseguard.gui.sla_calculator import format_budget
from pulseguard.notifiers.settings_notifier import DatabaseNotifier
from pulseguard.storage.records import MonitorRecord, require_monitor_name
from pulseguard.web.auth import (
    csrf_ok,
    ensure_csrf,
    hash_password,
    require_admin_password,
    require_api_key_value,
)
from pulseguard.web.board import (
    certificate_band,
    days_remaining,
    latency_chart,
    latest_by_name,
    monitor_state,
    sparkline,
    uptime,
    window,
)
from pulseguard.web.console import _password_matches, _repository, _signed_in
from pulseguard.web.demo import load_demo

_KINDS = ("http", "keyword", "defacement", "tcp", "dns", "tls", "heartbeat")
_CSV_LIMIT = 1_000_000
_BACKUP_LIMIT = 20_000_000
_HTTP = {"http", "keyword", "defacement"}
_HOST = {"tcp", "tls", "dns"}


def register_pages(app: Flask) -> None:
    """Attach the console pages."""

    @app.get("/dashboard")
    def dashboard() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        with _repository() as repo:
            body = _dashboard_body(repo)
        return _page("dashboard.html", **body)

    @app.get("/api/dashboard")
    def dashboard_json() -> tuple[Response, int] | Response:
        if _signed_in() is None:
            return jsonify({"error": "login required"}), 401
        with _repository() as repo:
            body = _dashboard_body(repo)
        return jsonify(_json_ready(body))

    @app.post("/demo")
    def demo() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        with _repository() as repo:
            load_demo(repo)
        _flash("Demo monitors saved")
        return redirect(url_for("dashboard"))

    @app.get("/monitors")
    def monitor_list() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        with _repository() as repo:
            rows = _rows(repo)
        return _page("monitors.html", rows=rows)

    @app.route("/monitors/new", methods=["GET", "POST"])
    def monitor_new() -> str | RedirectResponse | tuple[str, int]:
        return _monitor_form(None)

    @app.route("/monitors/<name>", methods=["GET", "POST"])
    def monitor_edit(name: str) -> str | RedirectResponse | tuple[str, int]:
        try:
            chosen = require_monitor_name(name)
        except ConfigValidationError as exc:
            return str(exc), 404
        return _monitor_form(chosen)

    @app.get("/monitors/<name>/view")
    def monitor_detail(name: str) -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        try:
            chosen = require_monitor_name(name)
        except ConfigValidationError as exc:
            return str(exc), 404
        with _repository() as repo:
            record = _find(repo, chosen)
            if record is None:
                return "unknown monitor", 404
            results = list(repo.results_for(chosen))
            incidents = [row for row in repo.incidents() if row.monitor == chosen]
            options = repo.monitor_options(chosen)
        latest = results[-1] if results else None
        latencies = [row.latency_ms for row in results[-100:]]
        now = utc_now()
        return _page(
            "monitor_detail.html",
            record=record,
            state=monitor_state(record, latest),
            message="" if latest is None else latest.message,
            chart=latency_chart(latencies),
            results=list(reversed(results[-20:])),
            incidents=incidents,
            uptime_24=_pct(uptime(window(results, now, 24))),
            uptime_7=_pct(uptime(window(results, now, 24 * 7))),
            uptime_30=_pct(uptime(window(results, now, 24 * 30))),
            ping=_ping_line(record, options),
        )

    @app.post("/monitors/<name>/pause")
    def monitor_pause(name: str) -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        chosen = _safe_name(name)
        if chosen is None:
            return "unknown monitor", 404
        with _repository() as repo:
            record = _find(repo, chosen)
            if record is None:
                return "unknown monitor", 404
            repo.update_monitor(chosen, enabled=not record.enabled)
        _flash("Monitor updated")
        return redirect(url_for("monitor_detail", name=chosen))

    @app.post("/monitors/<name>/check")
    def monitor_check(name: str) -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        chosen = _safe_name(name)
        if chosen is None:
            return "unknown monitor", 404
        try:
            message = _run_check(chosen)
        except PulseGuardError as exc:
            if str(exc).startswith("unknown monitor"):
                return "unknown monitor", 404
            _flash(str(exc), "bad")
            return redirect(url_for("monitor_detail", name=chosen))
        _flash(message)
        return redirect(url_for("monitor_detail", name=chosen))

    @app.post("/monitors/<name>/duplicate")
    def monitor_duplicate(name: str) -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        chosen = _safe_name(name)
        if chosen is None:
            return "unknown monitor", 404
        with _repository() as repo:
            record = _find(repo, chosen)
            if record is None:
                return "unknown monitor", 404
            copy_name = _copy_name(chosen)
            target = record.target
            options = repo.monitor_options(chosen)
            if record.kind == "heartbeat":
                target = HeartbeatCheck.new_token()
            repo.save_monitor(
                MonitorRecord(
                    name=copy_name,
                    kind=record.kind,
                    target=target,
                    interval_seconds=record.interval_seconds,
                    enabled=record.enabled,
                )
            )
            repo.save_options(copy_name, options)
        _flash("Monitor duplicated")
        return redirect(url_for("monitor_edit", name=copy_name))

    @app.post("/monitors/<name>/delete")
    def monitor_delete(name: str) -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        chosen = _safe_name(name)
        if chosen is None:
            return "unknown monitor", 404
        with _repository() as repo:
            removed = repo.delete_monitor(chosen)
            repo.delete_monitor_details(chosen)
            if not removed:
                return "unknown monitor", 404
        _flash("Monitor deleted")
        return redirect(url_for("monitor_list"))

    @app.get("/incidents")
    def incident_list() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        tab = request.args.get("tab", "open")
        if tab not in {"open", "resolved"}:
            tab = "open"
        with _repository() as repo:
            rows = repo.incidents(state=tab)
        return _page("incidents.html", rows=rows, tab=tab, advice=NOT_LEGAL_ADVICE)

    @app.get("/incidents/<int:incident_id>")
    def incident_detail(incident_id: int) -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        with _repository() as repo:
            row = _incident(repo, incident_id)
            if row is None:
                return "unknown incident", 404
            results = [
                item
                for item in repo.results_for(row.monitor)
                if _in_incident(item.checked_at, row.started_at, row.ended_at)
            ]
        return _page("incident.html", row=row, results=results, advice=NOT_LEGAL_ADVICE)

    @app.get("/incidents/<int:incident_id>/evidence")
    def incident_evidence(incident_id: int) -> str | RedirectResponse | Response | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        with _repository() as repo:
            row = _incident(repo, incident_id)
            if row is None:
                return "unknown incident", 404
            results = list(repo.results_for(row.monitor))
        root = _data_dir()
        folder = root / "evidence" / str(incident_id)
        if not _inside(folder, root):
            return "upload path is not allowed", 400
        try:
            opened = datetime.fromisoformat(row.started_at)
            write_evidence_pack(
                folder,
                monitor=row.monitor,
                opened_at=opened,
                results=results,
                now=utc_now(),
            )
        except (AlertError, ValueError) as exc:
            return str(exc), 400
        files = sorted(path for path in folder.iterdir() if path.is_file())
        if len(files) > 1:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for path in files:
                    archive.write(path, arcname=path.name)
            buffer.seek(0)
            return send_file(
                buffer,
                as_attachment=True,
                download_name="evidence.zip",
                mimetype="application/zip",
            )
        if not files:
            return "evidence pack is empty", 400
        return send_file(files[0], as_attachment=True, download_name=files[0].name)

    @app.get("/certificates")
    def certificates() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        now = utc_now()
        with _repository() as repo:
            latest = latest_by_name(list(repo.iter_results(None)))
            rows = []
            for record in repo.monitors():
                if record.kind != "tls":
                    continue
                result = latest.get(record.name)
                message = "" if result is None else result.message
                days = None if result is None else days_remaining(message, now)
                rows.append(
                    {
                        "name": record.name,
                        "message": message or "no results yet",
                        "days": days,
                        "band": certificate_band(days),
                    }
                )
        rows.sort(key=lambda item: item["days"] if isinstance(item["days"], int) else 10_000)
        return _page("certificates.html", rows=rows)

    @app.route("/alerts", methods=["GET", "POST"])
    def alerts() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        error = ""
        if request.method == "POST":
            if not csrf_ok():
                return "csrf token rejected", 400
            try:
                _save_alerts()
                _flash("Alert settings saved")
                return redirect(url_for("alerts"))
            except ConfigValidationError as exc:
                error = str(exc)
        with _repository() as repo:
            stored = _alert_form(repo)
            log = repo.alerts()
        return _page("alerts.html", log=log, settings=stored, error=error)

    @app.post("/alerts/test")
    def alert_test() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        status, error = _send_test()
        with _repository() as repo:
            repo.add_alert(
                created_at=utc_now(),
                monitor="smtp-test",
                kind="test",
                recipient=repo.get_setting("smtp_to") or "",
                status=status,
                error_text=error[:500],
            )
        _flash(error or "Test email sent", "bad" if error else "ok")
        return redirect(url_for("alerts"))

    @app.route("/sla", methods=["GET", "POST"])
    def sla() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        result = ""
        error = ""
        if request.method == "POST":
            if not csrf_ok():
                return "csrf token rejected", 400
            try:
                result = _sla_text()
            except ConfigValidationError as exc:
                error = str(exc)
        with _repository() as repo:
            names = [record.name for record in repo.monitors()]
        return _page(
            "sla.html",
            names=names,
            result=result,
            error=error,
            successes=request.form.get("successes", ""),
            total=request.form.get("total", ""),
            target=request.form.get("target", "0.99") or "0.99",
        )

    @app.route("/agents", methods=["GET", "POST"])
    def agents() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        error = ""
        if request.method == "POST":
            if not csrf_ok():
                return "csrf token rejected", 400
            try:
                _save_agent()
                _flash("Agent settings saved")
                return redirect(url_for("agents"))
            except ConfigValidationError as exc:
                error = str(exc)
        revealed = session.pop("agent_token_once", "")
        with _repository() as repo:
            seen = _agent_seen(repo.get_setting("agent_seen"))
            enabled = repo.get_setting("agent_enabled") == "1"
            host = repo.get_setting("agent_host") or "127.0.0.1"
            port = repo.get_setting("agent_port") or "8791"
            has_token = bool(repo.get_setting("agent_token"))
        engine = current_app.config.get("ENGINE")
        bound = engine.agent_port if engine is not None else None
        return _page(
            "agents.html",
            error=error,
            revealed=revealed if isinstance(revealed, str) else "",
            seen=seen,
            enabled=enabled,
            host=host,
            port=port,
            has_token=has_token,
            bound=bound,
        )

    @app.post("/agents/token")
    def agent_token() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        token = require_token(HeartbeatCheck.new_token())
        with _repository() as repo:
            repo.set_setting("agent_token", token)
        session["agent_token_once"] = token
        _flash("New agent token. Copy it now. It is not shown again.")
        return redirect(url_for("agents"))

    @app.route("/data", methods=["GET"])
    def data_page() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        return _page("data.html", report="")

    @app.post("/data/export")
    def data_export() -> str | RedirectResponse | Response | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        table = request.form.get("table", "results")
        fmt = request.form.get("format", "csv")
        if table not in {"results", "monitors"} or fmt not in {"csv", "json"}:
            return "unknown export", 400
        buffer = io.StringIO()
        with _repository() as repo:
            if table == "results" and fmt == "csv":
                write_results_csv(repo.iter_results(None), buffer)
            elif table == "results":
                write_results_json(repo.iter_results(None), buffer)
            elif fmt == "csv":
                write_monitors_csv(repo.monitors(), buffer)
            else:
                write_monitors_json(repo.monitors(), buffer)
        mime = "text/csv" if fmt == "csv" else "application/json"
        return Response(
            buffer.getvalue(),
            mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{table}.{fmt}"'},
        )

    @app.post("/data/import")
    def data_import() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        try:
            payload = _upload_bytes("file", _CSV_LIMIT)
            records = read_monitors_csv(io.StringIO(payload.decode("utf-8")))
        except UnicodeError:
            _flash("file must be utf-8 text", "bad")
            return redirect(url_for("data_page"))
        except (ConfigValidationError, PulseGuardError) as exc:
            _flash(str(exc), "bad")
            return redirect(url_for("data_page"))
        with _repository() as repo:
            for record in records:
                repo.save_monitor(record)
        _flash(f"Imported {len(records)} monitors")
        return redirect(url_for("data_page"))

    @app.post("/data/backup")
    def data_backup() -> str | RedirectResponse | Response | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        root = _data_dir()
        dest = root / "uploads" / f"{secrets.token_hex(8)}.pgdb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not _inside(dest, root):
            return "upload path is not allowed", 400
        with _repository() as repo:
            write_verified_backup(repo.snapshot_bytes(), dest)
        data = dest.read_bytes()
        dest.unlink()
        return Response(
            data,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="pulseguard.pgdb"'},
        )

    @app.post("/data/verify")
    def data_verify() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        try:
            path = _upload_path("file", _BACKUP_LIMIT, ".pgdb")
            digest = verify_backup(path)
        except (ConfigValidationError, StorageError) as exc:
            _flash(str(exc), "bad")
            return redirect(url_for("data_page"))
        _flash(f"checksum OK {digest}")
        return redirect(url_for("data_page"))

    @app.post("/data/restore")
    def data_restore() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        if request.form.get("confirm") != "RESTORE":
            _flash("Type RESTORE to confirm", "bad")
            return redirect(url_for("data_page"))
        database = Path(str(current_app.config["DATABASE"]))
        root = _data_dir()
        if not _inside(database, root):
            _flash("upload path is not allowed", "bad")
            return redirect(url_for("data_page"))
        try:
            path = _upload_path("file", _BACKUP_LIMIT, ".pgdb")
            payload = _checked_backup(path)
            safety = root / "uploads" / f"safety-{secrets.token_hex(4)}.pgdb"
            with _repository() as repo:
                write_verified_backup(repo.snapshot_bytes(), safety)
            database.write_bytes(payload)
        except (ConfigValidationError, StorageError, OSError) as exc:
            _flash(str(exc), "bad")
            return redirect(url_for("data_page"))
        _flash("Restored. Restart PulseGuard so open connections read the new file.")
        return redirect(url_for("data_page"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        error = ""
        if request.method == "POST":
            if not csrf_ok():
                return "csrf token rejected", 400
            try:
                _save_settings(user)
                _flash("Settings saved")
                return redirect(url_for("settings"))
            except ConfigValidationError as exc:
                error = str(exc)
        with _repository() as repo:
            public = repo.get_setting("public_status") != "0"
            api_key = repo.get_setting("api_key") or current_app.config.get("API_KEY", "")
        return _page("settings.html", error=error, public=public, api_key=api_key)

    @app.post("/settings/api-key")
    def settings_api_key() -> str | RedirectResponse | tuple[str, int]:
        denied = _guard()
        if denied is not None:
            return denied
        generated = require_api_key_value(_api_key())
        with _repository() as repo:
            repo.set_setting("api_key", generated)
        current_app.config["API_KEY"] = generated
        _flash("API key replaced")
        return redirect(url_for("settings"))

    @app.get("/help")
    def help_page() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        return _page("help.html")

    app.add_template_global(_duration, name="duration")


def _monitor_form(name: str | None) -> str | RedirectResponse | tuple[str, int]:
    """Create or edit one monitor. The validator message is shown on the form."""
    user = _need_user()
    if not isinstance(user, str):
        return user
    values: dict[str, object] = {"kind": "http", "interval": 30, "enabled": True}
    error = ""
    if request.method == "POST":
        if not csrf_ok():
            return "csrf token rejected", 400
        try:
            _save_monitor(name)
            _flash("Monitor saved")
            target = name or str(request.form.get("name", ""))
            return redirect(url_for("monitor_detail", name=target))
        except ConfigValidationError as exc:
            error = str(exc)
            values = _posted_values()
    elif name is not None:
        with _repository() as repo:
            record = _find(repo, name)
            if record is None:
                return "unknown monitor", 404
            values = _record_values(record, repo.monitor_options(name))
    with _repository() as repo:
        others = [record.name for record in repo.monitors() if record.name != name]
    return _page(
        "monitor_form.html",
        values=values,
        error=error,
        others=others,
        editing=name is not None,
        kinds=_KINDS,
    )


def _save_monitor(existing: str | None) -> None:
    """Validate with the check registry, then store the catalog row and options."""
    spec, options, enabled = _spec_from_form(existing)
    check = build_check(spec)
    if existing is not None and check.name != existing:
        raise ConfigValidationError("the monitor name cannot change")
    record = MonitorRecord(
        name=check.name,
        kind=check.kind,
        target=check.target,
        interval_seconds=int(check.interval),
        enabled=enabled,
    )
    with _repository() as repo:
        if existing is None and _find(repo, record.name) is not None:
            raise ConfigValidationError("a monitor with that name already exists")
        repo.save_monitor(record)
        repo.save_options(record.name, options)


def _spec_from_form(existing: str | None) -> tuple[dict[str, object], dict[str, object], bool]:
    """Build a check spec from the posted form."""
    kind = str(request.form.get("kind", ""))
    if kind not in _KINDS:
        raise ConfigValidationError("unknown check kind")
    name = existing if existing is not None else str(request.form.get("name", "")).strip()
    interval = _number("interval", default=30)
    spec: dict[str, object] = {"kind": kind, "name": name, "interval": interval}
    options: dict[str, object] = {}
    if kind in _HTTP or kind in _HOST:
        spec["target"] = str(request.form.get("target", "")).strip()
    if kind == "heartbeat":
        token = str(request.form.get("token", "")).strip() or HeartbeatCheck.new_token()
        spec["target"] = token
        grace = _number("grace_seconds", default=None)
        if grace is not None:
            spec["grace_seconds"] = grace
            options["grace_seconds"] = grace
    if kind in _HTTP:
        _copy_optional(spec, options, "expected_status", "method", "retries", "retry_backoff")
        spec["allow_private"] = _checked("allow_private")
        options["allow_private"] = spec["allow_private"]
    if kind in {"keyword", "defacement"}:
        spec["keyword"] = str(request.form.get("keyword", "")).strip()
        options["keyword"] = spec["keyword"]
        _copy_optional(spec, options, "max_body_bytes")
    if kind == "defacement":
        spec["expected_sha256"] = str(request.form.get("expected_sha256", "")).strip()
        options["expected_sha256"] = spec["expected_sha256"]
        _copy_optional(spec, options, "alert_after")
    if kind in {"tcp", "tls"}:
        port = _number("port", default=None)
        if port is None:
            raise ConfigValidationError("missing port")
        spec["port"] = port
        options["port"] = port
        spec["allow_private"] = _checked("allow_private")
        options["allow_private"] = spec["allow_private"]
    if kind == "dns":
        expected = [
            part.strip()
            for part in str(request.form.get("expected", "")).split(",")
            if part.strip()
        ]
        spec["expected"] = expected
        options["expected"] = expected
        spec["allow_private"] = _checked("allow_private")
        options["allow_private"] = spec["allow_private"]
    timeout = _number("timeout", default=None)
    if timeout is not None:
        spec["timeout"] = timeout
        options["timeout"] = timeout
    depends = [item for item in request.form.getlist("depends_on") if item and item != name]
    if len(depends) > 8:
        raise ConfigValidationError("depends_on must list at most 8 monitors")
    if depends:
        options["depends_on"] = depends
    enabled = _checked("enabled")
    return spec, options, enabled


def _copy_optional(spec: dict[str, object], options: dict[str, object], *names: str) -> None:
    """Copy posted numbers and short strings when the field is not blank."""
    for field in names:
        raw = str(request.form.get(field, "")).strip()
        if not raw:
            continue
        if field in {"expected_status", "retries", "max_body_bytes", "alert_after"}:
            if not raw.isdigit():
                raise ConfigValidationError(f"{field} must be an integer")
            spec[field] = int(raw)
        elif field == "retry_backoff":
            try:
                spec[field] = float(raw)
            except ValueError as exc:
                raise ConfigValidationError(f"{field} must be a number") from exc
        else:
            spec[field] = raw
        options[field] = spec[field]


def _number(field: str, default: int | None) -> int | None:
    """Return a posted integer, or the default when the field is blank."""
    raw = str(request.form.get(field, "")).strip()
    if not raw:
        return default
    if not raw.isdigit():
        raise ConfigValidationError(f"{field} must be an integer")
    return int(raw)


def _checked(field: str) -> bool:
    """Return True when a checkbox was ticked."""
    return request.form.get(field) == "1"


def _posted_values() -> dict[str, object]:
    """Return the posted form so a validation error can refill it."""
    values: dict[str, object] = {key: request.form.get(key, "") for key in request.form}
    values["depends_on"] = request.form.getlist("depends_on")
    values["enabled"] = _checked("enabled")
    values["allow_private"] = _checked("allow_private")
    return values


def _record_values(record: MonitorRecord, options: dict[str, object]) -> dict[str, object]:
    """Flatten a stored monitor into form values."""
    values: dict[str, object] = {
        "name": record.name,
        "kind": record.kind,
        "target": record.target,
        "token": record.target if record.kind == "heartbeat" else "",
        "interval": record.interval_seconds,
        "enabled": record.enabled,
    }
    values.update(options)
    expected = options.get("expected")
    if isinstance(expected, list):
        values["expected"] = ", ".join(str(item) for item in expected)
    return values


def _dashboard_body(repo: Any) -> dict[str, Any]:
    """Collect the summary and the cards from stored rows."""
    records = repo.monitors()
    results = list(repo.iter_results(None))
    latest = latest_by_name(results)
    now = utc_now()
    cards = []
    counts = {"up": 0, "down": 0, "unknown": 0, "paused": 0}
    certs = 0
    for record in records:
        result = latest.get(record.name)
        state = monitor_state(record, result)
        counts[state] = counts.get(state, 0) + 1
        mine = [item for item in results if item.monitor == record.name]
        recent = window(mine, now, 24)
        if record.kind == "tls" and result is not None:
            remaining = days_remaining(result.message, now)
            if remaining is not None and remaining <= 30:
                certs += 1
        cards.append(
            {
                "name": record.name,
                "kind": record.kind,
                "state": state,
                "message": "" if result is None else result.message,
                "checked": ""
                if result is None
                else result.checked_at.strftime("%Y-%m-%d %H:%M:%S"),
                "uptime": _pct(uptime(recent)),
                "spark": sparkline([item.latency_ms for item in mine]),
                "points": [item.latency_ms for item in mine[-20:]],
            }
        )
    day = window(results, now, 24)
    return {
        "cards": cards,
        "counts": counts,
        "open_incidents": len(repo.incidents(state="open")),
        "certs": certs,
        "uptime": _pct(uptime(day)),
        "empty": not records,
    }


def _json_ready(body: dict[str, Any]) -> dict[str, Any]:
    """Drop the SVG markup. The browser draws the sparkline from numbers."""
    cards = []
    for card in body["cards"]:
        copy = dict(card)
        copy.pop("spark", None)
        cards.append(copy)
    return {
        "counts": body["counts"],
        "open_incidents": body["open_incidents"],
        "certs": body["certs"],
        "uptime": body["uptime"],
        "monitors": cards,
    }


def _rows(repo: Any) -> list[dict[str, object]]:
    """Return the monitor table."""
    latest = latest_by_name(list(repo.iter_results(None)))
    rows = []
    for record in repo.monitors():
        result = latest.get(record.name)
        rows.append(
            {
                "name": record.name,
                "kind": record.kind,
                "state": monitor_state(record, result),
                "target": record.target if record.kind != "heartbeat" else "token hidden",
                "interval": record.interval_seconds,
            }
        )
    return rows


def _run_check(name: str) -> str:
    """Run one monitor now. The scheduler stores the row when it is running."""
    engine = current_app.config.get("ENGINE")
    if engine is not None:
        message = engine.run_now(name)
        if isinstance(message, str):
            return message
    with _repository() as repo:
        record = _find(repo, name)
        if record is None:
            raise ConfigValidationError(f"unknown monitor {name!r}")
        spec = {
            "kind": record.kind,
            "name": record.name,
            "target": record.target,
            "interval": record.interval_seconds,
        }
        spec.update(
            {key: value for key, value in repo.monitor_options(name).items() if key != "depends_on"}
        )
        result = build_check(spec).run()
        repo.add_result(result)
    return result.message


def _save_alerts() -> None:
    """Store SMTP fields and the open/resolve thresholds. A blank password stays."""
    failures = require_streak(
        _number("failures_before_open", default=3) or 3, "failures_before_open"
    )
    successes = require_streak(
        _number("successes_before_resolve", default=1) or 1,
        "successes_before_resolve",
    )
    with _repository() as repo:
        repo.set_setting("failures_before_open", str(failures))
        repo.set_setting("successes_before_resolve", str(successes))
        for field in ("smtp_host", "smtp_port", "smtp_user", "smtp_from", "smtp_to"):
            repo.set_setting(field, str(request.form.get(field, "")).strip())
        password = str(request.form.get("smtp_password", ""))
        if password:
            repo.set_setting("smtp_password", password)
        repo.set_setting("smtp_starttls", "1" if _checked("smtp_starttls") else "0")
        _check_smtp(repo)


def _check_smtp(repo: Any) -> None:
    """Reject a half-filled SMTP form. An empty host is allowed."""
    host = repo.get_setting("smtp_host") or ""
    if not host.strip():
        return
    DatabaseNotifier(lambda: _repository()).mapping()
    from pulseguard.notifiers.email_notifier import smtp_settings_from_env

    smtp_settings_from_env(DatabaseNotifier(lambda: _repository()).mapping())


def _alert_form(repo: Any) -> dict[str, object]:
    """Return SMTP fields for the form. The password value is never included."""
    return {
        "failures_before_open": repo.get_setting("failures_before_open") or "3",
        "successes_before_resolve": repo.get_setting("successes_before_resolve") or "1",
        "smtp_host": repo.get_setting("smtp_host") or "",
        "smtp_port": repo.get_setting("smtp_port") or "587",
        "smtp_user": repo.get_setting("smtp_user") or "",
        "smtp_from": repo.get_setting("smtp_from") or "",
        "smtp_to": repo.get_setting("smtp_to") or "",
        "smtp_starttls": repo.get_setting("smtp_starttls") == "1",
        "password_set": bool(repo.get_setting("smtp_password")),
    }


def _send_test() -> tuple[str, str]:
    """Send a test message and return ``(status, error)``."""
    result = CheckResult(
        monitor="smtp-test",
        kind="http",
        ok=True,
        latency_ms=0,
        message="test email",
        severity=Severity.INFO,
        checked_at=utc_now(),
    )
    try:
        DatabaseNotifier(lambda: _repository()).send(result)
    except AlertError as exc:
        if str(exc) == "not configured":
            return "not-configured", "not configured"
        return "failed", str(exc)
    except ConfigValidationError as exc:
        return "failed", str(exc)
    return "sent", ""


def _sla_text() -> str:
    """Return the calculator text, or the from-my-data text."""
    if request.form.get("source") == "data":
        name = require_monitor_name(str(request.form.get("monitor", "")))
        hours = _number("hours", default=24) or 24
        target = float(str(request.form.get("target", "0.99")))
        with _repository() as repo:
            results = window(list(repo.results_for(name)), utc_now(), hours)
        good = sum(1 for item in results if item.ok)
        if not results:
            return "no samples"
        return format_budget(good, len(results), target)
    successes = _number("successes", default=None)
    total = _number("total", default=None)
    target = float(str(request.form.get("target", "0.99")))
    if successes is None or total is None:
        raise ConfigValidationError("success count must be an integer")
    return format_budget(successes, total, target)


def _save_agent() -> None:
    """Store the collector switch. The bind address stays loopback or private."""
    host = require_agent_host(str(request.form.get("host", "127.0.0.1")).strip())
    port = require_agent_port(_number("port", default=8791) or 8791, allow_ephemeral=False)
    enabled = "1" if _checked("enabled") else "0"
    with _repository() as repo:
        repo.set_setting("agent_host", host)
        repo.set_setting("agent_port", str(port))
        repo.set_setting("agent_enabled", enabled)


def _save_settings(username: str) -> None:
    """Change the password and the public-page switch. The API key is a separate button."""
    with _repository() as repo:
        repo.set_setting("public_status", "1" if _checked("public_status") else "0")
        new_password = str(request.form.get("new_password", ""))
        if not new_password:
            return
        if not _password_matches(repo, username, request.form.get("current_password")):
            raise ConfigValidationError("login failed")
        password = require_admin_password(new_password)
        salt, digest = hash_password(password)
        del password
        repo.save_user(username, salt.hex(), digest.hex())


def _agent_seen(raw: str | None) -> list[tuple[str, str]]:
    """Return agent name and last-seen time pairs."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    return sorted((str(key), str(value)) for key, value in parsed.items())


def _page(template: str, **extra: Any) -> str:
    """Render a signed-in page. The caller already checked the login."""
    user = _signed_in() or ""
    text = session.pop("flash", "")
    level = session.pop("flash_level", "ok")
    with _repository() as repo:
        dot = _dot(repo)
    return render_template(
        template,
        user=user,
        csrf_token=ensure_csrf(),
        dot=dot,
        flash=text if isinstance(text, str) else "",
        flash_level=level if isinstance(level, str) else "ok",
        **extra,
    )


def _dot(repo: Any) -> str:
    """Return the global state word for the top bar."""
    latest = latest_by_name(list(repo.iter_results(None)))
    states = [monitor_state(record, latest.get(record.name)) for record in repo.monitors()]
    if "down" in states:
        return "down"
    if "unknown" in states or not states:
        return "unknown"
    if all(item == "paused" for item in states):
        return "paused"
    return "up"


def _need_user() -> str | RedirectResponse | tuple[str, int]:
    """Return the username, or a redirect to login or setup."""
    user = _signed_in()
    if user is None:
        return redirect(url_for("console_login"))
    return user


def _guard() -> str | RedirectResponse | tuple[str, int] | None:
    """Refuse a change that is anonymous or missing CSRF."""
    if _signed_in() is None:
        return redirect(url_for("console_login"))
    if not csrf_ok():
        return "csrf token rejected", 400
    return None


def _flash(text: str, level: str = "ok") -> None:
    """Store one toast. Do not put a token or a password in ``text``."""
    session["flash"] = text
    session["flash_level"] = level


def _find(repo: Any, name: str) -> MonitorRecord | None:
    """Return one catalog row, or None."""
    found: MonitorRecord | None = None
    for record in repo.monitors():
        if record.name == name and isinstance(record, MonitorRecord):
            found = record
    return found


def _incident(repo: Any, incident_id: int) -> Any:
    """Return one incident row, or None."""
    for row in repo.incidents():
        if row.id == incident_id:
            return row
    return None


def _safe_name(name: str) -> str | None:
    """Return a monitor name, or None when it is not a valid name."""
    try:
        return require_monitor_name(name)
    except ConfigValidationError:
        return None


def _copy_name(name: str) -> str:
    """Return a new name based on ``name``."""
    candidate = f"{name}-copy"
    if len(candidate) > 128:
        raise ConfigValidationError("the copy name is too long")
    return require_monitor_name(candidate)


def _ping_line(record: MonitorRecord, options: dict[str, object]) -> str:
    """Return a curl line for a heartbeat. Other kinds return an empty string."""
    del options
    if record.kind != "heartbeat":
        return ""
    url = request.host_url.rstrip("/") + "/ping/" + record.target
    return f"curl -fsS {url!r}"


def _in_incident(checked: datetime, started: str, ended: str) -> bool:
    """Return True when a result falls inside the incident window."""
    try:
        start = datetime.fromisoformat(started)
    except ValueError:
        return False
    if checked < start:
        return False
    if not ended:
        return True
    try:
        stop = datetime.fromisoformat(ended)
    except ValueError:
        return True
    return checked <= stop


def _upload_bytes(field: str, limit: int) -> bytes:
    """Read one upload into memory. The client filename is ignored."""
    upload = request.files.get(field)
    if upload is None:
        raise ConfigValidationError("choose a file")
    data = upload.read(limit + 1)
    if not isinstance(data, bytes):
        raise ConfigValidationError("choose a file")
    if len(data) > limit:
        raise ConfigValidationError("file is too large")
    if not data:
        raise ConfigValidationError("file is empty")
    return data


def _upload_path(field: str, limit: int, suffix: str) -> Path:
    """Store an upload under the data directory with a generated name."""
    data = _upload_bytes(field, limit)
    root = _data_dir()
    folder = root / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{secrets.token_hex(8)}{suffix}"
    if not _inside(dest, root):
        raise ConfigValidationError("upload path is not allowed")
    dest.write_bytes(data)
    return dest


def _checked_backup(path: Path) -> bytes:
    """Return a backup payload after the checksum matches."""
    from pulseguard.exporters.backup import read_verified_backup

    return read_verified_backup(path)


def _data_dir() -> Path:
    """Return the configured data directory."""
    return Path(str(current_app.config["DATA_DIR"]))


def _inside(path: Path, root: Path) -> bool:
    """Return True when ``path`` stays inside ``root``."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _pct(value: float | None) -> str:
    """Render a fraction as a percent."""
    if value is None:
        return "—"
    text = f"{value * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _api_key() -> str:
    """Return a new API key in the allowed alphabet."""
    return secrets.token_urlsafe(18).replace("-", "a").replace("_", "b")


def _duration(started: str, ended: str) -> str:
    """Return a short duration. Used by the incident table."""
    try:
        start = datetime.fromisoformat(started)
        stop = datetime.fromisoformat(ended) if ended else datetime.now(UTC)
    except ValueError:
        return ""
    seconds = int((stop - start).total_seconds())
    if seconds < 0:
        return ""
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"
