"""Morning-brief + sync-warning enqueueing (spec §101, §102, §105, §135).

The scheduler CREATES work (notifications.outbox rows) — it never delivers
(that is somatriq_notifications) and performs no analysis beyond assembling
the brief text it enqueues, once per morning, over the shared TODAY reader.

Rules (grill decision on partial data):

* one morning_brief row per enabled channel per local day (any status
  counts as sent for dedup — a failed row must not trigger re-spam);
* the emit window [MORNING_BRIEF_TIME, 12:00) doubles as the backfill
  window: a service that was down at the configured time emits on startup
  within the same morning, marker included, rather than skipping;
* at brief time, a newest observation older than SYNC_STALE_HOURS is
  surfaced as ONE sync_warning row per channel per day with the marker text
  in the payload (spec §101). No observations at all is NOT a sync warning
  — the brief itself says "no data yet", which is that case's marker.
"""

import json
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_analytics.brief import build_morning_brief
from somatriq_analytics.today_data import assemble_today
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .schedule import in_morning_window


async def enabled_channels(session: AsyncSession) -> list[dict[str, Any]]:
    """All enabled notification channels (single-owner system, any user)."""
    result = await session.execute(
        text(
            "SELECT id, kind, target FROM notifications.channels WHERE enabled ORDER BY created_at"
        )
    )
    return [{"id": row[0], "kind": row[1], "target": row[2]} for row in result.all()]


async def ensure_ntfy_channel(session: AsyncSession, topic: str) -> None:
    """Idempotently register the env-configured ntfy topic for the owner."""
    if not topic.strip():
        return
    user_id = (
        await session.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one_or_none()
    if user_id is None:
        return
    await session.execute(
        text(
            "INSERT INTO notifications.channels (user_id, kind, target) "
            "VALUES (:user_id, 'ntfy', :topic) "
            "ON CONFLICT (user_id, kind, target) DO NOTHING"
        ),
        {"user_id": user_id, "topic": topic.strip()},
    )
    await session.commit()


async def _already_enqueued_today(session: AsyncSession, kind: str, local_day: date) -> bool:
    """Any outbox row of this kind for this local day (any status).

    Dedup keys on the payload's own ``date`` — the local day the row was
    emitted for — rather than created_at, so a DB-clock skew (or an injected
    test clock) can neither duplicate nor swallow a day's brief. Both
    scheduler-produced kinds always carry the date.
    """
    result = await session.execute(
        text(
            "SELECT 1 FROM notifications.outbox "
            "WHERE kind = :kind AND payload->>'date' = :day "
            "LIMIT 1"
        ),
        {"kind": kind, "day": local_day.isoformat()},
    )
    return result.first() is not None


async def enqueue(
    session: AsyncSession, channel_id: uuid.UUID, kind: str, payload: dict[str, Any]
) -> None:
    """Append one outbox row (NOT committed — callers batch + commit once)."""
    await session.execute(
        text(
            "INSERT INTO notifications.outbox (channel_id, kind, payload) "
            "VALUES (:channel_id, :kind, CAST(:payload AS jsonb))"
        ),
        {"channel_id": channel_id, "kind": kind, "payload": json.dumps(payload)},
    )


def sync_stale_text(now: datetime, newest: datetime | None, stale_hours: int) -> str | None:
    """Marker text when the newest observation is too old (spec §101)."""
    if newest is None:
        return None
    age = now - newest
    if age <= timedelta(hours=stale_hours):
        return None
    hours = age.total_seconds() / 3600
    return (
        "Sync warning: newest observation is "
        f"{round(hours, 1):g} hours old (limit {stale_hours} hours). "
        "Check that your phone is syncing."
    )


async def morning_tick(
    session: AsyncSession,
    *,
    tz: ZoneInfo,
    brief_time: time,
    stale_hours: int,
    now: datetime,
    ntfy_topic: str = "",
) -> int:
    """One scheduler pass. Returns the number of outbox rows enqueued."""
    if not in_morning_window(now.astimezone(tz), brief_time):
        return 0

    await ensure_ntfy_channel(session, ntfy_topic)
    channels = await enabled_channels(session)
    if not channels:
        return 0

    local_day: date = now.astimezone(tz).date()
    enqueued = 0

    if not await _already_enqueued_today(session, "morning_brief", local_day):
        data = await assemble_today(session, tz=tz, now=now)
        brief_text = build_morning_brief(data, data.coverage_ratio)
        for channel in channels:
            await enqueue(
                session,
                channel["id"],
                "morning_brief",
                {
                    "text": brief_text,
                    "category": "routine",
                    "date": local_day.isoformat(),
                },
            )
            enqueued += 1
        await session.commit()

    newest = (
        await session.execute(text("SELECT max(ts) FROM timeseries.heart_rate"))
    ).scalar_one_or_none()
    warning = sync_stale_text(now, newest, stale_hours)
    if warning is not None and not await _already_enqueued_today(
        session, "sync_warning", local_day
    ):
        for channel in channels:
            await enqueue(
                session,
                channel["id"],
                "sync_warning",
                {"text": warning, "category": "system", "date": local_day.isoformat()},
            )
            enqueued += 1
        await session.commit()

    return enqueued
