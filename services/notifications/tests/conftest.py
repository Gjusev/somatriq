"""Notifications service test fixtures.

Integration tests need the migrated TimescaleDB (outbox/channels state
transitions are the system under test). Tests auto-skip when the database
is unreachable (CI unit job has no DB).
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parents[3]
from somatriq_db.testing import (  # noqa: E402 — replaces the per-dir copy
    TEST_DATABASE_URL,
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import

from somatriq_db.engine import get_engine, get_session_factory  # noqa: E402


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
