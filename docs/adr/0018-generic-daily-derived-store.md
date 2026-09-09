# ADR 0018 — Generic daily-derived store

Status: Accepted · Date: 2026-09-09 · Grill session `docs/plans/2026-09-09-product-blocks.md` (P4)

## Context

Block 1 introduces derived daily values with independent algorithm lifecycles (Sleep Need, Today Plan; later Behavior Insights, capacity trajectory). `derived.daily_features` already exists, but it is a *feature set*: one coherent, column-per-feature representation pinned by `feature_set_version`. Sleep Need and Today Plan are not features of that set — they version as their own algorithms (`somatriq_sleep_need_v1`, `somatriq_day_plan_v1`), on their own schedules.

## Decision

**`derived.daily_derived` is a generic store**: one row per `(local date, algorithm_version)` with a `jsonb` payload, effective timezone, and `computed_at`; **latest-state semantics** via same-version upsert (ADR 0012 pattern — refresh within a version, version bumps write new rows, versions run in parallel). History accrues from day one so longitudinal surfaces (Explore "planned vs actual") read stored values instead of recomputing history. The plan *as sent* is already preserved verbatim in `notifications.outbox` payloads — the store does not duplicate that job.

## Alternatives

- **Column-per-metric tables (daily_features pattern)** — rejected: one table per algorithm, each with typed columns to maintain; fine for a coherent feature set, sprawling for independent algorithm lifecycles.
- **Read-through only, no persistence** — rejected: Explore would recompute history on every load and no planned-vs-actual record would accrue.

## Consequences

- Two derived shapes coexist by design: `daily_features` (typed feature set) and `daily_derived` (generic per-algorithm). Not drift — different concepts.
- Payload schemas are owned by the frozen algorithm contracts in `somatriq_contracts`; the table never validates them.
- No `user_id`, consistent with `daily_features` (single-user deployment; multi-user arrives with its own migration).
