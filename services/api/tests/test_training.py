"""Training endpoints (spec §78-80, §104, §205 M12; ADR 0009).

Integration tests against the migrated TimescaleDB. The system under test is
the write path (session + sets in one transaction, muscle_group from the
catalog, weight omitted = bodyweight), the live summaries with weekly ISO
aggregates, and the §80 personal response: lagged load→next-day-recovery
correlations in the correlations-matrix wire shape, with the 14-day floor,
the skipped-with-reason honesty and the causal note asserted verbatim.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_analytics.correlations import CAUSAL_LANGUAGE_NOTE, ZERO_VARIANCE_REASON
from somatriq_api.accounts import create_access_token
from somatriq_api.errors import ApiError
from somatriq_api.training import router as training_router
from somatriq_db.engine import get_session
from somatriq_db.testing import TEST_DATABASE_URL, requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

UTC_TZ = ZoneInfo("UTC")
TRAINING = "/api/v1/training"

SPEC_SETS = [
    {"exercise": "Chest press", "weight_kg": 180.0, "reps": 8},
    {"exercise": "Chest press", "weight_kg": 170.0, "reps": 9},
    {"exercise": "Chest press", "weight_kg": 160.0, "reps": 10},
]


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    from somatriq_db.engine import get_engine

    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def training_client(db: AsyncSession) -> Iterator[TestClient]:
    """App with only the training router (plus the flat ApiError body),
    served through a per-test engine — mirrors test_experiments.py."""
    from somatriq_api.main import api_error_handler

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
    app.exception_handler(ApiError)(api_error_handler)
    app.include_router(training_router)
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client


async def _auth_headers() -> dict[str, str]:
    from somatriq_db.engine import get_session_factory

    async with get_session_factory()() as session:
        user_id = (
            await session.execute(text("SELECT id FROM identity.users LIMIT 1"))
        ).scalar_one()
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _last_days(n: int) -> list[date]:
    today = datetime.now(UTC).date()
    return [today - timedelta(days=n - 1 - i) for i in range(n)]


# ── seeding ───────────────────────────────────────────────────────────────


async def _seed_training_day(
    db: AsyncSession, day: date, exercise: str, weight: float, reps: int
) -> None:
    """One single-set session at local noon of ``day`` (UTC tz in tests)."""
    user_id = (
        await db.execute(text("SELECT id FROM identity.users LIMIT 1"))
    ).scalar_one()
    noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
    session_id = await db.execute(
        text(
            "INSERT INTO health.training_sessions (user_id, source, ts, raw_text) "
            "VALUES (:user_id, 'api', :ts, :raw_text) RETURNING id"
        ),
        {"user_id": user_id, "ts": noon, "raw_text": None},
    )
    session_uuid = session_id.scalar_one()
    await db.execute(
        text(
            "INSERT INTO health.training_sets "
            "(session_id, exercise, muscle_group, weight_kg, reps, set_index) "
            "VALUES (:session_id, :exercise, :muscle_group, :weight_kg, :reps, 0)"
        ),
        {
            "session_id": session_uuid,
            "exercise": exercise,
            "muscle_group": "quads" if exercise == "squat" else "other",
            "weight_kg": weight,
            "reps": reps,
        },
    )
    await db.commit()


async def _seed_resting_hr(db: AsyncSession, values: list[tuple[date, float]]) -> None:
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, data_quality, "
            "sample_count, coverage_ratio, algorithm_version) "
            "VALUES (:day, 'daily_heart/v1', 'UTC', :rhr, 'good', 86400, 1.0, "
            "'somatriq_rhr_v1')"
        ),
        [{"day": day, "rhr": rhr} for day, rhr in values],
    )
    await db.commit()


async def _seed_rmssd_nights(
    db: AsyncSession, values: list[tuple[date, int]]
) -> None:
    """One sleep session per wake-date whose RR window yields exactly
    ``rmssd_ms``: RR [800, 800+d, 800, 800+d] has deltas [d, -d, d] →
    RMSSD = d (all pairs survive the delta-400 filter for d <= 400)."""
    user_id, device_id = (
        await db.execute(
            text(
                "SELECT u.id, d.id FROM identity.users u, identity.devices d "
                "ORDER BY u.created_at, d.active_from LIMIT 1"
            )
        )
    ).one()
    for day, rmssd_ms in values:
        start = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
        srid = f"resp-{day.isoformat()}"
        await db.execute(
            text(
                "INSERT INTO health.sleep_sessions "
                "(user_id, device_id, source_record_id, start_ts, end_ts) "
                "VALUES (:user_id, :device_id, :srid, :start, :end)"
            ),
            {
                "user_id": user_id,
                "device_id": device_id,
                "srid": srid,
                "start": start,
                "end": start + timedelta(hours=1),
            },
        )
        rr = [800, 800 + rmssd_ms, 800, 800 + rmssd_ms]
        await db.execute(
            text(
                "INSERT INTO timeseries.rr_interval "
                "(user_id, device_id, source_record_id, ts, rr_ms, seq) "
                "VALUES (:user_id, :device_id, :srid, :ts, :rr_ms, :seq)"
            ),
            [
                {
                    "user_id": user_id,
                    "device_id": device_id,
                    "srid": f"{srid}-{i}",
                    "ts": start + timedelta(minutes=10, seconds=i),
                    "rr_ms": value,
                    "seq": i,
                }
                for i, value in enumerate(rr)
            ],
        )
    await db.commit()


# ── POST /sessions ────────────────────────────────────────────────────────


@requires_db
async def test_create_requires_jwt(training_client: TestClient) -> None:
    response = training_client.post(
        f"{TRAINING}/sessions", json={"sets": SPEC_SETS}
    )
    assert response.status_code == 401


@requires_db
async def test_create_returns_live_summary(
    training_client: TestClient, db: AsyncSession
) -> None:
    response = training_client.post(
        f"{TRAINING}/sessions",
        json={"sets": SPEC_SETS, "raw_text": "Chest press 180x8 170x9 160x10"},
        headers=await _auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "api"
    assert body["raw_text"] == "Chest press 180x8 170x9 160x10"
    summary = body["summary"]
    assert summary["set_count"] == 3
    assert summary["tonnage_kg"] == pytest.approx(4570.0)
    assert summary["bodyweight_sets"] == 0
    assert summary["best_e1rm_by_exercise"] == {"chest press": pytest.approx(228.0)}
    assert summary["volume_by_group"] == {"chest": pytest.approx(4570.0)}
    assert summary["exercises"] == ["chest press"]

    stored = await db.execute(
        text(
            "SELECT exercise, muscle_group, weight_kg, set_index "
            "FROM health.training_sets ORDER BY set_index"
        )
    )
    rows = stored.all()
    assert len(rows) == 3
    assert rows[0] == ("chest press", "chest", 180.0, 0)


@requires_db
async def test_create_weight_omitted_is_bodyweight(
    training_client: TestClient, db: AsyncSession
) -> None:
    response = training_client.post(
        f"{TRAINING}/sessions",
        json={"sets": [{"exercise": "Pull-ups", "reps": 8}]},
        headers=await _auth_headers(),
    )
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["bodyweight_sets"] == 1
    assert summary["tonnage_kg"] == 0.0
    assert summary["relative_intensity"] is None
    weight = await db.execute(text("SELECT weight_kg FROM health.training_sets"))
    assert weight.scalar_one() is None


@requires_db
async def test_create_rejects_nonpositive_reps(
    training_client: TestClient, db: AsyncSession
) -> None:
    response = training_client.post(
        f"{TRAINING}/sessions",
        json={"sets": [{"exercise": "Squat", "weight_kg": 100, "reps": 0}]},
        headers=await _auth_headers(),
    )
    assert response.status_code == 422
    count = await db.execute(text("SELECT count(*) FROM health.training_sessions"))
    assert count.scalar_one() == 0


@requires_db
async def test_create_rejects_empty_sets(
    training_client: TestClient, db: AsyncSession
) -> None:
    response = training_client.post(
        f"{TRAINING}/sessions", json={"sets": []}, headers=await _auth_headers()
    )
    assert response.status_code == 422


# ── GET /sessions ─────────────────────────────────────────────────────────


@requires_db
async def test_list_summaries_and_weekly_aggregates(
    training_client: TestClient, db: AsyncSession
) -> None:
    # Two sessions: today and 8 days ago (different ISO weeks in general,
    # same exercise; tonnage 800 each).
    days = _last_days(10)
    await _seed_training_day(db, days[-1], "squat", 100.0, 8)
    await _seed_training_day(db, days[-1] - timedelta(days=8), "squat", 100.0, 8)

    response = training_client.get(f"{TRAINING}/sessions", params={"days": 30})
    assert response.status_code == 200
    body = response.json()
    assert len(body["sessions"]) == 2
    # Newest first.
    assert body["sessions"][0]["ts"] >= body["sessions"][1]["ts"]
    for row in body["sessions"]:
        assert row["summary"]["tonnage_kg"] == pytest.approx(800.0)
        assert row["summary"]["set_count"] == 1
        assert row["summary"]["exercises"] == ["squat"]
    # Weekly aggregates: one entry per ISO week present, tonnage summed.
    assert sum(w["tonnage_kg"] for w in body["weekly"]) == pytest.approx(1600.0)
    assert sum(w["hard_sets"] for w in body["weekly"]) == 2
    labels = [w["week"] for w in body["weekly"]]
    assert labels == sorted(labels)


@requires_db
async def test_list_empty_is_empty(training_client: TestClient) -> None:
    response = training_client.get(f"{TRAINING}/sessions")
    assert response.status_code == 200
    body = response.json()
    assert body["sessions"] == []
    assert body["weekly"] == []


@requires_db
async def test_list_days_bounds_the_window(
    training_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(10)
    await _seed_training_day(db, days[0], "squat", 100.0, 8)  # 10 days ago
    response = training_client.get(f"{TRAINING}/sessions", params={"days": 5})
    assert response.status_code == 200
    assert response.json()["sessions"] == []


# ── GET /response (§80) ───────────────────────────────────────────────────


@requires_db
async def test_response_correlates_load_with_next_day_recovery(
    training_client: TestClient, db: AsyncSession
) -> None:
    """20 days of rising tonnage, falling next-day resting HR and falling
    next-day RMSSD → the two tonnage pairs are perfect negatives; the
    hard-set series is constant (1 per day) → its pairs are skipped with
    the zero-variance reason."""
    days = _last_days(20)
    for i, day in enumerate(days):
        await _seed_training_day(db, day, "squat", 100.0 + 5.0 * i, 8)
    await _seed_resting_hr(db, [(d, 60.0 - 0.2 * i) for i, d in enumerate(days)])
    await _seed_rmssd_nights(db, [(d, 120 - 4 * i) for i, d in enumerate(days)])

    response = training_client.get(f"{TRAINING}/response", params={"days": 30})
    assert response.status_code == 200
    body = response.json()

    assert body["lag_days"] == 1
    assert body["method"] == "spearman"
    assert body["note"] == CAUSAL_LANGUAGE_NOTE

    pairs = {(row["pair"][0], row["pair"][1]): row for row in body["pairs"]}
    # Last training day has no tomorrow in the recovery series → n = 19.
    tonnage_rhr = pairs[("training_tonnage", "resting_hr")]
    assert tonnage_rhr["n"] == 19
    assert tonnage_rhr["r"] == pytest.approx(-1.0, abs=1e-9)
    assert tonnage_rhr["band"] == "strong"
    assert tonnage_rhr["significant"] is True
    assert "student-t" in tonnage_rhr["p_method"]

    tonnage_rmssd = pairs[("training_tonnage", "rmssd")]
    assert tonnage_rmssd["n"] == 19
    assert tonnage_rmssd["r"] == pytest.approx(-1.0, abs=1e-9)

    # Multiple-comparisons honesty over the two computed coefficients.
    assert body["n_tests"] == 2
    assert body["bonferroni_alpha"] == pytest.approx(0.05 / 2)

    # Constant hard-set series → skipped, with the honest reason.
    skipped = {(row["pair"][0], row["pair"][1]): row for row in body["skipped"]}
    assert set(skipped) == {
        ("training_hard_sets", "resting_hr"),
        ("training_hard_sets", "rmssd"),
    }
    assert skipped[("training_hard_sets", "resting_hr")]["reason"] == ZERO_VARIANCE_REASON


@requires_db
async def test_response_empty_database_skips_everything(
    training_client: TestClient,
) -> None:
    response = training_client.get(f"{TRAINING}/response")
    assert response.status_code == 200
    body = response.json()
    assert body["pairs"] == []
    assert len(body["skipped"]) == 4
    assert body["n_tests"] == 0
    assert body["bonferroni_alpha"] == pytest.approx(0.05)
    for row in body["skipped"]:
        assert row["reason"] == "insufficient overlap (n=0, need >= 14)"
    assert body["note"] == CAUSAL_LANGUAGE_NOTE


@requires_db
async def test_response_short_history_is_insufficient_overlap(
    training_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(10)
    for i, day in enumerate(days):
        await _seed_training_day(db, day, "squat", 100.0 + 5.0 * i, 8)
    await _seed_resting_hr(db, [(d, 60.0 - 0.2 * i) for i, d in enumerate(days)])

    response = training_client.get(f"{TRAINING}/response", params={"days": 30})
    assert response.status_code == 200
    body = response.json()
    assert body["pairs"] == []
    reasons = {row["reason"] for row in body["skipped"]}
    # The seeded pairs share 9 days (10 training days, no tomorrow for the
    # last); the rmssd pairs have nothing seeded at all.
    assert "insufficient overlap (n=9, need >= 14)" in reasons
    assert "insufficient overlap (n=0, need >= 14)" in reasons


@requires_db
async def test_response_rejects_bad_days_and_method(
    training_client: TestClient,
) -> None:
    assert (
        training_client.get(f"{TRAINING}/response", params={"days": 10}).status_code
        == 422
    )
    assert (
        training_client.get(
            f"{TRAINING}/response", params={"method": "kendall"}
        ).status_code
        == 422
    )
