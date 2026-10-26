"""Agent client. The context manager is the way to close the socket.

``__del__`` closes a socket the caller forgot. It is the only ``__del__`` in
PulseGuard. Do not rely on it; ``with AgentClient(...)`` runs even when a
later line raises.
"""

from __future__ import annotations

import contextlib
import socket

from pulseguard.agent.protocol import (
    ACK,
    FRAME_TIMEOUT_SECONDS,
    encode_result,
    read_message,
    require_agent_host,
    require_agent_port,
    require_frame_timeout,
    require_token,
)
from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ProtocolError


class AgentClient:
    """Send one result per connection and wait for the acknowledgement.

    Prefer ``with AgentClient(...) as client``. ``__del__`` is only a fallback
    for a socket that was left open.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        timeout: float = FRAME_TIMEOUT_SECONDS,
    ) -> None:
        """Validate the address and token. Nothing is contacted yet."""
        self._sock: socket.socket | None = None
        self._host = require_agent_host(host)
        self._port = require_agent_port(port, allow_ephemeral=False)
        self._token = require_token(token)
        self._timeout = require_frame_timeout(timeout)

    def connect(self) -> None:
        """Open the TCP connection when it is not already open."""
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise ProtocolError("agent connection failed") from exc
        sock.settimeout(self._timeout)
        self._sock = sock

    def send(self, result: CheckResult) -> None:
        """Send ``result`` and require an acknowledgement."""
        self.connect()
        sock = self._sock
        if sock is None:
            raise ProtocolError("agent connection failed")
        try:
            sock.sendall(encode_result(result, self._token))
            frame = read_message(sock)
        except OSError as exc:
            raise ProtocolError("agent connection failed") from exc
        if frame.msg_type != ACK:
            raise ProtocolError("agent did not acknowledge")

    def close(self) -> None:
        """Close the socket. A second call does nothing."""
        sock = self._sock
        self._sock = None
        if sock is None:
            return
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            sock.close()

    def __enter__(self) -> AgentClient:
        """Connect and return this client."""
        self.connect()
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the socket when the ``with`` block ends."""
        self.close()

    def __del__(self) -> None:
        """Close a forgotten socket. Prefer the context manager."""
        close = getattr(self, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            return
