"""Replay pipeline: verify a stored batch, re-decode it into parallel
observation versions (ADR 0012) — the live hypertable is never touched.
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from somatriq_db.models import RawBatch, ReplayedObservation
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_replay.decode import DecodedObservation, FrameDecoder
from somatriq_replay.journal import JournalReport, iter_frames, verify_blob
from somatriq_replay.registry import RawBatchInfo


class BatchNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ReplayResult:
    batch_id: UUID
    decoder_version: str
    frames_seen: int
    observations_decoded: int
    inserted: int
    already_present: int


async def _get_batch(session: AsyncSession, batch_id: UUID) -> RawBatchInfo:
    row = (
        await session.execute(select(RawBatch).where(RawBatch.batch_id == batch_id))
    ).scalar_one_or_none()
    if row is None:
        raise BatchNotFound(str(batch_id))
    return RawBatchInfo(
        batch_id=row.batch_id,
        device_id=row.device_id,
        codec=row.codec,
        journal_version=row.journal_version,
        frame_count=row.frame_count,
        payload_sha256=row.payload_sha256,
        byte_size=row.byte_size,
        blob_path=row.blob_path,
        storage_state=row.storage_state,
        received_at=row.received_at,
    )


async def verify_batch(
    session: AsyncSession, batch_id: UUID, raw_root: Path | str
) -> JournalReport:
    info = await _get_batch(session, batch_id)
    return verify_blob(
        info.resolve(raw_root),
        expected_sha256=info.payload_sha256,
        expected_frame_count=info.frame_count,
    )


async def replay_batch(
    session: AsyncSession,
    batch_id: UUID,
    decoder: FrameDecoder,
    raw_root: Path | str,
    *,
    user_id: UUID,
) -> ReplayResult:
    """Re-decode one archived batch with `decoder` and write every resulting
    observation into timeseries.replayed_observations (idempotent: existing
    rows for this decoder version are counted, not rewritten)."""
    info = await _get_batch(session, batch_id)
    frames = iter_frames(info.resolve(raw_root).read_bytes())
    decoded: list[DecodedObservation] = []
    for frame in frames:
        if (obs := decoder.decode(frame)) is not None:
            decoded.append(obs)

    inserted = 0
    for obs in decoded:
        stmt = (
            pg_insert(ReplayedObservation)
            .values(
                decoder_version=decoder.decoder_version,
                user_id=user_id,
                device_id=info.device_id,
                source_record_id=obs.source_record_id,
                ts=obs.ts,
                bpm=obs.bpm,
                raw_batch_id=batch_id,
                source_frame_epoch_ms=obs.source_frame_epoch_ms,
            )
            .on_conflict_do_nothing()
            .returning(ReplayedObservation.source_record_id)
        )
        row: str | None = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            inserted += 1
    await session.commit()

    return ReplayResult(
        batch_id=batch_id,
        decoder_version=decoder.decoder_version,
        frames_seen=len(frames),
        observations_decoded=len(decoded),
        inserted=inserted,
        already_present=len(decoded) - inserted,
    )
