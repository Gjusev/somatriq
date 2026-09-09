"""Pure sleep-need math: somatriq_sleep_need_v1 + 7-day sleep debt
(Block 1 plan 2026-09-09; grill P1/P3; ADR 0009/0012).

DB-free by design (ADR 0009 — deterministic Python, never an LLM); every
constant lives in the frozen contract `somatriq_contracts.plan`. A
prescriptive heuristic (CONTEXT.md "Sleep Need"), never a Prediction.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from somatriq_contracts.plan import (
    SLEEP_NEED_ALGORITHM,
    SLEEP_NEED_DEBT_CAP_MIN,
    SLEEP_NEED_DEBT_PAYOFF_RATE,
    SLEEP_NEED_LOAD_CAP_MIN,
    SLEEP_NEED_LOAD_MIN_PER_STRAIN,
    SLEEP_NEED_MAX_MIN,
    SLEEP_NEED_MIN_MIN,
    SLEEP_NEED_RECOVERY_CAP_MIN,
    SLEEP_NEED_RECOVERY_MIN_PER_POINT,
    SLEEP_NEED_RECOVERY_THRESHOLD,
    SleepNeedContribution,
    SleepNeedResult,
)

_MISSING_BASELINE_NOTE = "baseline missing — the day has not earned a recommendation"


@dataclass(frozen=True)
class SleepDebt:
    """Rolling-window sleep debt (grill P3): capped, asymmetric, measured-nights-only."""

    debt_min: float | None  # None = unknown (no measured night in the window)
    measured_days: int
    unmeasured_days: int


def sleep_debt(actuals: Sequence[tuple[date, float | None]], baseline_min: float) -> SleepDebt:
    """Σ max(0, baseline − actual) over measured nights, capped at the frozen cap.

    Oversleeping never banks credit; unmeasured days are counted and excluded —
    never imputed (spec §158: absent-day-honest).
    """
    measured = [value for _, value in actuals if value is not None]
    unmeasured = len(actuals) - len(measured)
    if not measured:
        return SleepDebt(debt_min=None, measured_days=0, unmeasured_days=unmeasured)
    raw = sum(max(0.0, baseline_min - value) for value in measured)
    return SleepDebt(
        debt_min=min(raw, SLEEP_NEED_DEBT_CAP_MIN),
        measured_days=len(measured),
        unmeasured_days=unmeasured,
    )


def sleep_need_v1(
    day: date,
    *,
    baseline_sleep_min: float | None,
    sleep_debt_min: float | None,
    recent_load: float | None,
    recovery: float | None,
) -> SleepNeedResult:
    """Explainable somatriq_sleep_need_v1 (frozen formula in the contract).

    Without the baseline anchor there is no recommendation (minutes is null);
    the debt window being unknown is a caveat, not a missing input; optional
    adjustments (recent load, recovery) are listed when absent, never imputed.
    """
    if baseline_sleep_min is None:
        return SleepNeedResult(
            day=day,
            minutes=None,
            contributions=[
                SleepNeedContribution(
                    input=name, value=None, minutes_added=None, note=_MISSING_BASELINE_NOTE
                )
                for name in ("baseline", "sleep_debt", "recent_load", "recovery")
            ],
            missing_inputs=["sleep_baseline"],
            caveats=[],
        )

    total = baseline_sleep_min
    contributions = [
        SleepNeedContribution(input="baseline", value=baseline_sleep_min, minutes_added=0.0)
    ]
    caveats: list[str] = []
    missing_inputs: list[str] = []

    if sleep_debt_min is None:
        caveats.append("sleep debt unknown — no measured nights in the 7-day window")
        contributions.append(
            SleepNeedContribution(input="sleep_debt", value=None, minutes_added=0.0)
        )
    else:
        debt_added = SLEEP_NEED_DEBT_PAYOFF_RATE * min(sleep_debt_min, SLEEP_NEED_DEBT_CAP_MIN)
        total += debt_added
        contributions.append(
            SleepNeedContribution(
                input="sleep_debt", value=sleep_debt_min, minutes_added=debt_added
            )
        )

    if recent_load is None:
        missing_inputs.append("recent_load")
        contributions.append(
            SleepNeedContribution(input="recent_load", value=None, minutes_added=None)
        )
    else:
        load_added = min(recent_load * SLEEP_NEED_LOAD_MIN_PER_STRAIN, SLEEP_NEED_LOAD_CAP_MIN)
        total += load_added
        contributions.append(
            SleepNeedContribution(input="recent_load", value=recent_load, minutes_added=load_added)
        )

    if recovery is None:
        missing_inputs.append("recovery")
        contributions.append(
            SleepNeedContribution(input="recovery", value=None, minutes_added=None)
        )
    else:
        recovery_added = min(
            max(
                0.0, (SLEEP_NEED_RECOVERY_THRESHOLD - recovery) * SLEEP_NEED_RECOVERY_MIN_PER_POINT
            ),
            SLEEP_NEED_RECOVERY_CAP_MIN,
        )
        total += recovery_added
        contributions.append(
            SleepNeedContribution(input="recovery", value=recovery, minutes_added=recovery_added)
        )

    minutes = min(max(total, SLEEP_NEED_MIN_MIN), SLEEP_NEED_MAX_MIN)
    if total > SLEEP_NEED_MAX_MIN:
        caveats.append(f"clamped to maximum ({SLEEP_NEED_MAX_MIN:.0f} min)")
    elif total < SLEEP_NEED_MIN_MIN:
        caveats.append(f"clamped to minimum ({SLEEP_NEED_MIN_MIN:.0f} min)")

    return SleepNeedResult(
        day=day,
        minutes=minutes,
        algorithm_version=SLEEP_NEED_ALGORITHM,
        contributions=contributions,
        missing_inputs=missing_inputs,
        caveats=caveats,
    )
