"""Raw-archive registry: find stored batches, resolve blob paths (§49)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from somatriq_db.models import RawBatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RawBatchInfo:
    batch_id: UUID
    device_id: UUID
    codec: str
    journal_version: int
    frame_count: int
    payload_sha256: str
    byte_size: int
    blob_path: str
    storage_state: str
    received_at: datetime

    def resolve(self, raw_root: Path | str) -> Path:
        """Blob paths are stored absolute for the api container (§49 layout);
        tests may override the root."""
        p = Path(self.blob_path)
        return p if p.is_absolute() else Path(raw_root) / p


async def list_batches(session: AsyncSession, limit: int = 100) -> list[RawBatchInfo]:
    rows = (
        await session.execute(select(RawBatch).order_by(RawBatch.received_at).limit(limit))
    ).scalars().all()
    return [
        RawBatchInfo(
            batch_id=r.batch_id,
            device_id=r.device_id,
            codec=r.codec,
            journal_version=r.journal_version,
            frame_count=r.frame_count,
            payload_sha256=r.payload_sha256,
            byte_size=r.byte_size,
            blob_path=r.blob_path,
            storage_state=r.storage_state,
            received_at=r.received_at,
        )
        for r in rows
    ]
