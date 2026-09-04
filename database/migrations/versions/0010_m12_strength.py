"""M12 strength training: health.training_sessions + health.training_sets
(spec §78-80, §104, §205).

training_sessions is one logged workout: a timestamp, the source surface and
the verbatim raw_text as provenance (spec §104 — the deterministic
representation is editable, the source statement is kept). training_sets is
the deterministic parsed representation: one row per set, muscle_group the
PRIMARY group from the strength catalog (an unknown exercise is 'other',
never a guess), weight_kg NULL meaning BODYWEIGHT (no external load —
tonnage treats NULL as 0 and bodyweight sets are counted separately, never
invented as a load), and effort (rir/rpe) optional per set.

UNIQUE (session_id, exercise, set_index) makes a session's sets idempotent
per exercise and orders them; its backing unique index serves the
fetch-every-set-of-a-session read path via its (session_id) prefix. The
additional plain index on (session_id) is the slice contract's explicit
access-path marker for the FK-driven delete path (dropping a session looks
up its set rows by session_id alone).

Not hypertables: a session is an event row, its sets are children of it;
neither is a time-partitioned series (the daily muscular-load series for
§80 are aggregated reads over ts, not stored series).

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-05
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE health.training_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            ts timestamptz NOT NULL DEFAULT now(),
            source text NOT NULL DEFAULT 'telegram',
            raw_text text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT training_sessions_source_check
                CHECK (source IN ('telegram', 'web', 'api'))
        )
        """
    )
    # Read path: the user's sessions over a window, newest first.
    op.execute(
        """
        CREATE INDEX ix_training_sessions_user_ts
            ON health.training_sessions (user_id, ts DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE health.training_sets (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id uuid NOT NULL REFERENCES health.training_sessions(id),
            exercise text NOT NULL,
            muscle_group text NOT NULL,
            weight_kg double precision,
            reps integer NOT NULL,
            rir integer,
            rpe double precision,
            set_index integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT training_sets_reps_positive CHECK (reps > 0),
            CONSTRAINT training_sets_rir_nonnegative CHECK (rir IS NULL OR rir >= 0),
            CONSTRAINT training_sets_set_index_nonnegative
                CHECK (set_index >= 0),
            CONSTRAINT training_sets_weight_positive
                CHECK (weight_kg IS NULL OR weight_kg > 0),
            UNIQUE (session_id, exercise, set_index)
        )
        """
    )
    # Fetch path: every set of one session (FK delete cascade target lookups).
    op.execute(
        """
        CREATE INDEX ix_training_sets_session
            ON health.training_sets (session_id)
        """
    )


def downgrade() -> None:
    # FK-safe order: training_sets references training_sessions.
    op.execute("DROP TABLE IF EXISTS health.training_sets")
    op.execute("DROP TABLE IF EXISTS health.training_sessions")
