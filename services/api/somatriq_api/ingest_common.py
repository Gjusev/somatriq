"""Shared idempotent-batch machinery for every ingest family (ADR 0006).

All families — heart rate (M1) and the M6 observation families (vendor daily
scores, sleep sessions, RR intervals) — follow the same ADR 0006 discipline:

- the batch UUID is the sole replay idempotency key: a replayed UUID with the
  same content hash returns the original acknowledgement, and the same UUID
  with a different hash is rejected with 409 (never a silent rewrite);
- the content hash is canonical JSON over the family's payload — forensic,
  never a lookup key;
- the ingest.batches row plus the ingest.idempotency_keys row commit in the
  SAME transaction as the family's data rows.

The helpers were extracted from the M1 heart-rate endpoint (ingest.py) so the
wire behavior stays byte-identical across families; ingest.py and
observations.py both consume them.
"""

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from fastapi import Request
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestAck
from somatriq_db.models import IdempotencyKey, IngestBatch, RawBatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import resolve_single_user_target
from somatriq_api.errors import ApiError

DUPLICATE_REPLAY_WARNING = "duplicate batch replay"


def canonical_content_hash(payload: Mapping[str, object]) -> str:
    """Deterministic forensic hash of a family's canonical JSON (ADR 0006 §1)."""
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def resolve_identity(
    session: AsyncSession, http_request: Request
) -> tuple[uuid.UUID, uuid.UUID]:
    """Device-token principals win; otherwise the M1 single-user fallback."""
    user_id = getattr(http_request.state, "user_id", None)
    device_id = getattr(http_request.state, "device_id", None)
    if user_id is not None and device_id is not None:
        return user_id, device_id
    return await resolve_single_user_target(session)


async def replayed_ack_or_none(
    session: AsyncSession, batch_id: uuid.UUID, content_hash: str
) -> IngestAck | None:
    """The original acknowledgement for a replayed batch UUID, or None.

    A same-UUID/different-hash replay raises 409 IDEMPOTENCY_CONFLICT: the
    collector must mint a fresh batch UUID rather than rewrite history.
    """
    existing = (
        await session.execute(select(IngestBatch).where(IngestBatch.batch_id == batch_id))
    ).scalar_one_or_none()
    if existing is None:
        return None
    if existing.content_hash != content_hash:
        raise ApiError(
            status_code=409,
            code=ErrorCode.IDEMPOTENCY_CONFLICT,
            message=(
                f"batch {batch_id} was already accepted with different content; "
                "reusing a batch UUID requires identical records (ADR 0006)"
            ),
        )
    # Replay acks the raw truth stored the first time (ADR 0003 §50):
    # raw_ack comes from the persisted counters, never re-derived.
    return IngestAck(
        batch_id=batch_id,
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


async def register_accepted_batch(
    session: AsyncSession,
    *,
    batch_id: uuid.UUID,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    schema_version: str,
    content_hash: str,
    records_received: int,
    records_inserted: int,
    records_duplicate: int,
    raw_frame_count: int = 0,
    raw_bytes_stored: int = 0,
    raw_batch: RawBatch | None = None,
) -> None:
    """ingest.batches row (+ optional raw registry row) and the idempotency
    key, added in FK order; the caller owns the single transaction and commit."""
    session.add(
        IngestBatch(
            batch_id=batch_id,
            user_id=user_id,
            device_id=device_id,
            schema_version=schema_version,
            content_hash=content_hash,
            records_received=records_received,
            records_inserted=records_inserted,
            records_duplicate=records_duplicate,
            raw_frame_count=raw_frame_count,
            raw_bytes_stored=raw_bytes_stored,
        )
    )
    # Flush so batches precedes idempotency_keys/raw_batches: their FKs have
    # no relationship() edge for the unit of work to order by. Still one
    # transaction, one commit.
    await session.flush()
    if raw_batch is not None:
        session.add(raw_batch)
    session.add(IdempotencyKey(batch_id=batch_id, content_hash=content_hash))
