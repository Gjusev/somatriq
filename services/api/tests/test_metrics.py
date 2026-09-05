"""Heart-rate metric read endpoint behavior (spec §71, §99, §116; ADR 0002).

Integration tests against the migrated TimescaleDB — time_bucket alignment,
window filtering and the coverage envelope are the system under test, so they
run only when the test database is reachable and skip otherwise.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_api.accounts import require_account_jwt
from somatriq_api.metrics import router as metrics_router
from somatriq_db.engine import get_engine, get_session
from somatriq_db.models import Device, HeartRate, User
from somatriq_db.testing import TEST_DATABASE_URL, requires_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

BASE = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)  # aligned to a 5-minute boundary


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops.

    The engine in somatriq_db.engine is process-global and pools asyncpg
    connections on the loop that created them, while pytest-asyncio hands
    each test a fresh loop. Replacing the pool (without terminating its
    connections — that would need the dead loop) lets the db fixture open a
    connection on the current test's own loop every time. The teardown
    matters too: this file's last connection must not reach whatever test
    file runs after this one.
    """
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def metrics_client(
    db: AsyncSession, account_jwt_override: Callable[[], uuid.UUID]
) -> Iterator[TestClient]:
    """App with only the metrics router, served through a per-test engine.

    The module-level engine in somatriq_db.engine pools connections on the
    event loop that first touches them; TestClient answers requests on its
    own portal loop, so the app gets a dedicated engine created lazily on
    that loop (and disposed on it at shutdown). Seeding via the db fixture
    shares the same database through its own engine.

    Reads are JWT-guarded (spec §122); the guard is overridden here because
    this file pins the read semantics — the 401 contract itself lives in
    test_read_auth.py.

    Wiring the router into somatriq_api.main is a separate slice.
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
    app.dependency_overrides[require_account_jwt] = account_jwt_override
    with TestClient(app) as client:
        yield client


async def _insert_samples(db: AsyncSession, samples: list[tuple[datetime, float]]) -> None:
    """Insert heart-rate rows owned by the seeded single-user identity (M1).

    Ownership comes from the first seeded identity rows, never hardcoded ids.
    """
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    db.add_all(
        [
            HeartRate(
                user_id=user_id,
                device_id=device_id,
                source_record_id=f"metrics-test-{uuid.uuid4().hex}",
                ts=ts,
                bpm=bpm,
            )
            for ts, bpm in samples
        ]
    )
    await db.commit()


@requires_db
async def test_returns_raw_points_ordered(db: AsyncSession, metrics_client: TestClient) -> None:
    """bucket=none returns every raw point ascending with point-level coverage."""
    samples = [(BASE + timedelta(minutes=2 * i), 60.0 + i) for i in range(5)]
    await _insert_samples(db, samples)

    response = metrics_client.get(
        "/api/v1/metrics/heart_rate",
        params={
            "from_ts": BASE.isoformat(),
            "to_ts": (BASE + timedelta(minutes=10)).isoformat(),
            "bucket": "none",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 5
    assert len(body["points"]) == 5
    timestamps = [datetime.fromisoformat(p["ts"]) for p in body["points"]]
    assert timestamps[0] < timestamps[-1]
    assert timestamps == sorted(timestamps)
    assert all(p["sample_count"] == 1 for p in body["points"])
    # Expected points = 600 s window / 1 s cadence (spec §99): 5 of 600.
    assert body["coverage"] == round(5 / 600, 3)
    assert body["caveats"] == []


@requires_db
async def test_bucket_5m_aggregates(db: AsyncSession, metrics_client: TestClient) -> None:
    """bucket=5m returns per-bucket averages aligned to 5-minute boundaries."""
    samples = [
        (BASE + timedelta(seconds=30), 60.0),
        (BASE + timedelta(minutes=1, seconds=30), 70.0),
        (BASE + timedelta(minutes=2, seconds=30), 80.0),
        (BASE + timedelta(minutes=5, seconds=30), 90.0),
        (BASE + timedelta(minutes=6, seconds=30), 100.0),
        (BASE + timedelta(minutes=7, seconds=30), 110.0),
    ]
    await _insert_samples(db, samples)

    response = metrics_client.get(
        "/api/v1/metrics/heart_rate",
        params={
            "from_ts": BASE.isoformat(),
            "to_ts": (BASE + timedelta(minutes=10)).isoformat(),
            "bucket": "5m",
        },
    )

    assert response.status_code == 200
    body = response.json()
    points = body["points"]
    assert body["count"] == 2
    assert len(points) == 2
    assert [datetime.fromisoformat(p["ts"]) for p in points] == [
        BASE,
        BASE + timedelta(minutes=5),
    ]
    assert [p["bpm"] for p in points] == [70.0, 100.0]
    assert [p["sample_count"] for p in points] == [3, 3]
    # 2 covered buckets / 2 expected buckets in a 10-minute window.
    assert body["coverage"] == 1.0


@requires_db
async def test_window_filtering(db: AsyncSession, metrics_client: TestClient) -> None:
    """Only rows inside [from_ts, to_ts] are returned; boundary rows included."""
    samples = [
        (BASE - timedelta(minutes=1), 50.0),  # before the window
        (BASE + timedelta(seconds=30), 61.0),  # inside
        (BASE + timedelta(seconds=90), 62.0),  # inside
        (BASE + timedelta(minutes=10), 63.0),  # after the window
    ]
    await _insert_samples(db, samples)

    response = metrics_client.get(
        "/api/v1/metrics/heart_rate",
        params={
            "from_ts": BASE.isoformat(),
            "to_ts": (BASE + timedelta(seconds=120)).isoformat(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [datetime.fromisoformat(p["ts"]) for p in body["points"]] == [
        BASE + timedelta(seconds=30),
        BASE + timedelta(seconds=90),
    ]


@requires_db
async def test_empty_range_returns_200_with_caveat(
    db: AsyncSession, metrics_client: TestClient
) -> None:
    """An empty range is a valid answer: 200, no points, caveat present."""
    response = metrics_client.get(
        "/api/v1/metrics/heart_rate",
        params={
            "from_ts": "2020-01-01T00:00:00+00:00",
            "to_ts": "2020-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["points"] == []
    assert body["count"] == 0
    assert body["coverage"] == 0.0
    assert "no data in range" in body["caveats"]


@requires_db
async def test_invalid_window_422(metrics_client: TestClient) -> None:
    """from_ts must be strictly before to_ts; anything else is a VALIDATION 422."""
    for from_ts, to_ts in (
        ((BASE + timedelta(minutes=10)).isoformat(), BASE.isoformat()),  # from > to
        (BASE.isoformat(), BASE.isoformat()),  # from == to
    ):
        response = metrics_client.get(
            "/api/v1/metrics/heart_rate",
            params={"from_ts": from_ts, "to_ts": to_ts},
        )
        assert response.status_code == 422, (from_ts, to_ts)
        assert response.json()["detail"]["error_code"] == "VALIDATION"


@requires_db
async def test_last_hours_window_defaults_to_recent_range(
    db: AsyncSession, metrics_client: TestClient
) -> None:
    """Without explicit bounds the window is [now - last_hours, now] (UTC)."""
    now = datetime.now(UTC)
    await _insert_samples(
        db,
        [
            (now - timedelta(minutes=5), 72.0),  # inside the default window
            (now - timedelta(hours=3), 55.0),  # outside
        ],
    )

    response = metrics_client.get("/api/v1/metrics/heart_rate", params={"last_hours": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["points"][0]["bpm"] == 72.0
