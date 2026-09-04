"""M2: local account, pairing sessions, device tokens, raw batch registry.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04

ADR 0015 (credentials stored hashed — only sha256 digests of pairing codes
and device tokens ever touch disk) and ADR 0003 (raw blob registry: the
path/hash/size live in raw.raw_batches, the compressed bytes live on a
volume, never in PostgreSQL). ingest.batches additionally persists the raw
coverage counters so a replayed batch acks the raw truth that was stored the
first time (ADR 0006: the original acknowledgement is replayed verbatim).
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── identity: local account credentials (§122, ADR 0015) ──────────
    op.execute(
        """
        CREATE TABLE identity.account_credentials (
            user_id uuid PRIMARY KEY REFERENCES identity.users(id),
            username text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_login_at timestamptz
        )
        """
    )
    # code_hint keeps the polling UI legible: only the full code's sha256 is
    # authoritative, but the first 4 chars are safe to re-display.
    op.execute(
        """
        CREATE TABLE identity.pairing_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            code_hash text NOT NULL UNIQUE,
            code_hint text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            device_id uuid REFERENCES identity.devices(id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE identity.device_tokens (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            token_hash text NOT NULL UNIQUE,
            scopes text[] NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            expires_at timestamptz,
            revoked_at timestamptz
        )
        """
    )

    # ── raw: blob registry (§48-49, ADR 0003) ─────────────────────────
    op.execute(
        """
        CREATE TABLE raw.raw_batches (
            batch_id uuid PRIMARY KEY REFERENCES ingest.batches(batch_id),
            device_id uuid NOT NULL REFERENCES identity.devices(id),
            codec text NOT NULL,
            journal_version int NOT NULL,
            frame_count int NOT NULL,
            payload_sha256 varchar(64) NOT NULL,
            byte_size bigint NOT NULL,
            blob_path text NOT NULL,
            storage_state text NOT NULL DEFAULT 'confirmed',
            received_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ── ingest: persist raw coverage so replays ack the stored truth ──
    op.execute(
        """
        ALTER TABLE ingest.batches
            ADD COLUMN raw_frame_count integer NOT NULL DEFAULT 0,
            ADD COLUMN raw_bytes_stored bigint NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ingest.batches DROP COLUMN IF EXISTS raw_bytes_stored")
    op.execute("ALTER TABLE ingest.batches DROP COLUMN IF EXISTS raw_frame_count")
    op.execute("DROP TABLE IF EXISTS raw.raw_batches")
    op.execute("DROP TABLE IF EXISTS identity.device_tokens")
    op.execute("DROP TABLE IF EXISTS identity.pairing_sessions")
    op.execute("DROP TABLE IF EXISTS identity.account_credentials")
