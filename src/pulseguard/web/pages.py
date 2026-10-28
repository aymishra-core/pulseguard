"""Signed-in pages. Every change needs a login and a CSRF token.

Rendered pages do not fetch monitor URLs. Run-check is an explicit POST.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import secrets
import time
import zipfile
from datetime import UTC, datetime, timedelta
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

from pulseguard import __version__
from pulseguard.agent.protocol import require_agent_host, require_agent_port, require_token
from pulseguard.checks.base import build_check
from pulseguard.checks.defacement import DefacementCheck
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
    activity_lines,
    certificate_band,
    clock,
    clock_hm,
    clock_hms,
    clock_span,
    dashboard_hero,
    days_remaining,
    hint_for,
    hourly_counts,
    latency_chart,
    latest_by_name,
    local_day_start,
    mean_successful_ms,
    monitor_state,
    plain_message,
    segments,
    short_span,
    sparkline,
    stamp,
    uptime,
    window,
)
from pulseguard.web.console import _password_matches, _repository, _signed_in
from pulseguard.web.field_rules import (
    PLACEHOLDERS,
    blocked_monitors,
    port_problem,
    target_message,
    target_problem,
)

_KINDS = ("http", "keyword", "defacement", "tcp", "dns", "tls", "heartbeat")
_ICONS = {
    "http": "globe",
    "keyword": "search",
    "defacement": "eye",
    "tcp": "plug",
    "dns": "at",
    "tls": "shield",
    "heartbeat": "heart",
}
_BAR = {"up": "o", "down": "x", "gap": "g", "empty": "g"}
_NAV = {
    "dashboard": "dashboard",
    "monitor_list": "monitors",
    "monitor_new": "monitors",
    "monitor_edit": "monitors",
    "monitor_detail": "monitors",
    "incident_list": "incidents",
    "incident_detail": "incidents",
    "certificates": "certificates",
    "alerts": "alerts",
    "sla": "sla",
    "agents": "agents",
    "data_page": "data",
    "settings": "settings",
    "help_page": "help",
}
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
            snap = build_snapshot(repo, utc_now())
        return _page("dashboard.html", snapshot=snap)

    @app.get("/api/dashboard")
    def dashboard_json() -> tuple[Response, int] | Response:
        if _signed_in() is None:
            return jsonify({"error": "login required"}), 401
        offset = _tz_offset()
        with _repository() as repo:
            snap = build_snapshot(repo, utc_now(), offset_minutes=offset)
        tag = _etag(snap)
        if request.headers.get("If-None-Match") == tag:
            response = Response(status=304)
            response.headers["ETag"] = tag
            return response
        response = jsonify(_json_ready(snap))
        response.headers["ETag"] = tag
        return response

    @app.get("/api/palette")
    def palette_json() -> tuple[Response, int] | Response:
        """Return pages, monitors, and incidents for the command palette."""
        if _signed_in() is None:
            return jsonify({"error": "login required"}), 401
        with _repository() as repo:
            monitors = [
                {"name": record.name, "href": url_for("monitor_detail", name=record.name)}
                for record in repo.monitors()
            ]
            incidents = [
                {
                    "id": row.id,
                    "label": f"INC-{row.id:04d}",
                    "monitor": row.monitor,
                    "state": row.state,
                    "href": url_for("incident_detail", incident_id=row.id),
                }
                for row in repo.incidents()[:20]
            ]
        return jsonify({"pages": _palette_pages(), "monitors": monitors, "incidents": incidents})

    @app.get("/api/activity")
    def activity_json() -> tuple[Response, int] | Response:
        if _signed_in() is None:
            return jsonify({"error": "login required"}), 401
        try:
            marker = int(request.args.get("since", "0") or "0")
        except ValueError:
            marker = 0
        with _repository() as repo:
            snap = build_snapshot(repo, utc_now())
        rows = [row for row in snap["log"] if int(row["id"]) > marker]
        rows.sort(key=lambda row: str(row["at"]))
        feed = [row for row in snap["activity"] if int(row["id"]) > marker]
        feed.sort(key=lambda row: str(row["at"]))
        return jsonify(
            {
                "generated_at": snap["generated_at"],
                "generated_ms": snap["generated_ms"],
                "events": rows,
                "activity": feed,
            }
        )

    @app.get("/monitors")
    def monitor_list() -> str | RedirectResponse | tuple[str, int]:
        user = _need_user()
        if not isinstance(user, str):
            return user
        status = request.args.get("status", "")
        sort = request.args.get("sort", "")
        with _repository() as repo:
            snap = build_snapshot(repo, utc_now())
        shown, chosen, ordered = _filter_cards(list(snap["cards"]), status, sort)
        return _page(
            "monitors.html",
            snapshot=snap,
            cards=shown,
            list_status=chosen,
            list_sort=ordered,
        )

    @app.route("/monitors/new", methods=["GET", "POST"])
    def monitor_new() -> str | RedirectResponse | tuple[str, int]:
        return _monitor_form(None)

    @app.post("/monitors/run")
    def monitor_run_all() -> Response | str | RedirectResponse | tuple[str, int]:
        """Run every enabled monitor once. Login and CSRF are required."""
        denied = _guard()
        if denied is not None:
            return denied
        with _repository() as repo:
            names = [record.name for record in repo.monitors() if record.enabled]
        ran = 0
        for name in names:
            try:
                _run_check(name)
            except PulseGuardError:
                continue
            ran += 1
        if request.accept_mimetypes["application/json"] > request.accept_mimetypes["text/html"]:
            return jsonify({"ran": ran, "total": len(names)})
        _flash(f"Ran {ran} checks")
        return redirect(url_for("dashboard"))

    @app.post("/monitors/probe")
    def monitor_probe() -> (
        Response | tuple[Response, int] | str | RedirectResponse | tuple[str, int]
    ):
        """Run one unsaved check. Login, CSRF, and a rate limit are required."""
        denied = _guard()
        if denied is not None:
            return denied
        if not _probe_allowed(_signed_in() or ""):
            return jsonify({"error": "slow down"}), 429
        try:
            spec, _options, _enabled = _spec_from_form(None)
            check = build_check(spec)
        except ConfigValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        result = check.run()
        return jsonify(
            {
                "ok": result.ok,
                "state": "up" if result.ok else "down",
                "message": plain_message(result.message),
                "latency_ms": round(result.latency_ms),
            }
        )

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
        today = [row for row in results if row.checked_at.date() == now.date()]
        return _page(
            "monitor_detail.html",
            record=record,
            state=monitor_state(record, latest),
            message="" if latest is None else latest.message,
            plain=plain_message(latest.message) if latest is not None else "No results yet.",
            chart=latency_chart(latencies),
            results=list(reversed(results[-20:])),
            incidents=incidents,
            uptime_24=_pct(uptime(window(results, now, 24))),
            uptime_7=_pct(uptime(window(results, now, 24 * 7))),
            uptime_30=_pct(uptime(window(results, now, 24 * 30))),
            average=_latency_text(latencies, 0.5),
            p95=_latency_text(latencies, 0.95),
            checks_today=len(today),
            state_for=_state_span(results, now),
            bar=segments(results, now),
            ping=_ping_line(record, options),
            series=[
                [
                    stamp(row.checked_at),
                    None if row.message == "monitoring gap" else round(row.latency_ms),
                    bool(row.ok) and row.message != "monitoring gap",
                ]
                for row in results[-400:]
            ],
            failures=[stamp(row.checked_at) for row in results[-400:] if not row.ok],
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
        tab = request.args.get("status") or request.args.get("tab") or "open"
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
            ended = row.ended_at or "9999"
            alerts = [
                item
                for item in repo.alerts()
                if item.monitor == row.monitor and row.started_at <= item.created_at <= ended
            ]
        return _page(
            "incident.html",
            row=row,
            results=results,
            alerts=alerts,
            advice=NOT_LEGAL_ADVICE,
        )

    @app.post("/incidents/<int:incident_id>/close")
    def incident_close(incident_id: int) -> str | RedirectResponse | tuple[str, int]:
        """Close one open incident. Login and CSRF are required."""
        denied = _guard()
        if denied is not None:
            return denied
        with _repository() as repo:
            row = _incident(repo, incident_id)
            if row is None:
                return "unknown incident", 404
            if row.state != "open":
                _flash("Incident is already closed", "bad")
                return redirect(url_for("incident_detail", incident_id=incident_id))
            repo.close_incident(incident_id, utc_now(), "closed manually")
            monitor = row.monitor
        engine = current_app.config.get("ENGINE")
        tracker = getattr(engine, "incidents", None)
        release = getattr(tracker, "release", None)
        if callable(release):
            release(monitor)
        _flash("Incident closed")
        return redirect(url_for("incident_detail", incident_id=incident_id))

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
    app.add_template_global(hint_for, name="hint_for")
    app.add_template_global(clock, name="clock")
    app.add_template_global(stamp, name="stamp")
    app.add_template_global(sparkline, name="sparkline")


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
    kind = str(values.get("kind") or "http")
    with _repository() as repo:
        catalog = [record.name for record in repo.monitors()]
        stored = repo.option_map()
    edges = {item: _dependency_names(stored.get(item, {}).get("depends_on")) for item in catalog}
    blocked = blocked_monitors(edges, name)
    choices = [item for item in catalog if item != name and item not in blocked]
    return _page(
        "monitor_form.html",
        values=values,
        error=error,
        choices=choices,
        blocked=blocked,
        editing=name is not None,
        kinds=_KINDS,
        target_hint=target_message(kind),
        target_placeholder=_target_placeholder(kind),
    )


def _save_monitor(existing: str | None) -> None:
    """Validate with the check registry, then store the catalog row and options."""
    spec, options, enabled = _spec_from_form(existing)
    if (
        spec.get("kind") == "defacement"
        and spec.get("baseline") == "learn"
        and "expected_sha256" not in spec
        and existing is not None
    ):
        with _repository() as repo:
            previous = repo.monitor_options(existing).get("expected_sha256")
        if isinstance(previous, str) and previous:
            spec["expected_sha256"] = previous
            options["expected_sha256"] = previous
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
    problems: list[str] = []
    if kind in _HTTP or kind in _HOST:
        target = str(request.form.get("target", "")).strip()
        problem = target_problem(kind, target)
        if problem:
            problems.append(problem)
        else:
            spec["target"] = target
    if kind == "heartbeat":
        token = str(request.form.get("token", "")).strip() or HeartbeatCheck.new_token()
        spec["target"] = token
        grace = _number("grace_seconds", default=None)
        if grace is not None:
            spec["grace_seconds"] = grace
            options["grace_seconds"] = grace
    if kind in _HTTP and not problems:
        _copy_optional(spec, options, "expected_status", "method", "retries", "retry_backoff")
        spec["allow_private"] = _checked("allow_private")
        options["allow_private"] = spec["allow_private"]
    if kind in {"keyword", "defacement"}:
        spec["keyword"] = str(request.form.get("keyword", "")).strip()
        options["keyword"] = spec["keyword"]
        _copy_optional(spec, options, "max_body_bytes")
    if kind == "defacement":
        baseline = str(request.form.get("baseline", "learn")).strip()
        if baseline not in {"learn", "manual"}:
            raise ConfigValidationError("baseline must be learn or manual")
        options["baseline"] = baseline
        spec["baseline"] = baseline
        digest = str(request.form.get("expected_sha256", "")).strip()
        if baseline == "manual" or digest:
            spec["expected_sha256"] = digest
            options["expected_sha256"] = digest
        _copy_optional(spec, options, "alert_after")
    if kind in {"tcp", "tls"}:
        raw_port = str(request.form.get("port", "")).strip()
        problem = port_problem(kind, raw_port)
        if problem:
            problems.append(problem)
        else:
            number = 443 if kind == "tls" and raw_port == "" else int(raw_port)
            spec["port"] = number
            options["port"] = number
        spec["allow_private"] = _checked("allow_private")
        options["allow_private"] = spec["allow_private"]
    if problems:
        raise ConfigValidationError(" ".join(dict.fromkeys(problems)))
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


def _dependency_names(raw: object) -> list[str]:
    """Return the stored dependency names. Anything else is an empty list."""
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _target_placeholder(kind: str) -> str:
    """Return the target placeholder for the selected kind."""
    return PLACEHOLDERS.get(kind, "")


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


def build_snapshot(repo: Any, now: datetime, *, offset_minutes: int = 0) -> dict[str, Any]:
    """Read the catalog once and return every dashboard number from that read."""
    records = list(repo.monitors())
    results = list(repo.iter_results(None))
    incidents = list(repo.incidents())
    latest = latest_by_name(results)
    cards = []
    counts = {"up": 0, "down": 0, "unknown": 0, "paused": 0}
    certs = 0
    warns = 0
    next_cert: int | None = None
    next_cert_name = ""
    tls_count = 0
    for record in records:
        result = latest.get(record.name)
        state = monitor_state(record, result)
        counts[state] = counts.get(state, 0) + 1
        mine = [item for item in results if item.monitor == record.name]
        recent = window(mine, now, 24)
        remaining = None
        if record.kind == "tls":
            tls_count += 1
            if result is not None:
                remaining = days_remaining(result.message, now)
                if remaining is not None and remaining <= 30:
                    certs += 1
                if remaining is not None and (next_cert is None or remaining < next_cert):
                    next_cert = remaining
                    next_cert_name = record.name
        checked_at = None if result is None else result.checked_at
        raw = "" if result is None else result.message
        target = record.target if record.kind != "heartbeat" else "token hidden"
        tone = state
        if state == "unknown" or (state == "up" and remaining is not None and remaining <= 30):
            tone = "warn"
        if tone == "warn":
            warns += 1
        left = record.interval_seconds
        next_check = now
        if checked_at is not None:
            elapsed = int((now - checked_at).total_seconds())
            left = max(0, record.interval_seconds - elapsed)
            next_check = checked_at + timedelta(seconds=record.interval_seconds)
        if raw == "monitoring gap":
            line = "No data"
        elif result is None:
            line = "No results yet. The first check has not run."
        else:
            line = f"{plain_message(raw).rstrip('.')} · {round(result.latency_ms)} ms"
        labels = segments(mine, now, parts=48)
        pill_class, pill_label = _pill(state, tone)
        cards.append(
            {
                "name": record.name,
                "kind": record.kind,
                "icon": _ICONS.get(record.kind, "pulse"),
                "target": target,
                "state": state,
                "tone": tone,
                "pill_class": pill_class,
                "pill_label": pill_label,
                "message": raw,
                "plain": line,
                "latency": None
                if result is None or raw == "monitoring gap"
                else round(result.latency_ms),
                "checked": clock(checked_at),
                "checked_at": "" if checked_at is None else checked_at.isoformat(),
                "last_checked_at": "" if checked_at is None else checked_at.isoformat(),
                "interval": record.interval_seconds,
                "left": left,
                "next_check_at": next_check.isoformat(),
                "uptime": _pct(uptime(recent)),
                "spark": sparkline([item.latency_ms for item in mine]),
                "points": [
                    round(item.latency_ms)
                    for item in mine[-20:]
                    if item.message != "monitoring gap"
                ],
                "segments": labels,
                "bar": "".join(_BAR.get(part, "g") for part in labels),
            }
        )
    day = window(results, now, 24)
    enabled = {record.name for record in records if record.enabled}
    active = sorted(
        (row for row in incidents if row.state == "open" and row.monitor in enabled),
        key=lambda row: row.started_at,
    )
    hero = dashboard_hero(records, latest, active, results_exist=bool(results))
    active_times = [item.checked_at for item in results if item.monitor in enabled]
    last_sweep_at = max(active_times) if active_times else None
    ranked = sorted(
        range(len(results)),
        key=lambda index: (results[index].checked_at, results[index].monitor, index),
    )
    result_ids = {index: rank for rank, index in enumerate(ranked, start=1)}
    log_rows = [_log_row(results[index], result_ids[index]) for index in ranked]
    checks = _check_lines(results, ranked, result_ids)
    by_moment = {
        (item.monitor, item.checked_at.isoformat()): result_ids[index]
        for index, item in enumerate(results)
    }
    activity: list[dict[str, object]] = []
    for event in activity_lines(results, limit=50):
        activity.append(
            {
                "at": event["at"],
                "state": event["state"],
                "text": event["text"],
                "monitor": event["monitor"],
                "id": by_moment.get((event["monitor"], event["at"]), 0),
            }
        )
    opened = _aside(active, incidents, now)
    alerts_ready = bool((repo.get_setting("smtp_host") or "").strip())
    has_heartbeat = any(record.kind == "heartbeat" for record in records)
    open_rows = [
        {"id": row.id, "monitor": row.monitor, "opened_at": row.started_at, "state": row.state}
        for row in active
    ]
    last_incident = _last_incident(incidents)
    generated_ms = int(now.timestamp() * 1000)
    mean_ms = mean_successful_ms(day)
    day_start = local_day_start(now, offset_minutes)
    checks_today = sum(1 for item in results if item.checked_at >= day_start)
    snap = {
        "generated_at": now.astimezone(UTC).isoformat(),
        "generated_ms": generated_ms,
        "cards": cards,
        "monitors": cards,
        "counts": {
            **counts,
            "total": len(records),
            "expiring": certs,
        },
        "open_incidents": open_rows,
        "open_incident_count": len(active),
        "last_incident": last_incident,
        "incident_id": hero["incident_id"],
        "incident_since": hero["incident_since"],
        "incident_name": hero["incident_name"],
        "incident_plain": hero["incident_plain"],
        "open_for": opened["open_for"],
        "incident_hm": opened["incident_hm"],
        "open_since": opened["open_since"],
        "hero_class": hero["hero_class"],
        "hero_icon": hero["hero_icon"],
        "hero_title": hero["hero_title"],
        "hero_name": hero["hero_name"],
        "hero_plain": hero["hero_plain"],
        "last_cycle": "" if last_sweep_at is None else last_sweep_at.isoformat(),
        "stream": list(reversed(log_rows[-50:])),
        "checks": checks,
        "log": list(reversed(log_rows[-50:])),
        "alerts_ready": alerts_ready,
        "has_heartbeat": has_heartbeat,
        "trend_up": hourly_counts(results, now, ok=True),
        "trend_down": hourly_counts(results, now, ok=False),
        "trend_uptime": hourly_counts(results, now, ok=True),
        "certs": certs,
        "warns": warns,
        "tls_count": tls_count,
        "next_cert": next_cert,
        "next_cert_name": next_cert_name,
        "next_cert_expiry": None
        if next_cert is None
        else {"name": next_cert_name, "days": next_cert},
        "avg_ms": mean_ms,
        "mean_latency_ms": mean_ms,
        "checks_today": checks_today,
        "steps_done": sum((True, bool(records), alerts_ready, tls_count > 0)),
        "uptime": _pct(uptime(day)),
        "uptime_24h": _pct(uptime(day)),
        "empty": not records,
        "activity": activity,
        "open_ids": {row.monitor: row.id for row in active},
        "attention": _attention(
            records, latest, [row for row in incidents if row.state == "open"], now
        ),
        "history_note": opened["history_note"],
        "last_sweep": "—" if last_sweep_at is None else clock_hms(last_sweep_at),
        "last_sweep_at": "" if last_sweep_at is None else last_sweep_at.isoformat(),
        "ring": hero["ring"],
        "hero_state": hero["hero_state"],
        "status_dot": _dot_from(
            [monitor_state(record, latest.get(record.name)) for record in records]
        ),
        "nav_items": _nav_rows(records, latest, now),
        "palette_incidents": [
            {"id": row.id, "monitor": row.monitor, "label": f"INC-{row.id:04d}"}
            for row in incidents[:20]
        ],
    }
    snap["view"] = _present(snap)
    return snap


def _dashboard_body(repo: Any) -> dict[str, Any]:
    """Return the dashboard from one snapshot."""
    return build_snapshot(repo, utc_now())


def _log_row(item: CheckResult, row_id: int) -> dict[str, object]:
    """Return one real check for the live log."""
    row = _stream_row(item)
    row["id"] = row_id
    return row


def _aside(active: list[Any], incidents: list[Any], now: datetime) -> dict[str, str]:
    """Return the hero duration and the incident-tile subtitle from the same rows."""
    if active:
        earliest = min(active, key=lambda row: row.started_at)
        start = _parse_time(earliest.started_at) or now
        return {
            "open_for": clock_span(start, now),
            "incident_hm": clock_hm(start),
            "open_since": earliest.started_at,
            "history_note": "",
        }
    return {
        "open_for": "",
        "incident_hm": "",
        "open_since": "",
        "history_note": _note_from(incidents, now),
    }


def _note_from(incidents: list[Any], now: datetime) -> str:
    """Return the last resolved incident, or say that 30 days were quiet."""
    ended = [row for row in incidents if row.ended_at]
    if not ended:
        return "No incidents in 30 d"
    latest = max(ended, key=lambda row: row.ended_at)
    moment = _parse_time(latest.ended_at)
    if moment is None or (now - moment).days > 30:
        return "No incidents in 30 d"
    return f"Last incident {short_span(moment, now)} ago, resolved"


def _parse_time(value: str) -> datetime | None:
    """Parse a stored timestamp. Blank and broken values stay empty."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _last_incident(incidents: list[Any]) -> dict[str, object] | None:
    """Return the newest incident, open or resolved."""
    if not incidents:
        return None
    latest = max(incidents, key=lambda row: row.ended_at or row.started_at)
    return {
        "id": latest.id,
        "opened_at": latest.started_at,
        "resolved_at": latest.ended_at or None,
    }


def _etag(snap: dict[str, Any]) -> str:
    """Hash the stored facts. A ticking clock does not change the tag."""
    counts = snap["counts"]
    core = {
        "checks_today": snap["checks_today"],
        "mean_latency_ms": snap["mean_latency_ms"],
        "uptime_24h": snap["uptime_24h"],
        "counts": counts,
        "open": [(row["id"], row["opened_at"]) for row in snap["open_incidents"]],
        "last": snap["last_incident"],
        "sweep": snap["last_sweep_at"],
        "monitors": [
            (
                card["name"],
                card["state"],
                card["message"],
                card["last_checked_at"] if "last_checked_at" in card else card["checked_at"],
                card["latency"],
                card["uptime"],
            )
            for card in snap["cards"]
        ],
        "activity": [row.get("id") for row in snap["activity"]],
        "log": [row.get("id") for row in snap["log"]],
        "cert": snap["next_cert_expiry"],
    }
    raw = json.dumps(core, sort_keys=True, default=str).encode()
    return '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'


def _tz_offset() -> int:
    """Return the viewer's offset in minutes east of UTC. The page defaults to UTC."""
    raw = request.args.get("offset", "0")
    try:
        offset = int(raw)
    except ValueError:
        return 0
    if offset < -14 * 60 or offset > 14 * 60:
        return 0
    return offset


def _json_ready(body: dict[str, Any]) -> dict[str, Any]:
    """Return the snapshot without SVG markup."""
    ready = {key: value for key, value in body.items() if key != "cards"}
    monitors = []
    for card in body["cards"]:
        copy = dict(card)
        copy.pop("spark", None)
        monitors.append(copy)
    ready["monitors"] = monitors
    ready["cards"] = monitors
    return ready


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
        check = build_check(spec)
        if isinstance(check, DefacementCheck):

            def _remember(digest: str) -> None:
                stored = repo.monitor_options(name)
                stored["expected_sha256"] = digest
                stored["baseline"] = "learn"
                repo.save_options(name, stored)

            check._persist_baseline = _remember
        result = check.run()
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


_probes: dict[str, list[float]] = {}


def _probe_allowed(user: str) -> bool:
    """Allow 30 probes an hour and at least 2 seconds between them."""
    now = time.monotonic()
    recent = [stamp for stamp in _probes.get(user, []) if now - stamp < 3600]
    if len(recent) >= 30 or (recent and now - recent[-1] < 2):
        _probes[user] = recent
        return False
    recent.append(now)
    _probes[user] = recent
    return True


def _page(template: str, **extra: Any) -> str:
    """Render a signed-in page from one snapshot. The caller already checked the login."""
    user = _signed_in() or ""
    text = session.pop("flash", "")
    level = session.pop("flash_level", "ok")
    snap = extra.pop("snapshot", None)
    if snap is None:
        with _repository() as repo:
            snap = build_snapshot(repo, utc_now())
    context: dict[str, Any] = {
        "user": user,
        "csrf_token": ensure_csrf(),
        "flash": text if isinstance(text, str) else "",
        "flash_level": level if isinstance(level, str) else "ok",
    }
    context.update(snap)
    context.update(_shell_from(snap))
    context.update(extra)
    return render_template(template, **context)


def _active_open(repo: Any) -> list[Any]:
    """Return open incidents whose monitor exists and is still enabled."""
    enabled = {record.name for record in repo.monitors() if record.enabled}
    return [row for row in repo.incidents(state="open") if row.monitor in enabled]


def _palette_pages() -> list[dict[str, str]]:
    """Return the palette pages and their keyboard shortcuts."""
    return [
        {"label": "Dashboard", "href": url_for("dashboard"), "keys": "g d"},
        {"label": "Monitors", "href": url_for("monitor_list"), "keys": "g m"},
        {"label": "Incidents", "href": url_for("incident_list"), "keys": "g i"},
        {"label": "Certificates", "href": url_for("certificates"), "keys": "g c"},
        {"label": "New monitor", "href": url_for("monitor_new"), "keys": "n"},
        {"label": "Alerts", "href": url_for("alerts"), "keys": ""},
        {"label": "SLA reports", "href": url_for("sla"), "keys": ""},
        {"label": "Agents", "href": url_for("agents"), "keys": ""},
        {"label": "Data", "href": url_for("data_page"), "keys": ""},
        {"label": "Settings", "href": url_for("settings"), "keys": ""},
        {"label": "Help", "href": url_for("help_page"), "keys": ""},
        {"label": "Public status", "href": url_for("status_page"), "keys": ""},
    ]


def _filter_cards(
    cards: list[dict[str, Any]], status: str, sort: str
) -> tuple[list[dict[str, Any]], str, str]:
    """Apply ``?status=`` and ``?sort=`` to the monitor list."""
    chosen = status if status in {"down", "up", "paused", "unknown", "attn"} else ""
    ordered = sort if sort in {"uptime", "latency", "name"} else ""
    shown = list(cards)
    if chosen == "attn":
        shown = [
            card for card in shown if card["state"] in {"down", "unknown"} or card["tone"] == "warn"
        ]
    elif chosen:
        shown = [card for card in shown if card["state"] == chosen]
    if ordered == "name":
        shown.sort(key=lambda card: str(card["name"]))
    elif ordered == "uptime":
        shown.sort(key=_uptime_key)
    elif ordered == "latency":
        shown.sort(key=lambda card: (card.get("latency") is None, -(card.get("latency") or 0)))
    return shown, chosen, ordered


def _uptime_key(card: dict[str, Any]) -> float:
    """Sort the worst known uptime first. Missing uptime stays at the end."""
    text = str(card.get("uptime", "")).rstrip("%")
    try:
        return float(text)
    except ValueError:
        return 101.0


def _nav_rows(
    records: list[MonitorRecord], latest: dict[str, CheckResult], now: datetime
) -> list[dict[str, str]]:
    """Return sidebar monitors, failing first."""
    rows: list[tuple[int, str, dict[str, str]]] = []
    for record in records:
        result = latest.get(record.name)
        state = monitor_state(record, result)
        dot = state
        if state == "up" and record.kind == "tls" and result is not None:
            remaining = days_remaining(result.message, now)
            if remaining is not None and remaining <= 30:
                dot = "warn"
        rank = {"down": 0, "unknown": 1, "warn": 1, "up": 2, "paused": 3}.get(dot, 2)
        rows.append((rank, record.name, {"name": record.name, "dot": dot}))
    rows.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in rows]


def _attention(
    records: list[MonitorRecord],
    latest: dict[str, CheckResult],
    open_rows: list[Any],
    now: datetime,
) -> list[dict[str, object]]:
    """Return one Needs attention row per failing, paused-incident, or expiring monitor."""
    by_name = {row.monitor: row for row in open_rows}
    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for record in records:
        result = latest.get(record.name)
        state = monitor_state(record, result)
        incident = by_name.get(record.name)
        if state == "down" or (state == "paused" and incident is not None):
            start = result.checked_at if result is not None else now
            if incident is not None:
                try:
                    start = datetime.fromisoformat(incident.started_at)
                except ValueError:
                    start = result.checked_at if result is not None else now
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            plain = (
                "No results yet." if result is None else plain_message(result.message).rstrip(".")
            )
            found.append(
                {
                    "name": record.name,
                    "pill": "Down" if state == "down" else "Paused",
                    "tone": "down" if state == "down" else "paused",
                    "plain": plain,
                    "age": f"failing for {short_span(start, now)}",
                    "incident_id": 0 if incident is None else incident.id,
                }
            )
            seen.add(record.name)
    for record in records:
        if record.name in seen or record.kind != "tls":
            continue
        result = latest.get(record.name)
        if result is None:
            continue
        remaining = days_remaining(result.message, now)
        if remaining is None or remaining > 30:
            continue
        incident = by_name.get(record.name)
        found.append(
            {
                "name": record.name,
                "pill": "Expiring",
                "tone": "warn",
                "plain": plain_message(result.message).rstrip("."),
                "age": f"expires in {remaining} d",
                "incident_id": 0 if incident is None else incident.id,
            }
        )
    return found


def _history_note(repo: Any, now: datetime) -> str:
    """Return the last resolved incident, or say that 30 days were quiet."""
    return _note_from(list(repo.incidents()), now)


def _pill(state: str, tone: str) -> tuple[str, str]:
    """Return the pill class and label for one monitor card."""
    css = "warn" if tone == "warn" else state
    if state == "up" and tone == "warn":
        label = "Expiring"
    elif state == "up":
        label = "Up"
    elif state == "down":
        label = "Down"
    elif state == "paused":
        label = "Paused"
    else:
        label = "Unknown"
    return css, label


def _present(snap: dict[str, Any]) -> dict[str, Any]:
    """Return the status strings the first paint and applyState both use."""
    counts = snap["counts"]
    down = int(counts["down"])
    up = int(counts["up"])
    total = int(counts["total"])
    open_count = int(snap["open_incident_count"])
    failing = [item for item in snap["nav_items"] if item["dot"] == "down"]
    banner_name = str(failing[0]["name"]) if failing else ""
    banner_plain = ""
    if banner_name:
        raw = next(card["message"] for card in snap["cards"] if card["name"] == banner_name)
        if raw:
            banner_plain = plain_message(str(raw)).rstrip(".")
    banner = None
    if down and banner_name:
        banner = {
            "name": banner_name,
            "plain": banner_plain,
            "href": f"/monitors/{banner_name}/view",
        }
    hero_name = str(snap["hero_name"] or "")
    if hero_name:
        subline = {
            "kind": "named",
            "name": hero_name,
            "href": f"/monitors/{hero_name}/view",
            "plain": str(snap["hero_plain"] or ""),
        }
    elif snap["empty"]:
        subline = {"kind": "empty", "name": "", "href": "", "plain": ""}
    else:
        subline = {"kind": "none", "name": "", "href": "", "plain": ""}
    actions: list[dict[str, str]] = []
    if down == 1 and hero_name:
        actions.append(
            {
                "class": "btn btn-danger",
                "href": f"/monitors/{hero_name}/view",
                "label": "Open monitor",
            }
        )
    elif down > 1:
        actions.append(
            {
                "class": "btn btn-danger",
                "href": "/monitors?status=down",
                "label": f"View {down} failing",
            }
        )
    elif snap["empty"]:
        actions.append(
            {"class": "btn btn-primary", "href": "/monitors/new", "label": "Add monitor"}
        )
    incident_id = snap["incident_id"]
    if incident_id:
        actions.append(
            {
                "class": "btn btn-ghost",
                "href": f"/incidents/{incident_id}",
                "label": "Open incident",
            }
        )
    if down == 1 and banner_name:
        chip_href = f"/monitors/{banner_name}/view"
    elif down:
        chip_href = "/monitors?status=down"
    elif open_count:
        chip_href = "/incidents?status=open"
    elif total:
        chip_href = "/monitors"
    else:
        chip_href = "/dashboard"
    if down:
        chip = {"tone": "down", "href": chip_href, "label": f"{down} failing"}
    elif open_count:
        chip = {"tone": "warn", "href": chip_href, "label": f"{open_count} open"}
    elif total:
        chip = {
            "tone": "ok" if up == total else "warn",
            "href": chip_href,
            "label": f"{up}/{total} healthy",
        }
    else:
        chip = {"tone": "warn", "href": chip_href, "label": "Awaiting first sweep"}
    if open_count:
        aside = {
            "kind": "open",
            "open_for": snap["open_for"],
            "open_since": snap["open_since"],
            "note": "",
        }
        incident_sub = {"kind": "open", "since": snap["open_since"], "hm": snap["incident_hm"]}
    elif snap["history_note"]:
        aside = {"kind": "note", "open_for": "", "open_since": "", "note": snap["history_note"]}
        incident_sub = {"kind": "none", "since": "", "hm": ""}
    else:
        aside = {"kind": "none", "open_for": "", "open_since": "", "note": ""}
        incident_sub = {"kind": "none", "since": "", "hm": ""}
    warns = int(snap["warns"])
    state = str(snap["hero_state"] or "")
    latency = "—" if snap["mean_latency_ms"] is None else str(snap["mean_latency_ms"])
    return {
        "banner": banner,
        "chip": chip,
        "badges": {"fail": down, "open": open_count, "cert": int(counts["expiring"])},
        "ring": snap["ring"],
        "icon": snap["hero_icon"],
        "hero_class": snap["hero_class"] or "",
        "eye": "Service state" + (f" · {state}" if state else ""),
        "title": snap["hero_title"],
        "subline": subline,
        "actions": actions,
        "aside": aside,
        "incident_sub": incident_sub,
        "tabs": {
            "all": f"All {total}",
            "up": f"Up {up}",
            "down": f"Down {down}",
            "down_n": down,
            "expiring": f"Expiring {warns}",
            "attn": f"Needs attention {down + warns}",
        },
        "sweep": snap["last_sweep"],
        "sweep_at": snap["last_sweep_at"],
        "checks": str(snap["checks_today"]),
        "latency": latency,
        "open": str(open_count),
        "mix": f"{up} up · {down} down",
        "total": str(total),
        "uptime": "—" if str(snap["uptime"]) == "—" else str(snap["uptime"])[:-1],
        "cert": "—" if snap["next_cert"] is None else str(snap["next_cert"]),
        "cert_sub": str(snap["next_cert_name"] or "No certificate watched"),
    }


def _shell_from(snap: dict[str, Any]) -> dict[str, object]:
    """Return the top bar and badges from a snapshot. This does not read the database."""
    view = snap["view"]
    banner = view["banner"] or {}
    counts = snap["counts"]
    return {
        "dot": snap["status_dot"],
        "headline": snap["hero_title"] if isinstance(snap["hero_title"], str) else "",
        "nav": _NAV.get(request.endpoint or "", ""),
        "nav_incidents": view["badges"]["open"],
        "nav_certs": view["badges"]["cert"],
        "nav_failing": view["badges"]["fail"],
        "nav_total": counts["total"],
        "nav_healthy": counts["up"],
        "nav_names": [card["name"] for card in snap["cards"]],
        "nav_incident_rows": snap["palette_incidents"],
        "chip_href": view["chip"]["href"],
        "banner_name": banner.get("name", ""),
        "banner_plain": banner.get("plain", ""),
        "version": __version__,
    }


def _dot_from(states: list[str]) -> str:
    """Return the global state word for the top bar."""
    if "down" in states:
        return "down"
    if "unknown" in states or not states:
        return "unknown"
    if all(item == "paused" for item in states):
        return "paused"
    return "up"


def _dot(repo: Any) -> str:
    """Return the global state word for the top bar."""
    latest = latest_by_name(list(repo.iter_results(None)))
    states = [monitor_state(record, latest.get(record.name)) for record in repo.monitors()]
    return _dot_from(states)


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


def _latency_text(values: list[float], quantile: float) -> str:
    """Return one latency quantile rounded to a whole millisecond."""
    clean = sorted(value for value in values if value >= 0)
    if not clean:
        return "—"
    if quantile <= 0.5:
        chosen = sum(clean) / len(clean)
    else:
        index = min(len(clean) - 1, max(0, math.ceil(quantile * len(clean)) - 1))
        chosen = clean[index]
    return f"{round(chosen)} ms"


def _state_span(results: list[Any], now: datetime) -> str:
    """Return how long the current up or down run has lasted."""
    if not results:
        return "not checked yet"
    latest = results[-1]
    if latest.message == "monitoring gap":
        return "no data in the latest window"
    start = latest.checked_at
    for item in reversed(results):
        if item.message == "monitoring gap" or item.ok != latest.ok:
            break
        start = item.checked_at
    seconds = max(0, int((now - start).total_seconds()))
    minutes, _sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    clock = f"{hours}h {minutes}m" if hours else f"{minutes}m"
    word = "up" if latest.ok else "down"
    return f"{word} for {clock}"


def _check_lines(
    results: list[CheckResult],
    ranked: list[int],
    result_ids: dict[int, int],
) -> list[dict[str, object]]:
    """Return completed checks, newest first, for the live log."""
    lines: list[dict[str, object]] = []
    for index in reversed(ranked):
        item = results[index]
        if item.message == "monitoring gap":
            continue
        lines.append(
            {
                "id": result_ids[index],
                "at": item.checked_at.isoformat(),
                "time": clock_hms(item.checked_at),
                "name": item.monitor,
                "result": "UP" if item.ok else "DOWN",
                "message": " ".join((item.message or "").split()),
                "latency": round(item.latency_ms),
            }
        )
        if len(lines) == 50:
            break
    return lines


def _stream_row(item: CheckResult) -> dict[str, object]:
    """Return one terminal-log line from a stored result."""
    if item.message == "monitoring gap":
        mark = "GAP"
    elif item.ok:
        mark = "PASS"
    else:
        mark = "FAIL"
    return {
        "at": item.checked_at.isoformat(),
        "name": item.monitor,
        "mark": mark,
        "latency": round(item.latency_ms),
        "plain": plain_message(item.message) if item.message else "",
    }


def _average_ms(results: list[CheckResult]) -> int | None:
    """Return the mean latency as a whole number of milliseconds."""
    samples = [row.latency_ms for row in results if row.message != "monitoring gap"]
    if not samples:
        return None
    return round(sum(samples) / len(samples))


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
