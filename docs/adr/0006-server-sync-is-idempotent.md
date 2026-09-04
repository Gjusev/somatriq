# ADR 0006 — Server sync is idempotent

Status: Accepted · Date: 2026-09-04 · Resolves grill blocker B2

## Context

"Do not allow sync retries to create duplicates" (§221) is non-negotiable, yet the spec listed four idempotency mechanisms (§40) with no precedence, defined no observation natural key anywhere, and left `failed_permanent` without a trigger, wire shape, or owner. The first TDD test (§207 step 13) is a dedup test, and Timescale hypertable unique constraints are expensive to retrofit (ADR 0002).

## Decision

The ingest contract is:

1. **Batch idempotency:** the **batch UUID is the sole idempotency key**, enforced by a unique index on `ingest.idempotency_keys`. A replayed UUID returns the original acknowledgement. The content hash is stored for forensics; the same UUID with a different hash returns **409 Conflict**.
2. **Record identity:** the per-record natural key is `(user_id, device_id, source_record_id)`, unique per hypertable. Distinct `source_record_id` = distinct row — same-instant duplicates across sources and legitimate identical samples are representable; retries dedup naturally. Re-sent records inside an accepted batch count in `records_duplicate` (§42).
3. **Permanent rejection:** validation failures that cannot be fixed by retry return **HTTP 422** with `accepted: false`, a stable machine-readable `error_code` (§157), and per-record details. Mobile marks the batch `failed_permanent`, retains it (and its raw) indefinitely, and surfaces a persistent sync warning; the server writes `ingest.failures` and a system notification. Retriable conditions (5xx, timeouts, throttling) map to `RetryableIngestError` and mobile backoff.
4. **Acknowledgement authority:** mobile marks a batch synchronized only on a valid ack (§42); a **raw-ack** covering the raw batch is the only event that later authorizes raw pruning (ADR 0003).

## Alternatives

- **Per-record dedup on `(metric, source_timestamp)`** — rejected: cannot distinguish retries from real repeats, forces a ruling on legitimate same-instant duplicates.
- **Server-authoritative rejection custody (phone drops after server records)** — rejected: single custodian of rejected health data; violates local-first custodianship.

## Consequences

- The M1 hypertable DDL is writable now: unique index on `(user_id, device_id, source_record_id)` from the first migration.
- Live-stream heart rate (§183) persists through normal ingest, so websocket display dedups against the same natural key.
- Golden fixtures for replayed batches (§153) test this contract end to end.
