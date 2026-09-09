"""/api/v1/journal/insights behavior (Block 2; grill P9; ADR 0009).

Seeds a clean negative caffeine effect over the trailing window (exposed
days -> lower next-day HRV), silent days with outcomes but no journal
activity (they must join no group), and flat strain. Asserts the frozen
family shape, the honest gate, and the association-only language.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INSIGHTS = "/api/v1/journal/insights"


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_effect(db: AsyncSession, tz: ZoneInfo) -> None:
    """Last 22 local days: 10 caffeine days (next-day HRV 40), 10 note-only
    days (next-day HRV 80), 2 silent outcome-only days (HRV 999 — they join
    no group), flat strain on the journaled days."""
    user_id, device_id = (
        (await db.execute(text("SELECT id FROM identity.users LIMIT 1"))).scalar_one(),
        (await db.execute(text("SELECT id FROM identity.devices LIMIT 1"))).scalar_one(),
    )
    now = datetime.now(UTC)
    exposed: list[date] = []
    unexposed: list[date] = []
    silent: list[date] = []
    for offset in range(2, 24):
        day = (now - timedelta(days=offset)).astimezone(tz).date()
        if offset % 2 == 0 and len(exposed) < 10:
            exposed.append(day)  # offsets 2,4,…,20 -> 10 days
        elif offset % 2 == 1 and len(unexposed) < 10:
            unexposed.append(day)  # offsets 3,5,…,21 -> 10 days
        elif len(silent) < 2:
            silent.append(day)  # offsets 22,23: outcomes only, no journal

    events: list[tuple[datetime, str, uuid.UUID]] = []
    dailies: list[tuple[uuid.UUID, uuid.UUID, date, str, float]] = []
    for day in exposed:
        ts = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(hours=10)
        events.append((ts.astimezone(UTC), "caffeine", user_id))
        dailies.append((user_id, device_id, day + timedelta(days=1), "avg_hrv", 40.0))
    for day in unexposed:
        ts = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(hours=10)
        events.append((ts.astimezone(UTC), "note", user_id))
        dailies.append((user_id, device_id, day + timedelta(days=1), "avg_hrv", 80.0))
    for day in silent:
        dailies.append((user_id, device_id, day + timedelta(days=1), "avg_hrv", 999.0))
    for day in exposed + unexposed:
        dailies.append((user_id, device_id, day, "strain", 10.0))

    for event_ts, kind, owner in events:
        await db.execute(
            text(
                "INSERT INTO health.journal_events (user_id, source, kind, ts) "
                "VALUES (:user_id, 'web', :kind, :ts)"
            ),
            {"user_id": owner, "kind": kind, "ts": event_ts},
        )
    for owner, device, day, metric, value in dailies:
        await db.execute(
            text(
                "INSERT INTO health.daily_observations "
                "(user_id, device_id, day, metric, value) "
                "VALUES (:user_id, :device_id, :day, :metric, :value)"
            ),
            {"user_id": owner, "device_id": device, "day": day, "metric": metric, "value": value},
        )
    await db.commit()


@requires_db
async def test_negative_caffeine_effect_detected(api: httpx.AsyncClient, db: AsyncSession) -> None:
    from somatriq_api.settings import get_settings

    tz = ZoneInfo(get_settings().user_timezone)
    await _seed_effect(db, tz)
    headers = await _register(api)

    response = await api.get(INSIGHTS, params={"days": 30}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    row = next(r for r in body["rows"] if r["behavior"] == "caffeine" and r["outcome"] == "avg_hrv")
    assert row["status"] == "ok"
    assert row["n_exposed"] == 10
    assert row["n_unexposed"] == 10  # the two silent days did not join
    assert row["median_exposed"] == pytest.approx(40.0)
    assert row["median_unexposed"] == pytest.approx(80.0)
    assert row["median_difference"] == pytest.approx(-40.0)
    assert row["p_value"] < 0.05
    assert row["q_value"] <= row["p_value"] + 1e-12
    assert "association, not causation" in row["method"]
    assert row["confounders"]["strain_median_exposed"] == pytest.approx(10.0)

    alcohol = next(
        r for r in body["rows"] if r["behavior"] == "alcohol" and r["outcome"] == "avg_hrv"
    )
    assert alcohol["status"] == "keep_logging"
    assert "never causal claims" in body["note"]


@requires_db
async def test_family_shape_is_frozen(api: httpx.AsyncClient, db: AsyncSession) -> None:
    from somatriq_api.settings import get_settings
    from somatriq_contracts.journal import BEHAVIOR_EXPOSURES, BEHAVIOR_OUTCOMES

    tz = ZoneInfo(get_settings().user_timezone)
    await _seed_effect(db, tz)
    headers = await _register(api)

    body = (await api.get(INSIGHTS, params={"days": 30}, headers=headers)).json()
    pairs = [(r["behavior"], r["outcome"]) for r in body["rows"]]
    assert pairs == [
        (behavior, outcome) for behavior in BEHAVIOR_EXPOSURES for outcome in BEHAVIOR_OUTCOMES
    ]


@requires_db
async def test_days_bounds_validated(api: httpx.AsyncClient) -> None:
    headers = await _register(api)
    too_small = await api.get(INSIGHTS, params={"days": 7}, headers=headers)
    assert too_small.status_code == 422, too_small.text
