# Somatriq design tokens

Status: v1, 2026-09-09. This document is the versioned source of truth for the web workspace and the Android collector. Tokens describe product meaning; platform implementations may use native names while preserving the values and semantics below.

## Visual register

Somatriq is calm, precise, and scientific. The interface should feel like a trustworthy instrument: quiet surfaces, legible evidence, explicit data-quality states, and one restrained teal accent. Decoration never competes with measurements.

## Color

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `surface.canvas` | `#F3F1EB` | `#111816` | App background |
| `surface.panel` | `#FBFAF6` | `#18211E` | Cards and grouped controls |
| `surface.raised` | `#FFFFFF` | `#202B27` | Menus and transient overlays |
| `ink.primary` | `#172421` | `#F3F1EB` | Primary text and iconography |
| `ink.secondary` | `#5C6864` | `#AAB5B0` | Supporting text |
| `line.subtle` | `#D8D8D0` | `#33403B` | Rules and card borders |
| `accent.primary` | `#2E796D` | `#63C7B5` | Selected states and primary actions |
| `accent.soft` | `#DCEDE8` | `#213D37` | Accent background |

Do not encode health meaning through the accent color alone.

## Data-quality and health states

| Token | Value | Meaning |
| --- | --- | --- |
| `state.good` | `#2E796D` | Expected coverage or a completed operation |
| `state.watch` | `#A66B1F` | Partial coverage, stale data, or an uncertain result |
| `state.problem` | `#A3473F` | Failed operation, invalid sample, or missing required data |
| `state.unknown` | `#6B7471` | No conclusion can be drawn |

Every state must include a text label or icon with an accessible name. `good`, `watch`, and `problem` describe system/data quality, not a diagnosis or medical judgment.

## Chart palette

Use a small, color-blind-aware sequence and prefer direct labels over legends.

| Token | Value | Default series |
| --- | --- | --- |
| `chart.1` | `#2E796D` | Primary metric |
| `chart.2` | `#4D6F8E` | Comparison metric |
| `chart.3` | `#A66B1F` | Intervention or annotation |
| `chart.4` | `#7A5E8E` | Additional series |
| `chart.grid` | `#D8D8D0` | Grid and zero lines |
| `chart.missing` | `#8C9591` | Explicit missing/unknown intervals |

Never interpolate across missing intervals without an explicit visual treatment. Raw, normalized, and derived series must remain distinguishable in labels and accessible descriptions.

## Typography

- UI sans: system font stack (`Inter` when bundled, then platform sans).
- Data mono: `ui-monospace`, `SFMono-Regular`, `Menlo`, `Consolas`, monospace.
- Display: `clamp(2.25rem, 6vw, 5rem)`, line-height `0.94`, weight `600`.
- Section title: `clamp(1.35rem, 2vw, 2rem)`, line-height `1.1`, weight `600`.
- Body: `1rem`, line-height `1.55`, weight `400`.
- Label: `0.75rem`, line-height `1.25`, weight `600`, letter-spacing `0.08em`.

Use tabular numbers for measurements, dates, durations, and percentages.

## Spacing

Base unit: 4 px.

| Token | Value |
| --- | --- |
| `space.1` | `0.25rem` |
| `space.2` | `0.5rem` |
| `space.3` | `0.75rem` |
| `space.4` | `1rem` |
| `space.6` | `1.5rem` |
| `space.8` | `2rem` |
| `space.12` | `3rem` |
| `space.16` | `4rem` |
| `space.24` | `6rem` |

## Radius and elevation

| Token | Value | Use |
| --- | --- | --- |
| `radius.control` | `0.5rem` | Inputs and buttons |
| `radius.panel` | `0.75rem` | Cards and grouped surfaces |
| `radius.app-icon` | platform mask | Never bake a second outer mask into maskable artwork |
| `elevation.0` | none | Default surfaces |
| `elevation.1` | `0 1px 2px rgb(23 36 33 / 8%)` | Menus and focused raised surfaces |

Prefer borders and surface contrast over shadows.

## Motion

| Token | Value | Use |
| --- | --- | --- |
| `motion.fast` | `120ms` | Hover/focus feedback |
| `motion.base` | `180ms` | Small state transitions |
| `motion.slow` | `280ms` | Deliberate panel changes |
| `motion.ease` | `cubic-bezier(0.2, 0, 0, 1)` | Default easing |

Motion must communicate a state change. Respect `prefers-reduced-motion`; disable non-essential transforms and animated chart drawing when reduced motion is requested.

## Accessibility and responsive rules

- Minimum pointer target: 44 × 44 CSS pixels.
- Visible keyboard focus uses a 2 px `accent.primary` outline plus 2 px offset.
- Body copy maintains at least WCAG AA contrast; measurements and status text are never low-contrast decoration.
- Mobile layouts are content-prioritized rather than desktop columns squeezed smaller.
- Heading order follows the visual hierarchy: one `h1`, section `h2`, nested card `h3`.

## Brand mark

The mark combines a signal trace with a sparse analytical matrix. The canonical files live in `docs/brand/`; platform exports must preserve the dark field, teal trace, clear safe area, and a simplified monochrome form. The Android adaptive foreground never contains text.
