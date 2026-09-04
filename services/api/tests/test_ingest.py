"""M1 ingest endpoint: idempotent batch writes (ADR 0006, spec §41-42).

The router is mounted onto the app at import time because wiring it in
main.py is owned by a separate change; the guard keeps this slice
self-contained without double-mounting once that wiring lands.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from conftest import requires_db as _untyped_requires_db
from fastapi.routing import APIRoute
from somatriq_api.ingest import router
from somatriq_api.main import app
from somatriq_api.security import require_ingest_token
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
    """Client with the ingest token guard bypassed via dependency override."""
    app.dependency_overrides[require_ingest_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    app.dependency_overrides.pop(require_ingest_token, None)


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
        assert rejected.json()["detail"]["error_code"] == "AUTHENTICATION"

        accepted = await guarded_api.post(
            INGEST_PATH, json=body, headers={"X-Somatriq-Token": "probe-token"}
        )
        assert accepted.status_code == 200
    finally:
        # Leave an empty cache so later settings reads see restored env vars.
        get_settings.cache_clear()
