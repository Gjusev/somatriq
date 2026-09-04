"""Initial PostgreSQL schemas (spec §54).

Revision ID: 0001
Revises:
Create Date: 2026-09-04
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = [
    "identity",
    "ingest",
    "raw",
    "timeseries",
    "health",
    "derived",
    "research",
    "ai",
    "notifications",
    "audit",
    "system",
]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    # Schemas are empty at this revision; later revisions own their objects.
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
