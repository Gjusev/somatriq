# ADR 0013 — Multiple data sources are preserved; canonical resolution is deterministic and time-scoped

Status: Accepted · Date: 2026-09-04

## Context

Multiple sources will provide the same concept (WHOOP / Huawei / Health Connect heart rate; §66). Originals are never discarded (§221). The grill found the source model statically configured: priority had no temporal semantics, devices had no lifecycle, and "wearables can change" (§1) had no mechanism — a WHOOP 4→5 migration would silently blend two instruments into every baseline, anomaly, and experiment.

## Decision

Every source's observations are stored permanently, each under its natural key (ADR 0006); discarding originals is forbidden. The **SourceResolver** is deterministic — inputs: metric, time-scoped priority, coverage, quality, overlap, user preference (§68) — and its output is recorded per metric per period as the Canonical Source, with resolution changes acting as invalidation triggers (bumping `feature_set_version`, ADR 0012). **Devices carry `active_from`/`active_to`** and provenance includes device + firmware + collector build. An instrument boundary (device replacement or model change) is a first-class event: baselines re-baseline across it, the personal-response model segments on it, and the quality engine watches for distribution shift across it. Source priority configurations are time-scoped so pre- and post-change data are never silently blended.

## Alternatives

- **Static global priority (as spec'd)** — rejected: no instrument-change semantics; silently corrupts personal science claims.
- **Last-writer-wins canonical overwrite** — rejected: discards originals; violates §221.

## Consequences

- The identity schema (§55) models device lifecycles from M1.
- The Data Quality page (§119) surfaces source conflicts and instrument boundaries.
- Canonical-source changes are auditable events with bounded downstream recomputation (§138-140).
