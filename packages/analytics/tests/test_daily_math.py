"""Pure daily-summary math (spec §72 slice, ADR 0012/0017).

The SQL layer does the bucketing; these tests pin the deterministic math on
the Python side: the frozen RHR v1 definition and contract assembly.
"""

from datetime import date

from somatriq_analytics.daily import resting_hr, summarize_day


def test_resting_hr_exact_math_30_buckets() -> None:
    """30 buckets 50..79: lowest 3 are 50, 51, 52 -> mean 51.0."""
    medians = [float(v) for v in range(50, 80)]
    assert resting_hr(medians) == 51.0


def test_resting_hr_empty() -> None:
    assert resting_hr([]) is None


def test_resting_hr_below_bucket_threshold() -> None:
    """29 non-empty buckets is one short of RHR_MIN_BUCKETS -> null."""
    medians = [float(v) for v in range(50, 79)]  # 29 values
    assert resting_hr(medians) is None


def test_resting_hr_at_exact_threshold() -> None:
    """Exactly RHR_MIN_BUCKETS (30) buckets computes; lowest-3 of equal values."""
    assert resting_hr([60.0] * 30) == 60.0


def test_resting_hr_takes_lowest_three_regardless_of_order() -> None:
    medians = [90.0, 50.0, 70.0, 51.0, 52.0] + [60.0] * 25
    assert resting_hr(medians) == 51.0


def test_summarize_day_assembles_contract_model() -> None:
    summary = summarize_day(
        date=date(2026, 9, 3),
        timezone="Europe/Madrid",
        bucket_medians=[float(v) for v in range(50, 80)],
        hr_min=50.0,
        hr_mean=64.5,
        hr_max=79.0,
        sample_count=86400,
        coverage_ratio=1.0,
    )
    assert summary.date == date(2026, 9, 3)
    assert summary.timezone == "Europe/Madrid"
    assert summary.resting_hr == 51.0
    assert summary.hr_min == 50.0
    assert summary.hr_mean == 64.5
    assert summary.hr_max == 79.0
    assert summary.sample_count == 86400
    assert summary.coverage_ratio == 1.0
    assert summary.data_quality == "good"
    assert summary.algorithm_version == "somatriq_rhr_v1"


def test_summarize_day_empty_day_is_nulls_and_insufficient() -> None:
    summary = summarize_day(
        date=date(2026, 9, 3),
        timezone="UTC",
        bucket_medians=[],
    )
    assert summary.resting_hr is None
    assert summary.hr_min is None
    assert summary.hr_mean is None
    assert summary.hr_max is None
    assert summary.sample_count == 0
    assert summary.coverage_ratio == 0.0
    assert summary.data_quality == "insufficient"
    assert summary.algorithm_version is None


def test_summarize_day_partial_coverage_grades_fair() -> None:
    summary = summarize_day(
        date=date(2026, 9, 3),
        timezone="UTC",
        bucket_medians=[60.0] * 40,
        hr_min=58.0,
        hr_mean=61.0,
        hr_max=75.0,
        sample_count=25920,
        coverage_ratio=0.3,
    )
    assert summary.data_quality == "fair"
    assert summary.resting_hr == 60.0
