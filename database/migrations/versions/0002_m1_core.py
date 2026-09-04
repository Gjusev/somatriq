"""M1 core tables: identity seeds, ingest bookkeeping, heart_rate hypertable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04

TimescaleDB note (deviation recorded against ADR 0006): every unique index on
a hypertable must include the partitioning column, so per-record uniqueness is
(user_id, device_id, source_record_id, ts) rather than the three-column
natural key alone. Retry replays carry identical records, so §221
"retries must not duplicate" holds; the batch UUID remains the sole replay
idempotency key. Schema-level enforcement of the strict three-column key
would require a non-hypertable side table — revisit only with evidence.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SEED_USER_ID = "00000000-0000-4000-8000-000000000001"
SEED_DEVICE_ID = "00000000-0000-4000-8000-0000000000a1"
SEED_SOURCE_ID = "00000000-0000-4000-8000-0000000000b1"


def upgrade() -> None:
    # ── identity (§55) ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE identity.users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            display_name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE identity.devices (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            name text NOT NULL,
            model text,
            active_from timestamptz NOT NULL DEFAULT now(),
            active_to timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE identity.data_sources (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            provider text NOT NULL,
            collector text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (device_id, provider)
        )
        """
    )

    # ── ingest (§56, ADR 0006) ────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE ingest.batches (
            batch_id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES identity.users(id),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            schema_version text NOT NULL,
            content_hash varchar(64) NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            records_received integer NOT NULL,
            records_inserted integer NOT NULL,
            records_duplicate integer NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ingest.idempotency_keys (
            batch_id uuid PRIMARY KEY REFERENCES ingest.batches(batch_id),
            content_hash varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ingest.failures (
            id bigserial PRIMARY KEY,
            batch_id uuid NOT NULL,
            error_code text NOT NULL,
            detail jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── timeseries (§57, ADR 0002) ────────────────────────────────────
    op.execute(
        """
        CREATE TABLE timeseries.heart_rate (
            user_id uuid NOT NULL,
            device_id uuid NOT NULL,
            source_record_id text NOT NULL,
            ts timestamptz NOT NULL,
            bpm double precision NOT NULL,
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
            'timeseries.heart_rate', 'ts',
            chunk_time_interval => interval '1 day',
            if_not_exists => TRUE, migrate_data => TRUE
        )
        """
    )

    # ── system (metric catalog, §69 gap-critic fold-in) ───────────────
    op.execute(
        """
        CREATE TABLE system.metrics (
            name text PRIMARY KEY,
            unit text NOT NULL,
            valid_min double precision,
            valid_max double precision,
            expected_cadence_seconds integer,
            valid_aggregations text[] NOT NULL DEFAULT '{}'
        )
        """
    )

    # ── M1 seeds: single local user, synthetic device, catalog entry ──
    op.execute(
        f"""
        INSERT INTO identity.users (id, display_name)
        VALUES ('{SEED_USER_ID}', 'local-user')
        """
    )
    op.execute(
        f"""
        INSERT INTO identity.devices (id, user_id, name, model)
        VALUES ('{SEED_DEVICE_ID}', '{SEED_USER_ID}', 'synthetic-01', 'synthetic')
        """
    )
    op.execute(
        f"""
        INSERT INTO identity.data_sources (id, device_id, provider, collector)
        VALUES ('{SEED_SOURCE_ID}', '{SEED_DEVICE_ID}', 'synthetic', 'somatriq-mobile')
        """
    )
    op.execute(
        """
        INSERT INTO system.metrics
            (name, unit, valid_min, valid_max, expected_cadence_seconds, valid_aggregations)
        VALUES ('heart_rate', 'bpm', 20.0, 250.0, 1, ARRAY['none','1m','5m','1h'])
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS system.metrics")
    op.execute("DROP TABLE IF EXISTS timeseries.heart_rate")
    op.execute("DROP TABLE IF EXISTS ingest.failures")
    op.execute("DROP TABLE IF EXISTS ingest.idempotency_keys")
    op.execute("DROP TABLE IF EXISTS ingest.batches")
    op.execute("DROP TABLE IF EXISTS identity.data_sources")
    op.execute("DROP TABLE IF EXISTS identity.devices")
    op.execute("DROP TABLE IF EXISTS identity.users")
