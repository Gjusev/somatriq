"""Ingest wire contracts (spec §41-42, ADR 0006).

The batch UUID is the sole idempotency key; the content hash is forensic
(same UUID + different hash -> 409). Per-record identity is
(user_id, device_id, source_record_id, ts) — see the Timescale note in
migration 0002 for why ts participates in the unique index.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from somatriq_contracts.catalog import HEART_RATE_BPM_MAX, HEART_RATE_BPM_MIN


class IngestRecord(BaseModel):
    """One decoded heart-rate observation from the collector."""

    source_record_id: str = Field(min_length=1, max_length=200)
    ts: datetime
    bpm: float

    @model_validator(mode="after")
    def _physiologically_plausible(self) -> "IngestRecord":
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
    batch_id: UUID
    schema_version: str = Field(default="1", min_length=1, max_length=20)
    records: list[IngestRecord] = Field(min_length=1, max_length=10_000)


class IngestAck(BaseModel):
    batch_id: UUID
    accepted: bool
    records_received: int
    records_inserted: int
    records_duplicate: int
    warnings: list[str] = Field(default_factory=list)
    server_time: datetime


class IngestErrorDetail(BaseModel):
    error_code: str
    message: str
    details: list[dict[str, str]] = Field(default_factory=list)
