"""Daily heart summary endpoint behavior (spec §72, §174; ADR 0012/0017).

Integration tests against the migrated TimescaleDB. The system under test is
the local-day bucketing SQL (5-minute medians, DST-correct day windows), the
read-through cache into derived.daily_features, and the contract response
shape — so they run only when the test database is reachable.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_api import metrics
from somatriq_api.metrics import router as metrics_router
from somatriq_api.security import require_read_principal
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_db.engine import get_engine, get_session
from somatriq_db.models import DailyFeature, Device, User
from somatriq_db.testing import TEST_DATABASE_URL, requires_db
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def metrics_client(
    db: AsyncSession, account_jwt_override: Callable[[], uuid.UUID]
) -> Iterator[TestClient]:
    """App with only the metrics router, served through a per-test engine.

    Mirrors test_metrics.py: the app gets a dedicated engine created lazily
    on the TestClient portal loop; seeding via the db fixture shares the same
    database through its own engine. The JWT guard is overridden (the 401
    contract lives in test_read_auth.py).
    """
    engine: AsyncEngine | None = None

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        nonlocal engine
        if engine is None:
            engine = create_async_engine(TEST_DATABASE_URL)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(metrics_router)
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_read_principal] = account_jwt_override
    with TestClient(app) as client:
        yield client


def _freeze_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Anchor the request clock so the local-day window is deterministic."""
    monkeypatch.setattr(metrics, "_now", lambda: moment)


async def _seed_samples(
    db: AsyncSession, samples: Iterable[tuple[datetime, float]]
) -> None:
    """Insert heart-rate rows directly, owned by the seeded single-user identity."""
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    prefix = uuid.uuid4().hex
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"daily-test-{prefix}-{i}",
            "ts": ts,
            "bpm": bpm,
        }
        for i, (ts, bpm) in enumerate(samples)
    ]
    await db.execute(
        text(
            "INSERT INTO timeseries.heart_rate "
            "(user_id, device_id, source_record_id, ts, bpm) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :bpm)"
        ),
        rows,
    )
    await db.commit()


def _utc_day_samples(
    day: date, quiet_buckets: dict[int, float], base_bpm: float = 70.0
) -> Iterator[tuple[datetime, float]]:
    """One sample per second for a full UTC day; bucket index -> override bpm."""
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    for second in range(86400):
        yield base + timedelta(seconds=second), quiet_buckets.get(second // 300, base_bpm)


async def _daily_rows(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(DailyFeature).where(
            DailyFeature.feature_set_version == FEATURE_SET_VERSION
        )
    )
    return result.scalar_one()


@requires_db
async def test_full_day_resting_hr_math_exact(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully sampled day: resting_hr is the mean of the 3 quietest 5-min medians."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    # Quiet window 02:00-02:15 local (UTC day): three buckets at 50/51/52.
    quiet = {24: 50.0, 25: 51.0, 26: 52.0}
    await _seed_samples(db, _utc_day_samples(date(2026, 8, 20), quiet))

    response = metrics_client.get("/api/v1/metrics/daily", params={"days": 2})

    assert response.status_code == 200
    days = response.json()["days"]
    assert [d["date"] for d in days] == ["2026-08-20", "2026-08-21"]

    full = days[0]
    assert full["sample_count"] == 86400
    assert full["resting_hr"] == 51.0  # mean(50, 51, 52)
    assert full["hr_min"] == 50.0
    assert full["hr_max"] == 70.0
    expected_mean = (300 * (50.0 + 51.0 + 52.0) + 85500 * 70.0) / 86400
    assert full["hr_mean"] == pytest.approx(expected_mean, abs=0.01)
    assert full["coverage_ratio"] == 1.0
    assert full["data_quality"] == "good"
    assert full["algorithm_version"] == "somatriq_rhr_v1"

    # The request day itself is empty but explicitly present (gap rendering).
    empty = days[1]
    assert empty["date"] == "2026-08-21"
    assert empty["sample_count"] == 0
    assert empty["resting_hr"] is None
    assert empty["data_quality"] == "insufficient"


@requires_db
async def test_partial_day_coverage_grades_fair(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """30% of a day sampled: coverage 0.3 -> fair (contract thresholds)."""
    _freeze_now(monkeypatch, datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))
    base = datetime(2026, 8, 20, tzinfo=UTC)
    await _seed_samples(
        db,
        ((base + timedelta(seconds=s), 65.0) for s in range(25920)),  # 7.2 h = 30%
    )

    response = metrics_client.get("/api/v1/metrics/daily", params={"days": 1})

    assert response.status_code == 200
    day = response.json()["days"][0]
    assert day["date"] == "2026-08-20"
    assert day["sample_count"] == 25920
    assert day["coverage_ratio"] == pytest.approx(25920 / 86400)
    assert day["data_quality"] == "fair"
    assert day["resting_hr"] == 65.0  # 87 buckets, all medians 65


@requires_db
async def test_empty_day_returns_nulls_and_insufficient(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A day with zero samples is included with nulls + insufficient (spec §174)."""
    _freeze_now(monkeypatch, datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    response = metrics_client.get("/api/v1/metrics/daily", params={"days": 1})

    assert response.status_code == 200
    days = response.json()["days"]
    assert len(days) == 1
    day = days[0]
    assert day == {
        "date": "2026-08-20",
        "timezone": "UTC",
        "resting_hr": None,
        "hr_min": None,
        "hr_mean": None,
        "hr_max": None,
        "sample_count": 0,
        "coverage_ratio": 0.0,
        "data_quality": "insufficient",
        "algorithm_version": None,
    }
    # The empty day is persisted too, so the gap survives the cache.
    assert await _daily_rows(db) == 1


@requires_db
async def test_dst_fallback_madrid_days_are_correct(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Europe/Madrid 2026-10-25 fall-back: the 25-hour local day loses nothing.

    Local 2026-10-25 starts at 2026-10-24T22:00Z (CEST) and ends at
    2026-10-25T23:00Z (CET) — 25 UTC hours. Every sample must land on exactly
    one local day: no duplication, no loss across the boundary.
    """
    monkeypatch.setenv("USER_TIMEZONE", "Europe/Madrid")
    _freeze_now(monkeypatch, datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC))

    # Boundary sanity against the timezone rule itself.
    day_start = datetime(2026, 10, 25, 0, 0, 0, tzinfo=MADRID).astimezone(UTC)
    day_end = datetime(2026, 10, 26, 0, 0, 0, tzinfo=MADRID).astimezone(UTC)
    assert day_start == datetime(2026, 10, 24, 22, 0, 0, tzinfo=UTC)
    assert day_end == datetime(2026, 10, 25, 23, 0, 0, tzinfo=UTC)
    assert day_end - day_start == timedelta(hours=25)

    # 25 hours at one sample per minute (1500), then the next local day's
    # first hour at 1/minute starting exactly at local midnight (60).
    fall_back_day = (
        (day_start + timedelta(minutes=m), 60.0) for m in range(1500)
    )
    next_day = (
        (day_end + timedelta(minutes=m), 70.0) for m in range(60)
    )
    await _seed_samples(db, [*fall_back_day, *next_day])

    response = metrics_client.get("/api/v1/metrics/daily", params={"days": 2})

    assert response.status_code == 200
    days = response.json()["days"]
    assert [d["date"] for d in days] == ["2026-10-25", "2026-10-26"]
    assert all(d["timezone"] == "Europe/Madrid" for d in days)

    first, second = days
    assert first["sample_count"] == 1500  # all 25 hours attributed once
    assert first["hr_min"] == 60.0
    assert first["hr_max"] == 60.0
    assert first["resting_hr"] == 60.0  # 300 buckets, all medians 60
    assert second["sample_count"] == 60  # boundary sample belongs here
    assert second["hr_min"] == 70.0
    assert second["resting_hr"] is None  # 12 buckets < 30 -> null
    # No duplicated or lost samples across the 25-hour day.
    assert first["sample_count"] + second["sample_count"] == 1560

    # Cross-check: the SQL day window equals the zoneinfo-derived bounds.
    in_window = cast(
        int,
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM timeseries.heart_rate "
                    "WHERE ts >= :start AND ts < :end"
                ),
                {"start": day_start, "end": day_end},
            )
        ).scalar_one(),
    )
    assert in_window == first["sample_count"]


@requires_db
async def test_read_through_cache_serves_stable_rows(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call returns cached values; rows exist exactly once per day."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    base = datetime(2026, 8, 20, tzinfo=UTC)
    await _seed_samples(db, [(base + timedelta(seconds=s), 68.0) for s in range(0, 43200, 2)])

    first = metrics_client.get("/api/v1/metrics/daily", params={"days": 3})
    assert first.status_code == 200
    assert await _daily_rows(db) == 3  # every requested day persisted, once

    # Backfill AFTER the first compute: the cached rows must still serve.
    await _seed_samples(db, [(base + timedelta(seconds=s), 90.0) for s in range(1, 43200, 2)])

    second = metrics_client.get("/api/v1/metrics/daily", params={"days": 3})
    assert second.status_code == 200
    assert second.json() == first.json()
    assert await _daily_rows(db) == 3  # cache hits did not duplicate rows


@requires_db
async def test_response_matches_contract_shape(
    db: AsyncSession, metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys and version pinning match the frozen DailySummaryResponse exactly."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))

    response = metrics_client.get("/api/v1/metrics/daily", params={"days": 2})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"feature_set_version", "days"}
    assert body["feature_set_version"] == FEATURE_SET_VERSION == "daily_heart/v1"
    dates = [d["date"] for d in body["days"]]
    assert dates == sorted(dates) == ["2026-08-20", "2026-08-21"]
    expected_day_keys = {
        "date",
        "timezone",
        "resting_hr",
        "hr_min",
        "hr_mean",
        "hr_max",
        "sample_count",
        "coverage_ratio",
        "data_quality",
        "algorithm_version",
    }
    assert all(set(d) == expected_day_keys for d in body["days"])


@requires_db
async def test_days_param_bounds_422(
    metrics_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """days must stay within 1..120; outside is a validation 422."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    for days in (0, -1, 121):
        response = metrics_client.get("/api/v1/metrics/daily", params={"days": days})
        assert response.status_code == 422, days

async def test_open_day_refreshes_on_late_samples(
    metrics_client: TestClient, db: AsyncSession
) -> None:
    """Today's cached row must NOT pin mid-accumulation values (grill
    decision 7: emit with coverage marker + silent recompute). Closed days
    stay frozen under ADR 0012."""
    now = datetime.now(UTC)

    def samples(tag: str, bpm: float, count: int, base: datetime) -> list[tuple[datetime, float]]:
        # count samples each in its OWN 5-minute bucket (distinct bucket starts)
        return [
            (base + timedelta(minutes=5 * i, seconds=1), bpm)
            for i in range(count)
        ]

    early = now - timedelta(hours=3)
    await _seed_samples(db, samples("early", 60.0, 30, early))
    await db.commit()

    r1 = metrics_client.get("/api/v1/metrics/daily?days=1")
    assert r1.status_code == 200, r1.text
    assert r1.json()["days"][-1]["resting_hr"] == 60.0

    await _seed_samples(db, samples("late", 50.0, 30, early - timedelta(minutes=1)))
    await db.commit()

    r2 = metrics_client.get("/api/v1/metrics/daily?days=1")
    days = r2.json()["days"][-1]
    # The 50 bpm samples now sit in the day's lowest buckets: the refreshed
    # resting_hr must drop below the pinned 60.0 (exact value depends on the
    # interleaving of the two seeding passes — refresh, not exact math, is
    # the regression being pinned).
    assert days["resting_hr"] is not None and days["resting_hr"] < 60.0, days
