"""M7 journal + chat channels: health.journal_events, notifications tables
(spec §101-105).

journal_events is the deterministic store behind Telegram/quick logging
(spec §103): source and kind are CHECK-constrained vocabularies, structured
carries the validated payload (caffeine: quantity only when literally
stated, else estimated=false — quantity is NEVER invented), and the verbatim
text is kept for provenance. notifications.channels binds the owner's chat
(UNIQUE per user+kind+target — the /start owner binding is exclusive by
construction) and notifications.outbox is the send queue the scheduler
fills and the notifications service drains (spec §105).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-04
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE health.journal_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            source text NOT NULL,
            kind text NOT NULL,
            ts timestamptz NOT NULL DEFAULT now(),
            text text,
            structured jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT journal_events_source_check
                CHECK (source IN ('telegram', 'web', 'api')),
            CONSTRAINT journal_events_kind_check
                CHECK (kind IN ('journal', 'caffeine', 'training',
                                'experiment_checkin', 'note'))
        )
        """
    )
    # Read path: the day's events per user, newest first (brief + /status).
    op.execute(
        """
        CREATE INDEX ix_journal_events_user_ts
            ON health.journal_events (user_id, ts DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE notifications.channels (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES identity.users(id),
            kind text NOT NULL,
            target text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT channels_kind_check CHECK (kind IN ('telegram', 'ntfy')),
            CONSTRAINT channels_target_not_blank CHECK (length(btrim(target)) > 0),
            UNIQUE (user_id, kind, target)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notifications.outbox (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            channel_id uuid NOT NULL REFERENCES notifications.channels(id),
            kind text NOT NULL,
            payload jsonb NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            attempts integer NOT NULL DEFAULT 0,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            sent_at timestamptz,
            CONSTRAINT outbox_kind_check
                CHECK (kind IN ('morning_brief', 'sync_warning', 'anomaly', 'test')),
            CONSTRAINT outbox_status_check CHECK (status IN ('pending', 'sent', 'failed')),
            CONSTRAINT outbox_attempts_nonnegative CHECK (attempts >= 0)
        )
        """
    )
    # Drain path: pending rows oldest-first (spec §105 outbox).
    op.execute(
        """
        CREATE INDEX ix_outbox_status_created
            ON notifications.outbox (status, created_at)
        """
    )


def downgrade() -> None:
    # FK-safe order: outbox references channels; journal_events is independent.
    op.execute("DROP TABLE IF EXISTS notifications.outbox")
    op.execute("DROP TABLE IF EXISTS notifications.channels")
    op.execute("DROP TABLE IF EXISTS health.journal_events")
