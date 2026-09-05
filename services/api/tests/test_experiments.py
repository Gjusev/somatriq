"""Experiment endpoints (spec §83-86, §82, §204 M11; ADR 0009).

Integration tests against the migrated TimescaleDB. The system under test
is the lifecycle itself: creation backfills the BASELINE window (the last
baseline_days local days — the experiment starts by observing the status
quo), intervention rows materialize day-by-day via roll_forward, checkins
upsert the compliance ledger (spec §85), and completion runs the pure
evaluate() over each phase's COMPLIED days only — non-complied days are
excluded AND counted. Verdict wording is asserted verbatim (spec §82: even
a significant result says "consistent with", never "proves").

Writes AND reads require the account JWT (spec §122): every surface
authenticates via _auth_headers(), and the 401 flat body is asserted on
both the create and the read paths.
"""

import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_analytics.experiments import CAVEAT, MIN_DAYS_PER_PHASE
from somatriq_api.accounts import create_access_token
from somatriq_api.errors import ApiError
from somatriq_api.experiments import router as experiments_router
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

EXPERIMENTS = "/api/v1/experiments"

# The exact 7-point ramp of the unit tests (mean 50 / 60, var 28/6) so the
# wire-level numbers are the hand-derived ones: d = 10/sqrt(14/3),
# t = 5*sqrt(3), welch df = 12.
RAMP_BASELINE = [47.0, 48.0, 49.0, 50.0, 51.0, 52.0, 53.0]
RAMP_INTERVENTION = [57.0, 58.0, 59.0, 60.0, 61.0, 62.0, 63.0]


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    from somatriq_db.engine import get_engine

    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def experiments_client(db: AsyncSession) -> Iterator[TestClient]:
    """App with only the experiments router (plus the flat ApiError body),
    served through a per-test engine — mirrors test_correlations.py.

    The REAL account-JWT guard stays active: reads and writes alike
    authenticate via _auth_headers() (minted with SECRET_KEY=test-secret),
    and this file pins the 401 contract for the write surface itself."""
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
    app.include_router(experiments_router)
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client


async def _auth_headers() -> dict[str, str]:
    """JWT for the seeded single user (the guard is the system under test,
    not the auth surface)."""
    from somatriq_db.engine import get_session_factory

    async with get_session_factory()() as session:
        user_id = (
            await session.execute(text("SELECT id FROM identity.users LIMIT 1"))
        ).scalar_one()
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _today() -> date:
    return datetime.now(UTC).date()


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Caffeine cutoff",
        "hypothesis": "Avoiding caffeine after 14:00 raises my next-night HRV",
        "intervention": "No caffeine after 14:00",
        "metric": "avg_hrv",
        "direction": "increase",
    }
    payload.update(overrides)
    return payload


async def _seed_experiment(
    db: AsyncSession,
    *,
    started_days_ago: int,
    baseline_days: int = 7,
    intervention_days: int = 7,
    metric: str = "avg_hrv",
    direction: str = "increase",
) -> uuid.UUID:
    """Direct insert with a chosen start (the API always starts today)."""
    user_id = (
        await db.execute(text("SELECT id FROM identity.users LIMIT 1"))
    ).scalar_one()
    experiment_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO research.experiments "
            "(id, user_id, name, hypothesis, intervention, metric, direction, "
            " baseline_days, intervention_days, started_at) "
            "VALUES (:id, :user_id, 'seed', 'h', 'i', :metric, :direction, "
            "        :baseline_days, :intervention_days, :started_at)"
        ),
        {
            "id": experiment_id,
            "user_id": user_id,
            "metric": metric,
            "direction": direction,
            "baseline_days": baseline_days,
            "intervention_days": intervention_days,
            "started_at": datetime(
                _today().year, _today().month, _today().day, tzinfo=UTC
            )
            - timedelta(days=started_days_ago),
        },
    )
    await db.commit()
    return experiment_id


async def _seed_metric(
    db: AsyncSession, values: Iterable[tuple[date, float]]
) -> None:
    user_id, device_id = (
        await db.execute(
            text(
                "SELECT u.id, d.id FROM identity.users u, identity.devices d LIMIT 1"
            )
        )
    ).one()
    await db.execute(
        text(
            "INSERT INTO health.daily_observations "
            "(user_id, device_id, day, metric, value) "
            "VALUES (:user_id, :device_id, :day, 'avg_hrv', :value)"
        ),
        [{"user_id": user_id, "device_id": device_id, "day": day, "value": value}
         for day, value in values],
    )
    await db.commit()


async def _day_rows(db: AsyncSession, experiment_id: uuid.UUID) -> list[tuple[date, str, bool]]:
    result = await db.execute(
        text(
            "SELECT day, phase, complied FROM research.experiment_days "
            "WHERE experiment_id = :id ORDER BY day"
        ),
        {"id": experiment_id},
    )
    return [(row[0], str(row[1]), bool(row[2])) for row in result.all()]


def _window(
    started_days_ago: int, baseline_days: int, intervention_days: int
) -> tuple[date, date, date]:
    """(first_day, start_day, last_day) for a seeded experiment."""
    today = _today()
    start_day = today - timedelta(days=started_days_ago)
    first = start_day - timedelta(days=baseline_days - 1)
    last = start_day + timedelta(days=intervention_days)
    return first, start_day, last


# ── creation: the baseline window is backfilled, status quo first ─────────


@requires_db
async def test_create_requires_auth(experiments_client: TestClient) -> None:
    response = experiments_client.post(EXPERIMENTS, json=_body())
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


@requires_db
async def test_create_backfills_the_baseline_window(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    today = _today()
    response = experiments_client.post(
        EXPERIMENTS, json=_body(baseline_days=14), headers=await _auth_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["direction"] == "increase"
    assert body["window"]["first_day"] == (today - timedelta(days=13)).isoformat()
    assert body["current_phase"] == "baseline"  # today is a baseline day
    assert body["progress"]["baseline"] == {"elapsed": 14, "total": 14}
    assert body["progress"]["intervention"] == {"elapsed": 0, "total": 14}

    rows = await _day_rows(db, uuid.UUID(body["id"]))
    assert len(rows) == 14
    assert rows[0] == (today - timedelta(days=13), "baseline", True)
    assert rows[-1] == (today, "baseline", True)
    assert all(phase == "baseline" for _, phase, _ in rows)


@requires_db
async def test_create_rejects_unknown_metric(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    response = experiments_client.post(
        EXPERIMENTS, json=_body(metric="vibes"), headers=await _auth_headers()
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION"
    assert "unknown metric" in body["message"]
    stored = await db.execute(text("SELECT count(*) FROM research.experiments"))
    assert stored.scalar_one() == 0  # nothing half-created


@requires_db
async def test_create_rejects_bad_direction(experiments_client: TestClient) -> None:
    response = experiments_client.post(
        EXPERIMENTS, json=_body(direction="sideways"), headers=await _auth_headers()
    )
    assert response.status_code == 422  # pydantic literal validation


# ── roll_forward: intervention rows materialize as days pass ──────────────


@requires_db
async def test_read_rolls_the_window_forward(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    """Started 5 days ago (baseline 7 / intervention 7): today sits inside
    the intervention phase — a read materializes every missing day row
    through today and nothing beyond."""
    experiment_id = await _seed_experiment(db, started_days_ago=5)
    first, start_day, _ = _window(5, 7, 7)

    response = experiments_client.get(f"{EXPERIMENTS}/{experiment_id}")
    assert response.status_code == 401  # reads need the account JWT too (§122)
    response = experiments_client.get(
        f"{EXPERIMENTS}/{experiment_id}", headers=await _auth_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_phase"] == "intervention"
    assert body["progress"]["baseline"] == {"elapsed": 7, "total": 7}
    assert body["progress"]["intervention"] == {"elapsed": 5, "total": 7}

    rows = await _day_rows(db, experiment_id)
    assert len(rows) == 12  # 7 baseline + 5 intervention through today
    assert rows[0] == (first, "baseline", True)
    assert rows[6] == (start_day, "baseline", True)
    assert rows[7] == (start_day + timedelta(days=1), "intervention", True)
    assert rows[-1][0] == _today()  # never materializes future days


@requires_db
async def test_list_carries_progress(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=5)
    response = experiments_client.get(EXPERIMENTS, headers=await _auth_headers())
    assert response.status_code == 200, response.text
    listed = response.json()
    assert [item["id"] for item in listed] == [str(experiment_id)]
    item = listed[0]
    assert item["current_phase"] == "intervention"
    assert item["progress"]["intervention"]["elapsed"] == 5
    assert item["compliance"]["intervention"]["complied"] == 5
    assert item["compliance"]["intervention"]["total"] == 5


# ── checkins: the compliance ledger (spec §85) ────────────────────────────


@requires_db
async def test_checkin_upserts_today_and_explicit_days(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=5)
    today = _today()

    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin",
        json={"complied": False, "note": "had an espresso at 16:00"},
        headers=await _auth_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["day"] == today.isoformat()

    rows = {
        day: (phase, complied)
        for day, phase, complied in await _day_rows(db, experiment_id)
    }
    assert rows[today] == ("intervention", False)

    # Upsert: the same day flips back, and a note rides along.
    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin",
        json={"day": today.isoformat(), "complied": True, "note": "back on track"},
        headers=await _auth_headers(),
    )
    assert response.status_code == 200
    rows = {
        day: (phase, complied)
        for day, phase, complied in await _day_rows(db, experiment_id)
    }
    assert rows[today] == ("intervention", True)
    assert len(rows) == 12  # upsert, not a second row


@requires_db
async def test_checkin_outside_the_window_is_422(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=5)
    first, _, last = _window(5, 7, 7)
    headers = await _auth_headers()

    before = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin",
        json={"day": (first - timedelta(days=1)).isoformat(), "complied": True},
        headers=headers,
    )
    after = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin",
        json={"day": (last + timedelta(days=1)).isoformat(), "complied": True},
        headers=headers,
    )
    assert before.status_code == 422
    assert before.json()["error_code"] == "VALIDATION"
    assert after.status_code == 422


@requires_db
async def test_checkin_requires_auth_and_known_experiment(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=5)
    unauthenticated = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin", json={"complied": True}
    )
    assert unauthenticated.status_code == 401

    unknown = experiments_client.post(
        f"{EXPERIMENTS}/{uuid.uuid4()}/checkin",
        json={"complied": True},
        headers=await _auth_headers(),
    )
    assert unknown.status_code == 404


# ── completion: evaluate() over COMPLIED days, verdict wording (§82) ──────


async def _seed_full_window(started_days_ago: int = 20) -> None:
    """avg_hrv over the whole window using the exact ramp vectors: baseline
    [47..53], intervention [57..63] → d = 10/sqrt(14/3), t = 5*sqrt(3)."""
    from somatriq_db.engine import get_session_factory

    first, _, _ = _window(started_days_ago, 7, 7)
    baseline_days = [first + timedelta(days=i) for i in range(7)]
    intervention_days = [first + timedelta(days=7 + i) for i in range(7)]
    async with get_session_factory()() as session:
        await _seed_metric(
            session,
            zip(
                baseline_days + intervention_days,
                RAMP_BASELINE + RAMP_INTERVENTION,
                strict=True,
            ),
        )


@requires_db
async def test_complete_returns_the_hand_derived_evaluation(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    await _seed_full_window()

    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/complete", headers=await _auth_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None

    evaluation = body["evaluation"]
    assert evaluation["evaluation_version"] == "experiment_eval_v1"
    assert evaluation["verdict"] == "consistent with effect"
    assert evaluation["n_baseline"] == 7
    assert evaluation["n_intervention"] == 7
    assert evaluation["mean_baseline"] == pytest.approx(50.0)
    assert evaluation["mean_intervention"] == pytest.approx(60.0)
    assert evaluation["mean_difference"] == pytest.approx(10.0)
    assert evaluation["cohens_d"] == pytest.approx(10.0 / (14.0 / 3.0) ** 0.5, rel=1e-4)
    assert evaluation["welch_t"] == pytest.approx(5.0 * 3.0**0.5, rel=1e-4)
    assert evaluation["welch_df"] == 12
    assert evaluation["p_value"] < 0.05
    assert evaluation["excluded_noncomplied"] == 0
    assert evaluation["caveat"] == CAVEAT
    # The persisted row carries status only — evaluation is computed on read.
    status = await db.execute(
        text("SELECT status FROM research.experiments WHERE id = :id"),
        {"id": experiment_id},
    )
    assert status.scalar_one() == "completed"


@requires_db
async def test_detail_recomputes_the_evaluation_live(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    await _seed_full_window()
    completed = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/complete", headers=await _auth_headers()
    )
    assert completed.status_code == 200

    detail = experiments_client.get(f"{EXPERIMENTS}/{experiment_id}")
    assert detail.status_code == 401  # reads need the account JWT (§122)
    detail = experiments_client.get(
        f"{EXPERIMENTS}/{experiment_id}", headers=await _auth_headers()
    )
    assert detail.status_code == 200
    evaluation = detail.json()["evaluation"]
    assert evaluation is not None
    assert evaluation["verdict"] == "consistent with effect"
    assert evaluation["cohens_d"] == pytest.approx(10.0 / (14.0 / 3.0) ** 0.5, rel=1e-4)


@requires_db
async def test_noncomplied_days_are_excluded_and_counted(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    """Two 'no' days in the intervention phase drop n to 5 — below the
    7-day floor — and the verdict collapses to inconclusive; the days are
    reported as excluded, never silently dropped."""
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    await _seed_full_window()
    headers = await _auth_headers()
    first, _, _ = _window(20, 7, 7)
    for offset in (7, 8):  # first two intervention days
        response = experiments_client.post(
            f"{EXPERIMENTS}/{experiment_id}/checkin",
            json={"day": (first + timedelta(days=offset)).isoformat(), "complied": False},
            headers=headers,
        )
        assert response.status_code == 200

    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/complete", headers=headers
    )
    evaluation = response.json()["evaluation"]
    assert evaluation["n_intervention"] == 5
    assert evaluation["n_intervention"] < MIN_DAYS_PER_PHASE
    assert evaluation["excluded_noncomplied"] == 2
    assert evaluation["verdict"] == "inconclusive"


@requires_db
async def test_complete_without_metric_data_is_inconclusive(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/complete", headers=await _auth_headers()
    )
    assert response.status_code == 200
    evaluation = response.json()["evaluation"]
    assert evaluation["verdict"] == "inconclusive"
    assert evaluation["mean_baseline"] is None
    assert evaluation["p_value"] is None


@requires_db
async def test_complete_requires_auth_and_known_experiment(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    unauthenticated = experiments_client.post(f"{EXPERIMENTS}/{experiment_id}/complete")
    assert unauthenticated.status_code == 401

    unknown = experiments_client.post(
        f"{EXPERIMENTS}/{uuid.uuid4()}/complete", headers=await _auth_headers()
    )
    assert unknown.status_code == 404


@requires_db
async def test_checkin_on_completed_experiment_is_422(
    experiments_client: TestClient, db: AsyncSession
) -> None:
    experiment_id = await _seed_experiment(db, started_days_ago=20)
    headers = await _auth_headers()
    completed = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/complete", headers=headers
    )
    assert completed.status_code == 200

    response = experiments_client.post(
        f"{EXPERIMENTS}/{experiment_id}/checkin",
        json={"day": _today().isoformat(), "complied": True},
        headers=headers,
    )
    assert response.status_code == 422
