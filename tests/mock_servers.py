"""Local HTTP server for tests and a manual CLI demo. It never dials out."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlparse


class MockState:
    """Mutable facts about one mock server."""

    def __init__(self) -> None:
        """Start with an instant, empty response."""
        self.delay_seconds = 0.0
        self.last_user_agent = ""
        self.last_path = ""


class _ThreadingServer(ThreadingHTTPServer):
    """Request threads are daemons so a slow handler cannot hang the process."""

    daemon_threads: ClassVar[bool] = True
    allow_reuse_address: ClassVar[bool] = True


# Visible text is "ok". The same word inside script, style, and an attribute is not.
PAGE_BODY = (
    b"<html><style>.x{color:red}</style><script>secretword</script>"
    b'<p class="secretword">ok</p></html>'
)


def _handler_for(state: MockState) -> type[BaseHTTPRequestHandler]:
    """Build a handler class closed over ``state``."""

    class Handler(BaseHTTPRequestHandler):
        """Serve a few fixed paths on the loopback interface."""

        def log_message(self, format: str, *args: object) -> None:
            """Drop the default stderr access log."""
            return

        def do_GET(self) -> None:
            """Handle GET."""
            self._serve()

        def do_HEAD(self) -> None:
            """Handle HEAD."""
            self._serve()

        def _serve(self) -> None:
            """Record the request and answer from the path."""
            state.last_user_agent = self.headers.get("User-Agent", "")
            state.last_path = self.path
            if state.delay_seconds > 0:
                time.sleep(state.delay_seconds)
            path = urlparse(self.path).path
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/ok")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if path == "/ok":
                body = b"ok"
                code = 200
            elif path == "/page":
                body = PAGE_BODY
                code = 200
            else:
                body = b"missing"
                code = 404
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return Handler


class MockHttpServer:
    """Loopback HTTP server on an ephemeral port."""

    def __init__(self) -> None:
        """Bind 127.0.0.1:0 without accepting yet."""
        self.state = MockState()
        self._httpd = _ThreadingServer(("127.0.0.1", 0), _handler_for(self.state))
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Accept connections on a background thread."""
        if self._thread is not None:
            raise RuntimeError("mock server is already running")
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="pulseguard-mock-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop accepting and close the listening socket."""
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("mock server thread did not stop")
            self._thread = None
        self._httpd.server_close()

    @property
    def base_url(self) -> str:
        """Return the loopback URL of this server, without a trailing slash."""
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> MockHttpServer:
        """Start the server."""
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop the server."""
        self.stop()


def main() -> None:
    """Print the base URL and serve until Ctrl-C."""
    server = MockHttpServer()
    server.start()
    print(server.base_url, flush=True)
    try:
        thread = server._thread
        if thread is None:
            raise RuntimeError("mock server thread was not started")
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
