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
from pulseguard.checks import BaseCheck, CheckResult, HttpCheck, build_check
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
        if args.command == "export":
            return _run_export(args, output)
        if args.command == "import-monitors":
            return _run_import(args, output)
        if args.command == "backup":
            return _run_backup(args, output)
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
    _add_export(sub)
    _add_import(sub)
    _add_backup(sub)
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


def _run_check(args: argparse.Namespace, output: TextIO) -> int:
    """Run the checks, store them when ``--db`` is set, and print JSON."""
    checks = _checks_from_args(args)
    results = [check.run() for check in checks]
    if args.db is not None:
        _store_results(args.db, results)
    _write_results(results, output)
    return 0 if all(result.ok for result in results) else 1


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
