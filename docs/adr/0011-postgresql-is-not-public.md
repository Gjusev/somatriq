# ADR 0011 — PostgreSQL is not public

Status: Accepted · Date: 2026-09-04

## Context

The database holds the user's complete longitudinal health history. §28 forbids publishing 5432 to the internet; a personal VPS has no perimeter to spare.

## Decision

PostgreSQL runs only on the private Docker network of the Somatriq compose project. No Traefik route, no published host port. Administrative access occurs through SSH tunnel to the VPS, Dokploy's secure tooling, or a temporary controlled tunnel — never a permanent public port. The service exposes only its Docker healthcheck; credentials live in environment variables documented in `.env.example`, never in the repository.

## Alternatives

- **Published port with firewall allowlist** — rejected: still a permanent internet-facing surface; one firewall mistake away from exposure.
- **PgBouncer public endpoint** — rejected: same exposure class, no benefit at personal scale.

## Consequences

- The §211 post-deploy checklist verifies "database has no public port" mechanically.
- Any tooling that needs database access (backup verification, restore drills, admin queries) runs inside the network or through a tunnel.
- This is a §221 non-negotiable; superseding it requires a new ADR and explicit user authorization.
