"""M6 observation families: vendor daily scores, sleep sessions, RR intervals
(spec §41-42 family; ADR 0006 idempotency; ADR 0012 vendor scores are
Observations; sources NOOP Room DailyMetric / SleepSession / RrInterval).

Ingest reuses the heart-rate slice's ADR 0006 machinery from ingest_common:
batch UUID replay, forensic content hash over the family's canonical payload
(items/sessions/records + decoder_version), per-record natural-key dedup via
ON CONFLICT DO NOTHING, and the device-token or transitional global-token
principal. These families carry no raw section (the contracts reject one),
so acknowledgements are always observations-only — ``raw_ack=False`` never
authorizes collector-side pruning (ADR 0003 §50).

Reads are behind the account JWT like the other metric reads (spec §122)
and single-user by the M1 assumption: the daily vendor-observations card
(present days only — clients render their own gaps, unlike the HR daily card
which materializes filler days), the sleep session list with stages, and the
RR viewport series.
"""

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from somatriq_contracts.ingest import IngestAck
from somatriq_contracts.observations import (
    DailyObservationBatchRequest,
    RrIntervalBatchRequest,
    SleepBatchRequest,
)
from somatriq_db.engine import get_session
from somatriq_db.models import DailyObservation, RrInterval, SleepSession, SleepStage
from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import AccountJwtDep
from somatriq_api.ingest_common import (
    canonical_content_hash,
    register_accepted_batch,
    replayed_ack_or_none,
    resolve_identity,
)
from somatriq_api.security import ReadUserDep, require_ingest_principal
from somatriq_api.settings import get_settings

SessionDep = Annotated[AsyncSession, Depends(get_session)]

ingest_router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_ingest_principal)],
)
observations_router = APIRouter(prefix="/api/v1/observations", tags=["observations"])
sleep_router = APIRouter(prefix="/api/v1/sleep", tags=["sleep"])
rr_router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

# Per-record natural keys (migration 0006; the heart_rate hypertable note in
# migration 0002 applies to rr_interval too — the PK includes the
# partitioning column).
_DAILY_OBSERVATIONS_PK: Final[tuple[str, ...]] = ("user_id", "device_id", "day", "metric")
_SLEEP_SESSION_PK: Final[tuple[str, ...]] = (
    "user_id",
    "device_id",
    "source_record_id",
    "start_ts",
)
_RR_PK: Final[tuple[str, ...]] = ("user_id", "device_id", "source_record_id", "ts")

# Multi-row INSERTs must stay under asyncpg's 32 767-argument CLIENT limit —
# the driver rejects the statement before PostgreSQL (ceiling 65 535) ever
# sees it. Rows-per-statement = floor(32 767 / bound-params-per-row); the
# families below bind at most 8 parameters per row (RR), so one conservative
# constant serves all of them. Caught live 2026-09-05: the phone's first
# 20 000-record RR batch crashed rr-intervals at 5 000 rows × 8 = 40 000
# arguments (InterfaceError: the number of query arguments cannot exceed
# 32767); the contract caps a batch at 20 000 records, so the chunking must
# hold for that whole range.
_ASYNCPG_MAX_ARGS: Final = 32767
_MAX_PARAMS_PER_ROW: Final = 8
_INSERT_CHUNK_ROWS: Final = _ASYNCPG_MAX_ARGS // _MAX_PARAMS_PER_ROW  # 4095


def _family_ack(batch_id: uuid.UUID, received: int, inserted: int) -> IngestAck:
    """Observations-family ack: no raw section exists on this wire, so raw
    coverage is always zero (an observations-only ack never authorizes
    pruning, ADR 0003 §50)."""
    return IngestAck(
        batch_id=batch_id,
        accepted=True,
        records_received=received,
        records_inserted=inserted,
        records_duplicate=received - inserted,
        raw_ack=False,
        raw_frame_count=0,
        raw_bytes_stored=0,
        warnings=[],
        server_time=datetime.now(UTC),
    )


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    """Row-sized slices for multi-row INSERTs (see _INSERT_CHUNK_ROWS)."""
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


# ── ingest: vendor daily observations ────────────────────────────────────


@ingest_router.post("/daily-observations", response_model=IngestAck)
async def submit_daily_observations(
    request: DailyObservationBatchRequest, session: SessionDep, http_request: Request
) -> IngestAck:
    """Idempotent vendor daily-score batch (ADR 0006; metric names are
    catalog-governed by the frozen VENDOR_DAILY_METRICS contract)."""
    content_hash = canonical_content_hash(
        {
            "items": [item.model_dump(mode="json") for item in request.items],
            "decoder_version": request.decoder_version,
        }
    )
    replayed = await replayed_ack_or_none(session, request.batch_id, content_hash)
    if replayed is not None:
        return replayed

    user_id, device_id = await resolve_identity(session, http_request)

    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "day": item.day,
            "metric": item.metric,
            "value": item.value,
            "decoder_version": request.decoder_version,
            "raw_batch_id": request.batch_id,
        }
        for item in request.items
    ]
    inserted = 0
    for chunk in _chunks(rows, _INSERT_CHUNK_ROWS):
        result = await session.execute(
            pg_insert(DailyObservation)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=list(_DAILY_OBSERVATIONS_PK))
            .returning(DailyObservation.metric)
        )
        inserted += len(result.all())

    await register_accepted_batch(
        session,
        batch_id=request.batch_id,
        user_id=user_id,
        device_id=device_id,
        schema_version=request.schema_version,
        content_hash=content_hash,
        records_received=len(request.items),
        records_inserted=inserted,
        records_duplicate=len(request.items) - inserted,
    )
    await session.commit()
    return _family_ack(request.batch_id, len(request.items), inserted)


# ── ingest: sleep sessions (stages ride with their session) ──────────────


@ingest_router.post("/sleep-sessions", response_model=IngestAck)
async def submit_sleep_sessions(
    request: SleepBatchRequest, session: SessionDep, http_request: Request
) -> IngestAck:
    """Idempotent sleep-session batch; ack counts sessions (stages included).

    A session that conflicts on its natural key counts as a duplicate and its
    stages are skipped whole — a vendor record is accepted exactly once.
    """
    content_hash = canonical_content_hash(
        {
            "sessions": [s.model_dump(mode="json") for s in request.sessions],
            "decoder_version": request.decoder_version,
        }
    )
    replayed = await replayed_ack_or_none(session, request.batch_id, content_hash)
    if replayed is not None:
        return replayed

    user_id, device_id = await resolve_identity(session, http_request)

    inserted = 0
    for record in request.sessions:
        stmt = (
            pg_insert(SleepSession)
            .values(
                user_id=user_id,
                device_id=device_id,
                source_record_id=record.source_record_id,
                start_ts=record.start_ts,
                end_ts=record.end_ts,
                efficiency=record.efficiency,
                resting_hr=record.resting_hr,
                avg_hrv=record.avg_hrv,
                user_edited=record.user_edited,
                decoder_version=request.decoder_version,
                raw_batch_id=request.batch_id,
            )
            .on_conflict_do_nothing(index_elements=list(_SLEEP_SESSION_PK))
            .returning(SleepSession.source_record_id)
        )
        row: str | None = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            continue  # duplicate session → its stages are skipped with it
        inserted += 1
        if record.stages:
            stage_rows = [
                {
                    "session_user_id": user_id,
                    "session_device_id": device_id,
                    "session_source_record_id": record.source_record_id,
                    "session_start_ts": record.start_ts,
                    "state": stage.state,
                    "stage_start_ts": stage.start_ts,
                    "stage_end_ts": stage.end_ts,
                }
                for stage in record.stages
            ]
            # Same transaction, after the session row: the composite FK is
            # satisfied by the statement order above.
            await session.execute(pg_insert(SleepStage).values(stage_rows))

    await register_accepted_batch(
        session,
        batch_id=request.batch_id,
        user_id=user_id,
        device_id=device_id,
        schema_version=request.schema_version,
        content_hash=content_hash,
        records_received=len(request.sessions),
        records_inserted=inserted,
        records_duplicate=len(request.sessions) - inserted,
    )
    await session.commit()
    return _family_ack(request.batch_id, len(request.sessions), inserted)


# ── ingest: RR intervals ─────────────────────────────────────────────────


@ingest_router.post("/rr-intervals", response_model=IngestAck)
async def submit_rr_intervals(
    request: RrIntervalBatchRequest, session: SessionDep, http_request: Request
) -> IngestAck:
    """Idempotent RR-interval batch onto the timeseries hypertable."""
    content_hash = canonical_content_hash(
        {
            "records": [record.model_dump(mode="json") for record in request.records],
            "decoder_version": request.decoder_version,
        }
    )
    replayed = await replayed_ack_or_none(session, request.batch_id, content_hash)
    if replayed is not None:
        return replayed

    user_id, device_id = await resolve_identity(session, http_request)

    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": record.source_record_id,
            "ts": record.ts,
            "rr_ms": record.rr_ms,
            "seq": record.seq,
            "decoder_version": request.decoder_version,
            "raw_batch_id": request.batch_id,
        }
        for record in request.records
    ]
    inserted = 0
    for chunk in _chunks(rows, _INSERT_CHUNK_ROWS):
        result = await session.execute(
            pg_insert(RrInterval)
            .values(chunk)
            .on_conflict_do_nothing(index_elements=list(_RR_PK))
            .returning(RrInterval.ts)
        )
        inserted += len(result.all())

    await register_accepted_batch(
        session,
        batch_id=request.batch_id,
        user_id=user_id,
        device_id=device_id,
        schema_version=request.schema_version,
        content_hash=content_hash,
        records_received=len(request.records),
        records_inserted=inserted,
        records_duplicate=len(request.records) - inserted,
    )
    await session.commit()
    return _family_ack(request.batch_id, len(request.records), inserted)


# ── read: daily vendor observations ──────────────────────────────────────


class DayObservations(BaseModel):
    """One wake-date and its reported vendor values (absent metrics absent)."""

    day: date
    metrics: dict[str, float]


class DailyObservationsResponse(BaseModel):
    days: list[DayObservations]


@observations_router.get("/daily", response_model=DailyObservationsResponse)
async def read_daily_observations(
    user_id: AccountJwtDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=120)] = 14,
) -> DailyObservationsResponse:
    """Vendor daily observations over the last ``days`` local days; account
    JWT required (spec §122).

    Only days with at least one reported metric are listed — unlike the HR
    daily card, no filler days are synthesized; clients render their own
    gaps. When two devices reported the same (day, metric), the most recently
    received value wins (latest received_at, device_id as tie-break).
    """
    tz = ZoneInfo(get_settings().user_timezone)
    today = datetime.now(UTC).astimezone(tz).date()
    first_day = today - timedelta(days=days - 1)

    result = await session.execute(
        text(
            "SELECT DISTINCT ON (day, metric) day, metric, value "
            "FROM health.daily_observations "
            "WHERE day >= :first_day "
            "ORDER BY day, metric, received_at DESC, device_id"
        ),
        {"first_day": first_day},
    )
    by_day: dict[date, dict[str, float]] = {}
    for row in result.all():
        by_day.setdefault(row[0], {})[row[1]] = float(row[2])
    return DailyObservationsResponse(
        days=[DayObservations(day=day, metrics=metrics) for day, metrics in sorted(by_day.items())]
    )


# ── read: sleep sessions ─────────────────────────────────────────────────


class SleepStagePoint(BaseModel):
    state: str
    start_ts: datetime
    end_ts: datetime


class SleepSessionOut(BaseModel):
    source_record_id: str
    start_ts: datetime
    end_ts: datetime
    efficiency: float | None = None
    resting_hr: float | None = None
    avg_hrv: float | None = None
    user_edited: bool = False
    stages_count: int = 0
    stages: list[SleepStagePoint] = Field(default_factory=list)


class SleepSessionsResponse(BaseModel):
    sessions: list[SleepSessionOut]


@sleep_router.get("/sessions", response_model=SleepSessionsResponse)
async def read_sleep_sessions(
    user_id: ReadUserDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=120)] = 14,
) -> SleepSessionsResponse:
    """Sleep sessions starting within the last ``days`` days, ascending by
    start_ts, stages nested and counted; account JWT or data.read device
    token (spec §122). Duplicate reports of the same (source_record_id,
    start_ts) across devices resolve to the most recently received copy,
    stages included."""
    cutoff = datetime.now(UTC) - timedelta(days=days)

    result = await session.execute(
        text(
            "SELECT DISTINCT ON (source_record_id, start_ts) "
            "user_id, device_id, source_record_id, start_ts, end_ts, "
            "efficiency, resting_hr, avg_hrv, user_edited "
            "FROM health.sleep_sessions "
            "WHERE start_ts >= :cutoff "
            "ORDER BY source_record_id, start_ts, received_at DESC, device_id"
        ),
        {"cutoff": cutoff},
    )
    rows = result.all()
    if not rows:
        return SleepSessionsResponse(sessions=[])

    # Stages for exactly the winning session copies, keyed by their full
    # session identity (the composite FK columns).
    stage_result = await session.execute(
        select(
            SleepStage.session_source_record_id,
            SleepStage.session_start_ts,
            SleepStage.state,
            SleepStage.stage_start_ts,
            SleepStage.stage_end_ts,
        ).where(
            tuple_(
                SleepStage.session_user_id,
                SleepStage.session_device_id,
                SleepStage.session_source_record_id,
                SleepStage.session_start_ts,
            ).in_([(row[0], row[1], row[2], row[3]) for row in rows])
        )
    )
    stages_by_session: dict[tuple[str, datetime], list[SleepStagePoint]] = {}
    for srid, start_ts, state, stage_start, stage_end in stage_result.all():
        stages_by_session.setdefault((srid, start_ts), []).append(
            SleepStagePoint(state=state, start_ts=stage_start, end_ts=stage_end)
        )
    for stages in stages_by_session.values():
        stages.sort(key=lambda stage: stage.start_ts)

    sessions = [
        SleepSessionOut(
            source_record_id=row[2],
            start_ts=row[3],
            end_ts=row[4],
            efficiency=row[5],
            resting_hr=row[6],
            avg_hrv=row[7],
            user_edited=row[8],
            stages_count=len(stages_by_session.get((row[2], row[3]), [])),
            stages=stages_by_session.get((row[2], row[3]), []),
        )
        for row in rows
    ]
    sessions.sort(key=lambda out: (out.start_ts, out.source_record_id))
    return SleepSessionsResponse(sessions=sessions)


# ── read: RR-interval series ─────────────────────────────────────────────


class RrPoint(BaseModel):
    """One raw interval (sample_count=1) or one bucket mean with its count."""

    ts: datetime
    rr_ms: float
    sample_count: int = Field(default=1)


class RrSeriesResponse(BaseModel):
    metric: str = "rr_interval"
    unit: str = "ms"
    points: list[RrPoint]
    count: int
    caveats: list[str] = Field(default_factory=list)
    generated_at: datetime


_RR_BUCKET_INTERVALS: Final[dict[str, timedelta]] = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
}
_RR_RAW_LIMIT: Final = 5000
_RR_TRUNCATION_CAVEAT: Final = "result truncated to 5000 points; use a coarser bucket"
_RR_NO_DATA_CAVEAT: Final = "no data in range"


@rr_router.get("/rr", response_model=RrSeriesResponse)
async def read_rr_intervals(
    user_id: AccountJwtDep,
    session: SessionDep,
    last_hours: Annotated[int, Query(ge=1, le=24 * 400)] = 24,
    bucket: Annotated[str, Query(pattern="^(none|5m|1h)$")] = "5m",
) -> RrSeriesResponse:
    """RR-interval viewport series, mirroring the heart-rate endpoint shape;
    account JWT required (spec §122).

    Buckets carry the mean rr_ms plus the per-bucket sample count — the count
    is the coverage signal here, because rr_interval has no expected cadence
    (system.metrics seeds it NULL), so no coverage ratio is synthesized.
    Missing buckets are simply absent: gaps stay visible as gaps.
    """
    end = datetime.now(UTC)
    start = end - timedelta(hours=last_hours)
    caveats: list[str] = []

    if bucket == "none":
        # Raw intervals capped at the raw limit; the cap is reported, never
        # silent (mirrors the heart-rate raw read, spec §71).
        result = await session.execute(
            text(
                "SELECT ts, rr_ms FROM timeseries.rr_interval "
                "WHERE ts BETWEEN :start AND :end "
                "ORDER BY ts "
                "LIMIT :limit"
            ),
            {"start": start, "end": end, "limit": _RR_RAW_LIMIT + 1},
        )
        rows = result.all()
        if len(rows) > _RR_RAW_LIMIT:
            rows = rows[:_RR_RAW_LIMIT]
            caveats.append(_RR_TRUNCATION_CAVEAT)
        points = [RrPoint(ts=row[0], rr_ms=float(row[1]), sample_count=1) for row in rows]
    else:
        # Aggregation belongs in the database (ADR 0002): time_bucket means
        # with per-bucket counts. The interval reaches SQL as a bound
        # timedelta parameter, never via string interpolation.
        result = await session.execute(
            text(
                "SELECT time_bucket(:interval, ts) AS bucket_ts, "
                "avg(rr_ms) AS avg_rr, count(*) AS sample_count "
                "FROM timeseries.rr_interval "
                "WHERE ts BETWEEN :start AND :end "
                "GROUP BY 1 "
                "ORDER BY 1"
            ),
            {"interval": _RR_BUCKET_INTERVALS[bucket], "start": start, "end": end},
        )
        points = [
            RrPoint(ts=row[0], rr_ms=round(float(row[1]), 2), sample_count=int(row[2]))
            for row in result.all()
        ]

    if not points:
        caveats.append(_RR_NO_DATA_CAVEAT)
    return RrSeriesResponse(
        points=points,
        count=len(points),
        caveats=caveats,
        generated_at=datetime.now(UTC),
    )
