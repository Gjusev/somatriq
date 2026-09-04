# ADR 0012 — Derived values are versioned

Status: Accepted · Date: 2026-09-04

## Context

Historical calculation semantics must never be silently overwritten (§63); provenance is mandatory (§62); parallel algorithm comparison must stay possible (§63, §77). The grill surfaced two taxonomy hazards: vendor-computed scores (WHOOP HRV/recovery/strain/sleep) blur the DERIVED category with unknowable algorithm internals, and the daily feature store (§72) as one flat unversioned row would silently mutate every downstream consumer (baselines, experiments) when an algorithm or canonical source changes.

## Decision

**Vendor-computed scores are Observations** (source-reported measurements, `CONTEXT.md`), carrying provenance entries in the `system.algorithms` registry with provider name/version and a `code_commit: null` convention for opaque vendor algorithms. **Somatriq-computed values are Derived Metrics** with full §62 provenance (algorithm name + version + parameters + code commit + quality + calculated_at), registered in `system.algorithms` (§64). Algorithm versions (`somatriq_recovery_v1`, `_v2`, …) coexist; new versions write new rows, never overwrite.

**Daily features are keyed `(date, feature_set_version)`** with per-metric source provenance columns. A change to any input algorithm version or to canonical-source resolution bumps `feature_set_version` and rebuilds forward through the reprocessing tooling (§140) — experiments and predictions pin a features version exactly as predictions already pin model/features versions (§87), so within-experiment comparability survives algorithm evolution.

## Alternatives

- **In-place recalculation of derived rows** — rejected: destroys history, makes A/B comparison of algorithms impossible, violates §63.
- **Unversioned daily feature row** — rejected: silent semantic drift for every consumer.

## Consequences

- Storage grows with versions — bounded by version count, not data volume.
- "Which recovery algorithm best predicts my outcomes?" (§219) is answerable from stored data.
- Reprocessing (§140) selects by algorithm version; M4 replay writes parallel observation versions under the same rule.
