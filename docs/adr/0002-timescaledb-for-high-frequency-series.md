# ADR 0002 — TimescaleDB for high-frequency series

Status: Accepted · Date: 2026-09-04

## Context

Heart rate, RR intervals, SpO2, temperature, respiration, battery are high-frequency series (spec §57). The web must never fetch millions of raw samples to render a year-long chart (spec §71, §173). Alembic autogenerate cannot emit TimescaleDB DDL — `create_hypertable` and continuous aggregates are invisible to it (grill blocker B3).

## Decision

High-frequency observations live in TimescaleDB hypertables under the `timeseries` schema, each keyed by the natural key `(user_id, device_id, source_record_id)` (ADR 0006) with time as the hypertable partition column. Reading paths use continuous aggregates (1 min → 5 min → 1 h → 1 day) appropriate to the requested range. **All TimescaleDB DDL is hand-written SQL** invoked from Alembic migrations, with the extension version pinned in compose; CI tests migrations up and down (spec §134). Extremely high-frequency IMU data is not retained as relational rows: originals stay in the raw archive, and useful features are materialized (spec §57).

## Alternatives

- **Plain Postgres partitioning + materialized views** — rejected: manual chunk management, no built-in compression or continuous aggregates with real-time refresh.
- **InfluxDB for series** — rejected: second system of record violates ADR 0001.
- **Pre-aggregation in application code** — rejected: consistency and refresh become ad hoc.

## Consequences

- Schema changes on populated hypertables (unique indexes, key changes) can require rebuilds — the natural key must be right from M1 (ADR 0006).
- The pinned extension version upgrades only through a tested migration.
- Compression policies become available for long histories; retention follows spec §50.
