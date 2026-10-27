"""WCAG contrast for the design tokens, and the static size budget."""

from __future__ import annotations

import re
from pathlib import Path

_CSS = Path(__file__).resolve().parents[1] / "src" / "pulseguard" / "web" / "static" / "status.css"
_JS = Path(__file__).resolve().parents[1] / "src" / "pulseguard" / "web" / "static" / "app.js"
_PAIRS = (
    ("--ink", "--bg"),
    ("--ink", "--surface"),
    ("--muted", "--bg"),
    ("--muted", "--surface"),
    ("--on-accent", "--accent"),
    ("--ok", "--surface"),
    ("--warn", "--surface"),
    ("--danger", "--surface"),
    ("--info", "--surface"),
)


def _channel(value: int) -> float:
    """Return one sRGB channel as linear light."""
    scaled = value / 255
    if scaled <= 0.04045:
        return scaled / 12.92
    return ((scaled + 0.055) / 1.055) ** 2.4


def _luminance(color: str) -> float:
    """Return the relative luminance of a #rrggbb colour."""
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _contrast(fore: str, back: str) -> float:
    """Return the WCAG contrast ratio."""
    lighter = max(_luminance(fore), _luminance(back))
    darker = min(_luminance(fore), _luminance(back))
    return (lighter + 0.05) / (darker + 0.05)


def _block(css: str, selector: str) -> dict[str, str]:
    """Read custom properties from the first matching rule."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
    assert match is not None, selector
    found: dict[str, str] = {}
    for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", match.group(1)):
        found[name] = value.lower()
    return found


def test_token_contrast_meets_aa() -> None:
    """Text colours on their surfaces meet a 4.5:1 contrast ratio."""
    css = _CSS.read_text(encoding="utf-8")
    themes = {
        "light": _block(css, ":root"),
        "dark": _block(css, ':root[data-theme="dark"]'),
    }
    failures: list[str] = []
    for theme, tokens in themes.items():
        for fore, back in _PAIRS:
            ratio = _contrast(tokens[fore], tokens[back])
            if ratio < 4.5:
                failures.append(f"{theme} {fore} on {back} is {ratio:.2f}")
    assert failures == []


def test_static_files_stay_inside_the_size_budget() -> None:
    """CSS stays under 60 KB and JavaScript under 90 KB, uncompressed."""
    assert _CSS.stat().st_size < 60_000
    assert _JS.stat().st_size < 90_000


def test_templates_and_static_files_do_not_name_remote_hosts() -> None:
    """Templates and static files do not contain a URL the browser would fetch.

    The SVG namespace is allowed because a browser does not request it.
    Localhost and license files are the other exceptions.
    """
    root = _CSS.parents[1]
    pattern = re.compile(r"https?://[^\s\"')<]+")
    allowed = ("http://www.w3.org/2000/svg", "http://127.0.0.1", "http://localhost")
    found: list[str] = []
    for folder in ("templates", "static"):
        for path in (root / folder).rglob("*"):
            if path.suffix not in {".html", ".css", ".js", ".svg"}:
                continue
            for match in pattern.findall(path.read_text(encoding="utf-8")):
                if match.startswith(allowed) or "license" in path.as_posix():
                    continue
                found.append(f"{path.name}: {match}")
    assert found == []
