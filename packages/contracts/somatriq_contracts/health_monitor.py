"""Health Monitor contracts (Block 3; CONTEXT "Vital"; ADR 0009/0012).

somatriq_health_monitor_v1 (frozen — changes bump the version): each
vital's period median is compared to its personal baseline
(somatriq_baseline_v1, median + inclusive IQR over the frozen trailing
window BEFORE the report period) as a robust z. Status vocabulary is
directional against the personal baseline — `elevated` / `reduced` /
`within` — NEVER clinical: deviations are phrased "vs your baseline",
and the report carries a permanent non-medical disclaimer.
"""

from typing import Literal

from pydantic import BaseModel, Field

HEALTH_MONITOR_ALGORITHM = "somatriq_health_monitor_v1"

HEALTH_MONITOR_BASELINE_DAYS = 90  # trailing window BEFORE the report period
HEALTH_MONITOR_MIN_BASELINE_DAYS = 7  # somatriq_baseline_v1 minimum applies
HEALTH_MONITOR_Z_THRESHOLD = 1.0  # |z| >= threshold is elevated/reduced

HEALTH_MONITOR_ALLOWED_PERIODS: tuple[int, ...] = (30, 90, 180)

HEALTH_MONITOR_DISCLAIMER = (
    "Wellness analytics over your own data — not a medical device, not a "
    "diagnosis. Deviations are relative to your personal baseline."
)

VitalStatus = Literal["within", "elevated", "reduced", "building", "insufficient"]


class VitalSummary(BaseModel):
    vital: str
    label: str
    source: str  # provenance: e.g. "computed" | "vendor daily (avg_hrv)"
    n_days: int = 0
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    period_median: float | None = None
    baseline_median: float | None = None
    baseline_iqr: float | None = None
    robust_z: float | None = None
    status: VitalStatus = "insufficient"
    note: str | None = None
    algorithm_version: str = HEALTH_MONITOR_ALGORITHM


class HealthMonitorResponse(BaseModel):
    days: int
    timezone: str
    vitals: list[VitalSummary] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    disclaimer: str = HEALTH_MONITOR_DISCLAIMER
