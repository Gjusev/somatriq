"""Shared DB-test helpers, importable by package name (no bare `conftest`).

Every service's tests/conftest.py keeps its own fixtures, but the DSN, the
availability probe and the skip gate live HERE: multiple rootless test dirs
(api/tests, telegram/tests, agent_tests/…) all map a bare ``from conftest
import …`` to whichever conftest.py pytest happens to load first
(collection-order dependent — it broke when packages/agent_tests sorted
before services/api). Test modules import this module instead; conftests
re-export from it for their fixtures.

Only tests import this module — never production code.
"""

import asyncio
import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://somatriq:test@127.0.0.1:5433/somatriq_test",
)

# Tests must point the engine at the test DB before somatriq_db.engine is
# first used (the engine is lazy, so import order here is safe).
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)


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
