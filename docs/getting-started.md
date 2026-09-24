# Getting started

[← Documentation](README.md) · [Architecture](architecture.md)

## Verify the workspace

Install Python 3.12, `uv`, Node.js 20+ and npm. From the repository root:

```bash
uv sync --frozen
uv run pytest -q
cd apps/web
npm ci
npm run typecheck
npm run build
```

The web build produces static assets. This sequence checks the workspace;
it does not provision an API or a working device connection. `npm run dev`
starts the frontend for development, but authenticated data views still need
an API accessible on the same origin at `/api`.

## Test scope

Python tests that require TimescaleDB skip when the test database is
unreachable. A passing run with skipped tests is not a full integration run.
See the shared [database test helpers](../packages/db/somatriq_db/testing.py)
and [API fixtures](../services/api/tests/conftest.py) before setting up a
dedicated test database. Never point tests at a database containing real data.

## Deployment shape

The checked-in [compose.yml](../compose.yml) targets an existing Dokploy
deployment with an external `dokploy` network and Traefik routing. It is not
a complete laptop quickstart.

1. Copy `.env.example` to `.env` and replace placeholder passwords and signing keys.
2. Configure `SOMATRIQ_HOST`, database settings, storage and the existing edge network.
3. Keep PostgreSQL on the private Docker network; do not publish its service port.
4. Run `docker compose up --build` in that deployment environment.
5. Verify the migration gate, API health and first-run account flow before pairing a device.

Web, API and MCP share one origin at `/`, `/api` and `/mcp`. See
[ADR 0007](adr/0007-dokploy-is-deployment-platform.md) and
[ADR 0014](adr/0014-web-is-primary-rich-interface.md) for the routing design.

## Optional integrations

Local AI is the default privacy setting. Enabling an external model,
Telegram, remote notifications or remote backup changes the data path.
Review the corresponding settings in `.env.example` and the egress rules
before enabling an integration. No provider key is required for deterministic
statistics or the unit tests.
