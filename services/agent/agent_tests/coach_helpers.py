"""Shared test plumbing imported by name (never ``conftest``) — the
mcp_tests/agent_tests unique-directory lesson: importing ``conftest``
directly is not path-keyed, so shared names must live in uniquely named
modules.
"""

import asyncio
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://somatriq:test@127.0.0.1:5433/somatriq_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import

AI_ENV_KEYS = (
    "AI_PRIVACY_LEVEL",
    "AI_ALLOW_EXTERNAL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "USER_TIMEZONE",
)


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
