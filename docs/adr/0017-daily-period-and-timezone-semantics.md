# ADR 0017 — Daily period and timezone semantics

Status: Accepted · Date: 2026-09-04 · Product-owner decision 4

## Context

Daily features, baselines, and "HRV next day" analyses key on a calendar day, but the spec never defined which date a night of sleep belongs to, nor what a timezone change does to historical daily rows (§65 only forbids breaking midnight-spanning sleep). Every chart, baseline, and experiment inherits this choice.

## Decision

**Night attribution: wake date.** A night of sleep belongs to the local day on which the user wakes; "last night's sleep" always appears on today. Daily features, baselines, and lagged analyses (§80) use wake-date attribution consistently.

**Per-day effective timezone.** Each daily row stores the timezone that was in effect for that user-day. Timestamps remain UTC (tz-aware) at rest; local-day boundaries are derived using the stored effective timezone (§65).

**Timezone change → bounded recomputation.** When the user's timezone changes, only affected daily rows recomputed via the reprocessing tooling (§140) — history is rewritten with correct boundaries, not frozen and not blindly regenerated. DST transitions are exercised by the golden fixture "sleep over DST" (§153) whose expected values this ADR defines. Chart timezone ownership: charts render in the **day's effective timezone** by default (matching server day boundaries), with a user toggle for the browser timezone (§174).

## Alternatives

- **Start-date attribution + frozen history** — rejected: splits "last night's sleep" across two dates; history mixes day boundaries.
- **Fixed reference timezone with query-time conversion** — rejected: most stable for analytics but "today" is wrong while traveling and every surface pays conversion complexity.

## Consequences

- ADR 0012's `(date, feature_set_version)` keying composes with per-day effective timezones.
- Timezone changes carry bounded recompute cost, absorbed by existing reprocessing machinery.
- The morning brief and Today screen share one notion of "today"; freshness semantics (coverage marker per `CONTEXT.md`) are independent of day attribution.
