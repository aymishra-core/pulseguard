"""Check abstract base and the metaclass that registers kinds.

``CheckMeta`` demonstrates a metaclass: a subclass that does not set ``kind``
cannot be created. ``BaseCheck`` demonstrates an ABC, class versus instance
variables, ``_target`` encapsulation, name-mangled ``__interval``, and a
validating property.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar, cast

from pulseguard.checks.models import CheckResult
from pulseguard.exceptions import ConfigValidationError

# Bounds on operator input. They are validation limits, not measurements.
MIN_INTERVAL_SECONDS = 30.0
MAX_INTERVAL_SECONDS = 86_400.0
MAX_TIMEOUT_SECONDS = 120.0
MAX_NAME_LENGTH = 128


class CheckMeta(ABCMeta):
    """Register concrete check classes by the ``kind`` attribute on the class."""

    registry: ClassVar[dict[str, type[BaseCheck]]] = {}

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type[BaseCheck]:
        """Create the class and register it, or refuse it when ``kind`` is missing."""
        created = cast(
            "type[BaseCheck]",
            super().__new__(mcs, name, bases, namespace, **kwargs),
        )
        if not any(isinstance(base, CheckMeta) for base in bases):
            return created
        kind = namespace.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise TypeError(f"{name} must set a non-empty string class attribute 'kind'")
        cleaned = kind.strip()
        if cleaned != kind:
            raise TypeError(f"{name}.kind must not have surrounding whitespace")
        existing = mcs.registry.get(cleaned)
        if existing is not None and existing is not created:
            raise TypeError(f"kind {cleaned!r} is already registered to {existing.__name__}")
        mcs.registry[cleaned] = created
        return created


class BaseCheck(ABC, metaclass=CheckMeta):
    """One configured monitor target.

    There is no ``__del__`` here. Checks do not own a socket. Context managers
    are preferred anywhere a connection must be closed; the later agent
    connection is the only place a ``__del__`` fallback is planned.

    Class variables (shared): ``kind``, ``default_timeout``, ``default_interval``.
    Instance variables: ``name``, ``_target``, ``_timeout``, ``__interval``.
    """

    kind: ClassVar[str] = ""
    default_timeout: ClassVar[float] = 10.0
    default_interval: ClassVar[float] = 60.0

    def __init__(
        self,
        name: str,
        target: str,
        *,
        timeout: float | None = None,
        interval: float | None = None,
    ) -> None:
        """Store the target and validate timeout and interval."""
        self.name = _require_name(name)
        self._target = _require_target(target)
        self.last_elapsed = 0.0
        chosen_timeout = type(self).default_timeout if timeout is None else timeout
        chosen_interval = type(self).default_interval if interval is None else interval
        self.timeout = chosen_timeout
        self.interval = chosen_interval

    @property
    def target(self) -> str:
        """Return the configured target. There is no setter."""
        return self._target

    @property
    def timeout(self) -> float:
        """Return the request timeout in seconds."""
        return self._timeout

    @timeout.setter
    def timeout(self, value: object) -> None:
        """Validate and store the timeout. Demonstrates a property setter."""
        number = _as_float(value, "timeout")
        if number <= 0 or number > MAX_TIMEOUT_SECONDS:
            raise ConfigValidationError(
                f"timeout must be greater than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds"
            )
        self._timeout = number

    @property
    def interval(self) -> float:
        """Return the schedule interval in seconds."""
        return self.__interval

    @interval.setter
    def interval(self, value: object) -> None:
        """Validate and store the interval behind a name-mangled attribute."""
        number = _as_float(value, "interval")
        if number < MIN_INTERVAL_SECONDS or number > MAX_INTERVAL_SECONDS:
            raise ConfigValidationError(
                "interval must be between "
                f"{MIN_INTERVAL_SECONDS:g} and {MAX_INTERVAL_SECONDS:g} seconds"
            )
        self.__interval = number

    @abstractmethod
    def run(self) -> CheckResult:
        """Execute this check. Callers depend on this method, not the subclass."""

    @classmethod
    @abstractmethod
    def from_config(cls, spec: Mapping[str, Any]) -> BaseCheck:
        """Build one check of this kind from a config mapping."""


def build_check(spec: Mapping[str, Any]) -> BaseCheck:
    """Build a check by looking ``kind`` up in the metaclass registry."""
    if not isinstance(spec, Mapping):
        raise ConfigValidationError("monitor config must be an object")
    kind = spec.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ConfigValidationError("monitor kind must be a non-empty string")
    try:
        check_cls = CheckMeta.registry[kind]
    except KeyError as exc:
        raise ConfigValidationError(f"unknown check kind {kind!r}") from exc
    return check_cls.from_config(spec)


def _require_name(name: object) -> str:
    """Return a stripped monitor name or raise ``ConfigValidationError``."""
    if not isinstance(name, str):
        raise ConfigValidationError("name must be a string")
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_NAME_LENGTH:
        raise ConfigValidationError(f"name must be 1..{MAX_NAME_LENGTH} characters")
    return cleaned


def _require_target(target: object) -> str:
    """Return a non-empty target string."""
    if not isinstance(target, str):
        raise ConfigValidationError("target must be a string")
    cleaned = target.strip()
    if not cleaned or cleaned != target:
        raise ConfigValidationError("target must be a non-empty string without surrounding space")
    return cleaned


def _as_float(value: object, field: str) -> float:
    """Coerce an int or float. Booleans are rejected because they are ints."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"{field} must be a number")
    return float(value)


def reject_unknown(spec: Mapping[str, Any], allowed: frozenset[str]) -> None:
    """Reject config keys this check does not understand."""
    unknown = sorted(set(spec) - allowed)
    if unknown:
        raise ConfigValidationError(f"unknown fields: {', '.join(unknown)}")


def optional_value(spec: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Return ``spec[key]`` when present, otherwise ``default``."""
    if key not in spec:
        return default
    return spec[key]


def require_int(value: object, field: str) -> int:
    """Return an int. Booleans are rejected because they are ints."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{field} must be an integer")
    return value


def require_name_target(spec: Mapping[str, Any]) -> tuple[str, str]:
    """Return the name and target strings from a monitor object."""
    try:
        name = spec["name"]
        target = spec["target"]
    except KeyError as exc:
        missing = exc.args[0]
        raise ConfigValidationError(f"missing {missing}") from exc
    if not isinstance(name, str) or not isinstance(target, str):
        raise ConfigValidationError("name and target must be strings")
    return name, target


def prepare_spec(spec: Mapping[str, Any], *, kind: str, allowed: frozenset[str]) -> None:
    """Reject unknown keys and a mismatched ``kind``."""
    reject_unknown(spec, allowed)
    got = spec.get("kind", kind)
    if got != kind:
        raise ConfigValidationError(f"kind must be {kind!r}")


_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_disallowed_literal_host(host: str) -> bool:
    """Return True for localhost names and literal non-public IP addresses.

    Non-IP hostnames other than localhost return False. They are not resolved.
    """
    cleaned = host.lower().rstrip(".")
    if cleaned in _LOCAL_NAMES:
        return True
    try:
        parsed: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    mapped = getattr(parsed, "ipv4_mapped", None)
    if isinstance(mapped, ipaddress.IPv4Address):
        parsed = mapped
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    ):
        return True
    return isinstance(parsed, ipaddress.IPv4Address) and parsed in _CGNAT
