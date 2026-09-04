# ADR 0014 — The web is the primary rich interface (single origin, static-export CSR)

Status: Accepted · Date: 2026-09-04

## Context

The web app is the primary rich surface (§107: TypeScript, React, Next.js, TanStack Query, ECharts), installable as a PWA (§121), behind HttpOnly SameSite cookie sessions (§122). The grill confirmed the rendering mode and origin topology were undefined, and that split web/api origins (§19/27) plus cookie sessions open a cross-origin CSRF/cookie-scoping and service-worker-caching surface. Design register: calm, precise, scientific, information hierarchy over card grids (§15-16, §175) — WCAG 2.2 AA target (§18).

## Decision

**Single origin, path-based routing** (per ADR 0007): web at `/`, API at `/api`, MCP at `/mcp` on one Traefik host (§216's single URL). **Rendering mode: static-export CSR** — the app is an auth-gated single-page application; the server ships static assets and the API serves data. Cookie sessions stay SameSite=Lax with no CORS; the PWA service worker caches **only the unauthenticated app shell** so no health data persists on shared devices. Charts fetch viewport-appropriate aggregated data from continuous aggregates (§71, §173) — never raw-sample firehoses — with missing-data gaps rendered as gaps, quality indication, timezone-correct axes (§174, ADR 0017), and a semantic health-state vocabulary (not green/red) defined in `docs/design/` tokens (§175-176). ECharts renders charts; accessible data tables accompany canvas charts where screen-reader equivalence matters (§18).

## Alternatives

- **Next.js SSR/ISR** — rejected: an auth-gated personal dashboard gains little from server rendering, pays a Node runtime in compose, and complicates cookie forwarding; revisit only with evidence.
- **Separate web/api hostnames** — rejected: cross-origin cookie/CSRF surface for zero benefit (grill risk).

## Consequences

- One container fewer (static assets served by Traefik or a tiny static server), simpler CSRF story, offline shell that can never leak health data.
- ECharts bundles are code-split so pages without charts don't ship them (§173).
- Design tokens, health-state semantics, and the chart palette become a versioned deliverable in `docs/design/` before the first major UI milestone (M6).
