"""Admin replay surface: archive list → fidelity verify → versioned decode.

End-to-end through the REAL ingest path: an envelope carrying WHOOP 4.0
realtime frames (built from documented offsets) is ingested, the stored
blob is verified byte-for-byte, and replay decodes heart rates into
timeseries.replayed_observations without ever touching the live hypertable.
"""

import base64
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast
from uuid import UUID, uuid4

import httpx
import pytest
import zstandard
from conftest import requires_db as _untyped_requires_db
from somatriq_api.main import app
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_engine
from somatriq_db.models import HeartRate, ReplayedObservation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

pytestmark = [requires_db]

INGEST = "/api/v1/ingest/batches"
ADMIN = "/api/v1/admin"


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


@pytest.fixture()
async def account_headers(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
async def device_token_headers(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> dict[str, str]:
    session = await api.post("/api/v1/pairing/sessions", headers=account_headers)
    assert session.status_code == 200, session.text
    code = session.json()["pairing_code"]
    confirmed = await api.post(
        "/api/v1/pairing/confirm",
        json={"pairing_code": code, "device_name": "replay-test-collector"},
    )
    assert confirmed.status_code == 200, confirmed.text
    return {"Authorization": f"Bearer {confirmed.json()['token']}"}


def _whoop4_frame(ts_seconds: int, bpm: int) -> bytes:
    frame = bytearray(14)
    frame[0] = 0x01
    frame[1:3] = (8).to_bytes(2, "big")
    frame[4] = 40  # REALTIME_DATA
    frame[5] = 0
    frame[6:10] = ts_seconds.to_bytes(4, "little")
    frame[10:12] = (0).to_bytes(2, "little")
    frame[12] = bpm
    frame[13] = 0
    return bytes(frame)


async def _ingest_whoop_batch(
    api: httpx.AsyncClient, device_headers: dict[str, str], bpms: list[int]
) -> UUID:
    base = int(datetime.now(UTC).timestamp())
    raw = b"".join(
        len(_whoop4_frame(base + i, bpm)).to_bytes(4, "big")
        + (base + i).to_bytes(8, "big")
        + _whoop4_frame(base + i, bpm)
        for i, bpm in enumerate(bpms)
    )
    blob = zstandard.ZstdCompressor().compress(raw)
    records = [
        {
            "source_record_id": f"replay-e2e-{i}",
            "ts": (datetime.now(UTC) + timedelta(seconds=i)).isoformat(),
            "bpm": float(bpm),
        }
        for i, bpm in enumerate(bpms)
    ]
    batch_id = uuid4()
    envelope = {
        "batch_id": str(batch_id),
        "schema_version": "2",
        "decoder_version": "noop-android/test",
        "records": records,
        "raw": {
            "codec": "zstd",
            "journal_version": 1,
            "frame_count": len(bpms),
            "payload_b64": base64.b64encode(blob).decode(),
            "payload_sha256": hashlib.sha256(blob).hexdigest(),
            "uncompressed_bytes": len(raw),
        },
    }
    ack = await api.post(INGEST, json=envelope, headers=device_headers)
    assert ack.status_code == 200, ack.text
    assert ack.json()["raw_ack"] is True
    return batch_id


async def test_verify_and_replay_whoop_batch(
    api: httpx.AsyncClient,
    account_headers: dict[str, str],
    device_token_headers: dict[str, str],
    db: AsyncSession,
) -> None:
    batch_id = await _ingest_whoop_batch(api, device_token_headers, [58, 61, 64])

    listed = await api.get(f"{ADMIN}/raw-batches", headers=account_headers)
    assert listed.status_code == 200, listed.text
    ours = next(b for b in listed.json() if b["batch_id"] == str(batch_id))
    assert ours["frame_count"] == 3

    verified = await api.get(f"{ADMIN}/raw-batches/{batch_id}/verify", headers=account_headers)
    assert verified.status_code == 200, verified.text
    assert verified.json()["frame_count"] == 3

    replayed = await api.post(f"{ADMIN}/raw-batches/{batch_id}/replay", headers=account_headers)
    assert replayed.status_code == 200, replayed.text
    body = replayed.json()
    assert body["decoder_version"] == "whoop4-realtime-hr/v1"
    assert body["observations_decoded"] == 3
    assert body["inserted"] == 3

    rows = (
        await db.execute(
            select(ReplayedObservation).where(ReplayedObservation.raw_batch_id == batch_id)
        )
    ).scalars().all()
    assert sorted(int(r.bpm) for r in rows) == [58, 61, 64]
    assert all(r.decoder_version == "whoop4-realtime-hr/v1" for r in rows)

    # Idempotent re-replay: same decoder version — zero new rows.
    again = await api.post(f"{ADMIN}/raw-batches/{batch_id}/replay", headers=account_headers)
    assert again.json()["inserted"] == 0
    assert again.json()["already_present"] == 3


async def test_replay_never_touches_live_hypertable(
    api: httpx.AsyncClient,
    account_headers: dict[str, str],
    device_token_headers: dict[str, str],
    db: AsyncSession,
) -> None:
    before = (await db.execute(select(func.count()).select_from(HeartRate))).scalar_one()
    batch_id = await _ingest_whoop_batch(api, device_token_headers, [70])
    await api.post(f"{ADMIN}/raw-batches/{batch_id}/replay", headers=account_headers)
    await db.rollback()  # drop the snapshot pinned by the `before` count
    after = (await db.execute(select(func.count()).select_from(HeartRate))).scalar_one()
    assert after == before + 1  # only the ingest itself wrote the live table


async def test_admin_requires_account_jwt(
    api: httpx.AsyncClient, device_token_headers: dict[str, str]
) -> None:
    response = await api.get(f"{ADMIN}/raw-batches", headers=device_token_headers)
    assert response.status_code == 401
    assert response.json()["error_code"] == ErrorCode.AUTHENTICATION.value


async def test_unknown_batch_is_404(
    api: httpx.AsyncClient, account_headers: dict[str, str]
) -> None:
    response = await api.get(f"{ADMIN}/raw-batches/{uuid4()}/verify", headers=account_headers)
    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.NOT_FOUND.value
