"""HRV + explainable recovery contracts (spec §75-76, ADR 0012/0017).

somatriq_hrv_rmssd_v1 (frozen definition): over the RR intervals of each
sleep session (wake-date attribution ADR 0017), drop artifact PAIRS whose
successive |ΔRR| exceeds 400 ms (filter v1: simple-delta400), then
RMSSD = sqrt(mean(ΔRR²)). SDNN = std of valid RR; pNN50 = share of |ΔRR|
> 50 ms; mean RR kept. Every run records samples / valid_samples /
artifact_count / coverage / filter_version (§75 accounting).

somatriq_recovery_v1 (frozen formula — changes bump the version): robust z
of each input against its trailing-28-day personal baseline (median +
inclusive IQR, somatriq_baseline_v1), combined

    score = 100 * clamp( 0.5 + Σ wᵢ·tanh(z'ᵢ)/2 , 0, 1 )
    z'_hrv = +z_hrv ; z'_rhr = -z_rhr ; z'_sleep = +z_sleep

weights {hrv: 0.4, rhr: 0.3, sleep: 0.3}. Contribution is positive when
z' > +0.5, negative when z' < -0.5, else neutral. Missing inputs are
LISTED, never imputed; score is null unless all three inputs exist.
"""

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field

HRV_ALGORITHM = "somatriq_hrv_rmssd_v1"
HRV_FILTER_VERSION = "simple-delta400"
HRV_ARTIFACT_DELTA_MS = 400

RECOVERY_ALGORITHM = "somatriq_recovery_v1"
RECOVERY_WEIGHTS: dict[str, float] = {"hrv": 0.4, "rhr": 0.3, "sleep": 0.3}
RECOVERY_NEUTRAL_Z = 0.5
RECOVERY_BASELINE_DAYS = 28
RECOVERY_BASELINE_MIN_DAYS = 7

Contribution = Literal["positive", "negative", "neutral"]


class HrvSummary(BaseModel):
    """HRV over the day's sleep sessions (wake-date attribution)."""

    day: date_type
    rmssd_ms: float | None = None
    sdnn_ms: float | None = None
    pnn50: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_rr_ms: float | None = None
    samples: int = 0
    valid_samples: int = 0
    artifact_count: int = 0
    coverage: float = Field(ge=0.0, le=1.0, default=0.0)  # valid / samples
    filter_version: str = HRV_FILTER_VERSION
    algorithm_version: str = HRV_ALGORITHM
    session_count: int = 0


class RecoveryContribution(BaseModel):
    """One explainability row (spec §76: recovery must be explainable)."""

    input: Literal["hrv", "rhr", "sleep", "temperature", "training_load"]
    value: float | None = None
    baseline_median: float | None = None
    baseline_iqr: float | None = None
    robust_z: float | None = None
    contribution: Contribution = "neutral"
    note: str | None = None  # e.g. "input missing in v1"


class RecoveryResult(BaseModel):
    day: date_type
    score: float | None = Field(default=None, ge=0.0, le=100.0)
    algorithm_version: str = RECOVERY_ALGORITHM
    contributions: list[RecoveryContribution] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SleepSummary(BaseModel):
    """Last night (wake-date = today, ADR 0017)."""

    day: date_type
    duration_minutes: float | None = None
    efficiency: float | None = Field(default=None, ge=0.0, le=1.0)
    resting_hr: float | None = None
    avg_hrv: float | None = None
    source_record_ids: list[str] = Field(default_factory=list)


class TodayResponse(BaseModel):
    date: date_type
    timezone: str
    recovery: RecoveryResult | None = None
    hrv: HrvSummary | None = None
    sleep: SleepSummary | None = None
    resting_hr: float | None = None
    resting_hr_quality: str | None = None
    data_freshness_minutes: float | None = None  # minutes since newest observation
    caveats: list[str] = Field(default_factory=list)
