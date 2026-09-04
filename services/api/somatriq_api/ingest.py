"""Idempotent heart-rate batch ingest (spec §41-42, ADR 0006, ADR 0003).

Contract highlights:

- the batch UUID is the sole idempotency key: a replayed UUID with the same
  content hash returns the original acknowledgement, and the same UUID with a
  different hash is rejected with 409 (never a silent rewrite);
- the content hash covers the FULL v2 envelope — records + decoder_version +
  the raw payload hash — so a v1 replay of a previously-v2 batch conflicts,
  which is correct forensic behavior: the raw journal is part of the batch's
  identity (ADR 0003);
- per-record identity is the hypertable primary key
  (user_id, device_id, source_record_id, ts), so records re-sent inside an
  accepted batch are counted as duplicates rather than inserted twice;
- raw handling (envelope v2): the payload is verified against its sha256,
  written VERBATIM to the blob store (never decompressed server-side), and
  registered in raw.raw_batches in the SAME transaction as everything else.
  ``raw_ack=True`` only when the blob is durably written AND the registry row
  commits — it is the collector's only prune authorization (§50, ADR 0003);
- every write — heart-rate rows, the ingest.batches row, the raw registry
  row and the ingest.idempotency_keys row — happens in one transaction,
  committed once.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestAck, IngestBatchRequest, IngestErrorDetail
from somatriq_db.engine import get_session
from somatriq_db.models import HeartRate, IdempotencyKey, IngestBatch, RawBatch
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import resolve_single_user_target
from somatriq_api.rawstore import LocalVolumeRawBlobStore
from somatriq_api.security import require_ingest_principal
from somatriq_api.settings import get_settings

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_ingest_principal)],
)

DUPLICATE_REPLAY_WARNING = "duplicate batch replay"

# Per-record natural key, hypertable PK (ADR 0006 + migration 0002 note).
_HEART_RATE_PK = ("user_id", "device_id", "source_record_id", "ts")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _content_hash(request: IngestBatchRequest) -> str:
    """Deterministic forensic hash of the full envelope (ADR 0006 §1).

    v2 covers records + decoder_version + the raw payload's sha256; v1
    envelopes hash with both extras absent/None.
    """
    canonical = json.dumps(
        {
            "records": [record.model_dump(mode="json") for record in request.records],
            "decoder_version": request.decoder_version,
            "raw": request.raw.payload_sha256 if request.raw is not None else None,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _resolve_identity(
    session: AsyncSession, http_request: Request
) -> tuple[uuid.UUID, uuid.UUID]:
    """Device-token principals win; otherwise the M1 single-user fallback."""
    user_id = getattr(http_request.state, "user_id", None)
    device_id = getattr(http_request.state, "device_id", None)
    if user_id is not None and device_id is not None:
        return user_id, device_id
    return await resolve_single_user_target(session)


@router.post("/batches", response_model=IngestAck)
async def submit_batch(
    request: IngestBatchRequest, session: SessionDep, http_request: Request
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
        # Replay acks the raw truth stored the first time (ADR 0003 §50):
        # raw_ack comes from the persisted counters, never re-derived.
        return IngestAck(
            batch_id=request.batch_id,
            accepted=True,
            records_received=existing.records_received,
            records_inserted=0,
            records_duplicate=existing.records_received,
            raw_ack=existing.raw_frame_count > 0,
            raw_frame_count=existing.raw_frame_count,
            raw_bytes_stored=existing.raw_bytes_stored,
            warnings=[DUPLICATE_REPLAY_WARNING],
            server_time=datetime.now(UTC),
        )

    # Raw integrity gate BEFORE any write: the declared hash must equal the
    # sha256 of the decoded (still-compressed) bytes.
    raw_bytes: bytes | None = None
    if request.raw is not None:
        raw_bytes = request.raw.decoded_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != request.raw.payload_sha256:
            error = IngestErrorDetail(
                error_code=ErrorCode.VALIDATION.value,
                message="raw.payload_sha256 does not match the decoded payload bytes",
            )
            return JSONResponse(status_code=422, content=error.model_dump())

    user_id, device_id = await _resolve_identity(session, http_request)

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
                decoder_version=request.decoder_version,
                raw_batch_id=request.batch_id,
            )
            .on_conflict_do_nothing(index_elements=list(_HEART_RATE_PK))
            .returning(HeartRate.source_record_id)
        )
        row: str | None = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            inserted += 1

    duplicates = len(request.records) - inserted

    received_at = datetime.now(UTC)
    blob_path: str | None = None
    if request.raw is not None and raw_bytes is not None:
        store = LocalVolumeRawBlobStore(Path(get_settings().raw_dir))
        # The blob write PRECEDES the commit (fsync'd file, §49 layout): if
        # the transaction then fails, an orphan blob file may remain —
        # acceptable, because a replay of the same batch atomically
        # overwrites the same deterministic path.
        blob_path = str(
            store.write(user_id, device_id, request.batch_id, raw_bytes, received_at)
        )

    raw_frame_count = request.raw.frame_count if request.raw is not None else 0
    raw_bytes_stored = len(raw_bytes) if raw_bytes is not None else 0

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
            raw_frame_count=raw_frame_count,
            raw_bytes_stored=raw_bytes_stored,
        )
    )
    # Flush so batches precedes idempotency_keys/raw_batches: their FKs have
    # no relationship() edge for the unit of work to order by. Still one
    # transaction, one commit.
    await session.flush()
    if request.raw is not None and blob_path is not None:
        session.add(
            RawBatch(
                batch_id=request.batch_id,
                device_id=device_id,
                codec=request.raw.codec,
                journal_version=request.raw.journal_version,
                frame_count=request.raw.frame_count,
                payload_sha256=request.raw.payload_sha256,
                byte_size=raw_bytes_stored,
                blob_path=blob_path,
                storage_state="confirmed",
            )
        )
    session.add(IdempotencyKey(batch_id=request.batch_id, content_hash=content_hash))
    await session.commit()

    return IngestAck(
        batch_id=request.batch_id,
        accepted=True,
        records_received=len(request.records),
        records_inserted=inserted,
        records_duplicate=duplicates,
        raw_ack=request.raw is not None,
        raw_frame_count=raw_frame_count,
        raw_bytes_stored=raw_bytes_stored,
        warnings=[],
        server_time=datetime.now(UTC),
    )
