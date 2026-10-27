"""Command line for one-shot checks, SQLite history, export, and backup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from pulseguard import __version__
from pulseguard.agent.client import AgentClient
from pulseguard.agent.protocol import (
    parse_listen,
    require_agent_host,
    require_agent_port,
    require_token,
)
from pulseguard.checks import BaseCheck, CheckResult, HttpCheck, build_check
from pulseguard.checks.models import Severity, utc_now
from pulseguard.core.engine import Engine, load_plan
from pulseguard.exceptions import ConfigValidationError, PulseGuardError
from pulseguard.exporters import (
    read_monitors_csv,
    restore_verified_backup,
    verify_backup,
    write_monitors_csv,
    write_monitors_json,
    write_results_csv,
    write_results_json,
    write_verified_backup,
)
from pulseguard.logging_setup import setup_logging
from pulseguard.notifiers.email_notifier import EmailNotifier, smtp_settings_from_env
from pulseguard.storage import SqliteRepository


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    output = stdout if stdout is not None else sys.stdout
    try:
        _configure_logging(args)
        if args.command == "check":
            return _run_check(args, output)
        if args.command == "run":
            return _run_engine(args, output)
        if args.command == "export":
            return _run_export(args, output)
        if args.command == "import-monitors":
            return _run_import(args, output)
        if args.command == "backup":
            return _run_backup(args, output)
        if args.command == "push":
            return _run_push(args, output)
        if args.command == "web":
            return _run_web(args, output)
        if args.command == "console":
            return _run_console(args)
        raise ConfigValidationError(f"unknown command {args.command}")
    except ConfigValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except PulseGuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="pulseguard",
        description="Run configured checks and store history in SQLite.",
    )
    parser.add_argument("--version", action="version", version=f"pulseguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_check(sub)
    _add_run(sub)
    _add_export(sub)
    _add_import(sub)
    _add_backup(sub)
    _add_push(sub)
    _add_web(sub)
    _add_console(sub)
    return parser


def _add_check(sub: Any) -> None:
    """Add the one-shot ``check`` command."""
    check = sub.add_parser("check", help="run configured checks once and print JSON")
    check.add_argument("--name", default="adhoc", help="monitor name used with --url")
    check.add_argument("--url", help="http or https URL to probe")
    check.add_argument("--config", type=Path, help="JSON file with a monitors list")
    check.add_argument("--timeout", type=float, default=None, help="timeout in seconds for --url")
    check.add_argument("--expect", type=int, default=200, help="expected status for --url")
    check.add_argument(
        "--deny-private",
        action="store_true",
        help="reject localhost and literal private IPs for --url",
    )
    check.add_argument("--db", type=Path, help="SQLite file that stores this run")
    _add_log_args(check)


def _add_run(sub: Any) -> None:
    """Add ``run``. It schedules checks until the duration ends or the process is interrupted."""
    run = sub.add_parser("run", help="schedule checks, then shut the workers down")
    run.add_argument("--config", type=Path, required=True, help="JSON file with a monitors list")
    run.add_argument("--db", type=Path, required=True, help="SQLite file the writer thread opens")
    run.add_argument(
        "--duration",
        type=float,
        default=None,
        help="seconds to run before shutting down; omit to run until interrupted",
    )
    run.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="write an evidence pack here when an incident opens",
    )
    run.add_argument(
        "--agent-listen",
        default=None,
        help="collector address, for example 127.0.0.1:9700",
    )
    _add_log_args(run)


def _add_push(sub: Any) -> None:
    """Add ``push``. It sends one result to a collector and does not probe a URL."""
    push = sub.add_parser("push", help="send one result to an agent collector")
    push.add_argument("--host", default="127.0.0.1", help="collector address")
    push.add_argument("--port", type=int, required=True, help="collector port")
    push.add_argument("--name", required=True, help="monitor name")
    push.add_argument("--kind", default="http", help="result kind")
    push.add_argument("--message", default=None, help="result message")
    push.add_argument("--fail", action="store_true", help="send a failing result")
    _add_log_args(push)


def _add_export(sub: Any) -> None:
    """Add ``export`` for results or the monitor catalog."""
    export = sub.add_parser("export", help="write stored results or monitors as CSV or JSON")
    export.add_argument("--db", type=Path, required=True, help="SQLite database")
    export.add_argument("--table", choices=("results", "monitors"), default="results")
    export.add_argument("--format", choices=("csv", "json"), required=True)
    export.add_argument("--output", type=Path, required=True, help="file to write")
    export.add_argument("--monitor", default=None, help="only results for this monitor")
    _add_log_args(export)


def _add_import(sub: Any) -> None:
    """Add ``import-monitors`` for a catalog CSV."""
    importer = sub.add_parser("import-monitors", help="load a monitor catalog from CSV")
    importer.add_argument("--db", type=Path, required=True, help="SQLite database")
    importer.add_argument("--csv", type=Path, required=True, help="CSV file to read")
    _add_log_args(importer)


def _add_backup(sub: Any) -> None:
    """Add ``backup`` create, verify, and restore."""
    backup = sub.add_parser("backup", help="write, verify, or restore a checksummed snapshot")
    backup.add_argument("--db", type=Path, help="SQLite database to snapshot")
    backup.add_argument("--output", type=Path, help="backup file to write, or database to restore")
    backup.add_argument("--verify", type=Path, help="backup file to verify")
    backup.add_argument("--restore", type=Path, help="backup file to restore")
    _add_log_args(backup)


def _add_web(sub: Any) -> None:
    """Add ``web``. It serves status and runs the scheduler in this process."""
    web = sub.add_parser("web", help="serve the status page and run checks")
    web.add_argument("--config", type=Path, required=True, help="JSON file with a monitors list")
    web.add_argument("--db", type=Path, required=True, help="SQLite file")
    web.add_argument("--host", default="127.0.0.1", help="listen address")
    web.add_argument("--port", type=int, default=8080, help="listen port")
    _add_log_args(web)


def _add_console(sub: Any) -> None:
    """Add ``console``. It opens the desktop window."""
    console = sub.add_parser("console", help="open the desktop status window")
    console.add_argument("--db", type=Path, required=True, help="SQLite file")
    _add_log_args(console)


def _add_log_args(parser: argparse.ArgumentParser) -> None:
    """Add the shared logging flags."""
    parser.add_argument("--log-dir", type=Path, help="write a rotating log file here")
    parser.add_argument(
        "--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR, or CRITICAL"
    )


def _configure_logging(args: argparse.Namespace) -> None:
    """Apply the log level from the flag or PULSEGUARD_LOG_LEVEL."""
    level = args.log_level or os.environ.get("PULSEGUARD_LOG_LEVEL", "INFO")
    try:
        setup_logging(level=str(level), log_dir=args.log_dir)
    except ValueError as exc:
        raise ConfigValidationError(str(exc)) from exc


def _run_web(args: argparse.Namespace, output: TextIO) -> int:
    """Serve the site and run checks until the process is interrupted."""
    host = require_agent_host(args.host)
    port = require_agent_port(args.port, allow_ephemeral=False)
    secret, user, password, api_key = _web_secrets()
    from waitress import serve

    from pulseguard.checks.heartbeat import HeartbeatCheck
    from pulseguard.web.app import create_app

    checks, edges, settings = load_plan(args.config)
    heartbeats = [check for check in checks if isinstance(check, HeartbeatCheck)]
    database = args.db
    app = create_app(
        database=database,
        secret_key=secret,
        admin_user=user,
        admin_password=password,
        api_key=api_key,
        heartbeats=heartbeats,
    )
    del password
    engine = Engine(
        checks,
        repository_factory=lambda: SqliteRepository(database),
        dependencies=edges,
        workers=settings["workers"],
        queue_size=settings["queue_size"],
        host_limit=settings["host_limit"],
        failures_before_open=settings["failures_before_open"],
        successes_before_resolve=settings["successes_before_resolve"],
        notifier=_email_notifier(),
    )
    engine.start()
    output.write(f"web http://{host}:{port}\n")
    output.flush()
    try:
        serve(app, host=host, port=port, threads=4)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        raise ConfigValidationError("web server could not listen") from exc
    finally:
        engine.stop()
    return 0


def _web_secrets() -> tuple[str, str, str, str]:
    """Read the web secrets from the environment. They are not printed."""
    from pulseguard.web.auth import (
        require_admin_password,
        require_admin_user,
        require_api_key_value,
        require_secret_key,
    )

    return (
        require_secret_key(os.environ.get("PULSEGUARD_SECRET_KEY", "")),
        require_admin_user(os.environ.get("PULSEGUARD_ADMIN_USER", "")),
        require_admin_password(os.environ.get("PULSEGUARD_ADMIN_PASSWORD", "")),
        require_api_key_value(os.environ.get("PULSEGUARD_API_KEY", "")),
    )


def _run_console(args: argparse.Namespace) -> int:
    """Open the desktop window. Widgets stay on the main thread."""
    from pulseguard.gui.widgets import launch_console

    launch_console(args.db)
    return 0


def _run_check(args: argparse.Namespace, output: TextIO) -> int:
    """Run the checks, store them when ``--db`` is set, and print JSON."""
    checks = _checks_from_args(args)
    results = [check.run() for check in checks]
    if args.db is not None:
        _store_results(args.db, results)
    _write_results(results, output)
    return 0 if all(result.ok for result in results) else 1


def _run_engine(args: argparse.Namespace, output: TextIO) -> int:
    """Schedule checks, let the writer store them, then stop every thread."""
    checks, edges, settings = load_plan(args.config)
    if args.duration is not None:
        _require_duration(args.duration)
    database = args.db
    listen = _agent_listen(args.agent_listen)
    engine = Engine(
        checks,
        repository_factory=lambda: SqliteRepository(database),
        dependencies=edges,
        workers=settings["workers"],
        queue_size=settings["queue_size"],
        host_limit=settings["host_limit"],
        failures_before_open=settings["failures_before_open"],
        successes_before_resolve=settings["successes_before_resolve"],
        notifier=_email_notifier(),
        evidence_dir=args.evidence_dir,
        agent_host=None if listen is None else listen[0],
        agent_port=None if listen is None else listen[1],
        agent_token=None if listen is None else listen[2],
    )
    engine.start()
    try:
        engine.wait(args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
    opened = sum(1 for item in engine.incidents.transitions if item.action == "open")
    resolved = sum(1 for item in engine.incidents.transitions if item.action == "resolved")
    output.write(
        f"stored {engine.stored} results, deferred {engine.deferred}, "
        f"incidents opened {opened} resolved {resolved}\n"
    )
    if listen is not None:
        output.write(f"agent {listen[0]}:{listen[1]}\n")
    return 0


def _agent_listen(value: str | None) -> tuple[str, int, str] | None:
    """Read the collector address. The token comes from the environment."""
    if value is None:
        return None
    host, port = parse_listen(value)
    token = require_token(os.environ.get("PULSEGUARD_AGENT_TOKEN", ""))
    return host, port, token


def _run_push(args: argparse.Namespace, output: TextIO) -> int:
    """Send one result. The token is read from the environment and not printed."""
    token = require_token(os.environ.get("PULSEGUARD_AGENT_TOKEN", ""))
    host = require_agent_host(args.host)
    port = require_agent_port(args.port, allow_ephemeral=False)
    name = _push_name(args.name)
    kind = _push_kind(args.kind)
    failed = bool(args.fail)
    message = args.message if args.message is not None else ("HTTP 503" if failed else "HTTP 200")
    if (
        not isinstance(message, str)
        or not message
        or len(message) > 2000
        or any(char in message for char in "\r\n")
    ):
        raise ConfigValidationError("message must be 1..2000 characters on one line")
    result = CheckResult(
        monitor=name,
        kind=kind,
        ok=not failed,
        latency_ms=0,
        message=message,
        severity=Severity.CRITICAL if failed else Severity.OK,
        checked_at=utc_now(),
    )
    with AgentClient(host, port, token) as client:
        client.send(result)
    output.write(f"sent monitor={result.monitor}\n")
    return 0


def _push_name(value: object) -> str:
    """Reject a blank or oversized monitor name."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(char in value for char in "\r\n")
    ):
        raise ConfigValidationError("name must be 1..128 characters on one line")
    return value


def _push_kind(value: object) -> str:
    """Reject a kind the frame decoder would refuse."""
    letters = "abcdefghijklmnopqrstuvwxyz0123456789_-"
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 32
        or value[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(char not in letters for char in value)
    ):
        raise ConfigValidationError("kind must be 1..32 lowercase letters, digits, _ or -")
    return value


def _email_notifier() -> EmailNotifier | None:
    """Build an email notifier when SMTP is configured. An empty host sends nothing."""
    if not os.environ.get("PULSEGUARD_SMTP_HOST", "").strip():
        return None
    return EmailNotifier(smtp_settings_from_env())


def _require_duration(value: float) -> None:
    """Reject a duration outside the same upper bound as a check interval."""
    if value <= 0 or value > 86_400:
        raise ConfigValidationError("duration must be greater than 0 and at most 86400 seconds")


def _store_results(path: Path, results: Sequence[CheckResult]) -> None:
    """Insert one run in a single transaction."""
    with SqliteRepository(path) as repo, repo.transaction():
        for result in results:
            repo.add_result(result)


def _run_export(args: argparse.Namespace, output: TextIO) -> int:
    """Write one table to ``--output``."""
    if args.table == "monitors" and args.monitor is not None:
        raise ConfigValidationError("--monitor applies only when exporting results")
    with (
        SqliteRepository(args.db) as repo,
        args.output.open("w", encoding="utf-8", newline="") as handle,
    ):
        if args.table == "results":
            rows = repo.iter_results(args.monitor)
            count = (
                write_results_csv(rows, handle)
                if args.format == "csv"
                else write_results_json(rows, handle)
            )
            label = "results"
        else:
            records = repo.monitors()
            count = (
                write_monitors_csv(records, handle)
                if args.format == "csv"
                else write_monitors_json(records, handle)
            )
            label = "monitors"
    output.write(f"wrote {count} {label}\n")
    return 0


def _run_import(args: argparse.Namespace, output: TextIO) -> int:
    """Replace catalog rows from a CSV file in one transaction."""
    try:
        with args.csv.open(newline="", encoding="utf-8") as handle:
            records = read_monitors_csv(handle)
    except OSError as exc:
        raise ConfigValidationError(f"cannot read {args.csv}: {exc.strerror or exc}") from exc
    with SqliteRepository(args.db) as repo, repo.transaction():
        for record in records:
            repo.save_monitor(record)
    output.write(f"imported {len(records)} monitors\n")
    return 0


def _run_backup(args: argparse.Namespace, output: TextIO) -> int:
    """Create, verify, or restore one checksummed snapshot."""
    modes = [args.verify is not None, args.restore is not None, args.db is not None]
    if sum(modes) != 1:
        raise ConfigValidationError("use one of --verify, --restore, or --db")
    if args.verify is not None:
        if args.output is not None or args.db is not None:
            raise ConfigValidationError("--verify reads one backup file")
        digest = verify_backup(args.verify)
        output.write(f"sha256={digest}\n")
        return 0
    if args.restore is not None:
        if args.output is None:
            raise ConfigValidationError("--restore requires --output")
        digest = restore_verified_backup(args.restore, args.output)
        output.write(f"sha256={digest}\n")
        return 0
    if args.db is None or args.output is None:
        raise ConfigValidationError("--db requires --output")
    if args.output.exists() and args.db.resolve() == args.output.resolve():
        raise ConfigValidationError("backup output must be a different file from the database")
    with SqliteRepository(args.db) as repo:
        payload = repo.snapshot_bytes()
    digest = write_verified_backup(payload, args.output)
    output.write(f"sha256={digest}\n")
    return 0


def _checks_from_args(args: argparse.Namespace) -> list[BaseCheck]:
    """Build the checks for one CLI invocation."""
    if args.url and args.config:
        raise ConfigValidationError("use either --url or --config")
    if args.url:
        return [
            HttpCheck(
                args.name,
                args.url,
                timeout=args.timeout,
                expected_status=args.expect,
                allow_private=not args.deny_private,
            )
        ]
    if args.config:
        _reject_url_only_flags(args)
        return load_monitor_file(args.config)
    raise ConfigValidationError("provide --url or --config")


def _reject_url_only_flags(args: argparse.Namespace) -> None:
    """Refuse one-shot flags when the monitors come from a file."""
    if args.name != "adhoc" or args.timeout is not None or args.expect != 200 or args.deny_private:
        raise ConfigValidationError(
            "--name, --timeout, --expect, and --deny-private apply only with --url"
        )


def load_monitor_file(path: Path) -> list[BaseCheck]:
    """Load monitors from a JSON file and build them through the registry."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"invalid JSON in {path}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ConfigValidationError("config root must be an object")
    monitors = data.get("monitors")
    if not isinstance(monitors, list) or not monitors:
        raise ConfigValidationError("monitors must be a non-empty list")
    built: list[BaseCheck] = []
    for index, item in enumerate(monitors):
        if not isinstance(item, Mapping):
            raise ConfigValidationError(f"monitors[{index}] must be an object")
        built.append(build_check(_string_key_mapping(item, index)))
    return built


def _string_key_mapping(item: Mapping[object, Any], index: int) -> dict[str, Any]:
    """Copy a JSON object, rejecting non-string keys."""
    copied: dict[str, Any] = {}
    for key, value in item.items():
        if not isinstance(key, str):
            raise ConfigValidationError(f"monitors[{index}] keys must be strings")
        copied[key] = value
    return copied


def _write_results(results: Sequence[CheckResult], output: TextIO) -> None:
    """Print a JSON array of check results."""
    payload = [result.as_dict() for result in results]
    json.dump(payload, output, indent=2)
    output.write("\n")
