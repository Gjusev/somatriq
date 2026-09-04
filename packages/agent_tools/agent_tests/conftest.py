"""agent_tools test fixtures.

Integration tests run against the same local TimescaleDB container the api
and mcp suites share (127.0.0.1:5433) and auto-skip when it is down. This
conftest lives in ``agent_tests/`` (not ``tests/``) so its mypy module path
stays unique — the api suite owns ``tests.conftest`` and every mypy_path
base would otherwise resolve another ``tests/conftest.py`` to the same
module (the M9 mcp_tests lesson).
"""

import os
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from somatriq_db.engine import get_engine, get_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tool_helpers import REPO_ROOT, TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_db() -> None:
    cfg = Config(str(REPO_ROOT / "database" / "alembic.ini"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    """Drop pooled connections bound to other tests' (closed) event loops."""
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    """Migrated database, truncated between tests; synthetic device re-seeded.

    Mirrors the api/mcp conftest pattern, extended with the M7 tables the
    coach tools read (health.journal_events, notifications.*).
    """
    factory = get_session_factory()
    async with factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE TABLE timeseries.heart_rate, timeseries.rr_interval, "
                "health.sleep_stages, health.sleep_sessions, health.daily_observations, "
                "health.journal_events, notifications.outbox, notifications.channels, "
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
