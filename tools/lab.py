#!/usr/bin/env python3
"""Local site and TCP port for the PulseGuard demo monitors.

Port 8799 serves a page. Port 8798 accepts TCP connections and closes them.
The control page can ask this process to answer UP, DOWN, or DEFACED, and it
can ping a loopback PulseGuard URL. This file uses the standard library only.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

HOST = "127.0.0.1"
HTTP_PORT = 8799
TCP_PORT = 8798
_BODY = {
    "up": (200, b"Acme Shop\n"),
    "down": (500, b"Acme Shop\n"),
    "defaced": (200, b"Acme Shop is closed\n"),
}
_MODE = "up"
_LOCK = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    """Answer the shop page and the control form. The log is quiet."""

    def do_GET(self) -> None:
        """Serve the shop page or the control page."""
        path = urlparse(self.path).path
        if path == "/control":
            self._send(200, _control_page().encode("utf-8"))
            return
        with _LOCK:
            status, body = _BODY[_MODE]
        self._send(status, body)

    def do_POST(self) -> None:
        """Change the shop mode, or ping a loopback PulseGuard URL."""
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(min(length, 4096))
        form = parse_qs(raw.decode("utf-8", errors="replace"))
        if path == "/control":
            mode = form.get("mode", ["up"])[0]
            if mode in _BODY:
                with _LOCK:
                    global _MODE
                    _MODE = mode
            ping = form.get("ping", [""])[0]
            note = _ping(ping) if ping else ""
            self._send(200, _control_page(note).encode("utf-8"))
            return
        self._send(404, b"not found")

    def log_message(self, fmt: str, *args: object) -> None:
        """Skip the access log so a pasted token is not written here."""
        del fmt, args

    def _send(self, status: int, body: bytes) -> None:
        """Write one small response."""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _control_page(note: str = "") -> str:
    """Return the buttons and the ping box."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Lab control</title></head>
<body>
<h1>Lab control</h1>
<p>Current mode is sent on the next request to port {HTTP_PORT}.</p>
<form method="post">
<button name="mode" value="up">UP</button>
<button name="mode" value="down">DOWN</button>
<button name="mode" value="defaced">DEFACED</button>
</form>
<form method="post">
<label>Ping URL <input name="ping" placeholder="http://127.0.0.1:8080/ping/token"></label>
<button>Ping now</button>
</form>
<p>{note}</p>
</body></html>
"""


def _ping(url: str) -> str:
    """GET a loopback /ping/ URL. Other hosts are refused."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "127.0.0.1":
        return "ping refused: host must be 127.0.0.1"
    if not parsed.path.startswith("/ping/"):
        return "ping refused: path must start with /ping/"
    try:
        with urlopen(Request(url), timeout=3) as response:
            return f"ping {response.status}"
    except URLError:
        return "ping failed"
    except TimeoutError:
        return "ping failed"


def _serve_tcp() -> None:
    """Accept connections on 8798 and close them."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, TCP_PORT))
    server.listen(8)
    while True:
        conn, _addr = server.accept()
        conn.close()


def main() -> None:
    """Start both listeners. They stop when this process is stopped."""
    threading.Thread(target=_serve_tcp, name="lab-tcp", daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, HTTP_PORT), _Handler)
    print(f"lab http://{HOST}:{HTTP_PORT}/control")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
