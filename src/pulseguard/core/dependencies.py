"""Dependency graph for alert suppression.

Recursion walks ``depends_on`` to the furthest monitor that is down, and
refuses a cycle. Nested monitor groups are flattened the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pulseguard.exceptions import ConfigValidationError

# Caps on operator input. They are limits, not measurements.
MAX_DEPENDENCIES = 8
MAX_GROUP_DEPTH = 8


def flatten_monitors(items: Sequence[object], *, depth: int = 0) -> list[dict[str, Any]]:
    """Return a flat list of monitor objects. Groups recurse into ``monitors``."""
    if depth > MAX_GROUP_DEPTH:
        raise ConfigValidationError(
            f"monitor groups cannot nest more than {MAX_GROUP_DEPTH} levels"
        )
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ConfigValidationError("monitors must be a list")
    flat: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigValidationError(f"monitors[{index}] must be an object")
        if "monitors" in item and "kind" not in item:
            nested = item["monitors"]
            if not isinstance(nested, list):
                raise ConfigValidationError("a monitor group must contain a monitors list")
            flat.extend(flatten_monitors(nested, depth=depth + 1))
            continue
        flat.append(dict(item))
    return flat


def dependency_edges(
    specs: Sequence[Mapping[str, Any]],
    names: set[str],
) -> dict[str, tuple[str, ...]]:
    """Pull ``depends_on`` off each spec. Unknown names and cycles are rejected."""
    edges: dict[str, tuple[str, ...]] = {}
    for spec in specs:
        name = spec.get("name")
        if not isinstance(name, str):
            continue
        raw = spec.get("depends_on", [])
        edges[name] = _parents(name, raw, names)
    _reject_cycles(edges)
    return edges


def root_cause(
    name: str,
    down: set[str],
    edges: Mapping[str, Sequence[str]],
) -> str | None:
    """Return the furthest down dependency, or None when ``name`` is the root.

    Demonstrates recursion. ``down`` must not include ``name`` itself; callers
    pass the monitors that were already failing before this result.
    """
    parents = [parent for parent in edges.get(name, ()) if parent in down]
    if not parents:
        return None
    return _furthest(parents[0], down, edges, seen=set())


def _parents(name: str, raw: object, names: set[str]) -> tuple[str, ...]:
    """Validate one ``depends_on`` list."""
    if not isinstance(raw, list):
        raise ConfigValidationError(f"{name} depends_on must be a list")
    if len(raw) > MAX_DEPENDENCIES:
        raise ConfigValidationError(
            f"{name} cannot depend on more than {MAX_DEPENDENCIES} monitors"
        )
    parents: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or item.strip() != item:
            raise ConfigValidationError(f"{name} dependencies must be monitor names")
        if item == name:
            raise ConfigValidationError(f"{name} depends on itself")
        if item not in names:
            raise ConfigValidationError(f"{name} depends on unknown monitor {item!r}")
        if item not in parents:
            parents.append(item)
    return tuple(parents)


def _reject_cycles(edges: Mapping[str, Sequence[str]]) -> None:
    """Walk every edge. A repeated name on the stack is a cycle."""
    seen: set[str] = set()
    for name in edges:
        _walk(name, edges, seen, stack=set())


def _walk(
    name: str,
    edges: Mapping[str, Sequence[str]],
    seen: set[str],
    stack: set[str],
) -> None:
    """Depth-first search with the current path kept in ``stack``."""
    if name in stack:
        raise ConfigValidationError(f"dependency cycle at {name}")
    if name in seen:
        return
    stack.add(name)
    for parent in edges.get(name, ()):
        _walk(parent, edges, seen, stack)
    stack.remove(name)
    seen.add(name)


def _furthest(
    name: str,
    down: set[str],
    edges: Mapping[str, Sequence[str]],
    seen: set[str],
) -> str:
    """Follow down parents until one has no down parent of its own."""
    if name in seen:
        raise ConfigValidationError(f"dependency cycle at {name}")
    seen.add(name)
    for parent in edges.get(name, ()):
        if parent in down:
            return _furthest(parent, down, edges, seen)
    return name
