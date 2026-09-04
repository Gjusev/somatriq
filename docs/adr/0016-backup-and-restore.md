# ADR 0016 — Backup and restore

Status: Accepted · Date: 2026-09-04 · Fills the ADR gap found by the grill

## Context

§166 mandates two complementary backup layers without naming the authoritative one; TimescaleDB needs pre/post-restore handling; §172's disaster-recovery goal requires recovering secrets, and no founding ADR covered any of it.

## Decision

**The Somatriq backup service is authoritative**: scheduled `pg_dump` logical backups with TimescaleDB-aware pre/post restore hooks, plus an archive of the raw blob volume, encrypted, pushed to an external destination (S3-compatible / B2 / MinIO; §168) with retention 14 daily / 8 weekly / 12 monthly (§169), configurable. **Dokploy volume/database backups are the secondary layer** for fast volume-level recovery. Persistent data uses the named volumes of §167 so both layers address the same artifacts. **Verification is scheduled, not aspirational** (§170): object existence + checksum per run; periodic restore into a temporary isolated database. **Secret export**: an explicit encrypted secret-export procedure backs up credentials (never in plaintext, never in the repository), and a restored-secrets check is part of `docs/runbooks/restore.md`, which covers PostgreSQL, raw archive, configuration, secrets, and the Dokploy deployment (§171). Backup jobs and verification results are visible on the Admin page (§120).

## Alternatives

- **Dokploy backups as the sole layer** — rejected: no Timescale-aware restore, no raw-archive semantics, couples recovery to the platform.
- **Continuous WAL archiving only** — rejected: point-in-time elegance is unnecessary at this scale; harder to verify restores.

## Consequences

- The §172 test — new VPS recovers from git + database backup + raw backup + secret backup — is drillable from the runbook.
- Backup failures are loud: they surface via Admin, Data Quality, and system notifications (§158).
- Restore drills into an isolated database double as migration tests on real data.
