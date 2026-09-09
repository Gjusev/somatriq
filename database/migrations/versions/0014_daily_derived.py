"""Generic daily-derived store: derived.daily_derived (ADR 0018; grill P4).

One row per (local date, algorithm_version) with a jsonb payload — NOT a
hypertable and NOT part of daily_features' feature set (different concept:
independent algorithm lifecycles vs one coherent feature_set_version).
Latest-state semantics: same-version upsert refresh (ADR 0012 pattern);
a version bump writes new rows and both run in parallel. No user_id,
consistent with derived.daily_features (single-user deployment).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-09
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE derived.daily_derived (
            date date NOT NULL,
            algorithm_version text NOT NULL,
            timezone text NOT NULL,
            payload jsonb NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (date, algorithm_version)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS derived.daily_derived")
