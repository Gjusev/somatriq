<p align="center">
  <img src="docs/brand/somatriq-mark.svg" width="88" alt="Somatriq">
</p>

# Somatriq

**Private biometric intelligence, from raw signal to personal evidence.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/Python-3.12%20·%20uv-blue)
![PostgreSQL + TimescaleDB](https://img.shields.io/badge/PostgreSQL-TimescaleDB-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-services-green)
![NOOP fork collector](https://img.shields.io/badge/Collector-NOOP%20fork%20·%20PolyForm--NC-orange)

Somatriq is a self-hosted platform for collecting, preserving and understanding longitudinal health data. Its mobile Collector is based on a maintained fork of [NOOP](https://github.com/ryanbr/noop): NOOP supplies the working WHOOP Bluetooth foundation; the Somatriq fork adds a private sync and analysis path that you control.

![Biometric signals converging into the Somatriq measurement matrix](docs/brand/somatriq-signal-matrix.webp)

> Somatriq is independent, is not affiliated with WHOOP and is not a medical device. WHOOP is one supported hardware source.

## What it is now

Somatriq is an active, private prototype—not a finished consumer release. This repository contains implemented server, analytical and web slices that correspond to work planned across M0–M13; this does not mean every milestone is complete end to end:

- local account auth, phone pairing, scoped device tokens and revocation;
- idempotent batch ingest for immutable raw frames and canonical observations;
- heart-rate history, daily features, HRV and an explainable recovery score;
- a daily Today Plan (training guidance tier, target-strain range, bedtime
  window) with explainable Sleep Need, capped sleep debt and user
  preferences;
- a behavior journal as laboratory input: six quick-log behaviors across
  web, Telegram and a scoped mobile write path, transparent behavior
  insights (lag-1 associations with n, p, BH-FDR q and confounders) and
  one-click conversion into N-of-1 experiments;
- a longitudinal Explore surface (day-grain series with honest gaps,
  device boundaries, algorithm versions, timezone changes and owner
  annotations) and a private Health Monitor — vitals vs personal
  baseline with coverage and provenance plus a shareable deterministic
  PDF report, never a diagnosis;
- data coverage, freshness, provenance and versioned algorithm semantics;
- deterministic correlations and N-of-1 experiments with explicit compliance;
- strength-session logging, load summaries and next-day recovery analysis;
- preview-first CSV import plus JSON/CSV/raw-registry export;
- domain-scoped MCP, local-first AI provider plumbing, Telegram and notifications;
- TimescaleDB/PostgreSQL storage, migration gate, backup and Dokploy-oriented operations.

The Android Collector is a separate NOOP fork by design. It already contains the Somatriq identity, local raw journal, durable queue, pairing/sync surface and server-backed science views. Lifecycle-triggered sync and end-to-end validation against real bands remain the largest mobile gaps.

## Product surfaces

| Surface | Role |
|---|---|
| **Somatriq Mobile** | NOOP-based Android Collector: BLE, local SQLite, decoding, raw capture and offline queue |
| **Web** | Today, heart-rate history, daily features, correlations, experiments, training, devices and data ownership |
| **MCP** | Scoped health and analysis tools for external AI clients; no arbitrary SQL |
| **Telegram** | Quick health questions, event logging and experiment workflows |
| **API + workers** | Validation, idempotent ingest, deterministic analytics, reprocessing and notifications |

```mermaid
flowchart TB
    B[WHOOP 4.0 / 5.0 / MG] -- BLE --> C[NOOP-fork Collector<br/>local SQLite · durable offline queue]
    C -- idempotent queued HTTPS<br/>scoped device tokens --> ING[api · FastAPI<br/>validation · batch ingest]
    ING --> RAW[(raw archive<br/>immutable frames)]
    ING --> PG[(PostgreSQL + TimescaleDB<br/>system of record)]
    WRK[worker · scheduler<br/>deterministic analytics] --> PG
    RAW --> WRK
    WEB[web · Next.js static export] --> ING
    MCP[mcp · domain-scoped tools] --> PG
    TG[telegram · agent] --> LLM[AI explains structured results<br/>— never computes statistics]
    PG --> OUT[Today Plan · Explore · correlations<br/>N-of-1 experiments · PDF report]
```

Full detail and the decision record behind it: [`docs/architecture.md`](docs/architecture.md) and the [ADRs](docs/adr/).

## Screenshots

### Web dashboard

| Today and longitudinal analysis | Mobile-width dashboard |
|---|---|
| ![Somatriq desktop dashboard with demonstration data](docs/brand/screenshots/dashboard-desktop.png) | ![Somatriq mobile-width dashboard with demonstration data](docs/brand/screenshots/dashboard-mobile.png) |

### Mobile Collector direction

<p align="center">
  <img src="docs/brand/screenshots/mobile-app-concept.png" width="320" alt="Somatriq Mobile design target showing recovery, coverage, insight and sync provenance">
</p>

The mobile image is a design target for the separate NOOP fork, not a screenshot of an already-shipped build. All screenshots use synthetic demonstration values and contain no personal health data.

## What is still missing

The next useful work is product integration rather than more isolated backend surface area:

1. Wire the Android fork's foreground/background sync lifecycle and validate offline → reconnect → exactly-once ingest on physical hardware.
2. Run end-to-end hardware acceptance on WHOOP 4.0 and track upstream parity for WHOOP 5.0/MG decoded scores.
3. Build the richer **Explore** experience: multi-year timelines, source/provenance inspection, quality overlays and instrument-boundary markers.
4. Complete PWA installability with an unauthenticated-shell-only service worker and offline-state UX.
5. Complete OAuth 2.1 authorization for MCP clients and the explicit web grant flow; scoped PATs remain the service fallback.
6. Exercise deployment, monitoring and restore drills on the target Dokploy VPS without exposing PostgreSQL.
7. Add and validate further sources through the canonical Connector boundary instead of mixing vendor logic into the health domain.

## Principles

- **Own the evidence.** Raw measurements are preserved; exports are first-class.
- **Collect locally first.** Phone and server outages must not lose data.
- **Calculate before explaining.** Python performs statistics deterministically; AI explains structured results.
- **Show uncertainty.** Missingness, quality, coverage and freshness stay visible.
- **Version meaning.** Algorithm changes never silently rewrite historical semantics.

The master specification is [`docs/SPEC.md`](docs/SPEC.md), the shared domain language is [`CONTEXT.md`](CONTEXT.md), and the recorded product decisions are in [`docs/grill/2026-09-04-spec-grill.md`](docs/grill/2026-09-04-spec-grill.md).

## Repository map

| Path | Contents |
|---|---|
| `apps/web/` | Next.js static-export web application |
| `services/` | API, worker, scheduler, agent, MCP, Telegram and notifications |
| `packages/` | Contracts, database, analytics, agent tools, connectors and observability |
| `database/` | Alembic migrations and TimescaleDB structures |
| `infra/` | PostgreSQL, backup and monitoring assets |
| `docs/adr/` | Architecture Decision Records |
| `docs/brand/` | Brand mark, editorial image, usage notes and screenshots |
| `docs/design/` | Versioned visual tokens and accessibility rules |
| `docs/research/` | Durable protocol, platform and fork findings |

## Development

```bash
# Python workspace
uv sync
uv run pytest

# Web (static export)
cd apps/web
npm install
npm run typecheck
npm run build
```

Full stack: `cp .env.example .env` then `docker compose up --build`. The
Compose file targets the deployment shape — a Traefik edge on the external
`dokploy` network with `SOMATRIQ_HOST` routing `/`, `/api` and `/mcp`
(ADR 0007). On that host it includes the one-shot migration gate and keeps
PostgreSQL on the private network; on a bare laptop without the edge proxy,
run the checks above and develop the web app against a deployed API.

## License and lineage

This server repository is MIT-licensed. It contains no NOOP-derived code: it reuses documented BLE protocol facts, which NOOP's PolyForm Noncommercial license expressly declares uncopyrightable. The separate Android Collector fork inherits NOOP's PolyForm Noncommercial terms for NOOP-derived code. `NOTICE`, `ATTRIBUTION.md`, [ADR 0004](docs/adr/0004-noop-fork-is-mobile-collector.md) and the [fork research](docs/research/noop-fork-research.md) document the full position.

## Author

**Youssef Ouhaghi Ahmian** — [mokka-agentur.de](https://mokka-agentur.de) · [GitHub](https://github.com/Gjusev)
