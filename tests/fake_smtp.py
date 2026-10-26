"""A loopback SMTP server that accepts one message and stores the DATA payload."""

from __future__ import annotations

import socket
import threading


class FakeSmtp:
    """Speak enough SMTP for ``smtplib`` to deliver a message to 127.0.0.1."""

    def __init__(self) -> None:
        """Bind an ephemeral port. Call ``start`` or use this as a context manager."""
        self.messages: list[bytes] = []
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = int(self._sock.getsockname()[1])
        self._sock.listen(5)
        self._sock.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, name="fake-smtp", daemon=True)

    def __enter__(self) -> FakeSmtp:
        """Start the accept loop."""
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        """Stop the accept loop."""
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        """Accept connections until stopped."""
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                self._session(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _session(self, conn: socket.socket) -> None:
        """Handle one client. AUTH is accepted and the secret is not stored."""
        conn.settimeout(2)
        reader = conn.makefile("rb")
        self._send(conn, b"220 pulseguard.invalid ESMTP\r\n")
        collecting = False
        payload = bytearray()
        while True:
            line = reader.readline()
            if not line:
                break
            if collecting:
                if line in (b".\r\n", b".\n"):
                    collecting = False
                    self.messages.append(bytes(payload))
                    payload.clear()
                    self._send(conn, b"250 queued\r\n")
                else:
                    payload.extend(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.upper()
            if command.startswith(b"EHLO") or command.startswith(b"HELO"):
                self._send(conn, b"250-pulseguard.invalid\r\n250 AUTH PLAIN LOGIN\r\n")
            elif command.startswith(b"AUTH LOGIN"):
                self._send(conn, b"334 VXNlcm5hbWU6\r\n")
                reader.readline()
                self._send(conn, b"334 UGFzc3dvcmQ6\r\n")
                reader.readline()
                self._send(conn, b"235 ok\r\n")
            elif command.startswith(b"AUTH"):
                self._send(conn, b"235 ok\r\n")
            elif command.startswith(b"DATA"):
                collecting = True
                self._send(conn, b"354 end with <CRLF>.<CRLF>\r\n")
            elif command.startswith(b"QUIT"):
                self._send(conn, b"221 bye\r\n")
                break
            else:
                self._send(conn, b"250 ok\r\n")

    def _send(self, conn: socket.socket, data: bytes) -> None:
        """Write one reply."""
        conn.sendall(data)
