"""M5 daily feature store: derived.daily_features (spec §72, ADR 0012/0017).

One row per (local date, feature_set_version). Keyed by version so algorithm
evolution writes new rows instead of silently mutating history (ADR 0012);
the per-day effective timezone is stored on the row (ADR 0017). NOT a
hypertable: it is one row per day per version, not a time series.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE derived.daily_features (
            date date NOT NULL,
            feature_set_version text NOT NULL,
            timezone text NOT NULL,
            resting_hr double precision,
            hr_min double precision,
            hr_mean double precision,
            hr_max double precision,
            sample_count integer NOT NULL DEFAULT 0,
            coverage_ratio double precision NOT NULL DEFAULT 0,
            data_quality text NOT NULL,
            algorithm_version text,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (date, feature_set_version)
        )
        """
    )

    # Algorithm-registry seed (system.algorithms, spec §64 / ADR 0012 §62
    # provenance): SKIPPED — no `algorithms` table exists in the system
    # schema as of revisions 0001-0004 (checked: only system.metrics is
    # created there). The registry lands with its own later migration, which
    # will own the somatriq_rhr_v1 seed; creating the table here would
    # preempt that schema.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS derived.daily_features")
