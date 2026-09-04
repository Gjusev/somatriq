"""Pure daily heart-summary math (spec §72 slice, ADR 0012/0017).

DB-free by design: the SQL layer (services/api) does the day-window filtering
and 5-minute bucket medians; these helpers do the deterministic Python math
(ADR 0009 — never an LLM). All thresholds come from the frozen contract so
the algorithm and its constants cannot drift apart.
"""

from datetime import date

from somatriq_contracts.daily import (
    RHR_ALGORITHM,
    RHR_LOWEST_BUCKETS,
    RHR_MIN_BUCKETS,
    DailyHeartSummary,
    grade_quality,
)


def resting_hr(bucket_medians: list[float]) -> float | None:
    """Mean of the lowest ``RHR_LOWEST_BUCKETS`` of the day's 5-minute medians.

    ``None`` when fewer than ``RHR_MIN_BUCKETS`` non-empty buckets exist — the
    day simply has not observed enough quiet heart rate to claim a resting
    value (frozen RHR v1 definition; changes bump the algorithm version).
    """
    if len(bucket_medians) < RHR_MIN_BUCKETS:
        return None
    lowest = sorted(bucket_medians)[:RHR_LOWEST_BUCKETS]
    return round(sum(lowest) / len(lowest), 2)


def summarize_day(
    *,
    date: date,
    timezone: str,
    bucket_medians: list[float],
    hr_min: float | None = None,
    hr_mean: float | None = None,
    hr_max: float | None = None,
    sample_count: int = 0,
    coverage_ratio: float = 0.0,
) -> DailyHeartSummary:
    """Assemble the contract summary for one local day from SQL aggregates."""
    computed_resting_hr = resting_hr(bucket_medians)
    return DailyHeartSummary(
        date=date,
        timezone=timezone,
        resting_hr=computed_resting_hr,
        hr_min=None if hr_min is None else round(hr_min, 2),
        hr_mean=None if hr_mean is None else round(hr_mean, 2),
        hr_max=None if hr_max is None else round(hr_max, 2),
        sample_count=sample_count,
        coverage_ratio=coverage_ratio,
        data_quality=grade_quality(coverage_ratio),
        algorithm_version=RHR_ALGORITHM if computed_resting_hr is not None else None,
    )
