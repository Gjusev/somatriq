"""Pairing flow: issue → confirm → status polling (spec §43, §180; ADR 0015).

The code is returned exactly once and stored hashed; the collector's token is
returned exactly once and stored hashed; unknown vs consumed codes answer
identically so the endpoint leaks nothing.
"""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast
from uuid import UUID

import httpx
import pytest
from conftest import requires_db as _untyped_requires_db
from somatriq_api.main import app
from somatriq_contracts.pairing import (
    DEVICE_SCOPES,
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
)
from somatriq_db.engine import get_engine
from somatriq_db.models import DeviceToken, PairingSession
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

SESSIONS = "/api/v1/pairing/sessions"
CONFIRM = "/api/v1/pairing/confirm"
INGEST = "/api/v1/ingest/batches"


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the test loop; pool drained across loop boundaries."""
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


@pytest.fixture()
async def account_headers(api: httpx.AsyncClient) -> dict[str, str]:
    """Registered account → Authorization header for the web surfaces."""
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_session(api: httpx.AsyncClient, headers: dict[str, str]) -> dict[str, object]:
    response = await api.post(SESSIONS, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _expire_session(db: AsyncSession, session_id: UUID) -> None:
    """Fast-forward one session past its TTL without touching the clock."""
    await db.execute(
        update(PairingSession)
        .where(PairingSession.id == session_id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()


@requires_db
async def test_created_code_shape_and_ttl(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    body = await _create_session(api, account_headers)

    code = str(body["pairing_code"])
    assert len(code) == PAIRING_CODE_LENGTH
    assert all(char in PAIRING_CODE_ALPHABET for char in code)

    expires_at = datetime.fromisoformat(str(body["expires_at"]))
    remaining = expires_at - datetime.now(UTC)
    assert timedelta(minutes=9, seconds=30) < remaining <= timedelta(minutes=10)


@requires_db
async def test_confirm_happy_path_mints_scoped_token(
    api: httpx.AsyncClient, account_headers: dict[str, str], db: AsyncSession
) -> None:
    session_body = await _create_session(api, account_headers)

    confirmed = await api.post(
        CONFIRM,
        json={"pairing_code": session_body["pairing_code"], "device_name": "pixel-9"},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["token"].startswith("sqt_dev_")
    assert payload["scopes"] == list(DEVICE_SCOPES)
    assert payload["token_type"] == "device"

    # Only the hash is stored; the session is consumed and bound to the device.
    stored = (
        await db.execute(select(DeviceToken).where(DeviceToken.device_id == payload["device_id"]))
    ).scalar_one()
    from somatriq_api.accounts import hash_device_token

    assert stored.token_hash == hash_device_token(payload["token"])
    assert stored.scopes == list(DEVICE_SCOPES)
    pairing = (
        await db.execute(
            select(PairingSession).where(PairingSession.id == session_body["session_id"])
        )
    ).scalar_one()
    assert pairing.status == "consumed"
    assert pairing.consumed_at is not None
    assert pairing.device_id is not None


@requires_db
async def test_confirm_normalizes_lowercase_code(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    """Users type lowercase; the hash is computed over the normalized code."""
    session_body = await _create_session(api, account_headers)

    confirmed = await api.post(
        CONFIRM,
        json={
            "pairing_code": str(session_body["pairing_code"]).lower(),
            "device_name": "pixel-9",
        },
    )
    assert confirmed.status_code == 200, confirmed.text


@requires_db
async def test_expired_code_is_410(
    api: httpx.AsyncClient, account_headers: dict[str, str], db: AsyncSession
) -> None:
    session_body = await _create_session(api, account_headers)
    await _expire_session(db, UUID(str(session_body["session_id"])))

    response = await api.post(
        CONFIRM, json={"pairing_code": session_body["pairing_code"], "device_name": "pixel-9"}
    )
    assert response.status_code == 410
    assert response.json()["error_code"] == "PAIRING_CODE_EXPIRED"


@requires_db
async def test_reused_code_is_404_like_unknown(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    session_body = await _create_session(api, account_headers)
    first = await api.post(
        CONFIRM, json={"pairing_code": session_body["pairing_code"], "device_name": "pixel-9"}
    )
    assert first.status_code == 200

    reuse = await api.post(
        CONFIRM, json={"pairing_code": session_body["pairing_code"], "device_name": "pixel-9b"}
    )
    wrong = await api.post(
        CONFIRM, json={"pairing_code": "WWWWWWWW", "device_name": "pixel-9c"}
    )
    assert reuse.status_code == 404
    assert reuse.json()["error_code"] == "PAIRING_CODE_INVALID"
    assert wrong.status_code == 404
    assert wrong.json()["error_code"] == "PAIRING_CODE_INVALID"


@requires_db
async def test_status_polling_hint_pending_then_device_consumed(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    session_body = await _create_session(api, account_headers)
    session_id = str(session_body["session_id"])
    code = str(session_body["pairing_code"])

    pending = await api.get(f"{SESSIONS}/{session_id}", headers=account_headers)
    assert pending.status_code == 200
    pending_body = pending.json()
    assert pending_body["status"] == "pending"
    assert pending_body["pairing_code_hint"] == code[:4]
    assert pending_body["device"] is None

    confirmed = await api.post(CONFIRM, json={"pairing_code": code, "device_name": "pixel-9"})
    assert confirmed.status_code == 200
    device_id = confirmed.json()["device_id"]

    consumed = await api.get(f"{SESSIONS}/{session_id}", headers=account_headers)
    assert consumed.status_code == 200
    consumed_body = consumed.json()
    assert consumed_body["status"] == "consumed"
    assert consumed_body["device"] == {
        "device_id": device_id,
        "name": "pixel-9",
        "model": "noop-android",
    }


@requires_db
async def test_status_of_foreign_session_is_404(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    """One account cannot poll another account's session by UUID."""
    created = await _create_session(api, account_headers)

    stranger_headers = dict(account_headers)
    stranger_headers["Authorization"] = "Bearer not-a-jwt"
    response = await api.get(f"{SESSIONS}/{created['session_id']}", headers=stranger_headers)
    assert response.status_code == 401  # garbage JWT rejected before ownership

    unknown = await api.get(
        f"{SESSIONS}/00000000-0000-4000-8000-00000000dead", headers=account_headers
    )
    assert unknown.status_code == 404
    assert unknown.json()["error_code"] == "PAIRING_SESSION_NOT_FOUND"


@requires_db
async def test_session_requires_account_jwt(api: httpx.AsyncClient) -> None:
    response = await api.post(SESSIONS)
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"
