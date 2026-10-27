"""The test lab is a separate program. PulseGuard does not import it."""

from __future__ import annotations

import http.client
import importlib.util
import socket
import threading
import time
from pathlib import Path

_LAB = Path(__file__).resolve().parents[1] / "tools" / "lab.py"


def _load_lab():
    """Load tools/lab.py without starting it."""
    spec = importlib.util.spec_from_file_location("pulseguard_test_lab", _LAB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(port: int, path: str) -> tuple[int, bytes]:
    """Return one response. A 500 is a result, not an exception."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_lab_switches_the_page_and_accepts_tcp(monkeypatch) -> None:
    """Up, down, and defaced stay on the lab. The TCP port accepts a connection."""
    http_port = _free_port()
    tcp_port = _free_port()
    monkeypatch.setenv("LAB_HTTP_PORT", str(http_port))
    monkeypatch.setenv("LAB_TCP_PORT", str(tcp_port))
    lab = _load_lab()
    threading.Thread(target=lab.main, daemon=True).start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            status, body = _get(http_port, "/")
        except OSError:
            time.sleep(0.05)
            continue
        if status == 200 and b"Acme Shop" in body:
            break
    else:
        raise AssertionError("lab did not answer")
    assert _get(http_port, "/control/down")[0] == 302
    down_status, _down_body = _get(http_port, "/")
    assert down_status == 500
    assert _get(http_port, "/control/defaced")[0] == 302
    hacked_status, hacked = _get(http_port, "/")
    assert hacked_status == 200
    assert b"HACKED" in hacked
    with socket.create_connection(("127.0.0.1", tcp_port), timeout=2):
        pass
