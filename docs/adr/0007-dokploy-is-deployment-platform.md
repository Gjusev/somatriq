# ADR 0007 — Dokploy is the deployment platform

Status: Accepted · Date: 2026-09-04 · Resolves grill blocker B3 (migration ownership)

## Context

The VPS already runs Dokploy (§20); no second platform abstraction, Caddy, or Traefik instance is added (Dokploy owns ingress and TLS). The grill confirmed nobody owned migration execution: §134 forbids startup mutation and §211 verifies "migration completed", yet no migrate service existed in §25 and Dokploy has no first-class compose migration hook — seven services booting concurrently against an unmigrated database crash-loop. The split web/api origins (§19/27) with HttpOnly cookie sessions also opened an avoidable cross-origin CSRF surface.

## Decision

Somatriq deploys as one Dokploy Docker Compose project with the service list of §25 plus a **one-shot `somatriq_migrate` service** that runs `alembic upgrade head` and exits; every other service uses `depends_on: { somatriq_migrate: { condition: service_completed_successfully } }`. Hypertable and continuous-aggregate DDL is hand-written SQL with the TimescaleDB extension version pinned (ADR 0002); CI tests up+down migrations. **Routing is single-origin, path-based** under one Traefik host (§216): web at `/`, API at `/api`, MCP at `/mcp` — cookie sessions stay SameSite=Lax with no CORS (ADR 0014). Only web/api/mcp (and optionally ntfy) are public; postgres, worker, scheduler, agent, backup stay private (ADR 0011). Every container has a Docker healthcheck; the §211 post-deploy checklist gains "migrate service exited 0".

## Alternatives

- **Migration as a Dokploy MCP exec step** — rejected: the gate would live outside compose; local dev and rollbacks need it anyway.
- **Advisory-lock leader election at API startup** — rejected: violates §134's spirit; partial-boot races.
- **Separate web/api hostnames** — rejected: cross-origin cookies, CORS, and a bigger CSRF surface for zero benefit on a personal deployment.

## Consequences

- Deployments are reproducible from git (§24): compose, Dockerfiles, healthchecks, env documentation are version-controlled; Dokploy holds only deployment state.
- A failed migration halts the stack before any service serves stale-schema traffic.
- Local `docker compose up` behaves like production, including the migration gate.
