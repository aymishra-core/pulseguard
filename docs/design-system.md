# Design system

PulseGuard draws every screen from the tokens in `src/pulseguard/web/static/status.css`. The typeface is the system sans stack, so the app does not download a font. Icons are an inline SVG sprite in `templates/base.html`.

## Colour

Semantic names are `--ok`, `--warn`, `--danger`, `--info`, and `--neutral`. Surfaces are `--bg`, `--surface`, and `--surface-2`. Text is `--ink` on those surfaces, with `--muted` for secondary text. `--accent` and `--on-accent` are the primary button pair.

Light tokens live on `:root`. Dark tokens live on `:root[data-theme="dark"]` and, when the system prefers dark and the operator has not chosen light, on `:root:not([data-theme="light"])`. The theme toggle stores `pulseguard-theme` in `localStorage`. A private-mode browser that blocks storage still switches for the current page.

Text on a surface meets WCAG AA (4.5:1). `tests/test_design.py` computes the ratios from these variables.

## Spacing, radius, shadow, type, motion

Spacing is a 4 px scale: `--space-1` (4) through `--space-7` (48). Radius is `--radius` (10px) and `--radius-sm` (6px). `--shadow` is the only elevation. Type sizes are `--text-sm`, `--text-md`, `--text-lg`, and `--text-xl`. Metrics use tabular numbers.

Motion durations are `--motion-micro` (120ms), `--motion-standard` (200ms), and `--motion-page` (400ms). Entering motion uses `--ease-out`. Leaving motion uses `--ease-in`. Animation is limited to `transform` and `opacity`. `prefers-reduced-motion: reduce` turns animation and transitions off.

Layers are `--z-nav` (20), `--z-toast` (40), `--z-palette` (50), and `--z-modal` (60).

## Components

These classes are the shared controls: `button` / `.btn` (primary), `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.is-loading`, inputs and selects, checkboxes, `.pill`, `.card`, `.stat`, tables, `.segmented`, `.toast`, `.empty`, `.skeleton`, `.progress`, `kbd`, `[data-tooltip]`, and `dialog`. The sidebar, top bar, and command palette (`Ctrl` or `Cmd`+`K`) are in `base.html`.
