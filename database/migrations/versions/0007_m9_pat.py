"""M9 MCP personal access tokens: identity.personal_access_tokens (§123, ADR 0010).

Scoped PATs are the header-capable credential of the health MCP server
(spec §97 default connection scope ``health.read``; ADR 0010 keeps PATs as
the fallback credential type on the same enforcement path as OAuth 2.1
tokens, which land in a later increment). Only the sha256 hash is stored;
the raw ``sqt_pat_…`` value exists solely at mint time (§123).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identity.personal_access_tokens (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            name text NOT NULL,
            token_hash text NOT NULL UNIQUE,
            scopes text[] NOT NULL DEFAULT ARRAY['health.read'],
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            expires_at timestamptz,
            revoked_at timestamptz
        )
        """
    )
    # Read path: token hash lookup (authentication) and per-user listing.
    op.execute(
        """
        CREATE INDEX ix_personal_access_tokens_user
            ON identity.personal_access_tokens (user_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS identity.personal_access_tokens")
