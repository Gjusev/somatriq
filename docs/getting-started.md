# Getting started

## Requirements

- Python 3.12 with [uv](https://docs.astral.sh/uv/)
- Node.js and npm (for the web app)
- Docker (for the Compose deployment; not needed for the checks below)

## Checks (no database required)

```bash
git clone https://github.com/Gjusev/somatriq.git
cd somatriq
uv sync
uv run pytest -q        # database-dependent tests skip without their environment
cd apps/web
npm ci
npm run typecheck
npm run build
```

This verifies the Python workspace and builds the web frontend. It does
**not** start a complete local stack: API services, workers and the
database need their integration environment.

## Compose deployment

```bash
cp .env.example .env   # POSTGRES_*, SECRET_KEY, SOMATRIQ_HOST, ...
docker compose up --build
```

The Compose file targets the deployment shape, not a laptop: it expects a
Traefik edge on the external `dokploy` network with `SOMATRIQ_HOST`
routing `/`, `/api` and `/mcp`, and a one-shot migration gate. PostgreSQL
stays on the private `somatriq` network and is never published. On a
machine without that edge, use the checks above and develop the web app
against a deployed API.

## What is intentionally absent

- No public database port, no arbitrary SQL surface (ADR 0011).
- No bundled demo dataset: dashboards in the repository screenshots use
  synthetic values, not personal health data.
