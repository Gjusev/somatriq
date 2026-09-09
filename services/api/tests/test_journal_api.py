"""Journal quick-log API behavior (Block 2; grill P7/P10/P11; spec §103).

Full-app integration: the guard is the point — the account JWT logs with
source 'web', a paired device token with journal.write logs with source
'mobile', its absence is 403 AUTHORIZATION. Idempotency: a retried
client_event_id returns the SAME event. DELETE: user-authored kinds only;
system kinds are the system's audit trail (P11).
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from somatriq_api.main import app
from somatriq_contracts.pairing import DEVICE_TOKEN_PREFIX
from somatriq_db.engine import get_engine
from somatriq_db.models import DeviceToken
from somatriq_db.testing import requires_db
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

EVENTS = "/api/v1/journal/events"
JOURNAL = "/api/v1/journal"


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the test loop; pool drained across loop boundaries."""
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register_account(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _pair_device(api: httpx.AsyncClient, account_headers: dict[str, str]) -> tuple[str, str]:
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


@requires_db
async def test_behavior_kinds_roundtrip_with_quantity_rules(api: httpx.AsyncClient) -> None:
    headers = await _register_account(api)

    alcohol = await api.post(
        EVENTS,
        json={"kind": "alcohol", "structured": {"quantity": 2}, "text": "two beers"},
        headers=headers,
    )
    assert alcohol.status_code == 201, alcohol.text
    body = alcohol.json()
    assert body["source"] == "web"
    assert body["structured"] == {"quantity": 2}

    caffeine = await api.post(EVENTS, json={"kind": "caffeine"}, headers=headers)
    assert caffeine.status_code == 201, caffeine.text
    # No stated quantity -> estimated false, never a guess (spec §103).
    assert caffeine.json()["structured"] == {"estimated": False}

    stress = await api.post(EVENTS, json={"kind": "stress"}, headers=headers)
    assert stress.status_code == 201, stress.text
    assert stress.json()["structured"] is None


@requires_db
async def test_binary_kind_rejects_structured_payload(api: httpx.AsyncClient) -> None:
    headers = await _register_account(api)
    rejected = await api.post(
        EVENTS, json={"kind": "medication", "structured": {"intensity": 2}}, headers=headers
    )
    assert rejected.status_code == 422, rejected.text


@requires_db
async def test_client_event_id_retry_is_the_same_event(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = await _register_account(api)
    client_event_id = str(uuid.uuid4())

    first = await api.post(
        EVENTS,
        json={"kind": "meal", "client_event_id": client_event_id},
        headers=headers,
    )
    second = await api.post(
        EVENTS,
        json={"kind": "meal", "client_event_id": client_event_id},
        headers=headers,
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    count = (await db.execute(text("SELECT count(*) FROM health.journal_events"))).scalar_one()
    assert count == 1


@requires_db
async def test_day_read_is_local_day_newest_first(api: httpx.AsyncClient) -> None:
    headers = await _register_account(api)
    # 18:00Z is 2026-09-09 in both UTC and Europe/Madrid (day-stable).
    for hour, kind in ((18, "caffeine"), (19, "stress")):
        posted = await api.post(
            EVENTS,
            json={"kind": kind, "ts": f"2026-09-09T{hour}:00:00+00:00"},
            headers=headers,
        )
        assert posted.status_code == 201, posted.text

    day = await api.get(JOURNAL, params={"day": "2026-09-09"}, headers=headers)
    assert day.status_code == 200, day.text
    kinds = [event["kind"] for event in day.json()["events"]]
    assert kinds == ["stress", "caffeine"]  # newest first


@requires_db
async def test_delete_owner_kinds_only(api: httpx.AsyncClient, db: AsyncSession) -> None:
    headers = await _register_account(api)
    posted = await api.post(EVENTS, json={"kind": "note", "text": "typo"}, headers=headers)
    event_id = posted.json()["id"]

    removed = await api.delete(f"{EVENTS}/{event_id}", headers=headers)
    assert removed.status_code == 204, removed.text
    again = await api.delete(f"{EVENTS}/{event_id}", headers=headers)
    assert again.status_code == 404, again.text

    # A system-written kind is the system's audit trail — rejected explicitly.
    user_id = (await db.execute(text("SELECT id FROM identity.users LIMIT 1"))).scalar_one()
    system_row = (
        await db.execute(
            text(
                "INSERT INTO health.journal_events (user_id, source, kind) "
                "VALUES (:user_id, 'api', 'experiment_checkin') RETURNING id"
            ),
            {"user_id": user_id},
        )
    ).scalar_one()
    await db.commit()
    rejected = await api.delete(f"{EVENTS}/{system_row}", headers=headers)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error_code"] == "VALIDATION"


@requires_db
async def test_device_token_journals_as_mobile(api: httpx.AsyncClient) -> None:
    headers = await _register_account(api)
    token, _ = await _pair_device(api, headers)

    posted = await api.post(EVENTS, json={"kind": "caffeine"}, headers=_device_headers(token))
    assert posted.status_code == 201, posted.text
    assert posted.json()["source"] == "mobile"


@requires_db
async def test_device_token_without_journal_write_is_403(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    headers = await _register_account(api)
    token, device_id = await _pair_device(api, headers)
    await db.execute(
        update(DeviceToken)
        .where(DeviceToken.device_id == uuid.UUID(device_id))
        .values(scopes=["ingest.write", "device.read", "sync.read", "data.read"])
    )
    await db.commit()

    rejected = await api.post(EVENTS, json={"kind": "caffeine"}, headers=_device_headers(token))
    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["error_code"] == "AUTHORIZATION", rejected.text


@requires_db
async def test_unauthenticated_post_is_401(api: httpx.AsyncClient) -> None:
    response = await api.post(EVENTS, json={"kind": "note"})
    assert response.status_code == 401, response.text
