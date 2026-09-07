"""Grant data.read to existing device tokens (dashboard-client read path).

Newly minted tokens carry data.read via DEVICE_SCOPES (contracts, ADR 0015
scope vocabulary); this backfills every EXISTING identity.device_tokens row
so the already-paired phones — the ones about to become dashboard clients —
can read the owner's data without re-pairing. Additive and idempotent: the
WHERE NOT ('data.read' = ANY(scopes)) guard keeps re-runs and partially
migrated rows untouched, and revoked/expired tokens are granted too (scope
checks happen per-request at the guard, not here).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-07
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_DATA_READ = "data.read"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE identity.device_tokens
        SET scopes = array_append(scopes, '{_DATA_READ}')
        WHERE NOT ('{_DATA_READ}' = ANY(scopes))
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE identity.device_tokens
        SET scopes = array_remove(scopes, '{_DATA_READ}')
        """
    )
