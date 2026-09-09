# Somatriq brand system

Somatriq should look like a personal scientific instrument: calm, exact and independent. It must not borrow the visual language of WHOOP or present itself as a medical product.

## Mark

![Somatriq mark](somatriq-mark.svg)

The nine points represent a matrix of longitudinal measurements. The continuous `S` trace connects original evidence to interpretation without erasing provenance. The two brighter endpoints make ownership explicit: the user's data remains legible from source to result.

Use the mark with generous clear space. Do not add glows, gradients, a medical cross, a heart silhouette or traffic-light recovery colors.

## Palette

| Token | Value | Use |
|---|---:|---|
| Instrument | `#172421` | Primary dark surface and icon field |
| Paper | `#F6F5F0` | Editorial background |
| Signal | `#63C7B5` | Single identifying accent |
| Ink | `#1C2422` | Text and fine signal work |

The product can retain its user-selectable chart themes. Brand-owned surfaces use Instrument + Paper + Signal.

The full cross-platform token contract—spacing, radius, elevation, motion, data-quality states and chart colors—lives in [`../design/tokens.md`](../design/tokens.md).

## Typography and voice

- Geist for interface and editorial copy; Geist Mono for measurements, versions and provenance.
- Short, literal language. State coverage, freshness and uncertainty.
- Never imply causation from association, hide missing data or call Somatriq a medical device.

## Assets

- `somatriq-mark.svg` — canonical scalable mark.
- `somatriq-app-icon-1024.png` — raster master for store and legacy-icon export.
- `somatriq-signal-matrix.webp` — optimized editorial brand image for repository and release surfaces.
- `screenshots/mobile-app-concept.png` — clearly labeled mobile UI design target.
- `apps/web/app/icon.svg` — Next.js application icon.
- `apps/web/public/brand-mark.svg` — runtime/header asset.
- `apps/web/public/brand-mark-maskable.svg` — full-bleed PWA launcher asset.
- `apps/web/public/icon-*.png` — 192 px and 512 px regular/maskable PWA exports.
- `android/` — adaptive launcher-icon handoff for the separate NOOP fork.

The signal-matrix image was generated on 2026-09-09 with OpenAI's built-in image generation tool from a Somatriq-specific creative brief, then selected without further alteration.
