"""M6 observation families: vendor daily scores, sleep sessions, RR intervals
(spec §41-42 family; ADR 0012 vendor scores are Observations; ADR 0006
idempotency rides the M2 ingest.batches machinery).

TimescaleDB note (same deviation as migration 0002, recorded against ADR
0006): every unique index on a hypertable must include the partitioning
column, so rr_interval per-record uniqueness is (user_id, device_id,
source_record_id, ts) — retry replays carry identical records, and the batch
UUID remains the sole replay idempotency key.

health.daily_observations and health.sleep_sessions are plain tables (one row
per wake-date metric / per vendor session, not time series), so they carry
identity FKs; health.sleep_stages keeps FK integrity to its session via the
composite primary key.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-04
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── health: vendor daily observations (NOOP dailyMetric, §41) ──────
    op.execute(
        """
        CREATE TABLE health.daily_observations (
            user_id uuid NOT NULL REFERENCES identity.users(id),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            day date NOT NULL,
            metric text NOT NULL,
            value double precision NOT NULL,
            decoder_version text,
            raw_batch_id uuid,
            received_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, device_id, day, metric)
        )
        """
    )
    # Read path: one local day's card is one (user_id, day) lookup.
    op.execute(
        """
        CREATE INDEX ix_daily_observations_user_day
            ON health.daily_observations (user_id, day)
        """
    )

    # ── health: sleep sessions + stages (NOOP sleepSession, §42) ───────
    op.execute(
        """
        CREATE TABLE health.sleep_sessions (
            user_id uuid NOT NULL REFERENCES identity.users(id),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            source_record_id text NOT NULL,
            start_ts timestamptz NOT NULL,
            end_ts timestamptz NOT NULL,
            efficiency double precision,
            resting_hr double precision,
            avg_hrv double precision,
            user_edited boolean NOT NULL DEFAULT false,
            decoder_version text,
            raw_batch_id uuid,
            received_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, device_id, source_record_id, start_ts)
        )
        """
    )
    # id bigserial PK (simplest robust shape): a duplicate session is skipped
    # whole — stages replay with their session, so stage rows never collide.
    op.execute(
        """
        CREATE TABLE health.sleep_stages (
            id bigserial PRIMARY KEY,
            session_user_id uuid NOT NULL,
            session_device_id uuid NOT NULL,
            session_source_record_id text NOT NULL,
            session_start_ts timestamptz NOT NULL,
            state text NOT NULL,
            stage_start_ts timestamptz NOT NULL,
            stage_end_ts timestamptz NOT NULL,
            FOREIGN KEY (session_user_id, session_device_id,
                         session_source_record_id, session_start_ts)
                REFERENCES health.sleep_sessions (user_id, device_id, source_record_id, start_ts)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_sleep_stages_session
            ON health.sleep_stages (
                session_user_id, session_device_id, session_source_record_id,
                session_start_ts, stage_start_ts
            )
        """
    )

    # ── timeseries: RR intervals (NOOP rrInterval — our own HRV source) ─
    op.execute(
        """
        CREATE TABLE timeseries.rr_interval (
            user_id uuid NOT NULL,
            device_id uuid NOT NULL,
            source_record_id text NOT NULL,
            ts timestamptz NOT NULL,
            rr_ms integer NOT NULL,
            seq bigint NOT NULL,
            decoder_version text,
            raw_batch_id uuid,
            received_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, device_id, source_record_id, ts)
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'timeseries.rr_interval', 'ts',
            chunk_time_interval => interval '1 day',
            if_not_exists => TRUE, migrate_data => TRUE
        )
        """
    )

    # ── catalog: the rr_interval series metric (§69/§153; the vendor daily
    # metrics are catalog-governed by the frozen VENDOR_DAILY_METRICS
    # contract, not by system.metrics rows) ──────────────────────────────
    op.execute(
        """
        INSERT INTO system.metrics
            (name, unit, valid_min, valid_max, expected_cadence_seconds, valid_aggregations)
        VALUES ('rr_interval', 'ms', 200.0, 2500.0, NULL, ARRAY['none','5m','1h'])
        """
    )


def downgrade() -> None:
    # Dependency order: hypertable, then stages before their sessions, then
    # the daily observations table, then the catalog row.
    op.execute("DROP TABLE IF EXISTS timeseries.rr_interval")
    op.execute("DROP TABLE IF EXISTS health.sleep_stages")
    op.execute("DROP TABLE IF EXISTS health.sleep_sessions")
    op.execute("DROP TABLE IF EXISTS health.daily_observations")
    op.execute("DELETE FROM system.metrics WHERE name = 'rr_interval'")
