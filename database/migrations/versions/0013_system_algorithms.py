"""Algorithm registry: system.algorithms (ADR 0012 provenance; Block 1 plan).

Pays the debt migration 0005 documented ("lands with its own later
migration"): one row per immutable algorithm name. ``constants`` carries a
POINTER to the frozen contract module — the frozen numbers live in code and
must never be duplicated here (duplicates drift). Explore (Block 3) reads
this table for algorithm-version markers on longitudinal views.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE system.algorithms (
            name text PRIMARY KEY,
            description text NOT NULL,
            constants jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO system.algorithms (name, description, constants) VALUES
        ('somatriq_baseline_v1',
         'Robust personal baseline: median + inclusive IQR over trailing windows, warmup states.',
         '{"contract": "somatriq_analytics.recovery"}'),
        ('somatriq_hrv_rmssd_v1',
         'HRV RMSSD over sleep-session RR with the simple-delta400 artifact filter.',
         '{"contract": "somatriq_contracts.recovery"}'),
        ('somatriq_rhr_v1',
         'Resting HR from 5-minute local-day bucket medians.',
         '{"contract": "somatriq_api.metrics"}'),
        ('somatriq_recovery_v1',
         'Explainable recovery: weighted tanh of robust z (hrv .4 / rhr .3 / sleep .3).',
         '{"contract": "somatriq_contracts.recovery"}'),
        ('somatriq_sleep_need_v1',
         'Prescriptive sleep recommendation: baseline + debt + load + recovery.',
         '{"contract": "somatriq_contracts.plan"}'),
        ('somatriq_day_plan_v1',
         'Today plan: tier bands + debt demotion, quartile strain, bedtime window.',
         '{"contract": "somatriq_contracts.plan"}')
        """
    )


def downgrade() -> None:
    # Seeded rows die with the table; no other table references it yet.
    op.execute("DROP TABLE IF EXISTS system.algorithms")
