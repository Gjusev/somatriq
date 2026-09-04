"""Correlation endpoints (spec §81-82, §203 M10; ADR 0009).

Integration tests against the migrated TimescaleDB. The system under test is
the unified metric-series loading (derived features / vendor observations /
journal counts), the date alignment (same-day and lagged), and the honest
wire shapes: every result carries its method and the causal-language note,
and insufficient overlap is a VALID null result, never an error and never a
made-up coefficient.
"""

import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_analytics.correlation_data import MATRIX_METRICS
from somatriq_analytics.correlations import CAUSAL_LANGUAGE_NOTE, ZERO_VARIANCE_REASON
from somatriq_api.correlations import router as correlations_router
from somatriq_api.errors import ApiError
from somatriq_db.engine import get_session
from somatriq_db.models import Device, User
from somatriq_db.testing import TEST_DATABASE_URL, requires_db
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

UTC_TZ = ZoneInfo("UTC")
N_MATRIX_PAIRS = len(MATRIX_METRICS) * (len(MATRIX_METRICS) - 1) // 2


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    from somatriq_db.engine import get_engine

    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def correlations_client(db: AsyncSession) -> Iterator[TestClient]:
    """App with only the correlations router (plus the flat ApiError body),
    served through a per-test engine — mirrors test_daily.py."""
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
    app.include_router(correlations_router)
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client


def _today() -> date:
    return datetime.now(UTC).date()


def _last_days(n: int) -> list[date]:
    today = _today()
    return [today - timedelta(days=n - 1 - i) for i in range(n)]


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    return user_id, device_id


async def _seed_observations(
    db: AsyncSession, metric: str, values: Iterable[tuple[date, float]]
) -> None:
    user_id, device_id = await _ids(db)
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "day": day,
            "metric": metric,
            "value": value,
        }
        for day, value in values
    ]
    await db.execute(
        text(
            "INSERT INTO health.daily_observations "
            "(user_id, device_id, day, metric, value) "
            "VALUES (:user_id, :device_id, :day, :metric, :value)"
        ),
        rows,
    )
    await db.commit()


async def _seed_resting_hr(
    db: AsyncSession, values: Iterable[tuple[date, float]]
) -> None:
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


async def _seed_caffeine(db: AsyncSession, counts: Iterable[tuple[date, int]]) -> None:
    user_id, _ = await _ids(db)
    rows = []
    for day, count in counts:
        noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
        for j in range(count):
            rows.append(
                {
                    "user_id": user_id,
                    "source": "telegram",
                    "kind": "caffeine",
                    "ts": noon + timedelta(minutes=j),
                    "text": "coffee",
                }
            )
    await db.execute(
        text(
            "INSERT INTO health.journal_events (user_id, source, kind, ts, text) "
            "VALUES (:user_id, :source, :kind, :ts, :text)"
        ),
        rows,
    )
    await db.commit()


# ── /pair ────────────────────────────────────────────────────────────────


@requires_db
async def test_pair_unknown_metric_is_422_validation(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    await _seed_observations(db, "avg_hrv", [(_last_days(30)[-1], 50.0)])
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "not_a_metric", "metric_b": "avg_hrv"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION"
    assert "unknown metric" in body["message"]


@requires_db
async def test_pair_exact_positive_spearman_default(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(30)
    await _seed_observations(db, "avg_hrv", [(d, 40.0 + i * 0.5) for i, d in enumerate(days)])
    await _seed_observations(
        db, "recovery", [(d, 0.6 * (40.0 + i * 0.5) + 10.0) for i, d in enumerate(days)]
    )
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "avg_hrv", "metric_b": "recovery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "spearman"  # robustness default, documented
    assert body["n"] == 30
    assert body["r"] == pytest.approx(1.0, abs=1e-9)
    assert body["band"] == "strong"
    assert body["significant"] is True
    assert body["p_value"] == pytest.approx(0.0, abs=1e-9)
    assert "student-t" in body["p_method"]
    assert body["note"] == CAUSAL_LANGUAGE_NOTE
    assert body["alpha"] == pytest.approx(0.05)


@requires_db
async def test_pair_method_pearson_selected(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(20)
    await _seed_observations(db, "avg_hrv", [(d, float(i + 1)) for i, d in enumerate(days)])
    await _seed_observations(db, "recovery", [(d, 3.0 * (i + 1)) for i, d in enumerate(days)])
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "avg_hrv", "metric_b": "recovery", "method": "pearson"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "pearson"
    assert body["r"] == pytest.approx(1.0, abs=1e-9)


@requires_db
async def test_pair_insufficient_overlap_is_a_valid_null(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(5)
    await _seed_observations(db, "avg_hrv", [(d, float(i + 1)) for i, d in enumerate(days)])
    await _seed_observations(db, "recovery", [(d, float(i + 1)) for i, d in enumerate(days)])
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "avg_hrv", "metric_b": "recovery"},
    )
    assert response.status_code == 200  # honest null, not an error
    body = response.json()
    assert body["result"] is None
    assert body["reason"] == "insufficient overlap (n=5, need >= 14)"
    assert body["note"] == CAUSAL_LANGUAGE_NOTE


@requires_db
async def test_pair_zero_variance_is_a_valid_null(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(20)
    await _seed_observations(db, "strain", [(d, 55.0) for d in days])
    await _seed_observations(db, "recovery", [(d, float(i + 1)) for i, d in enumerate(days)])
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "strain", "metric_b": "recovery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] is None
    assert body["reason"] == ZERO_VARIANCE_REASON


@requires_db
async def test_pair_lag_alignment_next_day(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    """b[t] = a[t-1] exactly: lag=+1 (a today vs b tomorrow) re-pairs
    identical values → r = 1; the same-day join cannot."""
    days = _last_days(30)
    a_values = [40.0 + 10.0 * (i % 4) + i * 0.01 for i in range(30)]
    await _seed_observations(db, "avg_hrv", list(zip(days, a_values, strict=True)))
    b_values = [(days[i], a_values[i - 1]) for i in range(1, 30)]
    await _seed_observations(db, "recovery", b_values)

    lagged = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "avg_hrv", "metric_b": "recovery", "lag": 1},
    )
    assert lagged.status_code == 200
    body = lagged.json()
    assert body["n"] == 29
    assert body["r"] == pytest.approx(1.0, abs=1e-9)

    same_day = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "avg_hrv", "metric_b": "recovery"},
    )
    assert same_day.status_code == 200
    assert cast(float, same_day.json()["r"]) < 0.99


@requires_db
async def test_pair_journal_vs_computed_metric(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    """caffeine_count (journal) vs our resting_hr (derived features) — the
    three-source unification in one pair."""
    days = _last_days(20)
    await _seed_resting_hr(db, [(d, 60.0 - 0.2 * i) for i, d in enumerate(days)])
    await _seed_caffeine(db, [(d, i % 5 + 1) for i, d in enumerate(days)])
    response = correlations_client.get(
        "/api/v1/correlations/pair",
        params={"metric_a": "caffeine_count", "metric_b": "resting_hr", "days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 20
    assert body["r"] < 0  # caffeine up, resting down in the fixture


@requires_db
async def test_pair_rejects_bad_method_and_days(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    base = {"metric_a": "avg_hrv", "metric_b": "recovery"}
    assert (
        correlations_client.get(
            "/api/v1/correlations/pair", params={**base, "method": "kendall"}
        ).status_code
        == 422
    )
    assert (
        correlations_client.get(
            "/api/v1/correlations/pair", params={**base, "days": 10}
        ).status_code
        == 422
    )
    assert (
        correlations_client.get(
            "/api/v1/correlations/pair", params={**base, "lag": 99}
        ).status_code
        == 422
    )


# ── /matrix ──────────────────────────────────────────────────────────────


@requires_db
async def test_matrix_pairs_sorted_with_bonferroni_and_skipped(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    days = _last_days(40)
    hrv = [(d, 40.0 + i * 0.5) for i, d in enumerate(days)]
    await _seed_observations(db, "avg_hrv", hrv)
    await _seed_observations(
        db, "recovery", [(d, 100.0 - v) for d, v in hrv]  # perfect negative
    )
    await _seed_observations(db, "strain", [(d, 55.0) for d in days])  # constant
    await _seed_observations(db, "skin_temp_dev_c", [(d, 0.1) for d in days[:3]])

    response = correlations_client.get("/api/v1/correlations/matrix")
    assert response.status_code == 200
    body = response.json()

    assert body["method"] == "spearman"
    assert body["note"] == CAUSAL_LANGUAGE_NOTE
    pairs = body["pairs"]
    skipped = body["skipped"]

    # Sorted by |r| desc; ties are possible only at |r|=1 → stable order.
    magnitudes = [abs(row["r"]) for row in pairs]
    assert magnitudes == sorted(magnitudes, reverse=True)

    hrv_recovery = next(
        row for row in pairs if row["pair"] == ["avg_hrv", "recovery"]
    )
    assert hrv_recovery["r"] == pytest.approx(-1.0, abs=1e-9)
    assert hrv_recovery["band"] == "strong"
    assert hrv_recovery["n"] == 40

    # Multiple-comparisons honesty (spec §88).
    assert body["n_tests"] == len(pairs)
    assert body["bonferroni_alpha"] == pytest.approx(0.05 / len(pairs))
    for row in pairs:
        assert row["significant"] == (row["r"] is not None)

    # Every catalog pair appears exactly once, as a result or a skip.
    assert len(pairs) + len(skipped) == N_MATRIX_PAIRS
    seen = {tuple(row["pair"]) for row in pairs} | {tuple(row["pair"]) for row in skipped}
    assert len(seen) == N_MATRIX_PAIRS

    # Sparse and absent pairs are skipped WITH their reasons — honesty.
    skin_skip = next(row for row in skipped if row["pair"] == ["avg_hrv", "skin_temp_dev_c"])
    assert skin_skip["reason"] == "insufficient overlap (n=3, need >= 14)"
    absent_skip = next(row for row in skipped if row["pair"] == ["spo2_pct", "resp_rate_bpm"])
    assert absent_skip["reason"] == "insufficient overlap (n=0, need >= 14)"
    flat_skip = next(row for row in skipped if row["pair"] == ["recovery", "strain"])
    assert flat_skip["reason"] == ZERO_VARIANCE_REASON


@requires_db
async def test_matrix_empty_database_is_all_skipped(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    response = correlations_client.get("/api/v1/correlations/matrix")
    assert response.status_code == 200
    body = response.json()
    assert body["pairs"] == []
    assert len(body["skipped"]) == N_MATRIX_PAIRS
    assert body["n_tests"] == 0
    assert body["bonferroni_alpha"] == pytest.approx(0.05)
    assert body["note"] == CAUSAL_LANGUAGE_NOTE


@requires_db
async def test_matrix_days_window_bounds_the_series(
    correlations_client: TestClient, db: AsyncSession
) -> None:
    """Days outside the requested window never enter the join (n counts
    only in-window shared days)."""
    days = _last_days(30)
    await _seed_observations(db, "avg_hrv", [(d, float(i + 1)) for i, d in enumerate(days)])
    await _seed_observations(db, "recovery", [(d, float(i + 1)) for i, d in enumerate(days)])
    response = correlations_client.get(
        "/api/v1/correlations/matrix", params={"days": 20}
    )
    assert response.status_code == 200
    row = next(
        r for r in response.json()["pairs"] if r["pair"] == ["avg_hrv", "recovery"]
    )
    assert row["n"] == 20
