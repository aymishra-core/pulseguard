"""Kind-specific monitor form rules.

The browser copy in ``static/form.js`` uses these same sentences.
"""

from __future__ import annotations

URL_MESSAGE = "Enter a full URL, for example https://example.com"
TCP_MESSAGE = "Enter a host only, for example 127.0.0.1. Put the port in the Port field."
TLS_MESSAGE = "Enter a hostname only, for example www.example.com (no https://)."
DNS_MESSAGE = "Enter a hostname, for example www.example.com."
PORT_MESSAGE = "Enter a port from 1 to 65535"

_URL_KINDS = frozenset({"http", "keyword", "defacement"})
_HOST_KINDS = frozenset({"tcp", "tls", "dns"})
MESSAGES = {
    "http": URL_MESSAGE,
    "keyword": URL_MESSAGE,
    "defacement": URL_MESSAGE,
    "tcp": TCP_MESSAGE,
    "tls": TLS_MESSAGE,
    "dns": DNS_MESSAGE,
}
PLACEHOLDERS = {
    "http": "https://example.com",
    "keyword": "https://example.com",
    "defacement": "https://example.com",
    "tcp": "127.0.0.1",
    "tls": "www.example.com",
    "dns": "www.example.com",
}


def target_message(kind: str) -> str:
    """Return the helper and error sentence for one kind."""
    return MESSAGES.get(kind, "")


def target_problem(kind: str, target: object) -> str | None:
    """Return the kind's sentence when the target is not acceptable."""
    if kind == "heartbeat" or kind not in MESSAGES:
        return None
    text = target.strip() if isinstance(target, str) else ""
    if kind in _URL_KINDS:
        if text.startswith("http://") or text.startswith("https://"):
            return None
        return URL_MESSAGE
    if not _host_ok(text):
        return MESSAGES[kind]
    return None


def port_problem(kind: str, raw: object) -> str | None:
    """Return the port sentence for a missing or out-of-range tcp or tls port."""
    if kind not in {"tcp", "tls"}:
        return None
    text = raw.strip() if isinstance(raw, str) else ""
    if kind == "tls" and text == "":
        return None
    if not text.isdigit():
        return PORT_MESSAGE
    number = int(text)
    if number < 1 or number > 65535:
        return PORT_MESSAGE
    return None


def url_fix(kind: str, value: object) -> dict[str, object] | None:
    """Return a host and port parsed from a pasted URL, or None."""
    if kind not in _HOST_KINDS or not isinstance(value, str):
        return None
    parsed = _split_url(value.strip())
    if parsed is None:
        return None
    host, port = parsed
    label = f"Use {host} instead" if kind == "dns" else f"Use {host} and port {port} instead"
    return {"host": host, "port": port, "label": label}


def blocked_monitors(edges: dict[str, list[str]], name: str | None) -> list[str]:
    """Return monitors that depend on ``name``, directly or through others.

    ``edges`` maps each monitor to the monitors it depends on. Depending on
    one of the returned names would close a cycle.
    """
    if not name:
        return []
    dependents: dict[str, list[str]] = {}
    for child, parents in edges.items():
        for parent in parents:
            dependents.setdefault(parent, []).append(child)
    found: list[str] = []
    seen = {name}
    stack = [name]
    while stack:
        current = stack.pop()
        for child in dependents.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            stack.append(child)
    return found


def _host_ok(text: str) -> bool:
    """Return True when the text is a bare host, not a URL or host:port."""
    return (
        bool(text) and "://" not in text and "/" not in text and " " not in text and ":" not in text
    )


def _split_url(text: str) -> tuple[str, int] | None:
    """Return the host and port from an http or https URL. The host keeps its case."""
    lower = text.lower()
    if lower.startswith("https://"):
        scheme = "https"
        rest = text[8:]
    elif lower.startswith("http://"):
        scheme = "http"
        rest = text[7:]
    else:
        return None
    at = rest.find("@")
    if at >= 0:
        rest = rest[at + 1 :]
    for mark in ("/", "?", "#"):
        cut = rest.find(mark)
        if cut >= 0:
            rest = rest[:cut]
    if not rest:
        return None
    default = 443 if scheme == "https" else 80
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            return None
        host = rest[1:end]
        tail = rest[end + 1 :]
        if tail.startswith(":") and tail[1:].isdigit():
            return host, int(tail[1:])
        return (host, default) if host else None
    colon = rest.rfind(":")
    if colon >= 0 and rest[colon + 1 :].isdigit():
        host = rest[:colon]
        if not host:
            return None
        return host, int(rest[colon + 1 :])
    return rest, default
