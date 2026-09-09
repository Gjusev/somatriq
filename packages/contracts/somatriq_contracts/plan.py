"""Today Plan + Sleep Need contracts (Block 1 plan 2026-09-09; ADR 0012/0018).

somatriq_sleep_need_v1 (frozen — changes bump the version): a PRESCRIPTIVE
heuristic, never a Prediction (CONTEXT.md "Sleep Need"; grill P1). The
anchor is the personal 28-day median of measured sleep; adjustments are
additive and each carries its own explainability row:

    minutes = clamp( baseline
                   + payoff_rate · min(debt_7d, debt_cap)
                   + min(load_min_per_strain · strain, load_cap)
                   + min(max(0, (recovery_threshold − recovery)), recovery_cap)
                   , need_min, need_max )

Sleep Debt (grill P3): rolling 7-day Σ max(0, baseline − actual) over
MEASURED nights only, capped; oversleeping never banks credit; unmeasured
days are counted, never imputed. Debt unknown (no measured night) is a
caveat and a zero contribution — not a missing input.

Missing inputs are LISTED, never imputed. Without the baseline anchor the
recommendation is null — the day has not earned a number.
"""

from datetime import date as date_type
from datetime import time as time_type
from typing import Literal

from pydantic import BaseModel, Field

SLEEP_NEED_ALGORITHM = "somatriq_sleep_need_v1"

SLEEP_NEED_BASELINE_DAYS = 28
SLEEP_NEED_BASELINE_MIN_DAYS = 7  # somatriq_baseline_v1 minimum applies

SLEEP_NEED_DEBT_WINDOW_DAYS = 7
SLEEP_NEED_DEBT_CAP_MIN = 240.0
SLEEP_NEED_DEBT_PAYOFF_RATE = 0.5

SLEEP_NEED_LOAD_MIN_PER_STRAIN = 2.0  # vendor strain is 0-100
SLEEP_NEED_LOAD_CAP_MIN = 60.0

SLEEP_NEED_RECOVERY_THRESHOLD = 50.0  # at/above: no adjustment (never negative)
SLEEP_NEED_RECOVERY_MIN_PER_POINT = 1.0
SLEEP_NEED_RECOVERY_CAP_MIN = 30.0

SLEEP_NEED_MIN_MIN = 300.0
SLEEP_NEED_MAX_MIN = 660.0

# ── somatriq_day_plan_v1 (frozen — changes bump the version) ──────────────

DAY_PLAN_ALGORITHM = "somatriq_day_plan_v1"

# Tier bands on somatriq_recovery_v1's 0-100 score (edges inclusive above).
TIER_MODERATE_BELOW = 55.0
TIER_HARD_BELOW = 70.0
TIER_LIGHT_BELOW = 40.0

# Capped sleep debt at/above this demotes the tier by one (floor: rest).
TIER_DROP_DEBT_MIN = 120.0

# Target strain = inclusive-method quartile band of the personal 28-day
# vendor strain distribution (statistics.quantiles, method="inclusive").
STRAIN_HISTORY_DAYS = 28
STRAIN_MIN_DAYS = 14
STRAIN_PERCENTILE_METHOD: Literal["inclusive", "exclusive"] = "inclusive"
TIER_STRAIN_PERCENTILES: dict[str, tuple[int, int]] = {
    "rest": (0, 25),
    "light": (25, 50),
    "moderate": (50, 75),
    "hard": (75, 95),
}

# Bedtime window = wake time - sleep need, this many minutes each side.
BEDTIME_SPREAD_MIN = 15.0

Tier = Literal["rest", "light", "moderate", "hard"]


class SleepNeedContribution(BaseModel):

    input: Literal["baseline", "sleep_debt", "recent_load", "recovery"]
    value: float | None = None
    minutes_added: float | None = None
    note: str | None = None


class SleepNeedResult(BaseModel):
    day: date_type
    minutes: float | None = Field(default=None, ge=SLEEP_NEED_MIN_MIN, le=SLEEP_NEED_MAX_MIN)
    algorithm_version: str = SLEEP_NEED_ALGORITHM
    contributions: list[SleepNeedContribution] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class TargetStrainRange(BaseModel):
    """The tier's band of the personal strain distribution (vendor score)."""

    min: float = Field(ge=0.0)
    max: float


class BedtimeWindow(BaseModel):
    """Local clock times; a window that wraps before midnight belongs to the
    evening before the wake date."""

    start: time_type
    end: time_type


class DayPlanContribution(BaseModel):
    input: Literal["recovery", "sleep_debt", "sleep_need", "strain_history", "wake_time"]
    value: float | None = None
    note: str | None = None


class DayPlanResult(BaseModel):
    day: date_type
    tier: Tier | None = None
    target_strain: TargetStrainRange | None = None
    bedtime_window: BedtimeWindow | None = None
    algorithm_version: str = DAY_PLAN_ALGORITHM
    contributions: list[DayPlanContribution] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
