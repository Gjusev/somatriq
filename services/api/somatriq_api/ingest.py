"""Idempotent heart-rate batch ingest (spec §41-42, ADR 0006).

Contract highlights:

- the batch UUID is the sole idempotency key: a replayed UUID with the same
  content hash returns the original acknowledgement, and the same UUID with a
  different hash is rejected with 409 (never a silent rewrite);
- per-record identity is the hypertable primary key
  (user_id, device_id, source_record_id, ts), so records re-sent inside an
  accepted batch are counted as duplicates rather than inserted twice;
- every write — heart-rate rows, the ingest.batches row and the
  ingest.idempotency_keys row — happens in one transaction, committed once.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestAck, IngestBatchRequest, IngestErrorDetail
from somatriq_db.engine import get_session
from somatriq_db.models import Device, HeartRate, IdempotencyKey, IngestBatch, User
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.security import require_ingest_token

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_ingest_token)],
)

DUPLICATE_REPLAY_WARNING = "duplicate batch replay"

# Per-record natural key, hypertable PK (ADR 0006 + migration 0002 note).
_HEART_RATE_PK = ("user_id", "device_id", "source_record_id", "ts")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _content_hash(request: IngestBatchRequest) -> str:
    """Deterministic forensic hash of the batch payload (ADR 0006 §1)."""
    canonical = json.dumps(
        [record.model_dump(mode="json") for record in request.records],
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _resolve_single_user_target(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Single-user M1: resolve the seeded user and device by query, never by
    hardcoded UUID (real pairing lands with M2, ADR 0015)."""
    user_id: uuid.UUID | None = (
        await session.execute(select(User.id).order_by(User.created_at).limit(1))
    ).scalar_one_or_none()
    device_id: uuid.UUID | None = (
        await session.execute(select(Device.id).order_by(Device.active_from).limit(1))
    ).scalar_one_or_none()
    if user_id is None or device_id is None:
        msg = "single-user M1 requires seeded identity.users and identity.devices rows"
        raise RuntimeError(msg)
    return user_id, device_id


@router.post("/batches", response_model=IngestAck)
async def submit_batch(
    request: IngestBatchRequest, session: SessionDep
) -> IngestAck | JSONResponse:
    content_hash = _content_hash(request)

    existing = (
        await session.execute(select(IngestBatch).where(IngestBatch.batch_id == request.batch_id))
    ).scalar_one_or_none()

    if existing is not None:
        if existing.content_hash != content_hash:
            error = IngestErrorDetail(
                error_code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
                message=(
                    f"batch {request.batch_id} was already accepted with different content; "
                    "reusing a batch UUID requires identical records (ADR 0006)"
                ),
            )
            return JSONResponse(status_code=409, content=error.model_dump())
        return IngestAck(
            batch_id=request.batch_id,
            accepted=True,
            records_received=existing.records_received,
            records_inserted=0,
            records_duplicate=existing.records_received,
            warnings=[DUPLICATE_REPLAY_WARNING],
            server_time=datetime.now(UTC),
        )

    user_id, device_id = await _resolve_single_user_target(session)

    inserted = 0
    for record in request.records:
        stmt = (
            pg_insert(HeartRate)
            .values(
                user_id=user_id,
                device_id=device_id,
                source_record_id=record.source_record_id,
                ts=record.ts,
                bpm=record.bpm,
                raw_batch_id=request.batch_id,
            )
            .on_conflict_do_nothing(index_elements=list(_HEART_RATE_PK))
            .returning(HeartRate.source_record_id)
        )
        row: str | None = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            inserted += 1

    duplicates = len(request.records) - inserted

    session.add(
        IngestBatch(
            batch_id=request.batch_id,
            user_id=user_id,
            device_id=device_id,
            schema_version=request.schema_version,
            content_hash=content_hash,
            records_received=len(request.records),
            records_inserted=inserted,
            records_duplicate=duplicates,
        )
    )
    # Flush so batches precedes idempotency_keys: the FK has no relationship()
    # edge for the unit of work to order by. Still one transaction, one commit.
    await session.flush()
    session.add(IdempotencyKey(batch_id=request.batch_id, content_hash=content_hash))
    await session.commit()

    return IngestAck(
        batch_id=request.batch_id,
        accepted=True,
        records_received=len(request.records),
        records_inserted=inserted,
        records_duplicate=duplicates,
        warnings=[],
        server_time=datetime.now(UTC),
    )
