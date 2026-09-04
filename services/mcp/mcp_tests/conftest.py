"""MCP test fixtures.

Integration tests need a real TimescaleDB (time_bucket behavior, the daily
read-through cache and the PAT table are the system under test). The standard
setup is the local container the api tests share:

    docker run -d --name somatriq-test-pg -p 5433:5432 \
      -e POSTGRES_USER=somatriq -e POSTGRES_PASSWORD=test -e POSTGRES_DB=somatriq_test \
      timescale/timescaledb-ha:pg16-ts2.17

Tests auto-skip when the database is unreachable (CI unit job has no DB).

This conftest lives in ``mcp_tests/`` (not ``tests/``) so its mypy module
path stays unique per service — every mypy_path base resolves a ``tests``
directory to the same package name, and the api suite already owns
``tests.conftest``. Fixtures are injected; anything a test must import by
name lives in :mod:`helpers`.
"""

import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from helpers import REPO_ROOT, TEST_DATABASE_URL
from somatriq_db.engine import get_engine, get_session_factory
from somatriq_mcp.main import create_app
from somatriq_mcp.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette


@pytest.fixture(scope="session")
def migrated_db() -> None:
    cfg = Config(str(REPO_ROOT / "database" / "alembic.ini"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops.

    The engine in somatriq_db.engine is process-global and pools asyncpg
    connections on the loop that created them, while pytest-asyncio hands
    each test a fresh loop (same pattern as the api tests).
    """
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Per-test settings cache (USER_TIMEZONE default UTC unless overridden)."""
    monkeypatch.delenv("USER_TIMEZONE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    """Migrated database, truncated between tests; synthetic device re-seeded.

    Mirrors the api conftest: ephemeral tables truncated in one FK-safe
    statement (including the M6 observation tables and the M9 PAT table),
    pair-created devices/data_sources deleted, and the M1 synthetic seed
    re-created so seeding helpers have exactly one device to target.
    """
    factory = get_session_factory()
    async with factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE TABLE timeseries.heart_rate, timeseries.rr_interval, "
                "health.sleep_stages, health.sleep_sessions, health.daily_observations, "
                "derived.daily_features, "
                "ingest.failures, ingest.idempotency_keys, ingest.batches, "
                "raw.raw_batches, identity.device_tokens, "
                "identity.personal_access_tokens, "
                "identity.pairing_sessions, identity.account_credentials"
            )
        )
        await cleanup.execute(text("DELETE FROM identity.data_sources"))
        await cleanup.execute(text("DELETE FROM identity.devices"))
        await cleanup.execute(
            text(
                "INSERT INTO identity.devices (user_id, name, model) "
                "SELECT id, 'synthetic-01', 'synthetic' FROM identity.users "
                "ORDER BY created_at LIMIT 1"
            )
        )
        await cleanup.execute(
            text(
                "INSERT INTO identity.data_sources (device_id, provider, collector) "
                "SELECT id, 'synthetic', 'somatriq-mobile' FROM identity.devices "
                "WHERE name = 'synthetic-01'"
            )
        )
        await cleanup.commit()
    async with factory() as session:
        yield session


@pytest.fixture()
async def user_device_ids(db: AsyncSession) -> tuple[UUID, UUID]:
    """(user_id, device_id) of the seeded single user + synthetic device."""
    result = await db.execute(
        text(
            "SELECT u.id, d.id FROM identity.users u "
            "JOIN identity.devices d ON d.user_id = u.id "
            "WHERE d.name = 'synthetic-01' "
            "ORDER BY u.created_at LIMIT 1"
        )
    )
    row = result.one()
    return UUID(str(row[0])), UUID(str(row[1]))


@pytest.fixture()
async def app(db: AsyncSession) -> Starlette:
    """The MCP ASGI app (lifespan is driven per call — see helpers.call_tool)."""
    return create_app()
