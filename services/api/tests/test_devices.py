"""Device management: list + revoke (spec §44-45, §123-124; ADR 0015).

Revocation is observable end-to-end: the revoked token's next ingest write
gets 403 DEVICE_REVOKED, never a silent acceptance.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from conftest import requires_db as _untyped_requires_db
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

DEVICES = "/api/v1/devices"
INGEST = "/api/v1/ingest/batches"
BASE_TS = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the test loop; pool drained across loop boundaries."""
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


async def _account_headers(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _pair_device(
    api: httpx.AsyncClient, headers: dict[str, str], name: str = "pixel-9"
) -> dict[str, object]:
    session = await api.post("/api/v1/pairing/sessions", headers=headers)
    assert session.status_code == 200, session.text
    confirmed = await api.post(
        "/api/v1/pairing/confirm",
        json={"pairing_code": session.json()["pairing_code"], "device_name": name},
    )
    assert confirmed.status_code == 200, confirmed.text
    return dict(confirmed.json())


def _batch_body() -> dict[str, object]:
    return {
        "batch_id": str(uuid.uuid4()),
        "schema_version": "1",
        "records": [
            {
                "source_record_id": f"dev-{uuid.uuid4().hex[:8]}-{n}",
                "ts": (BASE_TS + timedelta(seconds=n)).isoformat(),
                "bpm": 60.0 + n,
            }
            for n in range(1)
        ],
    }


@requires_db
async def test_list_includes_seed_device_with_nulls_and_paired_device(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """The tokenless M1 seed device lists with nulls; the paired one fully."""
    headers = await _account_headers(api)
    paired = await _pair_device(api, headers)

    # A successful device-token ingest stamps last_used_at.
    ingest = await api.post(
        INGEST, json=_batch_body(), headers={"Authorization": f"Bearer {paired['token']}"}
    )
    assert ingest.status_code == 200, ingest.text

    listed = await api.get(DEVICES, headers=headers)
    assert listed.status_code == 200
    devices = listed.json()
    assert len(devices) == 2

    by_id = {device["device_id"]: device for device in devices}
    seed = by_id.pop(next(iter(set(by_id) - {paired["device_id"]})))
    assert seed["name"] == "synthetic-01"  # M1 seed device, no tokens → nulls
    assert seed["last_used_at"] is None
    assert seed["revoked_at"] is None

    mine = by_id[paired["device_id"]]
    assert mine["name"] == "pixel-9"
    assert mine["model"] == "noop-android"
    assert mine["last_used_at"] is not None
    assert mine["revoked_at"] is None


@requires_db
async def test_revoke_blocks_ingest_with_device_revoked(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = await _account_headers(api)
    paired = await _pair_device(api, headers, name="watch-1")
    token = str(paired["token"])
    device_id = str(paired["device_id"])

    ok = await api.post(INGEST, json=_batch_body(), headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    revoked = await api.post(f"{DEVICES}/{device_id}/revoke", headers=headers)
    assert revoked.status_code == 204

    blocked = await api.post(
        INGEST, json=_batch_body(), headers={"Authorization": f"Bearer {token}"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "DEVICE_REVOKED"

    # The web still sees the device, now with its revoked token surfaced.
    listed = await api.get(DEVICES, headers=headers)
    mine = next(d for d in listed.json() if d["device_id"] == device_id)
    assert mine["revoked_at"] is not None


@requires_db
async def test_revoke_unknown_device_is_404(api: httpx.AsyncClient) -> None:
    headers = await _account_headers(api)
    response = await api.post(
        f"{DEVICES}/{uuid.uuid4()}/revoke", headers=headers
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "DEVICE_NOT_FOUND"


@requires_db
async def test_unknown_device_token_is_401(api: httpx.AsyncClient) -> None:
    response = await api.post(
        INGEST, json=_batch_body(), headers={"Authorization": "Bearer sqt_dev_nope"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"
