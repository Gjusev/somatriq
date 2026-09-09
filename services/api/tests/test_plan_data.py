"""Plan assembler behavior (Block 1; ADR 0017/0018/0019; grill P2/P3).

Integration tests against the migrated TimescaleDB: sleep baseline over
wake dates ending yesterday, 7-night debt ending TODAY, vendor-daily
fallback + discrepancy caveats (never silent), wake-time preference with
documented default, and latest-state persistence to derived.daily_derived
(one row per algorithm per day, refreshed in place).
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from somatriq_analytics.plan_data import DEFAULT_WAKE_TIME, assemble_plan
from somatriq_db.engine import get_engine
from somatriq_db.models import Device, User
from somatriq_db.testing import requires_db
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)  # local day 2026-09-09
TODAY = date(2026, 9, 9)


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    return user_id, device_id


async def _seed_night(db: AsyncSession, wake: date, minutes: float, srid: str) -> None:
    """One session ending at Madrid 07:00 on the wake date."""
    user_id, device_id = await _ids(db)
    end = datetime.combine(wake, time(7, 0), tzinfo=MADRID).astimezone(UTC)
    start = end - timedelta(minutes=minutes)
    await db.execute(
        text(
            "INSERT INTO health.sleep_sessions "
            "(user_id, device_id, source_record_id, start_ts, end_ts) "
            "VALUES (:user_id, :device_id, :srid, :start, :end)"
        ),
        {"user_id": user_id, "device_id": device_id, "srid": srid, "start": start, "end": end},
    )


async def _seed_daily(db: AsyncSession, day: date, metric: str, value: float) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.daily_observations (user_id, device_id, day, metric, value) "
            "VALUES (:user_id, :device_id, :day, :metric, :value)"
        ),
        {"user_id": user_id, "device_id": device_id, "day": day, "metric": metric, "value": value},
    )


async def _seed_week(db: AsyncSession, today_minutes: float = 420.0) -> None:
    """7 baseline nights of 480 min ending yesterday + last night."""
    for i in range(7, 0, -1):
        await _seed_night(db, TODAY - timedelta(days=i), 480.0, f"night-{i}")
    await _seed_night(db, TODAY, today_minutes, "night-0")
    await db.commit()


async def _derived_rows(db: AsyncSession) -> list[tuple[str, dict]]:
    result = await db.execute(
        text(
            "SELECT algorithm_version, payload FROM derived.daily_derived "
            "WHERE date = :day ORDER BY algorithm_version"
        ),
        {"day": TODAY},
    )
    return [
        (algo, payload if isinstance(payload, dict) else json.loads(payload))
        for algo, payload in result.all()
    ]


@requires_db
async def test_need_from_session_history(db: AsyncSession) -> None:
    """Baseline 480 (7 nights ending yesterday); debt 60 (last night 420)
    -> need = 480 + 0.5·60 = 510. Strain/recovery absent -> listed, never
    imputed."""
    await _seed_week(db)

    data = await assemble_plan(db, tz=MADRID, now=NOW)

    assert data.sleep_need.minutes == pytest.approx(510.0)
    assert data.sleep_debt.debt_min == pytest.approx(60.0)
    assert data.sleep_baseline == (480.0, 0.0)
    assert set(data.sleep_need.missing_inputs) == {"recent_load", "recovery"}
    assert data.wake_time == DEFAULT_WAKE_TIME
    assert data.wake_source == "default"

    rows = dict(await _derived_rows(db))
    assert set(rows) == {"somatriq_sleep_need_v1", "somatriq_day_plan_v1"}
    assert rows["somatriq_sleep_need_v1"]["minutes"] == pytest.approx(510.0)


@requires_db
async def test_vendor_fallback_and_discrepancy_caveats(db: AsyncSession) -> None:
    """A session-less day with total_sleep_min is used via fallback; a day
    where sessions and the vendor daily disagree > 45 min is a caveat."""
    await _seed_week(db)
    # today-5: no session, vendor says 450 -> fallback.
    await db.execute(text("DELETE FROM health.sleep_sessions WHERE source_record_id = 'night-5'"))
    await _seed_daily(db, TODAY - timedelta(days=5), "total_sleep_min", 450.0)
    # today-4: session 480 vs vendor 560 -> discrepancy caveat (sessions win).
    await _seed_daily(db, TODAY - timedelta(days=4), "total_sleep_min", 560.0)
    await db.commit()

    data = await assemble_plan(db, tz=MADRID, now=NOW)

    assert any("vendor daily fallback on 1" in c for c in data.caveats)
    assert any("disagree" in c for c in data.caveats)
    # Baseline: 6×480 + 450 (fallback) -> median 480.
    assert data.sleep_baseline is not None
    assert data.sleep_baseline[0] == pytest.approx(480.0)


@requires_db
async def test_wake_preference_overrides_default(db: AsyncSession) -> None:
    """wake_time preference '07:30' anchors the bedtime window; need 510
    -> bedtime 23:00, window 22:45-23:15."""
    await _seed_week(db)
    user_id, _ = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO identity.user_preferences (user_id, key, value) "
            "VALUES (:user_id, 'wake_time', :value)"
        ),
        {"user_id": user_id, "value": json.dumps("07:30")},
    )
    await db.commit()

    data = await assemble_plan(db, tz=MADRID, now=NOW)

    assert data.wake_source == "preference"
    assert data.wake_time == time(7, 30)
    assert data.plan.bedtime_window is not None
    assert data.plan.bedtime_window.start == time(22, 45)
    assert data.plan.bedtime_window.end == time(23, 15)


@requires_db
async def test_bedtime_default_anchor(db: AsyncSession) -> None:
    """Without a preference the documented 07:00 default anchors: need 510
    -> window 22:15-22:45."""
    await _seed_week(db)

    data = await assemble_plan(db, tz=MADRID, now=NOW)

    assert data.plan.bedtime_window is not None
    assert data.plan.bedtime_window.start == time(22, 15)
    assert data.plan.bedtime_window.end == time(22, 45)


@requires_db
async def test_persist_is_latest_state_upsert(db: AsyncSession) -> None:
    """A second assembly refreshes in place: still one row per algorithm."""
    await _seed_week(db)

    await assemble_plan(db, tz=MADRID, now=NOW)
    await assemble_plan(db, tz=MADRID, now=NOW + timedelta(minutes=30))

    rows = await _derived_rows(db)
    assert len(rows) == 2


@requires_db
async def test_empty_history_degrades_visibly(db: AsyncSession) -> None:
    """No sleep history: null need + null tier + null bedtime — never
    imputed — and the plan still persists its degraded shape."""
    data = await assemble_plan(db, tz=MADRID, now=NOW)

    assert data.sleep_need.minutes is None
    assert data.sleep_need.missing_inputs == ["sleep_baseline"]
    assert data.plan.tier is None
    assert data.plan.bedtime_window is None
    assert data.caveats
    rows = dict(await _derived_rows(db))
    assert rows["somatriq_sleep_need_v1"]["minutes"] is None
