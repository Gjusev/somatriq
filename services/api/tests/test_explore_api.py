"""Explore read API behavior (Block 3; grill P12; ADR 0013/0017).

The honesty contract under test: gaps stay gaps (days without a value do
not appear), computed points carry their coverage, vendor points name the
dominant device, overlays expose device boundaries / algorithm registry /
journal kinds / training days / timezone changes, and the frozen 3-year
span cap rejects with guidance.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from somatriq_api.main import app
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SERIES = "/api/v1/explore/series"
CONTEXT = "/api/v1/explore/context"
TODAY = date(2026, 9, 9)


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


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    return (
        (await db.execute(text("SELECT id FROM identity.users LIMIT 1"))).scalar_one(),
        (await db.execute(text("SELECT id FROM identity.devices LIMIT 1"))).scalar_one(),
    )


@requires_db
async def test_series_gap_honesty_with_vendor_provenance(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    user_id, device_id = await _ids(db)
    second = (
        await db.execute(
            text(
                "INSERT INTO identity.devices (user_id, name, model) "
                "VALUES (:user_id, 'whoop-5', 'whoop') RETURNING id"
            ),
            {"user_id": user_id},
        )
    ).scalar_one()
    # avg_hrv on 3 days for the synthetic device, 1 overlapping day for the
    # second — the dominant source over the window is the synthetic one.
    days = [TODAY - timedelta(days=d) for d in (4, 3, 2)]
    for day in days:
        await db.execute(
            text(
                "INSERT INTO health.daily_observations (user_id, device_id, day, metric, value) "
                "VALUES (:u, :d, :day, 'avg_hrv', 55.0)"
            ),
            {"u": user_id, "d": device_id, "day": day},
        )
    await db.execute(
        text(
            "INSERT INTO health.daily_observations (user_id, device_id, day, metric, value) "
            "VALUES (:u, :d, :day, 'avg_hrv', 60.0)"
        ),
        {"u": user_id, "d": second, "day": days[0]},
    )
    await db.commit()
    headers = await _register(api)

    response = await api.get(
        SERIES,
        params={
            "metrics": "avg_hrv",
            "from_date": (TODAY - timedelta(days=6)).isoformat(),
            "to_date": TODAY.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    series = response.json()["metrics"][0]
    assert series["source_kind"] == "vendor_daily"
    assert series["dominant_device"] == "synthetic-01"
    assert [point["date"] for point in series["days"]] == [
        day.isoformat() for day in days
    ]  # 4 absent days stay absent — no filler
    assert all(point["coverage"] is None for point in series["days"])


@requires_db
async def test_series_computed_carries_coverage(api: httpx.AsyncClient, db: AsyncSession) -> None:
    user_id, _ = await _ids(db)
    del user_id
    day = TODAY - timedelta(days=1)
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, sample_count, "
            " coverage_ratio, data_quality, algorithm_version) "
            "VALUES (:day, :fsv, 'UTC', 52.0, 1440, 0.9, 'good', 'somatriq_rhr_v1')"
        ),
        {"day": day, "fsv": FEATURE_SET_VERSION},
    )
    await db.commit()
    headers = await _register(api)

    response = await api.get(
        SERIES,
        params={
            "metrics": "resting_hr",
            "from_date": (TODAY - timedelta(days=2)).isoformat(),
            "to_date": TODAY.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    series = response.json()["metrics"][0]
    assert series["source_kind"] == "computed"
    assert series["days"] == [{"date": day.isoformat(), "value": 52.0, "coverage": 0.9}]


@requires_db
async def test_context_overlays(api: httpx.AsyncClient, db: AsyncSession) -> None:
    user_id, device_id = await _ids(db)
    # A retired second device — its boundary overlaps the window's start.
    await db.execute(
        text(
            "INSERT INTO identity.devices (user_id, name, model, active_to) "
            "VALUES (:user_id, 'whoop-4-retired', 'whoop', :retired)"
        ),
        {
            "user_id": user_id,
            "retired": datetime.combine(TODAY - timedelta(days=5), datetime.min.time(), tzinfo=UTC),
        },
    )
    await db.execute(
        text(
            "INSERT INTO health.journal_events (user_id, source, kind, ts) "
            "VALUES (:user_id, 'web', 'caffeine', :ts)"
        ),
        {
            "user_id": user_id,
            "ts": datetime.combine(TODAY - timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        },
    )
    await db.execute(
        text(
            "INSERT INTO health.training_sessions (user_id, ts, source) "
            "VALUES (:user_id, :ts, 'api')"
        ),
        {
            "user_id": user_id,
            "ts": datetime.combine(TODAY - timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        },
    )
    # Two daily_features rows with different timezones -> one change point.
    for offset, zone in ((2, "UTC"), (1, "Europe/Madrid")):
        await db.execute(
            text(
                "INSERT INTO derived.daily_features "
                "(date, feature_set_version, timezone, sample_count, data_quality) "
                "VALUES (:day, :fsv, :zone, 0, 'insufficient')"
            ),
            {"day": TODAY - timedelta(days=offset), "fsv": FEATURE_SET_VERSION, "zone": zone},
        )
    del device_id
    await db.commit()
    headers = await _register(api)

    response = await api.get(
        CONTEXT,
        params={
            "from_date": (TODAY - timedelta(days=7)).isoformat(),
            "to_date": TODAY.isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {device["name"] for device in body["devices"]} >= {
        "synthetic-01",
        "whoop-4-retired",
    }
    algorithm_names = {algorithm["name"] for algorithm in body["algorithms"]}
    assert "somatriq_recovery_v1" in algorithm_names  # registry (migration 0013)
    assert body["journal_kinds"] == [{"kind": "caffeine", "days": 1}]
    assert body["training_days"] == [
        {"date": (TODAY - timedelta(days=1)).isoformat(), "sessions": 1}
    ]
    assert [change["timezone"] for change in body["timezone_changes"]] == [
        "UTC",
        "Europe/Madrid",
    ]


@requires_db
async def test_window_and_metric_validation(api: httpx.AsyncClient) -> None:
    headers = await _register(api)
    base = {"metrics": "avg_hrv", "from_date": "2026-01-01", "to_date": "2026-02-01"}

    unknown = await api.get(SERIES, params={**base, "metrics": "not_a_metric"}, headers=headers)
    assert unknown.status_code == 422, unknown.text

    inverted = await api.get(
        SERIES, params={**base, "from_date": "2026-03-01", "to_date": "2026-02-01"}, headers=headers
    )
    assert inverted.status_code == 422, inverted.text

    too_long = await api.get(
        SERIES,
        params={**base, "from_date": "2020-01-01", "to_date": "2026-02-01"},
        headers=headers,
    )
    assert too_long.status_code == 422, too_long.text
    assert "split the request" in too_long.json()["message"]


@requires_db
async def test_explore_requires_auth(api: httpx.AsyncClient) -> None:
    response = await api.get(
        SERIES, params={"metrics": "avg_hrv", "from_date": "2026-01-01", "to_date": "2026-02-01"}
    )
    assert response.status_code == 401, response.text
