"""Command line. ``check`` runs configured monitors once and prints JSON."""

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
from pulseguard.logging_setup import setup_logging


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    output = stdout if stdout is not None else sys.stdout
    try:
        _configure_logging(args)
        checks = _checks_from_args(args)
        results = [check.run() for check in checks]
    except ConfigValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except PulseGuardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _write_results(results, output)
    return 0 if all(result.ok for result in results) else 1


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="pulseguard",
        description="Run configured checks once and print JSON.",
    )
    parser.add_argument("--version", action="version", version=f"pulseguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
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
    check.add_argument("--log-dir", type=Path, help="write a rotating log file here")
    check.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return parser


def _configure_logging(args: argparse.Namespace) -> None:
    """Apply the log level from the flag or PULSEGUARD_LOG_LEVEL."""
    level = args.log_level or os.environ.get("PULSEGUARD_LOG_LEVEL", "INFO")
    try:
        setup_logging(level=str(level), log_dir=args.log_dir)
    except ValueError as exc:
        raise ConfigValidationError(str(exc)) from exc


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
