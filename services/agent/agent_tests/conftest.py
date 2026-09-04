"""Agent service test fixtures (coach DB tests share the api/mcp container).

This conftest lives in ``agent_tests/`` so its mypy module path stays
unique — the api suite owns ``tests.conftest`` (M9 mcp_tests lesson).
"""

import os
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from coach_helpers import AI_ENV_KEYS, REPO_ROOT, TEST_DATABASE_URL
from somatriq_db.engine import get_engine, get_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic provider matrix: no ambient AI configuration leaks in."""
    for key in AI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


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
    """Migrated database, truncated between tests; synthetic device re-seeded."""
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
async def user_id(db: AsyncSession) -> UUID:
    result = await db.execute(
        text(
            "SELECT u.id FROM identity.users u "
            "JOIN identity.devices d ON d.user_id = u.id "
            "WHERE d.name = 'synthetic-01' LIMIT 1"
        )
    )
    return UUID(str(result.one()[0]))
