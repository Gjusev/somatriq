"""Pure health-monitor math: somatriq_health_monitor_v1 (Block 3; CONTEXT
"Vital"; ADR 0009/0012).

Frozen semantics under test: each vital's period median is compared to
its personal baseline (somatriq_baseline_v1 over the trailing window
BEFORE the report period) as a robust z; |z| >= 1 is elevated/reduced,
anything else within — direction words against the personal baseline,
never clinical language. Under the 7-day baseline minimum the vital is
'building'; without period values it is 'insufficient'. Coverage is
n_days / expected days, always visible, never hidden (spec §158).
"""

import math

import pytest
from somatriq_analytics.health_monitor import health_monitor_v1
from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_BASELINE_DAYS,
    HEALTH_MONITOR_Z_THRESHOLD,
)


def _baseline_values(center: float, spread: float = 4.0, n: int = 30) -> list[float]:
    """A stable personal baseline around `center` (IQR = 2*spread)."""
    half = n // 2
    values = [center - spread] * half + [center + spread] * (n - half)
    values.sort()
    return values


def test_within_band_is_within() -> None:
    summary = health_monitor_v1(
        vital="resting_hr",
        period_values=[52.0, 53.0, 52.5, 53.5],
        baseline_values=_baseline_values(53.0),
        expected_days=30,
    )
    assert summary.status == "within"
    assert summary.period_median == pytest.approx(52.75)
    assert summary.robust_z is not None and abs(summary.robust_z) < HEALTH_MONITOR_Z_THRESHOLD
    assert summary.coverage == pytest.approx(4 / 30)
    assert summary.n_days == 4


def test_elevated_and_reduced_directions() -> None:
    # Baseline centered 50 with IQR 8 -> robust z of median m: (m-50)/4.
    elevated = health_monitor_v1(
        vital="resting_hr",
        period_values=[58.0] * 10,
        baseline_values=_baseline_values(50.0),
        expected_days=30,
    )
    assert elevated.status == "elevated"
    assert elevated.robust_z == pytest.approx(2.0)

    reduced = health_monitor_v1(
        vital="resting_hr",
        period_values=[42.0] * 10,
        baseline_values=_baseline_values(50.0),
        expected_days=30,
    )
    assert reduced.status == "reduced"
    assert reduced.robust_z == pytest.approx(-2.0)


def test_below_baseline_minimum_is_building_never_a_number() -> None:
    summary = health_monitor_v1(
        vital="spo2_pct",
        period_values=[98.0] * 10,
        baseline_values=[98.0] * (7 - 1),
        expected_days=30,
    )
    assert summary.status == "building"
    assert summary.robust_z is None
    assert "baseline" in (summary.note or "")


def test_no_period_values_is_insufficient() -> None:
    summary = health_monitor_v1(
        vital="skin_temp_dev_c",
        period_values=[],
        baseline_values=_baseline_values(0.1),
        expected_days=30,
    )
    assert summary.status == "insufficient"
    assert summary.status == "insufficient"
    assert summary.period_median is None


def test_note_is_never_clinical() -> None:
    """The direction note speaks of the personal baseline only."""
    summary = health_monitor_v1(
        vital="resp_rate_bpm",
        period_values=[20.0] * 10,
        baseline_values=_baseline_values(14.0),
        expected_days=30,
    )
    assert summary.status == "elevated"
    note = (summary.note or "").lower()
    assert "baseline" in note
    forbidden = ("abnormal", "diagnosis", "disease", "illness", "medical")
    assert not any(word in note for word in forbidden)


def test_degenerate_baseline_saturates_instead_of_dividing() -> None:
    """IQR 0: any deviation saturates (mirrors recovery's z saturation)."""
    summary = health_monitor_v1(
        vital="spo2_pct",
        period_values=[97.0] * 10,
        baseline_values=[98.0] * 30,
        expected_days=30,
    )
    assert summary.status == "reduced"
    assert summary.robust_z is not None
    assert math.isfinite(summary.robust_z)
    assert abs(summary.robust_z) >= 10.0


def test_baseline_window_constant_is_frozen() -> None:
    assert HEALTH_MONITOR_BASELINE_DAYS == 90
