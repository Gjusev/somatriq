# ADR 0001 — PostgreSQL is the system of record

Status: Accepted · Date: 2026-09-04

## Context

Somatriq needs one durable store for identity, ingest bookkeeping, health records, derived metrics, research data, AI artifacts, notifications, and audit (spec §52, §54-61). The deployment is a single personal VPS; operational simplicity and a single backup/restore path outweigh per-workload storage engines. The product explicitly refuses default MongoDB/InfluxDB/Qdrant/Pinecone/Elasticsearch.

## Decision

PostgreSQL is the sole system of record, extended with TimescaleDB (ADR 0002) and pgvector in one pinned self-built image (spec §53, recorded under `infra/postgres/`). Data is organized into explicit schemas: identity, ingest, raw, timeseries, health, derived, research, ai, notifications, audit, system. No additional database engine is introduced without a superseding ADR demonstrating clear need.

## Alternatives

- **InfluxDB / Timescale-only time-series store** — rejected: doubles the operational surface, weak relational integrity for identity/experiments/provenance, second backup path.
- **MongoDB** — rejected: provenance and experiment queries are relational; document model buys nothing here.
- **SQLite server-side** — rejected: concurrent worker/scheduler/API processes and WAL contention on a VPS.

## Consequences

- One engine to back up, restore, and pin; ADR 0016 defines Timescale-aware dump/restore.
- We maintain our own Postgres image with the three extensions pinned; extension upgrades are deliberate migrations.
- Vector search (AI memory) lives in pgvector; full-text in Postgres — no sidecar stores (spec §185).
- Scale ceiling is far beyond a personal deployment's needs; revisit only with evidence.
