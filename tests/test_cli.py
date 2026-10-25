"""CLI tests against the local mock server and config errors."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import runpy
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from pulseguard.cli import main
from pulseguard.logging_setup import setup_logging
from tests.mock_servers import MockHttpServer

ROOT = Path(__file__).resolve().parents[1]


def test_cli_check_url_prints_json() -> None:
    """``pulseguard check --url`` probes the mock server and prints a JSON array."""
    with MockHttpServer() as server:
        code, payload, logs = _run_check(
            [
                "check",
                "--name",
                "demo",
                "--url",
                f"{server.base_url}/ok?token=super-secret-value",
            ]
        )
    assert code == 0
    assert payload[0]["ok"] is True
    assert payload[0]["status_code"] == 200
    assert payload[0]["monitor"] == "demo"
    assert "super-secret-value" not in logs


def test_cli_failed_check_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """A completed check that is down is exit code 1, not a crash."""
    with MockHttpServer() as server:
        code = main(["check", "--url", f"{server.base_url}/missing", "--expect", "200"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload[0]["ok"] is False
    assert payload[0]["status_code"] == 404


def test_cli_config_file_and_logging(tmp_path: Path) -> None:
    """A JSON config runs through the registry, and the log file omits the URL."""
    with MockHttpServer() as server:
        path = tmp_path / "monitors.json"
        path.write_text(
            json.dumps(
                {
                    "monitors": [
                        {
                            "kind": "http",
                            "name": "web",
                            "target": f"{server.base_url}/ok?token=super-secret-value",
                            "retries": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        code, payload, logs = _run_check(
            ["check", "--config", str(path), "--log-dir", str(tmp_path)]
        )
    assert code == 0
    assert payload[0]["monitor"] == "web"
    text = (tmp_path / "pulseguard.log").read_text(encoding="utf-8")
    assert "level=INFO" in text
    assert "calling HttpCheck.run" in text
    assert "super-secret-value" not in text
    assert "super-secret-value" not in logs


def test_cli_config_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bad config and private-target refusal exit 2 with a message on stderr."""
    missing = main(["check", "--config", str(tmp_path / "missing.json")])
    assert missing == 2
    assert "cannot read" in capsys.readouterr().err

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert main(["check", "--config", str(broken)]) == 2

    both = tmp_path / "both.json"
    both.write_text(json.dumps({"monitors": []}), encoding="utf-8")
    assert main(["check", "--config", str(both), "--url", "http://127.0.0.1/"]) == 2

    assert main(["check", "--url", "http://127.0.0.1/health", "--deny-private"]) == 2
    assert main(["check"]) == 2

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"monitors": []}), encoding="utf-8")
    assert main(["check", "--config", str(empty)]) == 2

    listed = tmp_path / "list.json"
    listed.write_text("[]", encoding="utf-8")
    assert main(["check", "--config", str(listed)]) == 2

    shaped = tmp_path / "shape.json"
    shaped.write_text(json.dumps({"monitors": ["nope"]}), encoding="utf-8")
    assert main(["check", "--config", str(shaped)]) == 2

    flags = tmp_path / "flags.json"
    flags.write_text(
        json.dumps(
            {"monitors": [{"kind": "http", "name": "web", "target": "http://127.0.0.1/health"}]}
        ),
        encoding="utf-8",
    )
    assert main(["check", "--config", str(flags), "--name", "other"]) == 2
    assert "apply only with --url" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_log_level_env_and_rotating_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PULSEGUARD_LOG_LEVEL selects the level, and a bad level is rejected."""
    monkeypatch.setenv("PULSEGUARD_LOG_LEVEL", "DEBUG")
    with MockHttpServer() as server:
        code, _payload, logs = _run_check(["check", "--url", f"{server.base_url}/ok"])
    assert code == 0
    assert logging.getLogger("pulseguard").level == logging.DEBUG
    assert "level=INFO" in logs

    monkeypatch.setenv("PULSEGUARD_LOG_LEVEL", "LOUD")
    assert main(["check", "--url", "http://127.0.0.1/health"]) == 2

    setup_logging(level="INFO", log_dir=tmp_path)
    logging.getLogger("pulseguard.demo").info("hello-log")
    for handler in logging.getLogger("pulseguard").handlers:
        handler.flush()
    assert "hello-log" in (tmp_path / "pulseguard.log").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        setup_logging(level="nope")


def test_module_entrypoint_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m pulseguard`` reaches ``main``."""
    monkeypatch.setattr(sys, "argv", ["pulseguard", "--help"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("pulseguard", run_name="__main__")
    assert caught.value.code == 0


def test_cli_module_against_mock_server() -> None:
    """A subprocess proves the console module, not only the in-process function."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PULSEGUARD_LOG_LEVEL"] = "INFO"
    with MockHttpServer() as server:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pulseguard",
                "check",
                "--name",
                "subprocess",
                "--url",
                f"{server.base_url}/ok",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload[0]["ok"] is True
    assert payload[0]["monitor"] == "subprocess"
    assert server.base_url not in completed.stderr


def _run_check(argv: list[str]) -> tuple[int, list[dict[str, object]], str]:
    """Run the CLI in-process and return the code, JSON, and stderr."""
    stdout = StringIO()
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        code = main(argv, stdout=stdout)
    payload = json.loads(stdout.getvalue())
    assert isinstance(payload, list)
    return code, payload, stderr.getvalue()
