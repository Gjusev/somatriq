"""Plan + preferences endpoint behavior (Block 1; ADR 0018/0019).

Router-slice apps (conftest §"router-slice apps"): auth guards are
overridden because these files test the READ/write SEMANTICS — the guard
contract itself lives in test_read_auth/test_auth. The full-app wiring is
exercised by importing main in test_observations-style suites.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_api import plan as plan_module
from somatriq_api.accounts import require_account_jwt
from somatriq_api.plan import router as plan_router
from somatriq_api.preferences import router as preferences_router
from somatriq_api.security import require_read_principal
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
PLAN_PATH = "/api/v1/plan/today"
PREFS_PATH = "/api/v1/preferences"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
def app_client(
    account_jwt_override: Callable[[], uuid.UUID],
) -> Iterator[tuple[TestClient, Callable[[str], None]]]:
    """Both routers on one slice app; the plan clock is freezable."""
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
    app.include_router(plan_router)
    app.include_router(preferences_router)
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[require_read_principal] = account_jwt_override
    app.dependency_overrides[require_account_jwt] = account_jwt_override
    with TestClient(app) as client:
        yield (
            client,
            lambda moment: pytest.MonkeyPatch().setattr(plan_module, "_now", lambda: moment),
        )


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = (await db.execute(select(User.id).limit(1))).scalar_one()
    device_id = (await db.execute(select(Device.id).limit(1))).scalar_one()
    return user_id, device_id


async def _seed_week(db: AsyncSession) -> None:
    from datetime import time as time_type

    user_id, device_id = await _ids(db)
    for i in range(7, -1, -1):
        wake = date(2026, 9, 9) - timedelta(days=i)
        end = datetime.combine(wake, time_type(7, 0), tzinfo=MADRID).astimezone(UTC)
        start = end - timedelta(minutes=480.0 if i else 420.0)
        await db.execute(
            text(
                "INSERT INTO health.sleep_sessions "
                "(user_id, device_id, source_record_id, start_ts, end_ts) "
                "VALUES (:u, :d, :srid, :start, :end)"
            ),
            {"u": user_id, "d": device_id, "srid": f"night-{i}", "start": start, "end": end},
        )
    await db.commit()


@requires_db
def test_plan_today_requires_auth() -> None:
    """No override wired here: the bare router rejects unauthenticated."""
    from fastapi.testclient import TestClient as TC

    bare = FastAPI()
    # Flat business errors need the app-level handler (spec §157), exactly
    # as main.py wires it — the bare router is otherwise unmounted behavior.
    from somatriq_api.errors import ApiError
    from somatriq_api.main import api_error_handler

    bare.add_exception_handler(ApiError, api_error_handler)
    bare.include_router(plan_router)
    with TC(bare) as client:
        assert client.get(PLAN_PATH).status_code == 401


@requires_db
def test_plan_today_degrades_honestly_on_empty_data(
    app_client: tuple[TestClient, Callable[[datetime], None]],
) -> None:
    client, freeze = app_client
    freeze(NOW)
    body = client.get(PLAN_PATH).json()
    assert body["sleep_need"]["minutes"] is None
    assert body["sleep_need"]["missing_inputs"] == ["sleep_baseline"]
    assert body["plan"]["tier"] is None
    assert body["plan"]["bedtime_window"] is None
    assert body["wake_source"] == "default"
    assert body["wake_time"].startswith("07:00")
    assert body["caveats"]


@pytest.fixture()
async def seeded_week(db: AsyncSession) -> None:
    await _seed_week(db)


@requires_db
def test_preferences_roundtrip_and_plan_anchor(
    app_client: tuple[TestClient, Callable[[datetime], None]],
    seeded_week: None,
) -> None:
    client, freeze = app_client
    assert client.get(PREFS_PATH).json() == {"wake_time": None}

    put = client.put(PREFS_PATH, json={"wake_time": "07:30"})
    assert put.status_code == 200
    assert put.json() == {"wake_time": "07:30:00"}
    assert client.get(PREFS_PATH).json() == {"wake_time": "07:30:00"}

    # The plan reads the preference through the same store.
    freeze(NOW)
    body = client.get(PLAN_PATH).json()
    assert body["wake_source"] == "preference"
    assert body["sleep_need"]["minutes"] == pytest.approx(510.0)
    assert body["plan"]["bedtime_window"]["start"] == "22:45:00"
    assert body["plan"]["bedtime_window"]["end"] == "23:15:00"


@requires_db
def test_preferences_reject_unknown_keys(
    app_client: tuple[TestClient, Callable[[datetime], None]],
) -> None:
    client, _ = app_client
    assert client.put(PREFS_PATH, json={"unknown_key": 1}).status_code == 422


@requires_db
def test_preferences_empty_update_is_noop(
    app_client: tuple[TestClient, Callable[[datetime], None]],
) -> None:
    client, _ = app_client
    assert client.put(PREFS_PATH, json={}).status_code == 200
    assert client.get(PREFS_PATH).json() == {"wake_time": None}
