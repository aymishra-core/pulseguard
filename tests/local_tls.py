"""Loopback TLS server for tests. The certificate is generated locally."""

from __future__ import annotations

import socket
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path

# Longer than the 30-day certificate alert window, so a fresh cert is healthy.
_CERT_DAYS = "90"


class LocalTlsServer:
    """Serve one self-signed certificate on 127.0.0.1 and an ephemeral port."""

    def __init__(self) -> None:
        """Create a key, a certificate, and a listening socket."""
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.certificate = root / "cert.pem"
        self.key = root / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(self.key),
                "-out",
                str(self.certificate),
                "-days",
                _CERT_DAYS,
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certfile=self.certificate, keyfile=self.key)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        """Return the ephemeral port."""
        return int(self._sock.getsockname()[1])

    def __enter__(self) -> LocalTlsServer:
        """Start accepting handshakes."""
        self._thread = threading.Thread(target=self._serve, name="pulseguard-mock-tls", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop the thread and delete the key material."""
        self._stop.set()
        self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._dir.cleanup()

    def _serve(self) -> None:
        """Complete handshakes until the listening socket closes."""
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                tls = self._context.wrap_socket(conn, server_side=True)
            except ssl.SSLError:
                conn.close()
                continue
            tls.close()
