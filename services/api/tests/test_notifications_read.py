"""GET /api/v1/notifications/recent: the phone's poll replacement for the
internal-only ntfy subscribe (Fase 4, Q15b).

Pins the read contract: EITHER principal (account JWT or data.read device
token) lists the OWNER's recent outbox rows newest-first — another owner's
rows never leak — with ``limit`` clamped server-side, and the endpoint
strictly read-only: delivery state (status/attempts/sent_at writes) stays
the notifications service's job (spec §105). Auth failures mirror the
shared guard: no/garbage token → flat 401, missing data.read → 403
AUTHORIZATION (test_device_read_auth.py pins the same guard per route).
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.main import app
from somatriq_contracts.pairing import DEVICE_TOKEN_PREFIX
from somatriq_db.engine import get_engine
from somatriq_db.models import DeviceToken, NotificationChannel, NotificationOutbox, User
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

RECENT = "/api/v1/notifications/recent"


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client on the test loop; pool drained across loop boundaries."""
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


@pytest.fixture(autouse=True)
async def clean_notifications(db: AsyncSession) -> AsyncIterator[None]:
    """The api conftest truncates everything EXCEPT notifications.* — clear
    them here so row-count assertions are deterministic across tests."""
    await db.execute(text("TRUNCATE TABLE notifications.outbox, notifications.channels"))
    await db.commit()
    yield


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


async def _owner_id(db: AsyncSession) -> uuid.UUID:
    """The seeded single local user — register/pair both resolve here."""
    return (await db.execute(select(User.id).order_by(User.created_at).limit(1))).scalar_one()


async def _seed_owner_channel(
    db: AsyncSession, user_id: uuid.UUID, kind: str = "ntfy", target: str = "somatriq-test"
) -> uuid.UUID:
    channel = NotificationChannel(user_id=user_id, kind=kind, target=target, enabled=True)
    db.add(channel)
    await db.commit()
    return channel.id


async def _seed_outbox(
    db: AsyncSession,
    channel_id: uuid.UUID,
    kind: str,
    message: str,
    *,
    status: str = "sent",
    minutes_ago: int = 0,
) -> None:
    """One outbox row, created_at staggered so newest-first order is assertable."""
    row = NotificationOutbox(
        channel_id=channel_id,
        kind=kind,
        payload={"text": message, "date": "2026-09-07"},
        status=status,
        attempts=1 if status == "failed" else 0,
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        sent_at=datetime.now(UTC) - timedelta(minutes=minutes_ago) if status == "sent" else None,
    )
    db.add(row)
    await db.commit()


async def _seed_foreign_owner_with_rows(db: AsyncSession) -> None:
    """A second owner with queued notifications — never visible to our owner."""
    foreign_user = (
        await db.execute(
            text("INSERT INTO identity.users (display_name) VALUES ('foreign-user') RETURNING id")
        )
    ).scalar_one()
    channel = NotificationChannel(user_id=foreign_user, kind="telegram", target="666", enabled=True)
    db.add(channel)
    await db.flush()
    db.add(
        NotificationOutbox(
            channel_id=channel.id,
            kind="test",
            payload={"text": "FOREIGN secret brief"},
            status="pending",
        )
    )
    await db.commit()


@requires_db
async def test_recent_requires_authentication(api: httpx.AsyncClient) -> None:
    response = await api.get(RECENT)
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error_code"] == "AUTHENTICATION", body
    assert "detail" not in body, "error bodies must be flat"


@requires_db
async def test_recent_device_token_reads_owner_outbox(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """The paired phone polls with its data.read token: newest-first rows,
    message extracted from the payload, and NOTHING marked consumed."""
    account_headers = await _register_account(api)
    token, _device_id = await _pair_device(api, account_headers)
    channel_id = await _seed_owner_channel(db, await _owner_id(db))
    await _seed_outbox(
        db, channel_id, "sync_warning", "stale sync warning", status="pending", minutes_ago=5
    )
    await _seed_outbox(
        db, channel_id, "morning_brief", "Good morning brief", status="sent", minutes_ago=60
    )
    await _seed_outbox(db, channel_id, "anomaly", "anomaly note", status="sent", minutes_ago=120)

    response = await api.get(RECENT, headers=_device_headers(token))
    assert response.status_code == 200, response.text
    assert "detail" not in response.json(), "body must be flat"
    items = response.json()
    assert [item["message"] for item in items] == [
        "stale sync warning",
        "Good morning brief",
        "anomaly note",
    ]
    assert [item["kind"] for item in items] == ["sync_warning", "morning_brief", "anomaly"]
    assert all(item["channel"] == "ntfy" for item in items)
    assert items[0]["status"] == "pending" and items[0]["sent_at"] is None
    assert items[1]["status"] == "sent" and items[1]["sent_at"] is not None
    # Read-only: delivery state untouched — the notifications service owns it.
    rows = (
        await db.execute(
            select(NotificationOutbox.status, NotificationOutbox.attempts).order_by(
                NotificationOutbox.created_at
            )
        )
    ).all()
    assert [(status, attempts) for status, attempts in rows] == [
        ("sent", 0),
        ("sent", 0),
        ("pending", 0),
    ]


@requires_db
async def test_recent_is_scoped_to_owner(api: httpx.AsyncClient, db: AsyncSession) -> None:
    """Both principals are the owner: another owner's outbox rows never list."""
    account_headers = await _register_account(api)
    token, _device_id = await _pair_device(api, account_headers)
    channel_id = await _seed_owner_channel(db, await _owner_id(db))
    await _seed_outbox(db, channel_id, "morning_brief", "OWNER brief", status="sent")
    await _seed_foreign_owner_with_rows(db)

    for headers in (account_headers, _device_headers(token)):
        response = await api.get(RECENT, headers=headers)
        assert response.status_code == 200, response.text
        assert [item["message"] for item in response.json()] == ["OWNER brief"]


@requires_db
async def test_recent_without_data_read_scope_is_403(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    account_headers = await _register_account(api)
    token, device_id = await _pair_device(api, account_headers)
    await _strip_data_read(db, device_id)
    channel_id = await _seed_owner_channel(db, await _owner_id(db))
    await _seed_outbox(db, channel_id, "morning_brief", "OWNER brief")

    response = await api.get(RECENT, headers=_device_headers(token))
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error_code"] == "AUTHORIZATION", body
    assert "detail" not in body


@requires_db
async def test_recent_limit_is_clamped(api: httpx.AsyncClient, db: AsyncSession) -> None:
    """A poll cannot hurt the database: default 20, ceiling 100, floor 1 —
    always 200, never a validation error."""
    account_headers = await _register_account(api)
    channel_id = await _seed_owner_channel(db, await _owner_id(db))
    for minutes_ago in range(105):
        await _seed_outbox(
            db, channel_id, "morning_brief", f"brief-{minutes_ago}", minutes_ago=minutes_ago
        )

    default = await api.get(RECENT, headers=account_headers)
    assert default.status_code == 200, default.text
    items = default.json()
    assert len(items) == 20
    assert items[0]["message"] == "brief-0", "newest first even in the clamped slice"

    ceiling = await api.get(RECENT, params={"limit": 9999}, headers=account_headers)
    assert ceiling.status_code == 200, ceiling.text
    assert len(ceiling.json()) == 100

    floor = await api.get(RECENT, params={"limit": 0}, headers=account_headers)
    assert floor.status_code == 200, floor.text
    assert [item["message"] for item in floor.json()] == ["brief-0"]
