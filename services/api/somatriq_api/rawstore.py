"""Raw blob store (spec §48-49, ADR 0003).

Large original payloads never live in PostgreSQL: the compressed journal
segment is written verbatim to a volume at the §49 layout and only its
path/hash/size metadata reaches ``raw.raw_batches``. The payload is opaque —
already zstd on arrival — and is never decompressed server-side until the M4
replay tooling exists.
"""

import os
import uuid as uuid_module
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID


class RawBlobStore(Protocol):
    """Volume abstraction (§48); S3 lands later, the layout is the contract."""

    def write(
        self, user_id: UUID, device_id: UUID, batch_id: UUID, payload: bytes, received_at: datetime
    ) -> Path:
        """Durably store the verbatim blob and return its final path."""
        ...  # pragma: no cover - protocol signature only


class LocalVolumeRawBlobStore:
    """Local-volume writer: {root}/year/month/day/{user}/{device}/{batch}.zst.

    Date parts come from ``received_at`` and are zero-padded (§49). The write
    is atomic: payload lands in a temp file inside the final directory, is
    fsynced, then ``os.replace``d onto the final name — a reader either sees
    the previous complete blob or the new complete blob, never a partial one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def blob_path(
        self, user_id: UUID, device_id: UUID, batch_id: UUID, received_at: datetime
    ) -> Path:
        return (
            self.root
            / f"{received_at.year:04d}"
            / f"{received_at.month:02d}"
            / f"{received_at.day:02d}"
            / str(user_id)
            / str(device_id)
            / f"{batch_id}.zst"
        )

    def write(
        self, user_id: UUID, device_id: UUID, batch_id: UUID, payload: bytes, received_at: datetime
    ) -> Path:
        final = self.blob_path(user_id, device_id, batch_id, received_at)
        final.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp name: concurrent writers of the same batch never share
        # a temp file; os.replace keeps the final swap atomic on every OS.
        temp = final.with_name(f".{final.name}.tmp-{os.getpid()}-{uuid_module.uuid4().hex}")
        try:
            with open(temp, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, final)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return final
