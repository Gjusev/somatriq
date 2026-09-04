# ADR 0005 — Mobile is local-first

Status: Accepted · Date: 2026-09-04

## Context

Remote availability must never be required to preserve wearable data (spec §34, §221). The mandated flow is BLE → local persistent transaction → sync queue → background uploader → server; the forbidden flow is BLE → remote API → local database. The grill confirmed the durability semantics were underspecified: queue granularity drifted between record-level (§39) and batch-level (§37/42), and `failed_permanent` had no retention rule.

## Decision

The Collector writes every capture to SQLite in a local transaction before anything leaves the device; BLE protocol classes contain no networking code (§35). The sync queue is **batch-level**: entries are batches carrying raw + observation payloads, with states `pending / uploading / acknowledged / failed_retryable / failed_permanent` (`CONTEXT.md`). Retryable failures back off exponentially with jitter. **`failed_permanent` batches are retained on device indefinitely** — never raw-pruned, surfaced as a persistent sync warning — with the server recording a matching `ingest.failures` row and notification. Batching is the default cadence (5-minute target via WorkManager or the appropriate current mechanism; the spec's escape clause §36 is honored — a foreground-service expedited path may drive shorter intervals), with event-triggered early sync for completed sleep/workouts and manual force-sync. Server unavailability is normal operation, tested by the M3 offline gate (§196).

## Alternatives

- **Record-level queue entries** — rejected: conflicts with batch transport and §42's batch acknowledgement; reworded in `CONTEXT.md`.
- **Drop permanently rejected batches after server recording** — rejected: the phone remains a custodian of rejected health data until the user explicitly resolves it.

## Consequences

- Offline reliability is structural, not a feature: M3's disable-VPS/collect/restore/sync/no-loss/no-duplicates gate is a hard release gate.
- Local storage grows if the server rejects batches permanently — bounded by explicit user action.
- The phone is an offline-capable cache and capture system; the server is the long-term source of truth once data is acknowledged.
