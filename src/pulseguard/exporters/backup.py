"""Checksummed binary snapshot of a SQLite database file.

The layout is a fixed header plus the database bytes. This is not the agent
frame protocol. ``struct`` packs the header and SHA-256 covers the payload.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from pathlib import Path

from pulseguard.exceptions import StorageError

_MAGIC = b"PGDB"
_VERSION = 1
_HEADER = struct.Struct("!4sIQ32s")
assert _HEADER.size == 48, "backup header size drifted"


def write_verified_backup(payload: bytes, dest: Path) -> str:
    """Write ``payload`` and return the hex SHA-256 of those bytes."""
    if not payload:
        raise StorageError("backup payload is empty")
    if dest.exists():
        raise StorageError("backup output already exists")
    digest = hashlib.sha256(payload).digest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_HEADER.pack(_MAGIC, _VERSION, len(payload), digest) + payload)
    return digest.hex()


def read_verified_backup(path: Path) -> bytes:
    """Return the payload when the header, length, and SHA-256 all match."""
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise StorageError(f"cannot read backup: {exc.strerror or exc}") from exc
    if len(blob) < _HEADER.size:
        raise StorageError("backup file is truncated")
    magic, version, length, digest = _HEADER.unpack(blob[: _HEADER.size])
    if magic != _MAGIC:
        raise StorageError("backup header is not a PulseGuard database")
    if version != _VERSION:
        raise StorageError(f"unsupported backup version {version}")
    payload = blob[_HEADER.size :]
    if len(payload) != length:
        raise StorageError("backup length does not match the header")
    actual = hashlib.sha256(payload).digest()
    if not hmac.compare_digest(actual, digest):
        raise StorageError("backup checksum mismatch")
    return payload


def verify_backup(path: Path) -> str:
    """Check a backup and return the hex SHA-256 of its payload."""
    payload = read_verified_backup(path)
    return hashlib.sha256(payload).hexdigest()


def restore_verified_backup(source: Path, dest: Path) -> str:
    """Write a checked payload to ``dest`` and return its hex SHA-256."""
    if dest.exists():
        raise StorageError("restore output already exists")
    payload = read_verified_backup(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()
