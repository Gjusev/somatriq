"""Block 2 journal vocabulary + mobile writes (grill P7/P10/P11; plan §2.2).

- journal kinds += the five new behaviors (alcohol, medication, stress,
  meal, travel) — the six binary behaviors with caffeine (P7);
- source += 'mobile': the collector quick-logs with its own provenance
  identity and never holds the account JWT (P10);
- outbox kind += 'journal_reminder' (the opt-in evening reminder);
- client_event_id uuid NULL + UNIQUE (user_id, client_event_id): offline
  retry idempotency for mobile journal writes (ADR 0006 spirit — a retried
  event is the SAME event);
- backfill 'journal.write' onto every existing device token (pattern of
  migration 0011; new pairings carry it via DEVICE_SCOPES).

Downgrade is data-deleting by necessity: rows using the new vocabulary
cannot exist under the old CHECK constraints, so they are removed before
the constraints are restored (documented, deliberate).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-09
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_OLD_KINDS = "('journal', 'caffeine', 'training', 'experiment_checkin', 'note')"
_NEW_KINDS = (
    "('journal', 'caffeine', 'alcohol', 'medication', 'stress', 'meal', "
    "'travel', 'training', 'experiment_checkin', 'note')"
)
_OLD_SOURCES = "('telegram', 'web', 'api')"
_NEW_SOURCES = "('telegram', 'web', 'api', 'mobile')"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE health.journal_events
            DROP CONSTRAINT journal_events_kind_check,
            ADD CONSTRAINT journal_events_kind_check
                CHECK (kind IN ('journal', 'caffeine', 'alcohol', 'medication',
                                'stress', 'meal', 'travel', 'training',
                                'experiment_checkin', 'note'))
        """
    )
    op.execute(
        """
        ALTER TABLE health.journal_events
            DROP CONSTRAINT journal_events_source_check,
            ADD CONSTRAINT journal_events_source_check
                CHECK (source IN ('telegram', 'web', 'api', 'mobile'))
        """
    )
    op.execute(
        """
        ALTER TABLE health.journal_events
            ADD COLUMN client_event_id uuid
        """
    )
    # UNIQUE (not a primary key): NULL for server-side writers, one mobile
    # retry identity per user (Postgres UNIQUE ignores NULLs).
    op.execute(
        """
        ALTER TABLE health.journal_events
            ADD CONSTRAINT journal_events_client_event_unique
                UNIQUE (user_id, client_event_id)
        """
    )
    op.execute(
        """
        ALTER TABLE notifications.outbox
            DROP CONSTRAINT outbox_kind_check,
            ADD CONSTRAINT outbox_kind_check
                CHECK (kind IN ('morning_brief', 'sync_warning', 'anomaly',
                                'journal_reminder', 'test'))
        """
    )
    op.execute(
        """
        UPDATE identity.device_tokens
        SET scopes = array_append(scopes, 'journal.write')
        WHERE NOT ('journal.write' = ANY(scopes))
        """
    )


def downgrade() -> None:
    # Data-deleting by necessity: the old constraints cannot hold the new
    # vocabulary. Mobile-source events and behavior-kind events die here;
    # idempotency column and reminder queue entries go with them.
    op.execute(
        """
        DELETE FROM health.journal_events
        WHERE kind IN ('alcohol', 'medication', 'stress', 'meal', 'travel')
           OR source = 'mobile'
        """
    )
    op.execute(
        """
        DELETE FROM notifications.outbox WHERE kind = 'journal_reminder'
        """
    )
    op.execute(
        "ALTER TABLE health.journal_events "
        "DROP CONSTRAINT IF EXISTS journal_events_client_event_unique"
    )
    op.execute("ALTER TABLE health.journal_events DROP COLUMN IF EXISTS client_event_id")
    op.execute(
        f"""
        ALTER TABLE health.journal_events
            DROP CONSTRAINT journal_events_source_check,
            ADD CONSTRAINT journal_events_source_check
                CHECK (source IN {_OLD_SOURCES})
        """
    )
    op.execute(
        f"""
        ALTER TABLE health.journal_events
            DROP CONSTRAINT journal_events_kind_check,
            ADD CONSTRAINT journal_events_kind_check
                CHECK (kind IN {_OLD_KINDS})
        """
    )
    op.execute(
        """
        ALTER TABLE notifications.outbox
            DROP CONSTRAINT outbox_kind_check,
            ADD CONSTRAINT outbox_kind_check
                CHECK (kind IN ('morning_brief', 'sync_warning', 'anomaly', 'test'))
        """
    )
    op.execute(
        """
        UPDATE identity.device_tokens
        SET scopes = array_remove(scopes, 'journal.write')
        """
    )
