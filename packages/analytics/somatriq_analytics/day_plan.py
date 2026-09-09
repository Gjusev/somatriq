"""Pure day-plan math: somatriq_day_plan_v1 (Block 1 plan; grill P6).

DB-free by design (ADR 0009). Every constant lives in the frozen contract
`somatriq_contracts.plan`. The plan always exists and degrades visibly
(CONTEXT.md "Today Plan"): a missing recovery yields NO tier (never an
imputed one); the bedtime window survives without one.

Tier: recovery bands (40/55/70, edges inclusive above), demoted one tier
(floor: rest) when capped sleep debt reaches TIER_DROP_DEBT_MIN. Target
strain: the tier's inclusive-quartile band of the personal 28-day vendor
strain distribution — null while fewer than STRAIN_MIN_DAYS days. Bedtime
window: wake time − sleep need ± BEDTIME_SPREAD_MIN (a window may wrap to
the evening before the wake date).
"""

import statistics
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from somatriq_contracts.plan import (
    BEDTIME_SPREAD_MIN,
    DAY_PLAN_ALGORITHM,
    STRAIN_MIN_DAYS,
    STRAIN_PERCENTILE_METHOD,
    TIER_DROP_DEBT_MIN,
    TIER_HARD_BELOW,
    TIER_LIGHT_BELOW,
    TIER_MODERATE_BELOW,
    TIER_STRAIN_PERCENTILES,
    BedtimeWindow,
    DayPlanContribution,
    DayPlanResult,
    TargetStrainRange,
    Tier,
)

_TIER_ORDER: tuple[Tier, ...] = ("rest", "light", "moderate", "hard")


def _base_tier(recovery: float) -> Tier:
    if recovery >= TIER_HARD_BELOW:
        return "hard"
    if recovery >= TIER_MODERATE_BELOW:
        return "moderate"
    if recovery >= TIER_LIGHT_BELOW:
        return "light"
    return "rest"


def _percentile(values: Sequence[float], pct: int) -> float:
    """Inclusive-method percentile (linear interpolation). n=20 cut points
    cover every frozen band edge: p25 -> cut[4], p50 -> cut[9],
    p75 -> cut[14], p95 -> cut[18]; p0 is the floor 0.0."""
    if pct == 0:
        return 0.0
    cuts = statistics.quantiles(values, n=20, method=STRAIN_PERCENTILE_METHOD)
    return cuts[pct // 5 - 1]


def _target_strain(tier: Tier, strain_history: Sequence[float]) -> TargetStrainRange:
    lo_pct, hi_pct = TIER_STRAIN_PERCENTILES[tier]
    return TargetStrainRange(
        min=_percentile(strain_history, lo_pct),
        max=_percentile(strain_history, hi_pct),
    )


def day_plan_v1(
    day: date,
    *,
    recovery_score: float | None,
    sleep_debt_min: float | None,
    sleep_need_minutes: float | None,
    strain_history: Sequence[float],
    wake_time: time,
    baseline_days: int | None = None,
) -> DayPlanResult:
    """Explainable somatriq_day_plan_v1 over today's inputs (frozen contract)."""
    contributions: list[DayPlanContribution] = []
    caveats: list[str] = []
    missing_inputs: list[str] = []

    # ── tier (recovery bands + debt demotion) ─────────────────────────
    tier: Tier | None = None
    if recovery_score is None:
        missing_inputs.append("recovery")
        caveats.append("recovery not yet earned — training guidance unavailable")
        recovery_row = DayPlanContribution(input="recovery", value=None, note="missing")
    else:
        tier = _base_tier(recovery_score)
        recovery_note = f"recovery {recovery_score:.0f} -> {tier}"

    if sleep_debt_min is None:
        debt_row = DayPlanContribution(input="sleep_debt", value=None, note="unknown")
        if tier is not None:
            caveats.append("sleep debt unknown — tier not adjusted")
    elif tier is not None and sleep_debt_min >= TIER_DROP_DEBT_MIN:
        demoted = _TIER_ORDER[max(0, _TIER_ORDER.index(tier) - 1)]
        recovery_note += f", demoted to {demoted} by {sleep_debt_min:.0f} min debt"
        tier = demoted
        debt_row = DayPlanContribution(
            input="sleep_debt", value=sleep_debt_min, note="tier demoted one step"
        )
    else:
        debt_row = DayPlanContribution(
            input="sleep_debt", value=sleep_debt_min, note="below demotion threshold"
        )

    if recovery_score is not None:
        recovery_row = DayPlanContribution(
            input="recovery", value=recovery_score, note=recovery_note
        )

    # ── target strain ─────────────────────────────────────────────────
    if tier is None:
        target: TargetStrainRange | None = None
        strain_note = "no tier"
    elif len(strain_history) < STRAIN_MIN_DAYS:
        target = None
        caveats.append(f"building strain history (n={len(strain_history)}/{STRAIN_MIN_DAYS} days)")
        strain_note = "insufficient history"
    else:
        target = _target_strain(tier, strain_history)
        strain_note = f"inclusive quartiles of {len(strain_history)}-day distribution"
    strain_row = DayPlanContribution(
        input="strain_history", value=float(len(strain_history)), note=strain_note
    )

    # ── bedtime window ────────────────────────────────────────────────
    if sleep_need_minutes is None:
        window: BedtimeWindow | None = None
        caveats.append("no sleep need — bedtime window unavailable")
        need_note = "missing"
    else:
        wake_at = datetime.combine(day + timedelta(days=1), wake_time)
        center = wake_at - timedelta(minutes=sleep_need_minutes)
        window = BedtimeWindow(
            start=(center - timedelta(minutes=BEDTIME_SPREAD_MIN)).time(),
            end=(center + timedelta(minutes=BEDTIME_SPREAD_MIN)).time(),
        )
        need_note = f"bedtime {center.time():%H:%M} for {sleep_need_minutes:.0f} min"
    need_row = DayPlanContribution(input="sleep_need", value=sleep_need_minutes, note=need_note)
    wake_row = DayPlanContribution(
        input="wake_time",
        value=float(wake_time.hour * 60 + wake_time.minute),
        note="preference or documented default",
    )

    contributions = [recovery_row, debt_row, need_row, strain_row, wake_row]

    if baseline_days is not None and baseline_days < 28:
        caveats.append(f"building baselines ({baseline_days}/28 days)")

    return DayPlanResult(
        day=day,
        tier=tier,
        target_strain=target,
        bedtime_window=window,
        algorithm_version=DAY_PLAN_ALGORITHM,
        contributions=contributions,
        missing_inputs=missing_inputs,
        caveats=caveats,
    )
