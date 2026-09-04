# Somatriq

Self-hosted personal biometric intelligence platform. WHOOP → local-first Android collector (NOOP fork) → your own server: PostgreSQL, TimescaleDB, deterministic analytics, explainable scores, MCP access, Telegram.

Your body. Your data. Your intelligence.

> Somatriq is an independent personal data platform. It is not affiliated with WHOOP. WHOOP is a supported hardware source. Somatriq is not a medical device.

## Status

**M0 — repository bootstrap.** Founding documents are in place; skeletons land next. See `docs/SPEC.md` §193-206 for the milestone plan.

## Repository map

| Path | Contents |
|---|---|
| `docs/SPEC.md` | Master specification (source of truth) |
| `CONTEXT.md` | Domain glossary — one meaning per term |
| `AGENTS.md` | Mandatory engineering behavior |
| `docs/adr/` | Architecture Decision Records (0001-0017) |
| `docs/grill/` | Spec grill reports and the recorded product decisions |
| `apps/web/` | Next.js web application (static-export CSR) |
| `services/` | api · worker · scheduler · agent · mcp · telegram · notifications |
| `packages/` | core · contracts · db · analytics · agent_tools · connectors · observability |
| `database/` | Alembic migrations, SQL, aggregates, fixtures |
| `infra/` | compose · postgres · backup · monitoring |
| `prompts/` | Versioned AI prompts |
| `tests/` | integration · e2e · fixtures |

## Principles

Own your data. Local-first collection. Raw before interpretation. Statistics before AI. Explain every score. Everything exportable, traceable, versioned. See `docs/SPEC.md` §2 and the non-negotiables in §221.

## Development

```bash
# Python (uv workspace at the repository root)
uv sync
uv run pytest

# Web
cd apps/web && npm install && npm run build

# Full stack (local behavior mirrors production, incl. the migration gate)
docker compose up --build
```

## Deployment

One Dokploy Compose project on a private VPS. PostgreSQL is never public. Operations go through the official Dokploy MCP. See ADR 0007, ADR 0008, and `docs/runbooks/` (restore runbook lands with M0 backup work).

## License

Private for now. `LICENSE`, `NOTICE`, and `ATTRIBUTION.md` are placeholders pending the NOOP license inventory (ADR 0004); the final license is ratified before any publication.
