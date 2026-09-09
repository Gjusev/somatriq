"""Block 1 user preferences store (ADR 0019; grill P5/P5b 2026-09-09).

identity.user_preferences is THE typed key-value store for declared
personal settings (CONTEXT.md "User Preference" — declares intention,
never derived from behavior): ``wake_time`` now, ``journal_reminder_time``
in Block 2. The database stores shapeless jsonb; per-key typed validation
lives at the API edge (registered pydantic schemas), so the schema never
churns per setting.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity.user_preferences (
            user_id uuid NOT NULL REFERENCES identity.users(id),
            key text NOT NULL,
            value jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT user_preferences_key_not_blank
                CHECK (length(btrim(key)) > 0),
            PRIMARY KEY (user_id, key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity.user_preferences")
