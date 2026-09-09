"""Pure day-plan math: somatriq_day_plan_v1 (Block 1 plan; grill P2/P6).

Frozen semantics under test:
- Tier from recovery bands (40/55/70), demoted one tier when capped sleep
  debt reaches 2 h; recovery missing -> tier null with a caveat, never
  imputed (the plan degrades visibly, CONTEXT.md "Today Plan").
- Target strain = frozen quartile band of the personal 28-day vendor strain
  distribution (inclusive method); null while < 14 strain days.
- Bedtime window = wake time - sleep need ± 15 min; null without a need.
"""

from datetime import date, time

import pytest
from somatriq_analytics.day_plan import day_plan_v1
from somatriq_contracts.plan import (
    BEDTIME_SPREAD_MIN,
    DAY_PLAN_ALGORITHM,
    STRAIN_MIN_DAYS,
    TIER_DROP_DEBT_MIN,
)

DAY = date(2026, 9, 9)
WAKE = time(7, 0)

# 20 evenly spaced strain values 10..200: inclusive quartiles are
# p25=57.5, p50=105, p75=152.5, p95=190.5 (hand-computed, linear method).
STRAIN_20 = [float(v) for v in range(10, 210, 10)]


# ── tier ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("recovery", "expected"),
    [
        (80.0, "hard"),
        (70.0, "hard"),
        (60.0, "moderate"),
        (55.0, "moderate"),
        (45.0, "light"),
        (40.0, "light"),
        (20.0, "rest"),
    ],
)
def test_tier_bands_edges(recovery: float, expected: str) -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=recovery,
        sleep_debt_min=0.0,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.tier == expected


def test_tier_demoted_by_two_hour_debt() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=80.0,
        sleep_debt_min=TIER_DROP_DEBT_MIN,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.tier == "moderate"


def test_tier_demotion_floors_at_rest() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=20.0,
        sleep_debt_min=TIER_DROP_DEBT_MIN,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.tier == "rest"


def test_tier_not_demoted_below_threshold() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=80.0,
        sleep_debt_min=TIER_DROP_DEBT_MIN - 1.0,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.tier == "hard"


def test_missing_recovery_means_no_tier_never_imputed() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=None,
        sleep_debt_min=0.0,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.tier is None
    assert result.target_strain is None  # no tier -> no target
    assert result.missing_inputs == ["recovery"]
    assert any("recovery" in caveat for caveat in result.caveats)
    assert result.bedtime_window is not None  # the rest of the plan survives


# ── target strain ──────────────────────────────────────────────────────────


def test_target_strain_quartile_bands_hand_computed() -> None:
    cases = {
        "rest": (0.0, 57.5),
        "light": (57.5, 105.0),
        "moderate": (105.0, 152.5),
        "hard": (152.5, 190.5),
    }
    for recovery, (tier, (lo, hi)) in zip([20.0, 45.0, 60.0, 80.0], cases.items(), strict=True):
        result = day_plan_v1(
            DAY,
            recovery_score=recovery,
            sleep_debt_min=0.0,
            sleep_need_minutes=480.0,
            strain_history=STRAIN_20,
            wake_time=WAKE,
        )
        assert result.tier == tier
        assert result.target_strain is not None
        assert result.target_strain.min == pytest.approx(lo)
        assert result.target_strain.max == pytest.approx(hi)


def test_target_strain_null_below_min_days() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=80.0,
        sleep_debt_min=0.0,
        sleep_need_minutes=480.0,
        strain_history=[10.0] * (STRAIN_MIN_DAYS - 1),
        wake_time=WAKE,
    )
    assert result.tier == "hard"
    assert result.target_strain is None
    assert any("building strain history" in caveat for caveat in result.caveats)


# ── bedtime window ─────────────────────────────────────────────────────────


def test_bedtime_window_exact_hand_computed() -> None:
    """wake 07:00 - need 480 min -> 23:00, window 22:45-23:15."""
    result = day_plan_v1(
        DAY,
        recovery_score=60.0,
        sleep_debt_min=0.0,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.bedtime_window is not None
    assert result.bedtime_window.start == time(22, 45)
    assert result.bedtime_window.end == time(23, 15)


def test_bedtime_window_wraps_before_midnight() -> None:
    """wake 05:00 - need 660 min -> 18:00 previous evening."""
    result = day_plan_v1(
        DAY,
        recovery_score=60.0,
        sleep_debt_min=0.0,
        sleep_need_minutes=660.0,
        strain_history=STRAIN_20,
        wake_time=time(5, 0),
    )
    assert result.bedtime_window is not None
    assert result.bedtime_window.start == time(17, 45)
    assert result.bedtime_window.end == time(18, 15)


def test_bedtime_window_null_without_sleep_need() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=60.0,
        sleep_debt_min=0.0,
        sleep_need_minutes=None,
        strain_history=STRAIN_20,
        wake_time=WAKE,
    )
    assert result.bedtime_window is None
    assert any("bedtime" in caveat for caveat in result.caveats)


# ── shape & honesty ────────────────────────────────────────────────────────


def test_plan_always_lists_five_contributions() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=None,
        sleep_debt_min=None,
        sleep_need_minutes=None,
        strain_history=[],
        wake_time=WAKE,
    )
    assert [c.input for c in result.contributions] == [
        "recovery",
        "sleep_debt",
        "sleep_need",
        "strain_history",
        "wake_time",
    ]
    assert result.algorithm_version == DAY_PLAN_ALGORITHM


def test_baseline_warmup_caveat() -> None:
    result = day_plan_v1(
        DAY,
        recovery_score=60.0,
        sleep_debt_min=0.0,
        sleep_need_minutes=480.0,
        strain_history=STRAIN_20,
        wake_time=WAKE,
        baseline_days=12,
    )
    assert any("building baselines" in caveat for caveat in result.caveats)


def test_spread_constant_is_fifteen_minutes() -> None:
    assert BEDTIME_SPREAD_MIN == 15.0
