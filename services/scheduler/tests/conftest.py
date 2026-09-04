"""Scheduler service test fixtures.

Integration tests need the migrated TimescaleDB (brief outbox
constraints and the TODAY assembler's SQL are the system under test). Tests
auto-skip when the database is unreachable (CI unit job has no DB).
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://somatriq:test@127.0.0.1:5433/somatriq_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import

from somatriq_db.engine import get_engine, get_session_factory  # noqa: E402


def _database_available() -> bool:
    import asyncpg

    dsn = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn, timeout=2)
        await conn.close()

    try:
        asyncio.run(_probe())
    except Exception:  # noqa: BLE001 - availability probe must never raise
        return False
    return True


DB_AVAILABLE = _database_available()

requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="test database not available")


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
    """Per-test reset: every table this service writes or reads."""
    factory = get_session_factory()
    async with factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE TABLE notifications.outbox, notifications.channels, "
                "health.journal_events, "
                "timeseries.heart_rate, timeseries.rr_interval, "
                "health.sleep_stages, health.sleep_sessions, health.daily_observations, "
                "derived.daily_features"
            )
        )
        await cleanup.commit()
    async with factory() as session:
        yield session
