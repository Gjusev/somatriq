"""Database readiness check.

Kept dependency-free for the M0 skeleton: /ready proves the service can reach
PostgreSQL on the private network. Real engine/session wiring lands with the
M1 tracer bullet (packages/somatriq-db).
"""

import os

import asyncpg


async def database_ready() -> tuple[bool, str]:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False, "DATABASE_URL not configured"
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=3)
    except Exception as exc:  # noqa: BLE001 - readiness must never raise
        return False, f"database unreachable: {type(exc).__name__}"
    else:
        await conn.close()
        return True, "reachable"
