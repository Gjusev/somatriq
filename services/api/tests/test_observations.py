"""M6 observation families: vendor daily observations, sleep sessions, RR
intervals (spec §41-42 family; ADR 0006 idempotency, ADR 0012 vendor scores
are Observations).

The three ingest endpoints reuse the heart-rate slice's ADR 0006 discipline
(batch UUID replay, forensic content hash, per-record natural-key dedup,
device-token principals); the three reads are unauthenticated like the other
metric reads. Routers are wired into the app in main.py.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.main import app
from somatriq_api.security import require_ingest_principal
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DAILY_INGEST_PATH = "/api/v1/ingest/daily-observations"
SLEEP_INGEST_PATH = "/api/v1/ingest/sleep-sessions"
RR_INGEST_PATH = "/api/v1/ingest/rr-intervals"
DAILY_READ_PATH = "/api/v1/observations/daily"
SLEEP_READ_PATH = "/api/v1/sleep/sessions"
RR_READ_PATH = "/api/v1/metrics/rr"

DECODER_VERSION = "noop-android/6.0.0+somatriq"

_F = TypeVar("_F", bound=Callable[..., object])

# conftest declares the skip marker as an untyped expression; recast so the
# decorator keeps test functions typed under mypy strict.
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool() -> AsyncIterator[None]:
    """Empty the shared engine pool after each test (loop-bound connections)."""
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
    """Client with the real credential guard active (no override)."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


# ── payload builders ────────────────────────────────────────────────────


def _today_utc() -> date:
    return datetime.now(UTC).date()


def _daily_item(day: date, metric: str, value: float) -> dict[str, object]:
    return {"day": day.isoformat(), "metric": metric, "value": value}


def _daily_payload(batch_id: str, items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "schema_version": "1",
        "decoder_version": DECODER_VERSION,
        "items": items,
    }


def _stage_dict(state: str, start: datetime, minutes: float) -> dict[str, object]:
    return {
        "state": state,
        "start_ts": start.isoformat(),
        "end_ts": (start + timedelta(minutes=minutes)).isoformat(),
    }


def _session_dict(
    srid: str,
    start: datetime,
    stage_specs: list[tuple[str, float]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    end = start + timedelta(hours=8)
    stages: list[dict[str, object]] = []
    cursor = start + timedelta(minutes=10)
    for state, minutes in stage_specs or []:
        stages.append(_stage_dict(state, cursor, minutes))
        cursor += timedelta(minutes=minutes)
    body: dict[str, object] = {
        "source_record_id": srid,
        "start_ts": start.isoformat(),
        "end_ts": end.isoformat(),
        "stages": stages,
    }
    body.update(overrides)
    return body


def _sleep_payload(batch_id: str, sessions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "schema_version": "1",
        "decoder_version": DECODER_VERSION,
        "sessions": sessions,
    }


def _rr_record(n: int, ts: datetime, rr_ms: int = 800, seq: int | None = None) -> dict[str, object]:
    return {
        "source_record_id": f"rr-{n}",
        "ts": ts.isoformat(),
        "rr_ms": rr_ms,
        "seq": n if seq is None else seq,
    }


def _rr_payload(batch_id: str, records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "batch_id": batch_id,
        "schema_version": "1",
        "decoder_version": DECODER_VERSION,
        "records": records,
    }


async def _pair_device(client: httpx.AsyncClient, name: str = "pixel-obs") -> tuple[str, str]:
    """Register + pair; returns (device_token, device_id) for row assertions."""
    register = await client.post(
        "/api/v1/auth/register",
        json={"username": "obs-user", "password": "correct-horse-battery"},
    )
    assert register.status_code == 200, register.text
    session = await client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {register.json()['access_token']}"},
    )
    assert session.status_code == 200, session.text
    confirmed = await client.post(
        "/api/v1/pairing/confirm",
        json={"pairing_code": session.json()["pairing_code"], "device_name": name},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    return str(body["token"]), str(body["device_id"])


# ── daily observations: ingest ──────────────────────────────────────────


@requires_db
async def test_daily_observations_happy_path(api: httpx.AsyncClient, db: AsyncSession) -> None:
    """Items land one row per (day, metric) with decoder/batch provenance."""
    batch_id = str(uuid.uuid4())
    body = _daily_payload(
        batch_id,
        [
            _daily_item(_today_utc(), "resting_hr", 52.5),
            _daily_item(_today_utc(), "avg_hrv", 71.0),
            _daily_item(_today_utc() - timedelta(days=1), "recovery", 0.81),
        ],
    )

    response = await api.post(DAILY_INGEST_PATH, json=body)
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["accepted"] is True
    assert ack["records_received"] == 3
    assert ack["records_inserted"] == 3
    assert ack["records_duplicate"] == 0
    assert ack["raw_ack"] is False  # no raw section in this family, ever

    row = (
        await db.execute(
            text(
                "SELECT value, decoder_version, raw_batch_id "
                "FROM health.daily_observations WHERE metric = 'resting_hr'"
            )
        )
    ).one()
    assert row.value == 52.5
    assert row.decoder_version == DECODER_VERSION
    assert str(row.raw_batch_id) == batch_id


@requires_db
async def test_daily_observations_replay_counts_duplicates(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Same batch UUID + same content replays the ack without new rows."""
    body = _daily_payload(
        str(uuid.uuid4()),
        [
            _daily_item(_today_utc(), "resting_hr", 52.0),
            _daily_item(_today_utc(), "strain", 8.1),
        ],
    )
    assert (await api.post(DAILY_INGEST_PATH, json=body)).status_code == 200

    replay = await api.post(DAILY_INGEST_PATH, json=body)
    assert replay.status_code == 200
    replay_ack = replay.json()
    assert replay_ack["records_inserted"] == 0
    assert replay_ack["records_duplicate"] == 2
    assert "duplicate batch replay" in replay_ack["warnings"]

    count: int = (
        await db.execute(text("SELECT count(*) FROM health.daily_observations"))
    ).scalar_one()
    assert count == 2


@requires_db
async def test_daily_observations_tampered_replay_conflicts(api: httpx.AsyncClient) -> None:
    """Same UUID with different content is a 409, never a rewrite."""
    batch_id = str(uuid.uuid4())
    first = _daily_payload(batch_id, [_daily_item(_today_utc(), "resting_hr", 52.0)])
    assert (await api.post(DAILY_INGEST_PATH, json=first)).status_code == 200

    tampered = _daily_payload(batch_id, [_daily_item(_today_utc(), "resting_hr", 99.0)])
    conflict = await api.post(DAILY_INGEST_PATH, json=tampered)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


@requires_db
async def test_daily_observations_unknown_metric_rejected(api: httpx.AsyncClient) -> None:
    """Metric names outside the contract catalog fail validation with 422."""
    body = _daily_payload(str(uuid.uuid4()), [_daily_item(_today_utc(), "not_a_metric", 1.0)])
    response = await api.post(DAILY_INGEST_PATH, json=body)
    assert response.status_code == 422


@requires_db
async def test_daily_observations_overlapping_batch_counts_duplicates(
    api: httpx.AsyncClient,
) -> None:
    """A different batch re-sending known (day, metric) counts duplicates."""
    today = _today_utc()
    first = _daily_payload(
        str(uuid.uuid4()),
        [_daily_item(today, "resting_hr", 52.0), _daily_item(today, "strain", 8.0)],
    )
    assert (await api.post(DAILY_INGEST_PATH, json=first)).status_code == 200

    second = _daily_payload(
        str(uuid.uuid4()),
        [_daily_item(today, "resting_hr", 52.0), _daily_item(today, "avg_hrv", 70.0)],
    )
    ack = (await api.post(DAILY_INGEST_PATH, json=second)).json()
    assert ack["records_received"] == 2
    assert ack["records_inserted"] == 1
    assert ack["records_duplicate"] == 1


@requires_db
async def test_daily_observations_device_token_principal(
    guarded_api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Bearer sqt_dev_… writes land under the paired device, not the seed."""
    token, device_id = await _pair_device(guarded_api)
    body = _daily_payload(str(uuid.uuid4()), [_daily_item(_today_utc(), "steps", 9000.0)])

    response = await guarded_api.post(
        DAILY_INGEST_PATH, json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text

    row = (
        await db.execute(text("SELECT user_id, device_id FROM health.daily_observations"))
    ).one()
    assert str(row.device_id) == device_id


# ── sleep sessions: ingest ──────────────────────────────────────────────


@requires_db
async def test_sleep_sessions_happy_path_with_stages(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Sessions land with their stages attached in one transaction."""
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    body = _sleep_payload(
        str(uuid.uuid4()),
        [
            _session_dict(
                "sleep-1",
                start,
                [("deep", 90), ("rem", 30)],
                efficiency=0.93,
                resting_hr=51.0,
                avg_hrv=80.0,
                user_edited=True,
            ),
            _session_dict("sleep-2", start + timedelta(days=1)),
        ],
    )

    response = await api.post(SLEEP_INGEST_PATH, json=body)
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["records_received"] == 2
    assert ack["records_inserted"] == 2
    assert ack["records_duplicate"] == 0

    session_row = (
        await db.execute(
            text(
                "SELECT efficiency, resting_hr, avg_hrv, user_edited, decoder_version, "
                "raw_batch_id FROM health.sleep_sessions WHERE source_record_id = 'sleep-1'"
            )
        )
    ).one()
    assert session_row.efficiency == 0.93
    assert session_row.resting_hr == 51.0
    assert session_row.avg_hrv == 80.0
    assert session_row.user_edited is True
    assert session_row.decoder_version == DECODER_VERSION
    assert str(session_row.raw_batch_id) == str(body["batch_id"])

    stages: Sequence[str] = (
        await db.execute(
            text(
                "SELECT state FROM health.sleep_stages "
                "WHERE session_source_record_id = 'sleep-1' ORDER BY stage_start_ts"
            )
        )
    ).scalars().all()
    assert stages == ["deep", "rem"]


@requires_db
async def test_sleep_sessions_replay_skips_stages_too(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Replay: duplicate sessions count once and their stages are not rewritten."""
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    body = _sleep_payload(
        str(uuid.uuid4()), [_session_dict("sleep-1", start, [("deep", 60)])]
    )
    assert (await api.post(SLEEP_INGEST_PATH, json=body)).status_code == 200

    replay = await api.post(SLEEP_INGEST_PATH, json=body)
    assert replay.status_code == 200
    replay_ack = replay.json()
    assert replay_ack["records_inserted"] == 0
    assert replay_ack["records_duplicate"] == 1

    session_count: int = (
        await db.execute(text("SELECT count(*) FROM health.sleep_sessions"))
    ).scalar_one()
    stage_count: int = (
        await db.execute(text("SELECT count(*) FROM health.sleep_stages"))
    ).scalar_one()
    assert session_count == 1
    assert stage_count == 1


@requires_db
async def test_sleep_sessions_tampered_replay_conflicts(api: httpx.AsyncClient) -> None:
    """Same UUID with a tampered stage is a 409 (stages feed the hash)."""
    batch_id = str(uuid.uuid4())
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    first = _sleep_payload(batch_id, [_session_dict("sleep-1", start, [("deep", 60)])])
    assert (await api.post(SLEEP_INGEST_PATH, json=first)).status_code == 200

    tampered = _sleep_payload(batch_id, [_session_dict("sleep-1", start, [("deep", 61)])])
    conflict = await api.post(SLEEP_INGEST_PATH, json=tampered)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


@requires_db
async def test_sleep_stage_outside_session_bounds_rejected(api: httpx.AsyncClient) -> None:
    """Contract gate: stages must fit inside the session window (422)."""
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    rogue = _stage_dict("deep", start + timedelta(hours=7), minutes=120)  # spills past end
    session = _session_dict("sleep-1", start)
    session["stages"] = [rogue]
    body = _sleep_payload(str(uuid.uuid4()), [session])

    response = await api.post(SLEEP_INGEST_PATH, json=body)
    assert response.status_code == 422


@requires_db
async def test_sleep_sessions_overlapping_batch_skips_duplicate_stages(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """A duplicate session counts as duplicate and its stages are skipped."""
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    first = _sleep_payload(
        str(uuid.uuid4()), [_session_dict("sleep-1", start, [("deep", 60)])]
    )
    assert (await api.post(SLEEP_INGEST_PATH, json=first)).status_code == 200

    second = _sleep_payload(
        str(uuid.uuid4()),
        [
            _session_dict("sleep-1", start, [("deep", 60), ("rem", 20)]),
            _session_dict("sleep-2", start + timedelta(days=1)),
        ],
    )
    ack = (await api.post(SLEEP_INGEST_PATH, json=second)).json()
    assert ack["records_received"] == 2
    assert ack["records_inserted"] == 1  # only sleep-2 is new
    assert ack["records_duplicate"] == 1

    stage_count: int = (
        await db.execute(
            text(
                "SELECT count(*) FROM health.sleep_stages "
                "WHERE session_source_record_id = 'sleep-1'"
            )
        )
    ).scalar_one()
    assert stage_count == 1  # the old copy's stages, not the replay's two


@requires_db
async def test_sleep_sessions_device_token_principal(
    guarded_api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Bearer sqt_dev_… sessions land under the paired device."""
    token, device_id = await _pair_device(guarded_api)
    start = datetime(2026, 9, 1, 22, 0, tzinfo=UTC)
    body = _sleep_payload(
        str(uuid.uuid4()), [_session_dict("sleep-1", start, [("deep", 45)])]
    )

    response = await guarded_api.post(
        SLEEP_INGEST_PATH, json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text

    device_ids: Sequence[str] = (
        await db.execute(text("SELECT device_id::text FROM health.sleep_sessions"))
    ).scalars().all()
    assert device_ids == [device_id]


# ── RR intervals: ingest ────────────────────────────────────────────────


@requires_db
async def test_rr_intervals_happy_path(api: httpx.AsyncClient, db: AsyncSession) -> None:
    """Records land on the hypertable with decoder/batch provenance."""
    batch_id = str(uuid.uuid4())
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    body = _rr_payload(
        batch_id,
        [
            _rr_record(1, base, 812, 100),
            _rr_record(2, base + timedelta(seconds=1), 796, 101),
            _rr_record(3, base + timedelta(seconds=2), 780, 102),
        ],
    )

    response = await api.post(RR_INGEST_PATH, json=body)
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["records_received"] == 3
    assert ack["records_inserted"] == 3
    assert ack["records_duplicate"] == 0

    row = (
        await db.execute(
            text(
                "SELECT rr_ms, seq, decoder_version, raw_batch_id "
                "FROM timeseries.rr_interval WHERE source_record_id = 'rr-1'"
            )
        )
    ).one()
    assert row.rr_ms == 812
    assert row.seq == 100
    assert row.decoder_version == DECODER_VERSION
    assert str(row.raw_batch_id) == batch_id


@requires_db
async def test_rr_intervals_replay_counts_duplicates(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Same batch UUID + same content replays the ack without new rows."""
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    body = _rr_payload(
        str(uuid.uuid4()), [_rr_record(1, base), _rr_record(2, base + timedelta(seconds=1))]
    )
    assert (await api.post(RR_INGEST_PATH, json=body)).status_code == 200

    replay = await api.post(RR_INGEST_PATH, json=body)
    assert replay.status_code == 200
    replay_ack = replay.json()
    assert replay_ack["records_inserted"] == 0
    assert replay_ack["records_duplicate"] == 2

    count: int = (
        await db.execute(text("SELECT count(*) FROM timeseries.rr_interval"))
    ).scalar_one()
    assert count == 2


@requires_db
async def test_rr_intervals_tampered_replay_conflicts(api: httpx.AsyncClient) -> None:
    """Same UUID with different rr_ms is a 409."""
    batch_id = str(uuid.uuid4())
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    first = _rr_payload(batch_id, [_rr_record(1, base, 800)])
    assert (await api.post(RR_INGEST_PATH, json=first)).status_code == 200

    tampered = _rr_payload(batch_id, [_rr_record(1, base, 900)])
    conflict = await api.post(RR_INGEST_PATH, json=tampered)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"


@requires_db
async def test_rr_intervals_out_of_bounds_rejected(api: httpx.AsyncClient) -> None:
    """rr_ms outside the physiologic 200..2500 ms window fails with 422."""
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    body = _rr_payload(str(uuid.uuid4()), [_rr_record(1, base, rr_ms=100)])
    response = await api.post(RR_INGEST_PATH, json=body)
    assert response.status_code == 422


@requires_db
async def test_rr_intervals_intra_batch_duplicate_natural_key(api: httpx.AsyncClient) -> None:
    """The same (source_record_id, ts) twice inside one batch dedupes."""
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    body = _rr_payload(
        str(uuid.uuid4()),
        [_rr_record(1, base, 800), _rr_record(1, base, 820)],  # same natural key
    )
    response = await api.post(RR_INGEST_PATH, json=body)
    assert response.status_code == 200, response.text
    ack = response.json()
    assert ack["records_received"] == 2
    assert ack["records_inserted"] == 1
    assert ack["records_duplicate"] == 1


@requires_db
async def test_rr_intervals_device_token_principal(
    guarded_api: httpx.AsyncClient, db: AsyncSession
) -> None:
    """Bearer sqt_dev_… RR rows land under the paired device."""
    token, device_id = await _pair_device(guarded_api)
    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    body = _rr_payload(str(uuid.uuid4()), [_rr_record(1, base)])

    response = await guarded_api.post(
        RR_INGEST_PATH, json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text

    device_ids: Sequence[str] = (
        await db.execute(text("SELECT device_id::text FROM timeseries.rr_interval"))
    ).scalars().all()
    assert device_ids == [device_id]


@requires_db
async def test_rr_replay_of_daily_batch_uuid_conflicts(api: httpx.AsyncClient) -> None:
    """The batch UUID idempotency key is global across families (ADR 0006)."""
    batch_id = str(uuid.uuid4())
    daily = _daily_payload(batch_id, [_daily_item(_today_utc(), "resting_hr", 52.0)])
    assert (await api.post(DAILY_INGEST_PATH, json=daily)).status_code == 200

    base = datetime(2026, 9, 2, 6, 0, 0, tzinfo=UTC)
    rr = _rr_payload(batch_id, [_rr_record(1, base)])
    conflict = await api.post(RR_INGEST_PATH, json=rr)
    assert conflict.status_code == 409


# ── reads ───────────────────────────────────────────────────────────────


@requires_db
async def test_daily_read_lists_only_present_days(api: httpx.AsyncClient) -> None:
    """Ascending days with data only — no filler days, old data excluded."""
    today = _today_utc()
    body = _daily_payload(
        str(uuid.uuid4()),
        [
            _daily_item(today, "resting_hr", 52.0),
            _daily_item(today, "avg_hrv", 68.0),
            _daily_item(today - timedelta(days=1), "resting_hr", 54.0),
            _daily_item(today - timedelta(days=40), "recovery", 0.9),  # outside window
        ],
    )
    assert (await api.post(DAILY_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(DAILY_READ_PATH, params={"days": 14})
    assert response.status_code == 200
    days = response.json()["days"]
    assert [d["day"] for d in days] == [
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
    ]
    assert days[0]["metrics"] == {"resting_hr": 54.0}
    assert days[1]["metrics"] == {"resting_hr": 52.0, "avg_hrv": 68.0}


@requires_db
async def test_daily_read_prefers_freshest_device_report(guarded_api: httpx.AsyncClient) -> None:
    """Two devices reporting the same (day, metric): latest received wins."""
    day = _today_utc()
    seed_report = _daily_payload(str(uuid.uuid4()), [_daily_item(day, "resting_hr", 50.0)])
    assert (await guarded_api.post(DAILY_INGEST_PATH, json=seed_report)).status_code == 200
    token, _device_id = await _pair_device(guarded_api)
    paired_report = _daily_payload(str(uuid.uuid4()), [_daily_item(day, "resting_hr", 55.0)])
    assert (
        await guarded_api.post(
            DAILY_INGEST_PATH,
            json=paired_report,
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200

    response = await guarded_api.get(DAILY_READ_PATH, params={"days": 14})
    metrics = response.json()["days"][0]["metrics"]
    assert metrics["resting_hr"] == 55.0


@requires_db
async def test_daily_read_empty_window(api: httpx.AsyncClient) -> None:
    """A window with no data returns an empty days list."""
    body = _daily_payload(
        str(uuid.uuid4()), [_daily_item(_today_utc() - timedelta(days=40), "recovery", 0.9)]
    )
    assert (await api.post(DAILY_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(DAILY_READ_PATH, params={"days": 14})
    assert response.status_code == 200
    assert response.json()["days"] == []


@requires_db
async def test_daily_read_days_bounds_validated(api: httpx.AsyncClient) -> None:
    """days must stay within 1..120 (FastAPI query validation → 422)."""
    assert (await api.get(DAILY_READ_PATH, params={"days": 0})).status_code == 422
    assert (await api.get(DAILY_READ_PATH, params={"days": 121})).status_code == 422


@requires_db
async def test_sleep_read_returns_sessions_with_stages(api: httpx.AsyncClient) -> None:
    """Ascending by start_ts, stages nested, outside-window sessions excluded."""
    now = datetime.now(UTC)
    s1_start = now - timedelta(days=2)
    s2_start = now - timedelta(days=1)
    body = _sleep_payload(
        str(uuid.uuid4()),
        [
            _session_dict("sleep-old", now - timedelta(days=30), [("light", 30)]),
            _session_dict(
                "sleep-1",
                s1_start,
                [("deep", 60), ("rem", 20)],
                efficiency=0.93,
                resting_hr=51.0,
                avg_hrv=80.0,
                user_edited=True,
            ),
            _session_dict("sleep-2", s2_start),
        ],
    )
    assert (await api.post(SLEEP_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(SLEEP_READ_PATH, params={"days": 7})
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert [s["source_record_id"] for s in sessions] == ["sleep-1", "sleep-2"]

    first, second = sessions
    assert datetime.fromisoformat(first["start_ts"]) == s1_start
    assert datetime.fromisoformat(first["end_ts"]) == s1_start + timedelta(hours=8)
    assert first["efficiency"] == 0.93
    assert first["resting_hr"] == 51.0
    assert first["avg_hrv"] == 80.0
    assert first["user_edited"] is True
    assert first["stages_count"] == 2
    assert [stage["state"] for stage in first["stages"]] == ["deep", "rem"]
    assert second["stages_count"] == 0
    assert second["stages"] == []
    assert second["efficiency"] is None


@requires_db
async def test_sleep_read_empty_window(api: httpx.AsyncClient) -> None:
    """No sessions in window → empty list."""
    body = _sleep_payload(
        str(uuid.uuid4()),
        [_session_dict("sleep-old", datetime.now(UTC) - timedelta(days=30))],
    )
    assert (await api.post(SLEEP_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(SLEEP_READ_PATH, params={"days": 7})
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def _floor_to_bucket(ts: datetime, minutes: int) -> datetime:
    """Same 5-minute epoch alignment TimescaleDB time_bucket uses."""
    return ts.replace(minute=(ts.minute // minutes) * minutes, second=0, microsecond=0)


@requires_db
async def test_rr_read_bucket_math(api: httpx.AsyncClient) -> None:
    """5-minute buckets carry the mean rr_ms and the per-bucket count."""
    now = datetime.now(UTC)
    samples = [
        (now - timedelta(minutes=55), 800),
        (now - timedelta(minutes=53), 900),
        (now - timedelta(minutes=30), 700),
    ]
    body = _rr_payload(
        str(uuid.uuid4()),
        [_rr_record(i, ts, rr_ms) for i, (ts, rr_ms) in enumerate(samples)],
    )
    assert (await api.post(RR_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(RR_READ_PATH, params={"last_hours": 2, "bucket": "5m"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"] == "rr_interval"
    assert payload["unit"] == "ms"

    # Expected grouping computed independently of the database.
    totals: dict[datetime, tuple[float, int]] = {}
    for ts, rr_ms in samples:
        bucket = _floor_to_bucket(ts, 5)
        total, count = totals.get(bucket, (0.0, 0))
        totals[bucket] = (total + rr_ms, count + 1)

    points = payload["points"]
    assert payload["count"] == len(points) == len(totals)
    for point in points:
        bucket = datetime.fromisoformat(point["ts"])
        total, count = totals[bucket]
        assert point["sample_count"] == count
        assert point["rr_ms"] == pytest.approx(total / count)


@requires_db
async def test_rr_read_raw_points(api: httpx.AsyncClient) -> None:
    """bucket=none returns raw intervals ascending, one sample each."""
    now = datetime.now(UTC)
    samples = [
        (now - timedelta(minutes=10), 750),
        (now - timedelta(minutes=11), 810),
        (now - timedelta(minutes=9), 770),
    ]
    body = _rr_payload(
        str(uuid.uuid4()),
        [_rr_record(i, ts, rr_ms) for i, (ts, rr_ms) in enumerate(samples)],
    )
    assert (await api.post(RR_INGEST_PATH, json=body)).status_code == 200

    response = await api.get(RR_READ_PATH, params={"last_hours": 1, "bucket": "none"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    stamps = [datetime.fromisoformat(p["ts"]) for p in payload["points"]]
    assert stamps == sorted(stamps)
    assert {p["rr_ms"] for p in payload["points"]} == {750.0, 810.0, 770.0}
    assert all(p["sample_count"] == 1 for p in payload["points"])
    assert payload["caveats"] == []


@requires_db
async def test_rr_read_empty_window(api: httpx.AsyncClient) -> None:
    """No RR data in range → zero points with the explicit no-data caveat."""
    response = await api.get(RR_READ_PATH, params={"last_hours": 1})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 0
    assert payload["points"] == []
    assert "no data in range" in payload["caveats"]


@requires_db
async def test_rr_read_bucket_whitelist_validated(api: httpx.AsyncClient) -> None:
    """Buckets outside none/5m/1h fail query validation with 422."""
    response = await api.get(RR_READ_PATH, params={"bucket": "1m"})
    assert response.status_code == 422
