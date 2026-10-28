# Design system

PulseGuard draws every screen from the tokens in `src/pulseguard/web/static/status.css`. The 1.3.0 console follows the sci-fi telemetry reference: a grid-lined field, teal accent, HUD corner brackets, and a status ring. The reference tree `design/v0` was not in this repository, so the tokens were taken from the prototype screenshots and written here. No React, Tailwind, or remote font is shipped.

Type is the system sans stack (`--font`) plus a system monospace stack (`--mono`) for the version line, targets, times, the check stream, and KPI figures. No font file is bundled.

## Colour

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg` | `#e7f1ef` | `#071016` | Page field |
| `--surface` | `#ffffff` | `#0e1a22` | Panels and cards |
| `--surface-2` | `#f4faf8` | `#132430` | Inset wells |
| `--ink` | `#0c1c22` | `#e7f6f2` | Primary text |
| `--muted` | `#3c5550` | `#b7c9c4` | Secondary text |
| `--line` | `#c9ddd8` | `#1e3a42` | Borders |
| `--grid` | `#d3e4df` | `#12303a` | 48 px background grid |
| `--accent` | `#0c6e58` | `#5eead4` | Teal controls, active nav, rings |
| `--on-accent` | `#ffffff` | `#06261f` | Text on `--accent` |
| `--ok` | `#0b6b3a` | `#8fd0a8` | Up |
| `--warn` | `#8a4b08` | `#f0c27a` | Amber warning |
| `--danger` | `#9d2424` | `#f3b4b4` | Down and incidents |
| `--info` | `#0c5c6e` | `#9ad7e8` | Neutral information |
| `--violet` | `#5b21b6` | `#d6c8ff` | Spare accent, not used as body text |

`--ok-bg`, `--warn-bg`, `--danger-bg`, and `--info-bg` are the pill washes. `--glow` is the panel edge. Solid danger buttons stay `#9d2424` on `#ffffff` so the dark-theme danger text colour is not used as a fill.

Light tokens live on `:root`. Dark tokens live on `:root[data-theme="dark"]` and, when the system prefers dark and the operator has not chosen light, on `:root:not([data-theme="light"])`. The theme toggle stores `pulseguard-theme` in `localStorage`.

Text pairs in `tests/test_design.py` meet WCAG AA (4.5:1): ink and muted on the background and the surface, on-accent on accent, and ok, warn, danger, and info on the surface.

## Spacing, radius, shadow, type, motion

Spacing is a 4 px scale: `--space-1` (4) through `--space-7` (48). Radius is `--radius` (12px) and `--radius-sm` (8px). `--shadow` is the soft elevation and `--glow` is the teal edge.

Panels, cards, the hero, and auth cards draw HUD corners with a `::before` gradient. The page background is two 1 px gradients from `--grid`. The hero ring pulses with `transform` and `opacity` (`.ring::before`). The incident ring uses the `#i-x` glyph and a scale pulse. `prefers-reduced-motion: reduce` turns animation and transitions off.

Motion durations are `--motion-micro` (120ms), `--motion-standard` (200ms), and `--motion-page` (400ms). Entering motion uses `--ease-out`. Leaving motion uses `--ease-in`.

The uptime bar is 90 fixed slices. The bar is `overflow: hidden`. Each slice is `flex: 1 1 0` with `min-width: 0`, and grid children use `minmax(0, 1fr)` so the bar stays inside the card at 360, 768, 1024, 1440, and 1920 px.

KPI labels are one line (`Up`, `Down`, `Unknown`, `Incidents`, `Expiring`, `Uptime`). The figure sits in the first column and the sparkline in a fixed second column, so they do not overlap. Below 560 px the sparkline moves under the figure.

Layers are `--z-nav` (20), `--z-toast` (40), `--z-palette` (50), and `--z-modal` (60).

## Links

`--link` is the accent colour. `--link-offset` is the gap between the glyphs and the underline. A link in a sentence uses that colour and a 1 px underline. A link that is a name — the sidebar, a card title, the hero title, or a name in a table — inherits the text colour at weight 600. It picks up `--link` and the underline only on hover or keyboard focus. The focus ring stays the accent outline. A control that is a link, such as `.button`, does not use the text-link underline.

## Components

These classes are the shared controls: `button` / `.btn` (primary), `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.is-loading`, inputs and selects, checkboxes, `.pill`, `.card`, `.stat`, tables, `.segmented`, `.toast`, `.banner`, `.empty`, `.skeleton`, `.progress`, `meter`, `kbd`, `[data-tooltip]`, and `dialog`. The sidebar, top bar, and command palette (`Ctrl` or `Cmd`+`K`) are in `base.html`.

`.kind-cards` are the check-kind choices on Add monitor. `.timeline` is the incident history. `.uptime-bar` with `.seg` is the 90-slice history, including grey `.gap` and empty slices for missing data. `.public` is the signed-out status page. `.auth` is login, setup, and the branded error page.
