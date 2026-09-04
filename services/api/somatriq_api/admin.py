"""Admin surface for the raw archive: list, verify, replay (spec §140, §197).

Account-JWT only (owner operations; ADR 0015 — device tokens carry
ingest.write and can never reach these routes).
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session
from somatriq_replay.decode import WHOOP4_REALTIME_HR_V1, Whoop4RealtimeHrDecoder
from somatriq_replay.journal import JournalError
from somatriq_replay.pipeline import BatchNotFound, replay_batch, verify_batch
from somatriq_replay.registry import list_batches
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import AccountJwtDep
from somatriq_api.errors import ApiError
from somatriq_api.settings import get_settings

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class RawBatchView(BaseModel):
    batch_id: UUID
    device_id: UUID
    codec: str
    journal_version: int
    frame_count: int
    payload_sha256: str
    byte_size: int
    storage_state: str
    received_at: datetime


class VerifyView(BaseModel):
    batch_id: UUID
    frame_count: int
    sha256: str
    byte_size: int
    first_epoch_ms: int | None
    last_epoch_ms: int | None


class ReplayView(BaseModel):
    batch_id: UUID
    decoder_version: str
    frames_seen: int
    observations_decoded: int
    inserted: int
    already_present: int


@router.get("/raw-batches", response_model=list[RawBatchView])
async def get_raw_batches(session: SessionDep, _: AccountJwtDep) -> list[RawBatchView]:
    infos = await list_batches(session)
    return [
        RawBatchView(
            batch_id=i.batch_id,
            device_id=i.device_id,
            codec=i.codec,
            journal_version=i.journal_version,
            frame_count=i.frame_count,
            payload_sha256=i.payload_sha256,
            byte_size=i.byte_size,
            storage_state=i.storage_state,
            received_at=i.received_at,
        )
        for i in infos
    ]


@router.get("/raw-batches/{batch_id}/verify", response_model=VerifyView)
async def verify_raw_batch(
    batch_id: UUID, session: SessionDep, _: AccountJwtDep
) -> VerifyView:
    settings = get_settings()
    try:
        report = await verify_batch(session, batch_id, settings.raw_dir)
    except BatchNotFound:
        raise ApiError(404, ErrorCode.NOT_FOUND, "raw batch not found") from None
    except JournalError as exc:
        raise ApiError(422, ErrorCode.PERMANENT, f"replay verification failed: {exc}") from None
    return VerifyView(
        batch_id=batch_id,
        frame_count=report.frame_count,
        sha256=report.sha256,
        byte_size=report.byte_size,
        first_epoch_ms=report.first_epoch_ms,
        last_epoch_ms=report.last_epoch_ms,
    )


@router.post("/raw-batches/{batch_id}/replay", response_model=ReplayView)
async def replay_raw_batch(
    batch_id: UUID, session: SessionDep, user_id: AccountJwtDep
) -> ReplayView:
    settings = get_settings()
    try:
        result = await replay_batch(
            session,
            batch_id,
            Whoop4RealtimeHrDecoder(),
            settings.raw_dir,
            user_id=user_id,
        )
    except BatchNotFound:
        raise ApiError(404, ErrorCode.NOT_FOUND, "raw batch not found") from None
    except JournalError as exc:
        raise ApiError(422, ErrorCode.PERMANENT, f"replay failed: {exc}") from None
    return ReplayView(
        batch_id=result.batch_id,
        decoder_version=result.decoder_version,
        frames_seen=result.frames_seen,
        observations_decoded=result.observations_decoded,
        inserted=result.inserted,
        already_present=result.already_present,
    )


# Decoder registry (§140 reprocessing selects by algorithm version).
DECODERS = {WHOOP4_REALTIME_HR_V1: Whoop4RealtimeHrDecoder}


class RecomputeView(BaseModel):
    invalidated_days: int
    note: str


@router.post("/recompute/daily", response_model=RecomputeView)
async def recompute_daily(
    days: Annotated[int, Query(ge=1, le=366)] = 14,
    session: SessionDep = None,  # type: ignore[assignment]
    _: AccountJwtDep = None,  # type: ignore[assignment]
) -> RecomputeView:
    """§140 bounded reprocessing: drop the cached daily_features rows for the
    last `days` local days (this feature_set_version only). The next
    /metrics/daily read materializes them fresh from the hypertable — used
    when a closed day was cached before its data arrived (late backfill,
    timezone change, or an ingest replay). New feature_set_versions are
    never touched: those coexist per ADR 0012.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    today = datetime.now(UTC).astimezone(tz).date()
    first = today - timedelta(days=days - 1)
    result = await session.execute(
        text(
            "DELETE FROM derived.daily_features "
            "WHERE feature_set_version = :fsv AND date BETWEEN :first AND :last"
        ),
        {"fsv": FEATURE_SET_VERSION, "first": first, "last": today},
    )
    invalidated = cast(int, getattr(result, "rowcount", 0) or 0)
    await session.commit()
    return RecomputeView(
        invalidated_days=invalidated,
        note="rows dropped; the next /metrics/daily read recomputes them",
    )
