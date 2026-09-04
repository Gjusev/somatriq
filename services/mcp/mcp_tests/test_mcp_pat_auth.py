"""PAT authentication on the MCP endpoint (spec §123, §97; ADR 0010).

Covers the mint → verify happy path and every rejection class with its flat
api-shaped error body: unknown/expired token → 401 AUTHENTICATION, revoked
token → 403 AUTHORIZATION, missing scope → 403 AUTHORIZATION. Runs only when
the test database is reachable.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from helpers import call_tool, post_mcp
from helpers import requires_db as _helpers_requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette

# mypy cannot resolve the non-package helpers module, so its imports arrive
# as Any; re-bind the marker with its runtime type (same trick as api tests).
requires_db = cast(pytest.MarkDecorator, _helpers_requires_db)

_TOKEN_RE = re.compile(r"^sqt_pat_[A-Za-z0-9_-]{43}$")


@requires_db
async def test_mint_then_verify_happy_path(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import pats

    user_id, _device_id = user_device_ids
    minted = await pats.mint_pat(db, user_id, "laptop-claude")

    assert _TOKEN_RE.match(minted.token)
    assert minted.row.scopes == ["health.read"]
    assert minted.row.last_used_at is None
    assert minted.row.expires_at is None
    assert minted.row.revoked_at is None

    verified = await pats.verify_pat(db, minted.token)
    assert verified.user_id == user_id
    assert "health.read" in verified.scopes
    assert verified.row.last_used_at is not None  # bumped on use (spec §123)


@requires_db
async def test_wrong_token_is_flat_401(app: Starlette) -> None:
    for token in ("sqt_pat_" + "x" * 43, "sqt_dev_notapat", None):
        response = await post_mcp(app, token)
        assert response.status_code == 401
        body = response.json()
        assert body["error_code"] == "AUTHENTICATION"
        assert body["message"]
        assert body["details"] == []
        assert response.headers["www-authenticate"].startswith("Bearer")


@requires_db
async def test_expired_token_is_401(
    db: AsyncSession, app: Starlette, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import pats

    user_id, _ = user_device_ids
    minted = await pats.mint_pat(db, user_id, "old-token")
    await db.execute(
        text(
            "UPDATE identity.personal_access_tokens "
            "SET expires_at = :past WHERE id = :id"
        ),
        {"past": datetime.now(UTC) - timedelta(days=1), "id": minted.row.id},
    )
    await db.commit()

    response = await post_mcp(app, minted.token)
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


@requires_db
async def test_revoked_token_is_flat_403(
    db: AsyncSession, app: Starlette, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import pats

    user_id, _ = user_device_ids
    minted = await pats.mint_pat(db, user_id, "revoked-token")
    await db.execute(
        text(
            "UPDATE identity.personal_access_tokens "
            "SET revoked_at = now() WHERE id = :id"
        ),
        {"id": minted.row.id},
    )
    await db.commit()

    response = await post_mcp(app, minted.token)
    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == "AUTHORIZATION"
    assert body["message"] == "token revoked"
    assert body["details"] == []


@requires_db
async def test_missing_scope_is_flat_403(
    db: AsyncSession, app: Starlette, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import pats

    user_id, _ = user_device_ids
    minted = await pats.mint_pat(db, user_id, "narrow", scopes=("other.read",))

    response = await post_mcp(app, minted.token)
    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == "AUTHORIZATION"
    assert "health.read" in body["message"]


@requires_db
async def test_authenticated_tool_call_succeeds(
    db: AsyncSession, app: Starlette, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Full-stack proof: Bearer PAT → MCP handshake → tool result envelope."""
    from somatriq_mcp import pats

    user_id, _ = user_device_ids
    minted = await pats.mint_pat(db, user_id, "claude-desktop")

    result = await call_tool(minted.token, "get_daily_summary", {"days": 2})
    assert set(result) == {"data", "coverage", "sources", "quality", "caveats", "generated_at"}
    assert len(result["data"]["days"]) == 2


@requires_db
async def test_unauthenticated_tool_call_rejected_before_protocol(app: Starlette) -> None:
    """The gate runs before any MCP handling — a missing bearer never reaches
    the streamable-http session manager."""
    response = await post_mcp(app, None)
    assert response.status_code == 401
