"""Device tokens as READ principals: data.read scope on the owner reads.

The Android dashboard client holds a device token (ADR 0015) — it must read
the owner's data with it, while the account JWT path stays byte-identical
(spec §122; test_read_auth.py pins its 401s). These tests pin the device
side: data.read grants the read, its absence is 403 AUTHORIZATION (ingest
keeps working — ingest.write is untouched), and garbage is 401.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.main import app
from somatriq_contracts.pairing import DEVICE_TOKEN_PREFIX
from somatriq_db.engine import get_engine
from somatriq_db.models import DeviceToken
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

TODAY = "/api/v1/metrics/today"
TRAINING_SESSIONS = "/api/v1/training/sessions"
INGEST = "/api/v1/ingest/batches"

# The owner reads a data.read device token unlocks (minimal valid params).
DEVICE_READ_PATHS: list[tuple[str, dict[str, str]]] = [
    ("/api/v1/metrics/today", {}),
    ("/api/v1/metrics/daily", {}),
    ("/api/v1/metrics/heart_rate", {}),
    ("/api/v1/sleep/sessions", {}),
    ("/api/v1/correlations/pair", {"metric_a": "resting_hr", "metric_b": "avg_hrv"}),
    ("/api/v1/correlations/matrix", {}),
    ("/api/v1/training/sessions", {}),
    ("/api/v1/training/response", {}),
    ("/api/v1/experiments", {}),
    ("/api/v1/notifications/recent", {}),
]


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the test loop; pool drained across loop boundaries."""
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


async def _register_account(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _pair_device(api: httpx.AsyncClient, account_headers: dict[str, str]) -> tuple[str, str]:
    """Full ADR 0015 dance → (device token, device_id); scopes per contract."""
    session = await api.post("/api/v1/pairing/sessions", headers=account_headers)
    assert session.status_code == 200, session.text
    confirmed = await api.post(
        "/api/v1/pairing/confirm",
        json={"pairing_code": session.json()["pairing_code"], "device_name": "pixel-9"},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["token"].startswith(DEVICE_TOKEN_PREFIX)
    return str(payload["token"]), str(payload["device_id"])


def _device_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _strip_data_read(db: AsyncSession, device_id: str) -> None:
    """Rewind one token to the pre-data.read scope set (migration 0010 state)."""
    await db.execute(
        update(DeviceToken)
        .where(DeviceToken.device_id == uuid.UUID(device_id))
        .values(scopes=["ingest.write", "device.read", "sync.read"])
    )
    await db.commit()


async def _seed_foreign_training_session(db: AsyncSession) -> None:
    """A second user with a training session today — invisible to the owner."""
    foreign_user = (
        await db.execute(
            text("INSERT INTO identity.users (display_name) VALUES ('foreign-user') RETURNING id")
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO health.training_sessions (user_id, ts, source) "
            "VALUES (:user_id, now(), 'api')"
        ),
        {"user_id": foreign_user},
    )
    await db.commit()


@requires_db
async def test_device_token_with_data_read_reads_owner_paths(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    account_headers = await _register_account(api)
    token, _ = await _pair_device(api, account_headers)

    for path, params in DEVICE_READ_PATHS:
        response = await api.get(path, params=params, headers=_device_headers(token))
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"
        assert "detail" not in response.json(), f"{path}: body must be flat"


@requires_db
async def test_device_token_read_is_scoped_to_owner(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """The principal is the token owner: another user's sessions never list."""
    account_headers = await _register_account(api)
    token, _ = await _pair_device(api, account_headers)
    await _seed_foreign_training_session(db)

    response = await api.get(TRAINING_SESSIONS, headers=_device_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sessions"] == [], f"foreign session leaked: {body}"
    assert body["weekly"] == []


@requires_db
async def test_device_token_detail_read_scoped_to_owner(api: httpx.AsyncClient) -> None:
    """Experiments detail answers the device principal — 404 for a foreign id
    (the guard passed; ownership filtered), never 401/403."""
    account_headers = await _register_account(api)
    token, _ = await _pair_device(api, account_headers)

    response = await api.get(f"/api/v1/experiments/{uuid.uuid4()}", headers=_device_headers(token))
    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "NOT_FOUND"


@requires_db
async def test_device_token_without_data_read_403_but_ingests(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    account_headers = await _register_account(api)
    token, device_id = await _pair_device(api, account_headers)
    await _strip_data_read(db, device_id)

    rejected = await api.get(TODAY, headers=_device_headers(token))
    assert rejected.status_code == 403, rejected.text
    body = rejected.json()
    assert body["error_code"] == "AUTHORIZATION", body
    assert "data.read" in body["message"], body

    # ingest.write is untouched: the same token still writes (existing path).
    record = {
        "source_record_id": "hr-1",
        "ts": "2026-09-07T10:00:00+00:00",
        "bpm": 58.0,
    }
    accepted = await api.post(
        INGEST,
        json={"batch_id": str(uuid.uuid4()), "records": [record]},
        headers=_device_headers(token),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True


@requires_db
async def test_garbage_device_token_is_401(api: httpx.AsyncClient) -> None:
    response = await api.get(TODAY, headers=_device_headers(DEVICE_TOKEN_PREFIX + "garbage"))
    assert response.status_code == 401, response.text
    body = response.json()
    assert "detail" not in body, body
    assert body["error_code"] == "AUTHENTICATION", body


@requires_db
async def test_account_jwt_still_reads_everything(api: httpx.AsyncClient) -> None:
    """The account path is unchanged: every owner read answers 200 (detail
    404 — auth passed, ownership filtered), never 401/403."""
    account_headers = await _register_account(api)

    for path, params in DEVICE_READ_PATHS:
        response = await api.get(path, params=params, headers=account_headers)
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"

    detail = await api.get(f"/api/v1/experiments/{uuid.uuid4()}", headers=account_headers)
    assert detail.status_code == 404, detail.text
    assert detail.json()["error_code"] == "NOT_FOUND"
