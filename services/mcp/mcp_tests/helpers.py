"""Shared test plumbing that test modules must import by name (spec §161).

Everything a test file needs to IMPORT (as opposed to have injected by
conftest.py fixtures): the DB-availability skip marker and the raw-transport
helpers. Lives in its own uniquely named module — never ``conftest`` — so it
cannot collide with the api test suite's top-level conftest module in
``sys.modules`` (pytest's own conftest loading is path-keyed; direct imports
are not).
"""

import asyncio
import json
import os
from pathlib import Path

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from starlette.applications import Starlette

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://somatriq:test@127.0.0.1:5433/somatriq_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # before somatriq_db/engine import


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


async def call_tool(
    token: str, name: str, arguments: dict[str, object] | None = None
) -> dict[str, object]:
    """Drive one tool call through the real MCP streamable-http transport.

    A fresh app is built per call: the streamable session manager can only
    run once per instance. Its lifespan is entered and exited inside this
    coroutine — the anyio cancel scope must not cross task boundaries, so it
    cannot live in a pytest-asyncio async fixture (setup and teardown run as
    separate tasks).
    """
    from somatriq_mcp.main import create_app

    application: Starlette = create_app()
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with (
            httpx2.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
            streamable_http_client("http://testserver/mcp", http_client=http) as (
                read_stream,
                write_stream,
            ),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments or {})
            first = result.content[0]
            assert isinstance(first, TextContent), f"expected text content, got {type(first)}"
            return dict(json.loads(first.text))


async def post_mcp(application: Starlette, token: str | None) -> httpx2.Response:
    """Raw POST /mcp with an optional bearer — for flat auth-failure bodies."""
    transport = httpx2.ASGITransport(app=application)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
        return await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
        )
