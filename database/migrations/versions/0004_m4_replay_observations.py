"""M4 replay observations: parallel decoder versions (ADR 0012, spec §197).

Replay reprocessing never rewrites the live heart_rate hypertable — decoded
re-interpretations land here, keyed by decoder_version, so algorithm
versions coexist and "which decoder is right" stays answerable (§63).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE timeseries.replayed_observations (
            decoder_version text NOT NULL,
            user_id uuid NOT NULL,
            device_id uuid NOT NULL,
            source_record_id text NOT NULL,
            ts timestamptz NOT NULL,
            bpm double precision NOT NULL,
            raw_batch_id uuid NOT NULL,
            source_frame_epoch_ms bigint NOT NULL,
            replayed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (decoder_version, user_id, device_id, source_record_id, ts)
        )
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'timeseries.replayed_observations', 'ts',
            chunk_time_interval => interval '1 day',
            if_not_exists => TRUE, migrate_data => TRUE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_replayed_observations_batch
            ON timeseries.replayed_observations (raw_batch_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS timeseries.replayed_observations")
