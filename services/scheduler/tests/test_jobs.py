"""morning_tick against the migrated DB with a fake clock (grill decision:
emit at the configured time WITH markers; backfill within the same morning;
dedup per local day).
"""

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from somatriq_db.testing import requires_db
from somatriq_scheduler.jobs import morning_tick
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC_TZ = ZoneInfo("UTC")
BRIEF_TIME = time(7, 0)


async def _bind_channel(
    db: AsyncSession, kind: str = "telegram", target: str = "111"
) -> None:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO notifications.channels (user_id, kind, target) "
            "VALUES (:user_id, :kind, :target)"
        ),
        {"user_id": user_id, "kind": kind, "target": target},
    )
    await db.commit()


async def _seed_hr(db: AsyncSession, newest: datetime) -> None:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    device_id = (
        await db.execute(text("SELECT id FROM identity.devices ORDER BY active_from LIMIT 1"))
    ).scalar_one()
    prefix = uuid.uuid4().hex
    await db.execute(
        text(
            "INSERT INTO timeseries.heart_rate "
            "(user_id, device_id, source_record_id, ts, bpm) "
            "VALUES (:user_id, :device_id, :srid, :ts, :bpm)"
        ),
        {
            "user_id": user_id,
            "device_id": device_id,
            "srid": f"sched-{prefix}",
            "ts": newest,
            "bpm": 60.0,
        },
    )
    await db.commit()


async def _outbox(db: AsyncSession) -> list[tuple[str, str, dict[str, Any]]]:
    result = await db.execute(
        text(
            "SELECT o.kind, c.kind, o.payload FROM notifications.outbox o "
            "JOIN notifications.channels c ON c.id = o.channel_id "
            "ORDER BY o.created_at, o.id"
        )
    )
    return [
        (str(r[0]), str(r[1]), cast("dict[str, Any]", r[2])) for r in result.all()
    ]


@requires_db
async def test_no_channels_no_rows(db: AsyncSession) -> None:
    enqueued = await morning_tick(
        db,
        tz=UTC_TZ,
        brief_time=BRIEF_TIME,
        stale_hours=6,
        now=datetime(2026, 8, 21, 7, 5, tzinfo=UTC),
    )
    assert enqueued == 0
    assert await _outbox(db) == []


@requires_db
async def test_before_window_nothing(db: AsyncSession) -> None:
    await _bind_channel(db)
    enqueued = await morning_tick(
        db,
        tz=UTC_TZ,
        brief_time=BRIEF_TIME,
        stale_hours=6,
        now=datetime(2026, 8, 21, 6, 59, tzinfo=UTC),
    )
    assert enqueued == 0


@requires_db
async def test_at_time_enqueues_brief_per_channel(db: AsyncSession) -> None:
    await _bind_channel(db, "telegram", "111")
    await _bind_channel(db, "ntfy", "somatriq-alerts")
    enqueued = await morning_tick(
        db,
        tz=UTC_TZ,
        brief_time=BRIEF_TIME,
        stale_hours=6,
        now=datetime(2026, 8, 21, 7, 0, tzinfo=UTC),
    )
    assert enqueued == 2
    rows = await _outbox(db)
    assert [r[0] for r in rows] == ["morning_brief", "morning_brief"]
    assert sorted(r[1] for r in rows) == ["ntfy", "telegram"]
    for _, _, payload in rows:
        assert payload["category"] == "routine"
        assert payload["date"] == "2026-08-21"
        text_body = payload["text"]
        assert text_body.startswith("Good morning")
        # Honest markers on an empty day (grill decision: emit WITH marker).
        assert "Recovery\nnot available yet" in text_body
        assert "Data quality\ninsufficient — 0 percent coverage" in text_body


@requires_db
async def test_second_tick_same_day_dedups(db: AsyncSession) -> None:
    await _bind_channel(db)
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=now) == 1
    later = datetime(2026, 8, 21, 7, 30, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=later) == 0
    assert len(await _outbox(db)) == 1


@requires_db
async def test_backfill_after_downtime_within_same_morning(db: AsyncSession) -> None:
    """Service down at 07:00, up at 09:40: the brief still goes out (with
    its coverage markers) rather than being skipped until tomorrow."""
    await _bind_channel(db)
    now = datetime(2026, 8, 21, 9, 40, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=now) == 1
    rows = await _outbox(db)
    assert rows[0][0] == "morning_brief"


@requires_db
async def test_after_noon_no_backfill(db: AsyncSession) -> None:
    await _bind_channel(db)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=now) == 0


@requires_db
async def test_stale_data_adds_sync_warning_with_marker_text(db: AsyncSession) -> None:
    await _bind_channel(db)
    now = datetime(2026, 8, 21, 7, 5, tzinfo=UTC)
    await _seed_hr(db, now - timedelta(hours=8))
    enqueued = await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=now)
    assert enqueued == 2  # brief + warning
    rows = await _outbox(db)
    warning = next(p for kind, _, p in rows if kind == "sync_warning")
    assert warning["category"] == "system"
    assert warning["text"].startswith("Sync warning: newest observation is 8 hours old")


@requires_db
async def test_fresh_data_no_sync_warning(db: AsyncSession) -> None:
    await _bind_channel(db)
    now = datetime(2026, 8, 21, 7, 5, tzinfo=UTC)
    await _seed_hr(db, now - timedelta(hours=1))
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=now) == 1
    assert [r[0] for r in await _outbox(db)] == ["morning_brief"]


@requires_db
async def test_sync_warning_dedups_per_day(db: AsyncSession) -> None:
    await _bind_channel(db)
    first = datetime(2026, 8, 21, 7, 5, tzinfo=UTC)
    await _seed_hr(db, first - timedelta(hours=8))
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=first) == 2
    second = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=second) == 0


@requires_db
async def test_next_day_enqueues_again(db: AsyncSession) -> None:
    await _bind_channel(db)
    day_one = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=day_one) == 1
    day_two = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)
    assert await morning_tick(db, tz=UTC_TZ, brief_time=BRIEF_TIME, stale_hours=6, now=day_two) == 1
    assert len(await _outbox(db)) == 2


@requires_db
async def test_ntfy_topic_env_registers_channel(db: AsyncSession) -> None:
    """NTFY_TOPIC configured -> an ntfy channel is ensured and included."""
    now = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)
    enqueued = await morning_tick(
        db,
        tz=UTC_TZ,
        brief_time=BRIEF_TIME,
        stale_hours=6,
        now=now,
        ntfy_topic="somatriq-alerts",
    )
    assert enqueued == 1
    rows = await _outbox(db)
    assert rows[0][1] == "ntfy"
    # idempotent: a second tick (same day, deduped) still leaves one channel
    assert (
        await morning_tick(
            db,
            tz=UTC_TZ,
            brief_time=BRIEF_TIME,
            stale_hours=6,
            now=now,
            ntfy_topic="somatriq-alerts",
        )
        == 0
    )


@requires_db
async def test_payload_is_valid_json_string(db: AsyncSession) -> None:
    """The enqueued payload round-trips as JSON with the text marker."""
    await _bind_channel(db)
    await morning_tick(
        db,
        tz=UTC_TZ,
        brief_time=BRIEF_TIME,
        stale_hours=6,
        now=datetime(2026, 8, 21, 7, 0, tzinfo=UTC),
    )
    raw = (
        await db.execute(text("SELECT payload::text FROM notifications.outbox"))
    ).scalar_one()
    parsed = json.loads(str(raw))
    assert parsed["date"] == date(2026, 8, 21).isoformat()
    assert "Good morning" in parsed["text"]
