"""Pure sleep-need math: somatriq_sleep_need_v1 + sleep debt (Block 1 plan;
grill 2026-09-09 P1/P2/P3; ADR 0012/0018).

Frozen semantics under test:
- Sleep Need is a prescriptive heuristic (Calculated), never a Prediction.
- Sleep Debt: rolling 7-day, asymmetric (oversleep never banks credit),
  capped; computed over measured nights only, unmeasured days counted.
- Missing inputs are listed, never imputed; the anchor (baseline sleep) is
  required — without it minutes is null and the reason is explicit.
"""

from collections.abc import Sequence
from datetime import date, timedelta

import pytest
from somatriq_analytics.sleep_need import sleep_debt, sleep_need_v1
from somatriq_contracts.plan import (
    SLEEP_NEED_ALGORITHM,
    SLEEP_NEED_DEBT_CAP_MIN,
    SLEEP_NEED_DEBT_PAYOFF_RATE,
    SLEEP_NEED_LOAD_CAP_MIN,
    SLEEP_NEED_LOAD_MIN_PER_STRAIN,
    SLEEP_NEED_MAX_MIN,
    SLEEP_NEED_MIN_MIN,
)

DAY = date(2026, 9, 9)


def _window(actuals: Sequence[float | None]) -> list[tuple[date, float | None]]:
    """7 named days ending at DAY, newest last."""
    return [(DAY - timedelta(days=7 - i), value) for i, value in enumerate(actuals)]


# ── sleep debt ────────────────────────────────────────────────────────────


def test_sleep_debt_sums_shortfalls_and_ignores_oversleep() -> None:
    """baseline 480: shortfalls only [420,500,480,460,490,480,None] -> 60+20."""
    debt = sleep_debt(_window([420.0, 500.0, 480.0, 460.0, 490.0, 480.0, None]), 480.0)
    assert debt.debt_min == pytest.approx(80.0)
    assert debt.measured_days == 6
    assert debt.unmeasured_days == 1


def test_sleep_debt_never_banks_credit() -> None:
    """One 11-hour night among shortfalls cancels nothing: 6×30 + 0 = 180
    (values chosen under the 240-min cap so the cap is not what we test)."""
    debt = sleep_debt(_window([450.0] * 6 + [660.0]), 480.0)
    assert debt.debt_min == pytest.approx(180.0)


def test_sleep_debt_caps_at_frozen_cap() -> None:
    """7 nights of 300 against 480 -> 1260 raw -> capped at 240."""
    debt = sleep_debt(_window([300.0] * 7), 480.0)
    assert debt.debt_min == pytest.approx(SLEEP_NEED_DEBT_CAP_MIN)


def test_sleep_debt_all_unmeasured_is_none() -> None:
    """No measured night in the window: debt unknown, not zero."""
    debt = sleep_debt(_window([None] * 7), 480.0)
    assert debt.debt_min is None
    assert debt.unmeasured_days == 7


# ── sleep need ─────────────────────────────────────────────────────────────


def test_sleep_need_exact_hand_computed() -> None:
    """480 + debt 120×0.5=60 + strain 15×2.0=30 + (50-40)×1.0=10 -> 580."""
    result = sleep_need_v1(
        DAY,
        baseline_sleep_min=480.0,
        sleep_debt_min=120.0,
        recent_load=15.0,
        recovery=40.0,
    )
    assert result.minutes == pytest.approx(580.0)
    assert result.missing_inputs == []
    by_input = {c.input: c for c in result.contributions}
    assert by_input["baseline"].minutes_added == pytest.approx(0.0)
    assert by_input["sleep_debt"].minutes_added == pytest.approx(60.0)
    assert by_input["recent_load"].minutes_added == pytest.approx(30.0)
    assert by_input["recovery"].minutes_added == pytest.approx(10.0)


def test_sleep_need_without_baseline_is_null_and_explains() -> None:
    result = sleep_need_v1(
        DAY, baseline_sleep_min=None, sleep_debt_min=120.0, recent_load=15.0, recovery=40.0
    )
    assert result.minutes is None
    assert result.missing_inputs == ["sleep_baseline"]
    assert result.algorithm_version == SLEEP_NEED_ALGORITHM


def test_sleep_need_unknown_debt_is_zero_with_caveat() -> None:
    """Debt unknown (no measured nights) -> contribution 0 + explicit caveat."""
    result = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=None, recent_load=None, recovery=None
    )
    assert result.minutes == pytest.approx(480.0)
    assert result.missing_inputs == ["recent_load", "recovery"]
    assert any("sleep debt unknown" in caveat for caveat in result.caveats)
    by_input = {c.input: c for c in result.contributions}
    assert by_input["sleep_debt"].minutes_added == pytest.approx(0.0)


def test_sleep_need_optional_inputs_listed_never_imputed() -> None:
    result = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=80.0, recent_load=None, recovery=None
    )
    assert result.minutes == pytest.approx(480.0 + 80.0 * SLEEP_NEED_DEBT_PAYOFF_RATE)
    assert set(result.missing_inputs) == {"recent_load", "recovery"}


def test_sleep_need_clamps_to_maximum_with_caveat() -> None:
    """600 + 120 + 60 + 30 = 810 -> 660 with a clamp caveat."""
    result = sleep_need_v1(
        DAY,
        baseline_sleep_min=600.0,
        sleep_debt_min=SLEEP_NEED_DEBT_CAP_MIN,
        recent_load=SLEEP_NEED_LOAD_CAP_MIN / SLEEP_NEED_LOAD_MIN_PER_STRAIN,
        recovery=20.0,
    )
    assert result.minutes == pytest.approx(SLEEP_NEED_MAX_MIN)
    assert any("clamped" in caveat for caveat in result.caveats)


def test_sleep_need_clamps_to_minimum_with_caveat() -> None:
    result = sleep_need_v1(
        DAY, baseline_sleep_min=280.0, sleep_debt_min=0.0, recent_load=None, recovery=None
    )
    assert result.minutes == pytest.approx(SLEEP_NEED_MIN_MIN)
    assert any("clamped" in caveat for caveat in result.caveats)


def test_sleep_need_recovery_above_threshold_never_subtracts() -> None:
    result = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=0.0, recent_load=None, recovery=95.0
    )
    assert result.minutes == pytest.approx(480.0)
    by_input = {c.input: c for c in result.contributions}
    assert by_input["recovery"].minutes_added == pytest.approx(0.0)


def test_sleep_need_load_contribution_is_capped() -> None:
    """strain 50 -> 100 raw -> capped at 60."""
    result = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=0.0, recent_load=50.0, recovery=None
    )
    assert result.minutes == pytest.approx(480.0 + SLEEP_NEED_LOAD_CAP_MIN)


def test_sleep_need_always_lists_four_contributions() -> None:
    result = sleep_need_v1(
        DAY, baseline_sleep_min=None, sleep_debt_min=None, recent_load=None, recovery=None
    )
    assert [c.input for c in result.contributions] == [
        "baseline",
        "sleep_debt",
        "recent_load",
        "recovery",
    ]


def test_sleep_need_monotonic_in_debt() -> None:
    """More debt never lowers the recommendation (before clamping)."""
    low = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=0.0, recent_load=None, recovery=None
    )
    high = sleep_need_v1(
        DAY, baseline_sleep_min=480.0, sleep_debt_min=240.0, recent_load=None, recovery=None
    )
    assert low.minutes is not None and high.minutes is not None
    assert high.minutes > low.minutes
