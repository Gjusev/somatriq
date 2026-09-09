"""journal_reminder_tick against the migrated DB (Block 2): opt-in via the
``journal_reminder_time`` User Preference, fires once per local day after
the configured local time, dedups like the morning brief, and stays silent
without a well-formed preference.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_db.testing import requires_db
from somatriq_scheduler.jobs import journal_reminder_tick
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC_TZ = ZoneInfo("UTC")


async def _bind_channel(db: AsyncSession) -> None:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO notifications.channels (user_id, kind, target) "
            "VALUES (:user_id, 'telegram', '111')"
        ),
        {"user_id": user_id},
    )
    await db.commit()


async def _set_reminder(db: AsyncSession, value: str) -> None:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO identity.user_preferences (user_id, key, value) "
            "VALUES (:user_id, 'journal_reminder_time', :value)"
        ),
        {"user_id": user_id, "value": json.dumps(value)},
    )
    await db.commit()


async def _reminder_rows(db: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
    result = await db.execute(
        text("SELECT kind, payload FROM notifications.outbox WHERE kind = 'journal_reminder'")
    )
    return [(kind, row) for kind, row in result.all()]


@requires_db
async def test_no_preference_means_no_reminder(db: AsyncSession) -> None:
    await _bind_channel(db)
    enqueued = await journal_reminder_tick(
        db, tz=UTC_TZ, now=datetime(2026, 9, 9, 23, 0, tzinfo=UTC)
    )
    assert enqueued == 0
    assert await _reminder_rows(db) == []


@requires_db
async def test_reminder_fires_once_per_local_day_after_preference_time(
    db: AsyncSession,
) -> None:
    await _bind_channel(db)
    await _set_reminder(db, "21:30")
    late = datetime(2026, 9, 9, 21, 35, tzinfo=UTC)

    first = await journal_reminder_tick(db, tz=UTC_TZ, now=late)
    assert first == 1
    rows = await _reminder_rows(db)
    assert len(rows) == 1
    payload = rows[0][1]
    assert payload["category"] == "routine"
    assert payload["date"] == "2026-09-09"

    second = await journal_reminder_tick(db, tz=UTC_TZ, now=late + timedelta(minutes=5))
    assert second == 0  # deduped for the local day
    assert len(await _reminder_rows(db)) == 1


@requires_db
async def test_reminder_silent_before_preference_time(db: AsyncSession) -> None:
    await _bind_channel(db)
    await _set_reminder(db, "21:30")
    early = await journal_reminder_tick(db, tz=UTC_TZ, now=datetime(2026, 9, 9, 20, 0, tzinfo=UTC))
    assert early == 0


@requires_db
async def test_malformed_preference_is_silence_not_error(db: AsyncSession) -> None:
    await _bind_channel(db)
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO identity.user_preferences (user_id, key, value) "
            "VALUES (:user_id, 'journal_reminder_time', :value)"
        ),
        {"user_id": user_id, "value": json.dumps("not-a-time")},
    )
    await db.commit()

    enqueued = await journal_reminder_tick(
        db, tz=UTC_TZ, now=datetime(2026, 9, 9, 23, 0, tzinfo=UTC)
    )
    assert enqueued == 0
