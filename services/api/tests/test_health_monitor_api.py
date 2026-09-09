"""/api/v1/health-monitor behavior (Block 3): vitals vs personal baseline
with coverage and provenance; HRV falls back to vendor avg_hrv with a
caveat; the disclaimer rides every response; period values are validated.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MONITOR = "/api/v1/health-monitor"


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


async def _seed_metric(
    db: AsyncSession, metric: str, first: date, last: date, low: float, high: float
) -> None:
    """Alternate low/high daily values over [first, last] — a stable IQR."""
    user_id, device_id = (
        (await db.execute(text("SELECT id FROM identity.users LIMIT 1"))).scalar_one(),
        (await db.execute(text("SELECT id FROM identity.devices LIMIT 1"))).scalar_one(),
    )
    day = first
    toggle = True
    while day <= last:
        await db.execute(
            text(
                "INSERT INTO health.daily_observations (user_id, device_id, day, metric, value) "
                "VALUES (:u, :d, :day, :metric, :value)"
            ),
            {
                "u": user_id,
                "d": device_id,
                "day": day,
                "metric": metric,
                "value": low if toggle else high,
            },
        )
        toggle = not toggle
        day += timedelta(days=1)
    await db.commit()


@requires_db
async def test_empty_data_degrades_with_disclaimer(api: httpx.AsyncClient) -> None:
    headers = await _register(api)
    response = await api.get(MONITOR, params={"days": 30}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {vital["vital"] for vital in body["vitals"]} == {
        "hrv",
        "resting_hr",
        "resp_rate_bpm",
        "spo2_pct",
        "skin_temp_dev_c",
    }
    assert all(vital["status"] in ("insufficient", "building") for vital in body["vitals"])
    assert "not a medical device" in body["disclaimer"]
    assert any("vendor avg_hrv" in caveat for caveat in body["caveats"])


@requires_db
async def test_elevated_vital_against_personal_baseline(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    today = datetime.now(UTC).date()
    period_first = today - timedelta(days=29)
    # Baseline centered 97/99 (IQR 2); period consistently 99 -> z ≈ +2.
    await _seed_metric(
        db,
        "spo2_pct",
        period_first - timedelta(days=90),
        period_first - timedelta(days=1),
        97.0,
        99.0,
    )
    await _seed_metric(db, "spo2_pct", period_first, today, 99.0, 99.0)
    headers = await _register(api)

    response = await api.get(MONITOR, params={"days": 30}, headers=headers)
    assert response.status_code == 200, response.text
    spo2 = next(v for v in response.json()["vitals"] if v["vital"] == "spo2_pct")
    assert spo2["status"] == "elevated"
    assert spo2["period_median"] == pytest.approx(99.0)
    assert spo2["baseline_median"] == pytest.approx(98.0)
    assert spo2["robust_z"] == pytest.approx(1.0)  # boundary: >= threshold elevates
    assert spo2["coverage"] == pytest.approx(1.0)
    assert spo2["source"] == "vendor daily"


@requires_db
async def test_period_validation_and_auth(api: httpx.AsyncClient) -> None:
    headers = await _register(api)
    bad = await api.get(MONITOR, params={"days": 45}, headers=headers)
    assert bad.status_code == 422, bad.text
    unauthenticated = await api.get(MONITOR)
    assert unauthenticated.status_code == 401, unauthenticated.text
