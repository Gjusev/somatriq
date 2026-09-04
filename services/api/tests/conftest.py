"""Test fixtures.

Integration tests need a real TimescaleDB (unique constraints + time_bucket
behavior are the system under test). The standard setup is the local
container: docker run -d --name somatriq-test-pg -p 5433:5432
-e POSTGRES_USER=somatriq -e POSTGRES_PASSWORD=test -e POSTGRES_DB=somatriq_test
timescale/timescaledb-ha:pg16-ts2.17

Tests auto-skip when the database is unreachable (CI unit job has no DB).
"""

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
from somatriq_db.testing import (  # noqa: E402 — replaces the per-dir copy
    TEST_DATABASE_URL,
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import

from somatriq_api.settings import get_settings  # noqa: E402
from somatriq_db.engine import get_session_factory  # noqa: E402


@pytest.fixture(scope="session")
def migrated_db() -> None:
    cfg = Config(str(REPO_ROOT / "database" / "alembic.ini"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def raw_dir_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """Per-test SOMATRIQ_RAW_DIR (tmp) + stable SECRET_KEY, fresh settings cache."""
    raw_dir = tmp_path / "raw"
    monkeypatch.setenv("SOMATRIQ_RAW_DIR", str(raw_dir))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    yield raw_dir
    get_settings.cache_clear()


@pytest.fixture()
async def db(migrated_db: None) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    # Reset before each test: failures leave no residue for the next test.
    # Ephemeral tables are truncated (one FK-safe statement); pair-created
    # devices/data_sources are deleted and the M1 synthetic seed re-created,
    # because the single-user ingest fallback needs exactly one device.
    async with factory() as cleanup:
        await cleanup.execute(
            text(
                "TRUNCATE TABLE timeseries.heart_rate, timeseries.rr_interval, "
                "health.sleep_stages, health.sleep_sessions, health.daily_observations, "
                "health.journal_events, "
                "health.training_sets, health.training_sessions, "
                "research.experiment_days, research.experiments, "
                "derived.daily_features, "
                "ingest.failures, ingest.idempotency_keys, ingest.batches, "
                "raw.raw_batches, identity.device_tokens, "
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
def client(db: AsyncSession) -> Iterator[TestClient]:
    """TestClient bound to the migrated test database."""
    from somatriq_api.main import app

    with TestClient(app) as test_client:
        yield test_client
