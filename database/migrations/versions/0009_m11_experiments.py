"""M11 experiments: research.experiments + research.experiment_days
(spec §83-86, §204).

experiments is the N-of-1 container: a hypothesis, ONE intervention (what
changes), one outcome metric (validated against the correlation catalog at
the API layer), a direction, and the two phase lengths. It starts RUNNING
by observing the status quo — the baseline window is the LAST
baseline_days local days, backfilled at creation — and the intervention
days materialize one per day as they pass (the API's roll_forward helper).

experiment_days is the compliance ledger (spec §85: never assume adherence):
one row per experiment per calendar day, phase-tagged, complied defaulting
true (an unchecked day counts as kept — the check-in is how you say "no"),
note carrying the verbatim user remark. Not hypertables: one row per
day-experiment, never a time-partitioned series.

The UNIQUE (experiment_id, day) constraint is the (experiment_id, day)
index — its backing unique index serves the day-window reads; a duplicate
plain index would be pure redundancy.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE research.experiments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            name text NOT NULL,
            hypothesis text NOT NULL,
            intervention text NOT NULL,
            metric text NOT NULL,
            direction text NOT NULL,
            baseline_days integer NOT NULL DEFAULT 14,
            intervention_days integer NOT NULL DEFAULT 14,
            status text NOT NULL DEFAULT 'running',
            started_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT experiments_direction_check
                CHECK (direction IN ('increase', 'decrease', 'any')),
            CONSTRAINT experiments_status_check
                CHECK (status IN ('running', 'completed', 'abandoned')),
            CONSTRAINT experiments_baseline_days_positive
                CHECK (baseline_days > 0),
            CONSTRAINT experiments_intervention_days_positive
                CHECK (intervention_days > 0)
        )
        """
    )
    # Read path: the user's experiments by status (list view, one running set).
    op.execute(
        """
        CREATE INDEX ix_experiments_user_status
            ON research.experiments (user_id, status)
        """
    )
    op.execute(
        """
        CREATE TABLE research.experiment_days (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            experiment_id uuid NOT NULL REFERENCES research.experiments(id),
            day date NOT NULL,
            phase text NOT NULL,
            complied boolean NOT NULL DEFAULT true,
            note text,
            CONSTRAINT experiment_days_phase_check
                CHECK (phase IN ('baseline', 'intervention')),
            UNIQUE (experiment_id, day)
        )
        """
    )


def downgrade() -> None:
    # FK-safe order: experiment_days references experiments.
    op.execute("DROP TABLE IF EXISTS research.experiment_days")
    op.execute("DROP TABLE IF EXISTS research.experiments")
