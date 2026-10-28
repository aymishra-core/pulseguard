"""Length-prefixed agent frames.

The header is ``struct`` format ``!4sBBI`` and the magic is ``PGAG``. This is
not the ``PGDB`` backup header. The payload is UTF-8 JSON for one check result
plus the agent token. The token is compared and then dropped. It is not logged.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import re
import socket
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pulseguard.checks.base import MAX_NAME_LENGTH
from pulseguard.checks.models import CheckResult, Severity
from pulseguard.exceptions import ConfigValidationError, ProtocolError
from pulseguard.storage.records import MAX_KIND_LENGTH, MAX_STORED_MESSAGE_LENGTH

MAGIC = b"PGAG"
VERSION = 1
RESULT = 1
ACK = 2
NACK = 3
HEADER = struct.Struct("!4sBBI")
assert HEADER.size == 10, "agent header size drifted"

# Validation limits, not measurements.
MAX_PAYLOAD = 8192
MAX_CLIENTS = 8
FRAME_TIMEOUT_SECONDS = 5.0
MIN_FRAME_TIMEOUT_SECONDS = 0.1
MAX_FRAME_TIMEOUT_SECONDS = 30.0
MAX_LATENCY_MS = 3_600_000.0
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TYPES = frozenset({RESULT, ACK, NACK})


@dataclass(frozen=True)
class Frame:
    """One decoded frame. ``payload`` is the bytes after the header."""

    msg_type: int
    payload: bytes


def write_message(sock: socket.socket, msg_type: int, payload: bytes) -> None:
    """Send one frame. A closed socket becomes a protocol error."""
    try:
        sock.sendall(encode_message(msg_type, payload))
    except OSError as exc:
        raise ProtocolError("truncated frame") from exc


def encode_message(msg_type: int, payload: bytes) -> bytes:
    """Pack one frame. The payload is capped before it is prefixed."""
    if msg_type not in _TYPES:
        raise ProtocolError("bad type")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("frame too large")
    return HEADER.pack(MAGIC, VERSION, msg_type, len(payload)) + payload


def encode_result(result: CheckResult, token: str) -> bytes:
    """Pack one result. The token travels in the payload and is not a log field."""
    require_token(token)
    body = json.dumps({"token": token, "result": result.as_dict()}).encode("utf-8")
    return encode_message(RESULT, body)


def read_message(sock: socket.socket) -> Frame:
    """Read one frame. An oversized length is rejected before the body is read."""
    header = _read_exact(sock, HEADER.size)
    magic, version, msg_type, length = HEADER.unpack(header)
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if version != VERSION:
        raise ProtocolError("bad version")
    if msg_type not in _TYPES:
        raise ProtocolError("bad type")
    if length > MAX_PAYLOAD:
        raise ProtocolError("frame too large")
    payload = _read_exact(sock, length) if length else b""
    return Frame(msg_type, payload)


def decode_result(payload: bytes, token: str | Sequence[str]) -> CheckResult:
    """Return the result when the token matches. The token is not returned."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProtocolError("frame is not json") from exc
    if not isinstance(data, dict):
        raise ProtocolError("result rejected")
    supplied = data.get("token")
    expected = (token,) if isinstance(token, str) else tuple(token)
    if not isinstance(supplied, str) or not _any_token(supplied, expected):
        raise ProtocolError("agent token rejected")
    raw = data.get("result")
    if not isinstance(raw, dict):
        raise ProtocolError("result rejected")
    try:
        return _result_from_json(raw)
    except ProtocolError:
        raise
    except (ConfigValidationError, TypeError, ValueError) as exc:
        raise ProtocolError("result rejected") from exc


def parse_listen(value: str) -> tuple[str, int]:
    """Split ``host:port``. The host is a literal address and is not resolved."""
    if not isinstance(value, str) or value.count(":") != 1:
        raise ConfigValidationError("agent listen must look like 127.0.0.1:9700")
    host, port_text = value.split(":", 1)
    if not port_text.isdigit():
        raise ConfigValidationError("agent port must be an integer")
    return require_agent_host(host), require_agent_port(int(port_text), allow_ephemeral=False)


def require_agent_host(value: str) -> str:
    """Allow a loopback or private IPv4 literal. Names are not resolved."""
    if not isinstance(value, str):
        raise ConfigValidationError("agent host must be a literal IPv4 address")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConfigValidationError("agent host must be a literal IPv4 address") from exc
    # 0.0.0.0 reports itself as private, so an unspecified address is rejected on its own.
    allowed = (
        isinstance(parsed, ipaddress.IPv4Address)
        and not parsed.is_unspecified
        and not parsed.is_multicast
        and not parsed.is_link_local
        and not parsed.is_reserved
        and (parsed.is_loopback or parsed.is_private)
    )
    if not allowed:
        raise ConfigValidationError("agent host must be a loopback or private address")
    return str(parsed)


def require_agent_port(value: int, *, allow_ephemeral: bool) -> int:
    """Return a TCP port. Port 0 is only for a test bind."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError("agent port must be an integer")
    if value == 0 and allow_ephemeral:
        return 0
    if value < 1 or value > 65535:
        raise ConfigValidationError("agent port must be from 1 to 65535")
    return value


def require_token(value: str) -> str:
    """Return a token in the same shape as a heartbeat token."""
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ConfigValidationError("agent token must be 16..128 letters, digits, _ or -")
    return value


def require_frame_timeout(value: float) -> float:
    """Bound how long one frame read may block."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError("frame timeout must be a number")
    if value < MIN_FRAME_TIMEOUT_SECONDS or value > MAX_FRAME_TIMEOUT_SECONDS:
        raise ConfigValidationError(
            "frame timeout must be from "
            f"{MIN_FRAME_TIMEOUT_SECONDS:g} to {MAX_FRAME_TIMEOUT_SECONDS:g} seconds"
        )
    return float(value)


def _require_status(value: object) -> int | None:
    """Return a status code, or None. Booleans are rejected."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ProtocolError("result rejected")
    return value


def _any_token(supplied: str, expected: tuple[str, ...]) -> bool:
    """Return True when one stored token matches. The secret is not logged."""
    return any(isinstance(item, str) and _token_matches(supplied, item) for item in expected)


def _token_matches(supplied: str, expected: str) -> bool:
    """Compare tokens without branching on the secret's contents."""
    if len(supplied) != len(expected):
        return False
    return hmac.compare_digest(supplied, expected)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    """Read ``size`` bytes or raise when the peer closes early."""
    buf = bytearray()
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except TimeoutError as exc:
            raise ProtocolError("truncated frame") from exc
        except OSError as exc:
            raise ProtocolError("truncated frame") from exc
        if not chunk:
            raise ProtocolError("truncated frame")
        buf.extend(chunk)
    return bytes(buf)


def _result_from_json(raw: dict[str, object]) -> CheckResult:
    """Build a result from the JSON object. Assertion failures become a refusal."""
    monitor = raw.get("monitor")
    kind = raw.get("kind")
    ok = raw.get("ok")
    latency = raw.get("latency_ms")
    message = raw.get("message")
    severity = raw.get("severity")
    checked_at = raw.get("checked_at")
    status_code = raw.get("status_code")
    if not isinstance(monitor, str) or not monitor or len(monitor) > MAX_NAME_LENGTH:
        raise ProtocolError("result rejected")
    if any(char in monitor for char in "\r\n"):
        raise ProtocolError("result rejected")
    if not isinstance(kind, str) or _KIND_RE.fullmatch(kind) is None or len(kind) > MAX_KIND_LENGTH:
        raise ProtocolError("result rejected")
    if not isinstance(ok, bool):
        raise ProtocolError("result rejected")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise ProtocolError("result rejected")
    if latency < 0 or latency > MAX_LATENCY_MS:
        raise ProtocolError("result rejected")
    if not isinstance(message, str) or not message or any(char in message for char in "\r\n"):
        raise ProtocolError("result rejected")
    if len(message) > MAX_STORED_MESSAGE_LENGTH:
        raise ProtocolError("message is too long")
    if not isinstance(severity, str):
        raise ProtocolError("result rejected")
    if not isinstance(checked_at, str):
        raise ProtocolError("result rejected")
    moment = datetime.fromisoformat(checked_at)
    if moment.tzinfo is None:
        raise ProtocolError("result rejected")
    status = _require_status(status_code)
    try:
        return CheckResult(
            monitor=monitor,
            kind=kind,
            ok=ok,
            latency_ms=float(latency),
            message=message,
            severity=Severity(severity),
            checked_at=moment,
            status_code=status,
        )
    except AssertionError as exc:
        raise ProtocolError("result rejected") from exc
