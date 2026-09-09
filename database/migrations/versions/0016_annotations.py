"""Annotations: user-authored longitudinal notes (Block 3; grill P13).

health.annotations is the OWNER's narrative anchored to a date or date
range — distinct from Journal Events (it labels time, it does not log an
exposure) and from system facts (device change, algorithm version), which
stay computed overlays and are never stored here.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-09
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE health.annotations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            date_from date NOT NULL,
            date_to date,
            title text NOT NULL,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT annotations_title_not_blank CHECK (length(btrim(title)) > 0),
            CONSTRAINT annotations_range_ordered CHECK (date_to IS NULL OR date_to >= date_from)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_annotations_user_range
            ON health.annotations (user_id, date_from)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS health.annotations")
