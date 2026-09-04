"""Observation ingest contracts: vendor daily scores, sleep sessions, RR
intervals (spec §41-42 family, ADR 0006 idempotency, ADR 0012 vendor
scores are Observations).

All three families reuse the M2 machinery: batch UUID is the sole replay
idempotency key, content hash is forensic, per-record natural keys dedup,
device-token auth. Sources: NOOP Room tables DailyMetric, SleepSession,
RrInterval (docs/research/noop-android-schema.md).
"""

from datetime import date as date_type
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from somatriq_contracts.catalog import HEART_RATE_BPM_MAX, HEART_RATE_BPM_MIN
from somatriq_contracts.ingest import IngestAck

# ── vendor daily observations (NOOP dailyMetric; wake-date `day`) ────────

# wire metric names → NOOP column (documented for the collector mirror).
# weight_kg / body_fat_percent have NO NOOP column: they arrive via the CSV
# connector (§129 imports, e.g. a Withings scale export) — additive M13.
VENDOR_DAILY_METRICS: dict[str, str] = {
    "total_sleep_min": "totalSleepMin",
    "efficiency": "efficiency",
    "deep_min": "deepMin",
    "rem_min": "remMin",
    "light_min": "lightMin",
    "disturbances": "disturbances",
    "resting_hr": "restingHr",
    "avg_hrv": "avgHrv",
    "recovery": "recovery",
    "strain": "strain",
    "exercise_count": "exerciseCount",
    "spo2_pct": "spo2Pct",
    "skin_temp_dev_c": "skinTempDevC",
    "resp_rate_bpm": "respRateBpm",
    "steps": "steps",
    "active_kcal_est": "activeKcalEst",
    "avg_sdnn": "avgSdnn",
    "skin_temp_c": "skinTempC",
    "weight_kg": "—",
    "body_fat_percent": "—",
}


class DailyObservationItem(BaseModel):
    """One vendor-reported value for one wake-date (nulls are simply omitted)."""

    day: date_type
    metric: str
    value: float

    @model_validator(mode="after")
    def _metric_known(self) -> "DailyObservationItem":
        if self.metric not in VENDOR_DAILY_METRICS:
            msg = f"unknown metric {self.metric!r}; catalog: {sorted(VENDOR_DAILY_METRICS)}"
            raise ValueError(msg)
        return self


class DailyObservationBatchRequest(BaseModel):
    batch_id: UUID
    schema_version: str = Field(default="1", min_length=1, max_length=20)
    decoder_version: str | None = Field(default=None, max_length=100)
    items: list[DailyObservationItem] = Field(min_length=1, max_length=5_000)
    raw: object | None = None  # reserved: raw archival rides the HR envelope

    @model_validator(mode="after")
    def _no_raw_yet(self) -> "DailyObservationBatchRequest":
        if self.raw is not None:
            msg = "raw payload is not part of this family yet"
            raise ValueError(msg)
        return self


# ── sleep sessions (NOOP sleepSession; wake-date attribution ADR 0017) ──

SleepState = Literal["awake", "light", "deep", "rem"]


class SleepStage(BaseModel):
    state: SleepState
    start_ts: datetime
    end_ts: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "SleepStage":
        if self.end_ts <= self.start_ts:
            msg = "sleep stage end must be after start"
            raise ValueError(msg)
        if self.start_ts.tzinfo is None or self.end_ts.tzinfo is None:
            msg = "sleep stage timestamps must be timezone-aware"
            raise ValueError(msg)
        return self


class SleepSessionRecord(BaseModel):
    source_record_id: str = Field(min_length=1, max_length=200)
    start_ts: datetime
    end_ts: datetime
    efficiency: float | None = Field(default=None, ge=0.0, le=1.0)
    resting_hr: float | None = Field(default=None, ge=HEART_RATE_BPM_MIN, le=HEART_RATE_BPM_MAX)
    avg_hrv: float | None = Field(default=None, ge=0.0)
    user_edited: bool = False
    stages: list[SleepStage] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _session_sane(self) -> "SleepSessionRecord":
        if self.end_ts <= self.start_ts:
            msg = "sleep session end must be after start"
            raise ValueError(msg)
        if self.start_ts.tzinfo is None or self.end_ts.tzinfo is None:
            msg = "sleep session timestamps must be timezone-aware"
            raise ValueError(msg)
        for stage in self.stages:
            if stage.start_ts < self.start_ts or stage.end_ts > self.end_ts:
                msg = f"stage {stage.state} outside session bounds"
                raise ValueError(msg)
        return self


class SleepBatchRequest(BaseModel):
    batch_id: UUID
    schema_version: str = Field(default="1", min_length=1, max_length=20)
    decoder_version: str | None = Field(default=None, max_length=100)
    sessions: list[SleepSessionRecord] = Field(min_length=1, max_length=200)
    raw: object | None = None

    @model_validator(mode="after")
    def _no_raw_yet(self) -> "SleepBatchRequest":
        if self.raw is not None:
            msg = "raw payload is not part of this family yet"
            raise ValueError(msg)
        return self


# ── RR intervals (NOOP rrInterval — OUR OWN HRV source) ─────────────────


class RrIntervalRecord(BaseModel):
    source_record_id: str = Field(min_length=1, max_length=200)
    ts: datetime
    rr_ms: int = Field(ge=200, le=2500)  # physiologic 24..300 bpm
    seq: int = Field(ge=0)

    @model_validator(mode="after")
    def _tz(self) -> "RrIntervalRecord":
        if self.ts.tzinfo is None:
            msg = "ts must be timezone-aware (spec §65: UTC at rest)"
            raise ValueError(msg)
        return self


class RrIntervalBatchRequest(BaseModel):
    batch_id: UUID
    schema_version: str = Field(default="1", min_length=1, max_length=20)
    decoder_version: str | None = Field(default=None, max_length=100)
    records: list[RrIntervalRecord] = Field(min_length=1, max_length=20_000)
    raw: object | None = None

    @model_validator(mode="after")
    def _no_raw_yet(self) -> "RrIntervalBatchRequest":
        if self.raw is not None:
            msg = "raw payload is not part of this family yet"
            raise ValueError(msg)
        return self


__all__ = [
    "DailyObservationBatchRequest",
    "DailyObservationItem",
    "RrIntervalBatchRequest",
    "RrIntervalRecord",
    "SleepBatchRequest",
    "SleepSessionRecord",
    "SleepStage",
    "SleepState",
    "VENDOR_DAILY_METRICS",
    "IngestAck",
]
