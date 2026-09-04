"""N-of-1 experiment evaluation: baseline vs intervention (spec §83-86, §82;
M11 §204; ADR 0009 — deterministic Python, never an LLM).

An experiment is the ONE surface allowed to speak causally (spec §82) —
because it observes a baseline, then changes exactly one thing, then
observes again. Everything here still words its verdicts conservatively:
a significant result is "consistent with effect", never "proves" — this is
one person's data, and the caveat string says so on every result.

The statistics reuse :mod:`somatriq_analytics.correlations` machinery: the
exact two-tailed Student-t p-value comes from its regularized incomplete
beta (imported, not duplicated). Welch's t does not assume equal variances
(the intervention itself may change day-to-day spread, not just the level),
and its Satterthwaite df is floored to an integer for the shared tail — a
conservative rounding that can only enlarge p.

Honesty floors, by construction:

* fewer than ``MIN_DAYS_PER_PHASE`` (7) days in EITHER phase → verdict
  "inconclusive" — the numbers are still computed and reported, but a
  handful of days never earns a verdict;
* a degenerate phase (zero within-phase variance on both sides, different
  means) has no defined t/d — they come back ``None`` and the verdict is
  "inconclusive", never an invented p = 0 from perfectly flat data.

Everything here is DB-free; :mod:`somatriq_analytics.correlation_data`
loads the outcome series and the API layer joins it with the experiment's
complied days.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from somatriq_analytics.correlations import FAMILY_ALPHA, t_two_tailed_p

# Single-test threshold reuses the correlation family's nominal α.
ALPHA: Final = FAMILY_ALPHA

# The verdict floor: fewer complied days than this in either phase is
# "inconclusive" no matter how large the shift looks.
MIN_DAYS_PER_PHASE: Final = 7

# Spec §82 humility: attached verbatim to every evaluation, every surface.
CAVEAT: Final = (
    "N-of-1: your data only; replication requires repeating the experiment"
)

P_METHOD_WELCH: Final = (
    "welch t two-tailed (incomplete beta), df = welch-satterthwaite (floored)"
)

Direction = Literal["increase", "decrease", "any"]
Verdict = Literal["inconclusive", "consistent with effect", "opposite of hypothesis"]

VERDICT_INCONCLUSIVE: Final = "inconclusive"
VERDICT_CONSISTENT: Final = "consistent with effect"
VERDICT_OPPOSITE: Final = "opposite of hypothesis"

_DIRECTIONS: Final[frozenset[str]] = frozenset({"increase", "decrease", "any"})


@dataclass(frozen=True)
class ExperimentResult:
    """One baseline-vs-intervention comparison with its evidence (spec §86)."""

    n_baseline: int
    n_intervention: int
    mean_baseline: float | None
    mean_intervention: float | None
    mean_difference: float | None  # intervention − baseline
    cohens_d: float | None  # pooled SD; None when undefined
    welch_t: float | None
    welch_df: int | None
    p_value: float | None  # exact two-tailed via the shared incomplete beta
    p_method: str
    verdict: Verdict
    caveat: str


def evaluate(
    baseline_values: Sequence[float],
    intervention_values: Sequence[float],
    direction: Direction,
) -> ExperimentResult:
    """Compare the phases; the verdict words follow spec §82 exactly.

    * "inconclusive" — p ≥ α, or a phase below MIN_DAYS_PER_PHASE, or a
      degenerate (zero-variance) pair;
    * "consistent with effect" — p < α and the difference matches the
      hypothesis (either way when direction = any);
    * "opposite of hypothesis" — p < α against the stated direction.
    """
    if direction not in _DIRECTIONS:
        raise ValueError(
            f"direction must be 'increase', 'decrease' or 'any', got {direction!r}"
        )

    n_baseline = len(baseline_values)
    n_intervention = len(intervention_values)
    mean_baseline = statistics.fmean(baseline_values) if baseline_values else None
    mean_intervention = (
        statistics.fmean(intervention_values) if intervention_values else None
    )
    difference = (
        mean_intervention - mean_baseline
        if mean_baseline is not None and mean_intervention is not None
        else None
    )

    cohens_d: float | None = None
    welch_t: float | None = None
    welch_df: int | None = None
    p_value: float | None = None

    # Variances need n ≥ 2 per phase; below that the honest answer is None.
    if n_baseline >= 2 and n_intervention >= 2 and difference is not None:
        var_b = statistics.variance(baseline_values)
        var_i = statistics.variance(intervention_values)

        pooled = (
            (n_baseline - 1) * var_b + (n_intervention - 1) * var_i
        ) / (n_baseline + n_intervention - 2)
        if pooled > 0.0:
            cohens_d = difference / math.sqrt(pooled)
        elif difference == 0.0:
            cohens_d = 0.0  # identical flat phases: zero effect, defined

        se_b = var_b / n_baseline
        se_i = var_i / n_intervention
        se_squared = se_b + se_i
        if se_squared > 0.0:
            welch_t = difference / math.sqrt(se_squared)
            df = se_squared * se_squared / (
                se_b * se_b / (n_baseline - 1) + se_i * se_i / (n_intervention - 1)
            )
            # Floor to int for the shared incomplete-beta tail: rounding df
            # down can only enlarge p (conservative). Welch df is always
            # ≥ min(n)−1 ≥ 1; the max() guard is belt-and-braces.
            welch_df = max(int(df), 1)
            p_value = t_two_tailed_p(welch_t, welch_df)
        elif difference == 0.0:
            welch_t = 0.0
            p_value = 1.0  # identical flat phases: no difference, certain

    verdict: Verdict = VERDICT_INCONCLUSIVE
    if (
        p_value is not None
        and n_baseline >= MIN_DAYS_PER_PHASE
        and n_intervention >= MIN_DAYS_PER_PHASE
    ):
        assert difference is not None  # p only exists when the difference does
        if p_value < ALPHA:
            if direction == "any" or (direction == "increase" and difference > 0.0) or (
                direction == "decrease" and difference < 0.0
            ):
                verdict = VERDICT_CONSISTENT
            else:
                verdict = VERDICT_OPPOSITE

    return ExperimentResult(
        n_baseline=n_baseline,
        n_intervention=n_intervention,
        mean_baseline=mean_baseline,
        mean_intervention=mean_intervention,
        mean_difference=difference,
        cohens_d=cohens_d,
        welch_t=welch_t,
        welch_df=welch_df,
        p_value=p_value,
        p_method=P_METHOD_WELCH,
        verdict=verdict,
        caveat=CAVEAT,
    )
