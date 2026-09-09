"""Pure behavior-insight math: somatriq_behavior_insight_v1 (Block 2;
grill P7/P8/P9; ADR 0009).

Frozen semantics under test:
- Exposure day d pairs with outcome day d+1 (lag 1) — the only direction
  defensible without causal design (P9).
- Groups come from ACTIVELY JOURNALED days only: a day with no journal
  event at all is ambiguous (not logged ≠ absent) and joins no group —
  the compliance-honest choice.
- Gate: >= 7 exposed AND >= 7 unexposed days, else an honest keep-logging
  row, never a number.
- BH-FDR q over the executed tests; every row carries its method string;
  association language only, never causal.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from somatriq_analytics.behaviors import behavior_insights_v1, bh_qvalues
from somatriq_contracts.journal import BEHAVIOR_EXPOSURES, BEHAVIOR_OUTCOMES

DAY0 = date(2026, 6, 1)


def _days(n: int) -> list[date]:
    """Active days spaced by 2: with lag-1 outcomes, an exposure day's
    d+1 never collides with another active day (keeps tests unambiguous)."""
    return [DAY0 + timedelta(days=2 * i) for i in range(n)]


def _dataset(
    *,
    exposed_hrv: list[float],
    unexposed_hrv: list[float],
    extra_exposure_days: list[date] | None = None,
) -> dict[str, Any]:
    """Active days = exposed + unexposed days (all journaled by
    construction); HRV next-day values per group; strain confounder flat."""
    exposed_days = _days(len(exposed_hrv))
    unexposed_days = _days(len(exposed_hrv) + len(unexposed_hrv))[len(exposed_hrv) :]
    if extra_exposure_days:
        exposed_days = exposed_days + extra_exposure_days
    outcomes = {}
    for i, value in enumerate(exposed_hrv):
        outcomes[exposed_days[i] + timedelta(days=1)] = value
    for i, value in enumerate(unexposed_hrv):
        outcomes[unexposed_days[i] + timedelta(days=1)] = value
    return {
        "exposures": {"caffeine": set(exposed_days)},
        "outcomes": {"avg_hrv": outcomes},
        "active_days": set(exposed_days) | set(unexposed_days),
        "strain_by_day": {d: 10.0 for d in list(exposed_days) + list(unexposed_days)},
    }


def test_clear_effect_is_detected_with_lag_one() -> None:
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "avg_hrv")
    assert row.status == "ok"
    assert row.n_exposed == 8
    assert row.n_unexposed == 8
    assert row.median_difference == pytest.approx(20.0)
    assert row.p_value is not None and row.p_value < 0.05
    assert row.q_value is not None and row.q_value <= 1.0
    assert "association, not causation" in row.method
    assert "prior local day" in row.method


def test_below_gate_is_keep_logging_never_a_number() -> None:
    data = _dataset(exposed_hrv=[80.0] * 3, unexposed_hrv=[60.0] * 8)
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "avg_hrv")
    assert row.status == "keep_logging"
    assert row.p_value is None
    assert row.median_difference is None
    assert "3" in (row.note or "")


def test_silent_days_join_no_group() -> None:
    """A day with a next-day outcome but ZERO journal events is ambiguous —
    it must not inflate the unexposed group."""
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    # One extra outcome-only day: journaled nowhere.
    silent = DAY0 + timedelta(days=50)
    data["outcomes"]["avg_hrv"][silent + timedelta(days=1)] = 999.0
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "avg_hrv")
    assert row.n_unexposed == 8  # the silent day did not join


def test_exposure_pairs_only_with_next_day() -> None:
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    # Same-day value differs wildly — it must never enter the test.
    same_day = dict(data["outcomes"]["avg_hrv"])
    for exposed_day in data["exposures"]["caffeine"]:
        same_day[exposed_day] = 1.0
    data["outcomes"]["avg_hrv"] = same_day
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "avg_hrv")
    assert row.median_exposed == pytest.approx(80.0)  # next-day values, not same-day


def test_confounders_reported_per_group() -> None:
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    strain: dict[date, float] = data["strain_by_day"]
    exposed_days = sorted(data["exposures"]["caffeine"])
    for d in exposed_days:
        strain[d] = 20.0  # exposed days carried higher strain
    data["strain_by_day"] = strain
    # Overlap: alcohol on two of the caffeine days.
    data["exposures"]["alcohol"] = set(exposed_days[:2])
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "avg_hrv")

    assert row.confounders.strain_median_exposed == pytest.approx(20.0)
    assert row.confounders.strain_median_unexposed == pytest.approx(10.0)
    assert row.confounders.overlap_days["alcohol"] == 2


def test_row_family_is_frozen_and_ordered() -> None:
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    rows = behavior_insights_v1(**data)
    pairs = [(r.behavior, r.outcome) for r in rows]
    expected = [
        (behavior, outcome) for behavior in BEHAVIOR_EXPOSURES for outcome in BEHAVIOR_OUTCOMES
    ]
    assert pairs == expected


def test_missing_outcome_metric_skips_to_keep_logging() -> None:
    """An outcome with no data at all cannot produce numbers."""
    data = _dataset(exposed_hrv=[80.0] * 8, unexposed_hrv=[60.0] * 8)
    data["outcomes"]["recovery"] = {}
    rows = behavior_insights_v1(**data)
    row = next(r for r in rows if r.behavior == "caffeine" and r.outcome == "recovery")
    assert row.status == "keep_logging"


# ── Benjamini-Hochberg (the FDR engine, unit-pinned) ──────────────────────


def test_bh_qvalues_hand_computed() -> None:
    """p = (.01, .04, .03): BH step-up gives q = (.03, .04, .04)."""
    q = bh_qvalues([0.01, 0.04, 0.03])
    assert q[0] == pytest.approx(0.03)
    assert q[1] == pytest.approx(0.04)
    assert q[2] == pytest.approx(0.04)


def test_bh_qvalues_monotone_in_p_and_capped() -> None:
    p = [0.9, 0.95, 0.85, 0.0]
    q = bh_qvalues(p)
    assert all(0.0 <= value <= 1.0 for value in q)
    assert q[3] == 0.0
    # BH q is monotone in p: a larger p never earns a smaller q.
    for i in range(len(p)):
        for j in range(len(p)):
            if p[i] <= p[j]:
                assert q[i] <= q[j] + 1e-12


def test_bh_qvalues_empty() -> None:
    assert bh_qvalues([]) == []
