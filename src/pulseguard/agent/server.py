"""Threaded collector. One accept thread, and one short thread per connection.

Garbage on a connection closes that connection. The accept loop keeps running.
Accepted results are put on the caller's queue with ``put_nowait``.
"""

from __future__ import annotations

import contextlib
import logging
import socket
from queue import Full, Queue
from threading import BoundedSemaphore, Event, Lock, Thread

from pulseguard.agent.protocol import (
    ACK,
    FRAME_TIMEOUT_SECONDS,
    MAX_CLIENTS,
    NACK,
    RESULT,
    decode_result,
    read_message,
    require_agent_host,
    require_agent_port,
    require_token,
    write_message,
)
from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError, EngineError, ProtocolError
from pulseguard.logging_setup import LOGGER_NAME

LOGGER = logging.getLogger(f"{LOGGER_NAME}.agent.server")
_ACCEPT_POLL_SECONDS = 0.2


class Collector:
    """Accept agent connections and enqueue the results they carry."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        results: Queue[CheckResult],
        *,
        max_clients: int = MAX_CLIENTS,
        timeout: float = FRAME_TIMEOUT_SECONDS,
    ) -> None:
        """Validate the bind address. The socket opens in ``start``."""
        self._host = require_agent_host(host)
        self._port = require_agent_port(port, allow_ephemeral=True)
        self._token = require_token(token)
        self._results = results
        self._timeout = timeout
        self._max_clients = _require_clients(max_clients)
        self._stop = Event()
        self._slots = BoundedSemaphore(self._max_clients)
        self._lock = Lock()
        self._listen: socket.socket | None = None
        self._thread: Thread | None = None
        self._children: list[Thread] = []
        self._live: set[socket.socket] = set()
        self.port = port

    @property
    def thread(self) -> Thread | None:
        """Return the accept thread once ``start`` has created it."""
        return self._thread

    def start(self) -> None:
        """Bind and start the accept thread."""
        if self._thread is not None:
            raise EngineError("collector is already running")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._port))
            sock.listen(self._max_clients)
            sock.settimeout(_ACCEPT_POLL_SECONDS)
        except OSError as exc:
            sock.close()
            raise ConfigValidationError("agent collector could not listen") from exc
        self._listen = sock
        self.port = int(sock.getsockname()[1])
        self._stop.clear()
        self._thread = Thread(target=self._accept, name="pulseguard-collector")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop accepting, close live sockets, and join every collector thread."""
        self._stop.set()
        listen = self._listen
        self._listen = None
        if listen is not None:
            with contextlib.suppress(OSError):
                listen.close()
        self._join(self._thread, timeout)
        with self._lock:
            children = list(self._children)
            live = list(self._live)
        for conn in live:
            with contextlib.suppress(OSError):
                conn.close()
        for child in children:
            self._join(child, timeout)
        self._thread = None

    def __enter__(self) -> Collector:
        """Start the collector."""
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        """Stop the collector."""
        self.stop()

    def _accept(self) -> None:
        """Accept until stopped. A full client cap closes the new socket."""
        listen = self._listen
        if listen is None:
            return
        while not self._stop.is_set():
            try:
                conn, _addr = listen.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                conn.close()
                LOGGER.warning("agent connection deferred")
                continue
            worker = Thread(target=self._handle, args=(conn,), name="pulseguard-agent")
            with self._lock:
                if self._stop.is_set():
                    conn.close()
                    self._slots.release()
                    break
                self._children = [item for item in self._children if item.is_alive()]
                worker.start()
                self._children.append(worker)

    def _handle(self, conn: socket.socket) -> None:
        """Read one frame. A bad frame closes this connection only."""
        with self._lock:
            self._live.add(conn)
        try:
            conn.settimeout(self._timeout)
            frame = read_message(conn)
            if frame.msg_type != RESULT:
                raise ProtocolError("bad type")
            result = decode_result(frame.payload, self._token)
            try:
                self._results.put_nowait(result)
            except Full:
                write_message(conn, NACK, b"full")
                LOGGER.warning("agent result deferred monitor=%s", result.monitor)
                return
            write_message(conn, ACK, b"ok")
            LOGGER.info("agent result monitor=%s", result.monitor)
        except ProtocolError as exc:
            LOGGER.warning("agent frame rejected reason=%s", exc)
            self._refuse(conn)
        except OSError:
            LOGGER.warning("agent frame rejected reason=socket")
        finally:
            with self._lock:
                self._live.discard(conn)
            with contextlib.suppress(OSError):
                conn.close()
            self._slots.release()

    def _refuse(self, conn: socket.socket) -> None:
        """Best-effort refusal. A peer that already left is ignored."""
        try:
            write_message(conn, NACK, b"rejected")
        except ProtocolError:
            return

    def _join(self, thread: Thread | None, timeout: float) -> None:
        """Join one thread or raise when it stays alive."""
        if thread is None or not thread.is_alive():
            return
        thread.join(timeout)
        if thread.is_alive():
            raise EngineError(f"{thread.name} did not stop within {timeout:g} seconds")


def _require_clients(value: int) -> int:
    """Bound how many agent connections may be handled at once."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError("max_clients must be an integer")
    if value < 1 or value > MAX_CLIENTS:
        raise ConfigValidationError(f"max_clients must be from 1 to {MAX_CLIENTS}")
    return value
