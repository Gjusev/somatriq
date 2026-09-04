"""M1+M2 ingest endpoint: idempotent batch writes + raw envelope (ADR 0006, ADR 0003).

The router is mounted onto the app at import time because wiring it in
main.py is owned by a separate change; the guard keeps this slice
self-contained without double-mounting once that wiring lands.
"""

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar, cast

import httpx
import pytest
from conftest import requires_db as _untyped_requires_db
from fastapi.routing import APIRoute
from somatriq_api.ingest import router
from somatriq_api.main import app
from somatriq_api.security import require_ingest_principal
from somatriq_api.settings import get_settings
from somatriq_db.engine import get_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INGEST_PATH = "/api/v1/ingest/batches"
BASE_TS = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)

_F = TypeVar("_F", bound=Callable[..., object])

# conftest declares the skip marker as an untyped expression; recast so the
# decorator keeps test functions typed under mypy strict.
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)


def _mounted(path: str) -> bool:
    return any(isinstance(route, APIRoute) and route.path == path for route in app.router.routes)


if not _mounted(INGEST_PATH):
    app.include_router(router)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool() -> AsyncIterator[None]:
    """Empty the shared engine pool after each test.

    pytest-asyncio gives every test its own event loop and asyncpg
    connections are loop-bound; without disposal the pool would hand the
    next test a connection created on a dead loop.
    """
    yield
    await get_engine().dispose()


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Client with the ingest guard bypassed via dependency override."""
    app.dependency_overrides[require_ingest_principal] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    app.dependency_overrides.pop(require_ingest_principal, None)


@pytest.fixture()
async def guarded_api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """Client with the real token dependency active (no override)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def _record(n: int, bpm: float = 60.0, ts: str | None = None) -> dict[str, object]:
    effective_ts = ts if ts is not None else (BASE_TS + timedelta(seconds=n)).isoformat()
    return {"source_record_id": f"hr-{n}", "ts": effective_ts, "bpm": bpm}


def _payload(batch_id: str, records: list[dict[str, object]]) -> dict[str, object]:
    return {"batch_id": batch_id, "records": records}


async def _heart_rate_rows(db: AsyncSession) -> int:
    result = await db.execute(text("SELECT count(*) FROM timeseries.heart_rate"))
    count: int = result.scalar_one()
    return count


@requires_db
async def test_replayed_batch_creates_no_duplicates(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Same batch UUID + same content replays the ack without new rows."""
    body = _payload(str(uuid.uuid4()), [_record(1, 62.0), _record(2, 64.0), _record(3, 66.0)])

    first = await api.post(INGEST_PATH, json=body)
    assert first.status_code == 200
    ack = first.json()
    assert ack["accepted"] is True
    assert ack["records_received"] == 3
    assert ack["records_inserted"] == 3
    assert ack["records_duplicate"] == 0

    replay = await api.post(INGEST_PATH, json=body)
    assert replay.status_code == 200
    replay_ack = replay.json()
    assert replay_ack["accepted"] is True
    assert replay_ack["records_received"] == 3
    assert replay_ack["records_inserted"] == 0
    assert replay_ack["records_duplicate"] == 3

    assert await _heart_rate_rows(db) == 3


@requires_db
async def test_overlapping_batch_counts_duplicates(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """A different batch re-sending known records counts them as duplicates."""
    first_body = _payload(str(uuid.uuid4()), [_record(1, 62.0), _record(2, 64.0), _record(3, 66.0)])
    assert (await api.post(INGEST_PATH, json=first_body)).status_code == 200

    second_body = _payload(
        str(uuid.uuid4()),
        [_record(1, 62.0), _record(2, 64.0), _record(4, 68.0)],  # 2 replays + 1 new
    )
    second = await api.post(INGEST_PATH, json=second_body)
    assert second.status_code == 200
    ack = second.json()
    assert ack["records_received"] == 3
    assert ack["records_inserted"] == 1
    assert ack["records_duplicate"] == 2

    assert await _heart_rate_rows(db) == 4


@requires_db
async def test_same_uuid_different_content_conflicts(api: httpx.AsyncClient) -> None:
    """Reusing a batch UUID with different content is a 409, never a rewrite."""
    batch_id = str(uuid.uuid4())
    first = await api.post(INGEST_PATH, json=_payload(batch_id, [_record(1, 62.0)]))
    assert first.status_code == 200

    conflict = await api.post(INGEST_PATH, json=_payload(batch_id, [_record(1, 70.0)]))
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


@requires_db
async def test_invalid_bpm_rejected(api: httpx.AsyncClient) -> None:
    """Bpm outside the catalog range fails pydantic validation with 422."""
    response = await api.post(INGEST_PATH, json=_payload(str(uuid.uuid4()), [_record(1, 999.0)]))
    assert response.status_code == 422


@requires_db
async def test_naive_timestamp_rejected(api: httpx.AsyncClient) -> None:
    """Timestamps without timezone info violate spec §65 UTC-at-rest."""
    naive_ts = (BASE_TS + timedelta(seconds=1)).replace(tzinfo=None).isoformat()
    body = _payload(str(uuid.uuid4()), [_record(1, 62.0, ts=naive_ts)])
    response = await api.post(INGEST_PATH, json=body)
    assert response.status_code == 422


@requires_db
async def test_token_required_when_configured(
    guarded_api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With INGEST_TOKEN configured, writes require X-Somatriq-Token."""
    monkeypatch.setenv("INGEST_TOKEN", "probe-token")
    get_settings.cache_clear()
    body = _payload(str(uuid.uuid4()), [_record(1, 62.0)])
    try:
        rejected = await guarded_api.post(INGEST_PATH, json=body)
        assert rejected.status_code == 401
        assert rejected.json()["error_code"] == "AUTHENTICATION"

        accepted = await guarded_api.post(
            INGEST_PATH, json=body, headers={"X-Somatriq-Token": "probe-token"}
        )
        assert accepted.status_code == 200
    finally:
        # Leave an empty cache so later settings reads see restored env vars.
        get_settings.cache_clear()


# ── envelope v2: raw journal segment (ADR 0003, spec §47-51) ────────────


def _raw_payload(data: bytes | None = None, sha: str | None = None) -> dict[str, object]:
    """Opaque compressed-by-policy blob: the server never decompresses, so
    any bytes stand in for a real zstd journal in tests."""
    blob = data if data is not None else b"journal-segment-\x00\x01\x02" * 64
    return {
        "codec": "zstd",
        "journal_version": 1,
        "frame_count": 12,
        "payload_b64": base64.b64encode(blob).decode(),
        "payload_sha256": sha if sha is not None else hashlib.sha256(blob).hexdigest(),
        "uncompressed_bytes": 2048,
    }


def _v2_payload(
    batch_id: str,
    records: list[dict[str, object]],
    raw: dict[str, object] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "batch_id": batch_id,
        "schema_version": "2",
        "decoder_version": "noop-android/1.2.0+somatriq",
        "records": records,
    }
    if raw is not None:
        body["raw"] = raw
    return body


async def _seed_identity(db: AsyncSession) -> tuple[str, str]:
    result = await db.execute(
        text(
            "SELECT u.id, d.id FROM identity.users u, identity.devices d "
            "ORDER BY u.created_at, d.active_from LIMIT 1"
        )
    )
    row = result.one()
    return str(row[0]), str(row[1])


@requires_db
async def test_v2_raw_ack_blob_and_registry_row(
    api: httpx.AsyncClient, db: AsyncSession, raw_dir_setting: Path
) -> None:
    """v2: raw_ack true iff blob written + raw.raw_batches committed (§50)."""
    blob = b"opaque-zstd-stand-in" * 100
    body = _v2_payload(str(uuid.uuid4()), [_record(1, 58.0)], raw=_raw_payload(blob))

    response = await api.post(INGEST_PATH, json=body)
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["raw_ack"] is True
    assert ack["raw_frame_count"] == 12
    assert ack["raw_bytes_stored"] == len(blob)

    batch_id = str(body["batch_id"])
    user_id, device_id = await _seed_identity(db)
    now = datetime.now(UTC)
    blob_path = (
        raw_dir_setting
        / f"{now.year:04d}"
        / f"{now.month:02d}"
        / f"{now.day:02d}"
        / user_id
        / device_id
        / f"{batch_id}.zst"
    )
    assert blob_path.is_file()
    assert blob_path.read_bytes() == blob  # verbatim, never decompressed

    row = (
        await db.execute(
            text(
                "SELECT codec, journal_version, frame_count, byte_size, blob_path, storage_state "
                "FROM raw.raw_batches WHERE batch_id = :b"
            ),
            {"b": batch_id},
        )
    ).one()
    assert row.codec == "zstd"
    assert row.frame_count == 12
    assert row.byte_size == len(blob)
    assert row.blob_path == str(blob_path)
    assert row.storage_state == "confirmed"

    hr = (
        await db.execute(
            text(
                "SELECT decoder_version, raw_batch_id FROM timeseries.heart_rate "
                "WHERE source_record_id = 'hr-1'"
            )
        )
    ).one()
    assert hr.decoder_version == "noop-android/1.2.0+somatriq"
    assert str(hr.raw_batch_id) == batch_id


@requires_db
async def test_v2_sha_mismatch_is_422(api: httpx.AsyncClient) -> None:
    """payload_sha256 must equal sha256 of the decoded bytes — 422 otherwise."""
    body = _v2_payload(
        str(uuid.uuid4()), [_record(1, 58.0)], raw=_raw_payload(sha="0" * 64)
    )
    response = await api.post(INGEST_PATH, json=body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION"


@requires_db
async def test_v1_still_accepted_without_raw_ack(api: httpx.AsyncClient) -> None:
    """v1 envelopes remain valid and simply never earn raw_ack."""
    body = _payload(str(uuid.uuid4()), [_record(1, 60.0)])
    body["schema_version"] = "1"
    response = await api.post(INGEST_PATH, json=body)
    assert response.status_code == 200
    ack = response.json()
    assert ack["raw_ack"] is False
    assert ack["raw_frame_count"] == 0
    assert ack["raw_bytes_stored"] == 0


@requires_db
async def test_v2_replay_replays_the_raw_truth(api: httpx.AsyncClient) -> None:
    """Same UUID + same full-envelope hash → original ack, raw_ack included."""
    body = _v2_payload(str(uuid.uuid4()), [_record(1, 58.0)], raw=_raw_payload())

    first = await api.post(INGEST_PATH, json=body)
    assert first.status_code == 200
    assert first.json()["raw_ack"] is True

    replay = await api.post(INGEST_PATH, json=body)
    assert replay.status_code == 200
    replay_ack = replay.json()
    assert replay_ack["accepted"] is True
    assert replay_ack["records_inserted"] == 0
    assert replay_ack["records_duplicate"] == 1
    assert replay_ack["raw_ack"] is True
    assert replay_ack["raw_frame_count"] == 12


@requires_db
async def test_v1_replay_of_v2_batch_conflicts(api: httpx.AsyncClient) -> None:
    """Dropping the raw section changes the content hash → 409, no rewrite."""
    batch_id = str(uuid.uuid4())
    v2 = _v2_payload(batch_id, [_record(1, 58.0)], raw=_raw_payload())
    assert (await api.post(INGEST_PATH, json=v2)).status_code == 200

    v1 = _payload(batch_id, [_record(1, 58.0)])
    v1["schema_version"] = "1"
    conflict = await api.post(INGEST_PATH, json=v1)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


@requires_db
async def test_device_token_auth_end_to_end(guarded_api: httpx.AsyncClient) -> None:
    """Pair → ingest with Bearer sqt_dev_…: the M2 credential path works."""
    session = await guarded_api.post(
        "/api/v1/pairing/sessions",
        headers=await _account_auth(guarded_api),
    )
    assert session.status_code == 200, session.text
    confirmed = await guarded_api.post(
        "/api/v1/pairing/confirm",
        json={"pairing_code": session.json()["pairing_code"], "device_name": "pixel-9"},
    )
    assert confirmed.status_code == 200, confirmed.text
    token = confirmed.json()["token"]

    body = _v2_payload(str(uuid.uuid4()), [_record(1, 59.0)], raw=_raw_payload())
    response = await guarded_api.post(
        INGEST_PATH, json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["raw_ack"] is True


@requires_db
async def test_global_token_via_bearer_header(
    guarded_api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transitional INGEST_TOKEN also travels as Authorization: Bearer."""
    monkeypatch.setenv("INGEST_TOKEN", "probe-token")
    get_settings.cache_clear()
    body = _payload(str(uuid.uuid4()), [_record(1, 62.0)])
    try:
        accepted = await guarded_api.post(
            INGEST_PATH, json=body, headers={"Authorization": "Bearer probe-token"}
        )
        assert accepted.status_code == 200
    finally:
        get_settings.cache_clear()


async def _account_auth(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "ingest-user", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
