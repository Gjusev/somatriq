"""Ingest wire contracts (spec §41-42, ADR 0006, ADR 0003).

The batch UUID is the sole idempotency key; the content hash is forensic
(same UUID + different hash -> 409). Per-record identity is
(user_id, device_id, source_record_id, ts) — see the Timescale note in
migration 0002 for why ts participates in the unique index.

Schema v2 (M2, ADR 0003): the envelope carries decoded records and the
raw frame journal segment together. ``raw_ack=True`` in the acknowledgement
is the ONLY authorization for the collector to prune its local raw frames
for that batch — an observations-only ack never authorizes pruning.
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from somatriq_contracts.catalog import HEART_RATE_BPM_MAX, HEART_RATE_BPM_MIN
from somatriq_contracts.raw import RawPayload

SCHEMA_VERSION_V1 = "1"
SCHEMA_VERSION_V2 = "2"
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)


class IngestRecord(BaseModel):
    """One decoded heart-rate observation from the collector."""

    source_record_id: str = Field(min_length=1, max_length=200)
    ts: datetime
    bpm: float

    @model_validator(mode="after")
    def _physiologically_plausible(self) -> Self:
        if not HEART_RATE_BPM_MIN <= self.bpm <= HEART_RATE_BPM_MAX:
            msg = (
                f"bpm {self.bpm} outside catalog range [{HEART_RATE_BPM_MIN}, {HEART_RATE_BPM_MAX}]"
            )
            raise ValueError(msg)
        if self.ts.tzinfo is None:
            msg = "ts must be timezone-aware (spec §65: UTC at rest)"
            raise ValueError(msg)
        return self


class IngestBatchRequest(BaseModel):
    """Observations-only (v1) or observations + raw journal segment (v2)."""

    batch_id: UUID
    schema_version: str = Field(default=SCHEMA_VERSION_V2, min_length=1, max_length=20)
    decoder_version: str | None = Field(default=None, max_length=100)
    records: list[IngestRecord] = Field(min_length=1, max_length=10_000)
    raw: RawPayload | None = None

    @model_validator(mode="after")
    def _version_gates_raw(self) -> Self:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            msg = f"schema_version must be one of {SUPPORTED_SCHEMA_VERSIONS}"
            raise ValueError(msg)
        if self.raw is not None and self.schema_version != SCHEMA_VERSION_V2:
            msg = "raw payload requires schema_version 2"
            raise ValueError(msg)
        return self


class IngestAck(BaseModel):
    batch_id: UUID
    accepted: bool
    records_received: int
    records_inserted: int
    records_duplicate: int
    # Raw coverage (ADR 0003): raw_ack=True iff the blob was durably written
    # AND its raw.raw_batches row committed. This field authorizes phone-side
    # raw pruning for this batch — nothing else does.
    raw_ack: bool = False
    raw_frame_count: int = 0
    raw_bytes_stored: int = 0
    warnings: list[str] = Field(default_factory=list)
    server_time: datetime


class IngestErrorDetail(BaseModel):
    error_code: str
    message: str
    details: list[dict[str, str]] = Field(default_factory=list)
