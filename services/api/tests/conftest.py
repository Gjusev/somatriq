"""Test fixtures.

Integration tests need a real TimescaleDB (unique constraints + time_bucket
behavior are the system under test). The standard setup is the local
container: docker run -d --name somatriq-test-pg -p 5433:5432
-e POSTGRES_USER=somatriq -e POSTGRES_PASSWORD=test -e POSTGRES_DB=somatriq_test
timescale/timescaledb-ha:pg16-ts2.17

Tests auto-skip when the database is unreachable (CI unit job has no DB).
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://somatriq:test@127.0.0.1:5433/somatriq_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import

from somatriq_db.engine import get_session_factory  # noqa: E402


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


@pytest.fixture()
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    # Reset before each test: failures leave no residue for the next test.
    async with factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE TABLE timeseries.heart_rate, ingest.failures, "
                "ingest.idempotency_keys, ingest.batches"
            )
        )
        await cleanup.commit()
    async with factory() as session:
        yield session


@pytest.fixture()
def client(db: AsyncSession) -> Iterator[TestClient]:
    """TestClient bound to the migrated test database."""
    from somatriq_api.main import app

    with TestClient(app) as test_client:
        yield test_client
