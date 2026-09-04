"""Daily heart summary contracts (spec §72 slice, ADR 0012/0017).

M5 ships the heart-rate-computable subset of the daily feature store:
resting HR (somatriq_rhr_v1), min/mean/max, sample count, coverage and a
data-quality grade. Sleep-window semantics (wake-date attribution of a
NIGHT) apply when sleep data lands; v1 daily rows use the local calendar
day of the samples with the day's effective timezone stored per row.
"""

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field

FEATURE_SET_VERSION = "daily_heart/v1"
RHR_ALGORITHM = "somatriq_rhr_v1"

DataQuality = Literal["good", "fair", "poor", "insufficient"]

# Coverage thresholds vs expected cadence (wearables are worn ~12-16 h/day,
# so "good" starts at half a day of samples).
QUALITY_GOOD = 0.50
QUALITY_FAIR = 0.25
QUALITY_POOR = 0.08

# RHR v1 (frozen definition — changes bump the algorithm version, ADR 0012):
# bucket the local day's samples into 5-minute medians, take the mean of the
# LOWEST 3 bucket medians (≈15 min of quietest heart rate). Requires at
# least 30 non-empty buckets, else resting_hr is null.
RHR_MIN_BUCKETS = 30
RHR_LOWEST_BUCKETS = 3
RHR_BUCKET_MINUTES = 5


class DailyHeartSummary(BaseModel):
    date: date_type
    timezone: str  # the day's effective timezone (ADR 0017)
    resting_hr: float | None = None
    hr_min: float | None = None
    hr_mean: float | None = None
    hr_max: float | None = None
    sample_count: int = 0
    coverage_ratio: float = Field(ge=0.0, le=1.0, default=0.0)
    data_quality: DataQuality = "insufficient"
    algorithm_version: str | None = None  # set when resting_hr is present


class DailySummaryResponse(BaseModel):
    feature_set_version: str = FEATURE_SET_VERSION
    days: list[DailyHeartSummary] = Field(max_length=120)


def grade_quality(coverage_ratio: float) -> DataQuality:
    if coverage_ratio >= QUALITY_GOOD:
        return "good"
    if coverage_ratio >= QUALITY_FAIR:
        return "fair"
    if coverage_ratio >= QUALITY_POOR:
        return "poor"
    return "insufficient"
