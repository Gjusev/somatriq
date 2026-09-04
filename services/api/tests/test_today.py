"""Today endpoint behavior: GET /api/v1/metrics/today (spec §76; ADR 0012/0017).

Integration tests against the migrated TimescaleDB: wake-date attribution of
sleep, HRV over sleep-window RR (exact somatriq_hrv_rmssd_v1 math), the M5
read-through resting HR, 28-day-trailing baselines ending yesterday, DST day
handling, and freshness. Scores are hand-computed from the frozen
somatriq_recovery_v1 formula.
"""

import uuid
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_api import today
from somatriq_api.today import router as today_router
from somatriq_db.engine import get_engine, get_session
from somatriq_db.models import Device, User
from somatriq_db.testing import TEST_DATABASE_URL, requires_db
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

MADRID = ZoneInfo("Europe/Madrid")
TODAY_PATH = "/api/v1/metrics/today"

# Baseline nights: per-night RMSSD ([800, 800+d, 800, 800+d] has deltas
# +d/-d/+d, so RMSSD is exactly d), sleep minutes, and resting_hr.
NIGHTS: Sequence[tuple[int, int, int]] = [
    (90, 50, 59),
    (95, 55, 60),
    (100, 60, 60),
    (100, 60, 60),
    (105, 65, 61),
    (110, 70, 61),
    (115, 75, 62),
]


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def today_client(db: AsyncSession) -> Iterator[TestClient]:
    """App with only the today router, served through a per-test engine."""
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
    app.include_router(today_router)
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client


def _freeze_now(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Anchor the request clock so the local day is deterministic."""
    monkeypatch.setattr(today, "_now", lambda: moment)


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    return user_id, device_id


async def _seed_sleep(
    db: AsyncSession, srid: str, start: datetime, end: datetime
) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.sleep_sessions "
            "(user_id, device_id, source_record_id, start_ts, end_ts) "
            "VALUES (:user_id, :device_id, :srid, :start, :end)"
        ),
        {"user_id": user_id, "device_id": device_id, "srid": srid, "start": start, "end": end},
    )
    await db.commit()


async def _seed_rr(
    db: AsyncSession, prefix: str, points: Iterable[tuple[datetime, int]]
) -> None:
    user_id, device_id = await _ids(db)
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"{prefix}-{i}",
            "ts": ts,
            "rr_ms": rr_ms,
            "seq": i,
        }
        for i, (ts, rr_ms) in enumerate(points)
    ]
    await db.execute(
        text(
            "INSERT INTO timeseries.rr_interval "
            "(user_id, device_id, source_record_id, ts, rr_ms, seq) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :rr_ms, :seq)"
        ),
        rows,
    )
    await db.commit()


async def _seed_hr(db: AsyncSession, samples: Iterable[tuple[datetime, float]]) -> None:
    user_id, device_id = await _ids(db)
    prefix = uuid.uuid4().hex
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"today-test-{prefix}-{i}",
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


async def _seed_feature(db: AsyncSession, day: date, resting_hr: float) -> None:
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, data_quality, "
            "sample_count, coverage_ratio, algorithm_version) "
            "VALUES (:day, 'daily_heart/v1', 'UTC', :rhr, 'good', 86400, 1.0, "
            "'somatriq_rhr_v1')"
        ),
        {"day": day, "rhr": resting_hr},
    )
    await db.commit()


def _rr_pattern(base: datetime, d: int) -> list[tuple[datetime, int]]:
    """[800, 800+d, 800, 800+d] one second apart — RMSSD is exactly d."""
    return [(base + timedelta(seconds=i), rr) for i, rr in enumerate([800, 800 + d, 800, 800 + d])]


async def _seed_baseline_nights(db: AsyncSession, today: date, count: int) -> None:
    """Nights ending yesterday backwards: one session + RR + feature row each."""
    for i, (rmssd, sleep_min, rhr) in enumerate(NIGHTS[:count]):
        day = today - timedelta(days=count - i)  # oldest first, last = yesterday
        start = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
        end = start + timedelta(minutes=sleep_min)
        await _seed_sleep(db, f"s-{day.isoformat()}", start, end)
        points = _rr_pattern(start + timedelta(minutes=10), rmssd)
        await _seed_rr(db, f"rr-{day.isoformat()}", points)
        await _seed_feature(db, day, float(rhr))


@requires_db
async def test_today_exact_hrv_and_recovery_score(
    db: AsyncSession, today_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full day: exact RMSSD 105 and the hand-computed score 76.66.

    Baselines (7 nights ending yesterday): HRV (100, 10), RHR (60, 1),
    sleep (60, 10) — so today's z' = (+1, 0, +1) and
    score = 100·(0.5 + 0.7·tanh(1)/2) = 76.66.
    """
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    today_date = date(2026, 8, 21)
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)

    await _seed_sleep(db, "s-today", start, start + timedelta(minutes=65))
    await _seed_rr(db, "rr-today", _rr_pattern(start + timedelta(minutes=10), 105))
    await _seed_hr(
        db,
        (
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)  # 30 five-minute buckets -> resting_hr 60.0
        ),
    )
    await _seed_baseline_nights(db, today_date, count=7)

    response = today_client.get(TODAY_PATH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "date",
        "timezone",
        "recovery",
        "hrv",
        "sleep",
        "resting_hr",
        "resting_hr_quality",
        "data_freshness_minutes",
        "caveats",
    }
    assert body["date"] == "2026-08-21"
    assert body["timezone"] == "UTC"

    hrv = body["hrv"]
    assert hrv["day"] == "2026-08-21"
    assert hrv["rmssd_ms"] == 105.0
    assert hrv["sdnn_ms"] == 52.5
    assert hrv["pnn50"] == 1.0
    assert hrv["mean_rr_ms"] == 852.5
    assert hrv["samples"] == 4
    assert hrv["valid_samples"] == 4
    assert hrv["artifact_count"] == 0
    assert hrv["coverage"] == 1.0
    assert hrv["session_count"] == 1
    assert hrv["filter_version"] == "simple-delta400"
    assert hrv["algorithm_version"] == "somatriq_hrv_rmssd_v1"

    assert body["sleep"] == {
        "day": "2026-08-21",
        "duration_minutes": 65.0,
        "efficiency": None,
        "resting_hr": None,
        "avg_hrv": None,
        "source_record_ids": ["s-today"],
    }
    assert body["resting_hr"] == 60.0
    assert body["resting_hr_quality"] == "insufficient"  # 150 samples of a day
    assert body["data_freshness_minutes"] == 1.0  # newest HR 11:59Z, now 12:00Z

    recovery = body["recovery"]
    assert recovery["algorithm_version"] == "somatriq_recovery_v1"
    assert recovery["score"] == pytest.approx(76.66, abs=0.005)
    assert recovery["missing_inputs"] == []
    assert recovery["caveats"] == []
    by_input = {c["input"]: c for c in recovery["contributions"]}
    assert by_input["hrv"]["contribution"] == "positive"
    assert by_input["hrv"]["robust_z"] == pytest.approx(1.0)
    assert by_input["hrv"]["baseline_median"] == 100.0
    assert by_input["hrv"]["baseline_iqr"] == 10.0
    assert by_input["rhr"]["contribution"] == "neutral"
    assert by_input["sleep"]["contribution"] == "positive"
    assert by_input["sleep"]["robust_z"] == pytest.approx(1.0)
    assert by_input["temperature"]["contribution"] == "neutral"
    assert by_input["temperature"]["note"] == "input missing in v1"
    assert by_input["training_load"]["note"] == "input missing in v1"
    assert len(recovery["contributions"]) == 5
    assert body["caveats"] == []


@requires_db
async def test_today_missing_rr_lists_hrv_input_missing(
    db: AsyncSession, today_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sleep + RHR present but no RR in the window: score null, hrv missing."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    await _seed_sleep(db, "s-today", start, start + timedelta(minutes=60))
    await _seed_hr(
        db,
        (
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)
        ),
    )

    response = today_client.get(TODAY_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["hrv"] is None
    assert body["sleep"]["duration_minutes"] == 60.0
    assert body["resting_hr"] == 60.0
    recovery = body["recovery"]
    assert recovery["score"] is None
    assert recovery["missing_inputs"] == ["hrv"]
    by_input = {c["input"]: c for c in recovery["contributions"]}
    assert by_input["hrv"]["value"] is None
    assert by_input["hrv"]["note"] == "input missing"
    assert by_input["sleep"]["value"] == 60.0  # present inputs still explain


@requires_db
async def test_today_baseline_below_min_days_flags_caveats(
    db: AsyncSession, today_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All inputs present but only 3 baseline nights: caveats, never a score."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))
    today_date = date(2026, 8, 21)
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    await _seed_sleep(db, "s-today", start, start + timedelta(minutes=65))
    await _seed_rr(db, "rr-today", _rr_pattern(start + timedelta(minutes=10), 105))
    await _seed_hr(
        db,
        (
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)
        ),
    )
    await _seed_baseline_nights(db, today_date, count=3)

    response = today_client.get(TODAY_PATH)

    assert response.status_code == 200
    body = response.json()
    recovery = body["recovery"]
    assert recovery["score"] is None
    assert recovery["missing_inputs"] == []  # inputs exist; baselines do not
    assert len(recovery["caveats"]) == 3
    assert all("baseline insufficient" in c for c in recovery["caveats"])
    assert any("hrv" in c for c in recovery["caveats"])
    assert any("rhr" in c for c in recovery["caveats"])
    assert any("sleep" in c for c in recovery["caveats"])
    assert any("baseline" in c for c in body["caveats"])  # bubbled to the top


@requires_db
async def test_today_dst_fallback_madrid(
    db: AsyncSession, today_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Europe/Madrid 2026-10-25 fall-back: 25-hour local day, wake-date exact.

    A session ending 2026-10-26T00:30Z wakes on local 10-26 — excluded from
    today's sleep and from the yesterday-ending baseline.
    """
    monkeypatch.setenv("USER_TIMEZONE", "Europe/Madrid")
    _freeze_now(monkeypatch, datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC))

    in_day = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
    next_day = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    await _seed_sleep(db, "a", in_day, in_day + timedelta(hours=1))
    await _seed_sleep(db, "b", next_day, next_day + timedelta(hours=1))
    await _seed_rr(
        db, "rr-a", _rr_pattern(in_day + timedelta(minutes=10), 100)
    )
    await _seed_hr(
        db,
        (
            (datetime(2026, 10, 25, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)
        ),
    )

    response = today_client.get(TODAY_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-10-25"
    assert body["timezone"] == "Europe/Madrid"
    assert body["sleep"]["duration_minutes"] == 60.0  # only session "a"
    assert body["sleep"]["source_record_ids"] == ["a"]
    assert body["hrv"]["rmssd_ms"] == 100.0
    assert body["hrv"]["samples"] == 4
    assert body["resting_hr"] == 60.0
    assert any("DST" in c for c in body["caveats"])


@requires_db
async def test_today_empty_day_is_all_missing(
    db: AsyncSession, today_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No data at all: 200, every input listed missing, freshness null."""
    _freeze_now(monkeypatch, datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC))

    response = today_client.get(TODAY_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["hrv"] is None
    assert body["sleep"] is None
    assert body["resting_hr"] is None
    assert body["resting_hr_quality"] == "insufficient"
    assert body["data_freshness_minutes"] is None
    assert body["recovery"]["score"] is None
    assert body["recovery"]["missing_inputs"] == ["hrv", "rhr", "sleep"]
    assert body["caveats"] == []
