"""Pure behavior-insight math: somatriq_behavior_insight_v1 (Block 2;
grill P7/P8/P9; ADR 0009).

DB-free by design; constants live in the frozen contract
`somatriq_contracts.journal`. The unit is the EXPOSURE DAY: exposure on
day d, outcome on day d+1 (lag 1 — the only direction defensible without
a causal design). Groups come from ACTIVELY JOURNALED days only: a day
with no journal event at all is ambiguous (not logged ≠ absent) and joins
no group — the compliance-honest choice the method string states.

Every row is an association (evidence label `Associated`), never causal
language; below the n-gate it renders "keep logging", never a number.
q-values are Benjamini-Hochberg over the EXECUTED tests of the family
(7 exposures × 4 outcomes declared; executed = rows that passed the
gate), stated in the method string.
"""

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from somatriq_contracts.journal import (
    BEHAVIOR_EXPOSURES,
    BEHAVIOR_INSIGHT_ALGORITHM,
    BEHAVIOR_LAG_DAYS,
    BEHAVIOR_MIN_GROUP_DAYS,
    BEHAVIOR_OUTCOMES,
    BehaviorConfounders,
    BehaviorInsightRow,
)


def _rankdata(values: Sequence[float]) -> list[float]:
    """Average ranks for ties (matches the correlations module's spearman)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


def mann_whitney_u_p(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Two-sided Mann-Whitney U p-value: normal approximation with tie and
    continuity corrections — stdlib-only, like the rest of the analytics
    package (no scipy dependency), honest at the >=7-per-group gate."""
    n1, n2 = len(xs), len(ys)
    combined = list(xs) + list(ys)
    ranks = _rankdata(combined)
    rank_sum_1 = sum(ranks[:n1])
    u1 = rank_sum_1 - n1 * (n1 + 1) / 2
    u = min(u1, n1 * n2 - u1)
    mu = n1 * n2 / 2

    # Tie correction: sigma^2 = n1*n2/12 * ((N+1) - (sum t^3 - t)/(N(N-1))).
    n_total = n1 + n2
    counts: dict[float, int] = {}
    for value in combined:
        counts[value] = counts.get(value, 0) + 1
    tie_term = sum(t**3 - t for t in counts.values())
    sigma_sq = n1 * n2 / 12 * ((n_total + 1) - tie_term / (n_total * (n_total - 1)))
    if sigma_sq <= 0:
        return 1.0  # identical constant groups: no evidence either way
    z = (abs(u - mu) - 0.5) / math.sqrt(sigma_sq)
    if z <= 0:
        return 1.0
    return math.erfc(z / math.sqrt(2))


def bh_qvalues(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg step-up q-values, capped at 1.0, monotone in p.

    Deterministic hand implementation (no statsmodels dependency): sort,
    walk from the largest p downward carrying the running minimum of
    m/rank·p, then restore the input order.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    q_by_index: dict[int, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, min(1.0, m / rank * p_values[idx]))
        q_by_index[idx] = running
    return [q_by_index[i] for i in range(m)]


def _median_or_none(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _confounders(
    behavior: str,
    exposed_days: Sequence[date],
    unexposed_days: Sequence[date],
    exposures: Mapping[str, Iterable[date]],
    strain_by_day: Mapping[date, float],
) -> BehaviorConfounders:
    exposed_strain = [strain_by_day[d] for d in exposed_days if d in strain_by_day]
    unexposed_strain = [strain_by_day[d] for d in unexposed_days if d in strain_by_day]
    exposed_set = set(exposed_days)
    overlap = {
        other: len(exposed_set & set(other_days))
        for other, other_days in exposures.items()
        if other != behavior
    }
    return BehaviorConfounders(
        strain_median_exposed=_median_or_none(exposed_strain),
        strain_median_unexposed=_median_or_none(unexposed_strain),
        overlap_days=overlap,
    )


def behavior_insights_v1(
    exposures: Mapping[str, Iterable[date]],
    outcomes: Mapping[str, Mapping[date, float]],
    strain_by_day: Mapping[date, float],
    *,
    active_days: Iterable[date],
    lag_days: int = BEHAVIOR_LAG_DAYS,
    min_group_days: int = BEHAVIOR_MIN_GROUP_DAYS,
) -> list[BehaviorInsightRow]:
    """The frozen behavior-insight family: exposure d -> outcome d+1."""
    lag = timedelta(days=lag_days)
    journal_days = set(active_days)
    exposure_sets = {name: set(days) for name, days in exposures.items()}

    executed: list[tuple[BehaviorInsightRow, float]] = []
    rows: list[BehaviorInsightRow] = []

    for behavior in BEHAVIOR_EXPOSURES:
        exposed_days = sorted(journal_days & exposure_sets.get(behavior, set()))
        unexposed_days = sorted(journal_days - exposure_sets.get(behavior, set()))
        confounders = _confounders(
            behavior, exposed_days, unexposed_days, exposure_sets, strain_by_day
        )
        for outcome in BEHAVIOR_OUTCOMES:
            outcome_by_day = outcomes.get(outcome, {})
            exposed_values = [
                outcome_by_day[d + lag] for d in exposed_days if d + lag in outcome_by_day
            ]
            unexposed_values = [
                outcome_by_day[d + lag] for d in unexposed_days if d + lag in outcome_by_day
            ]
            if len(exposed_values) < min_group_days or len(unexposed_values) < min_group_days:
                rows.append(
                    BehaviorInsightRow(
                        behavior=behavior,
                        outcome=outcome,
                        status="keep_logging",
                        n_exposed=len(exposed_values),
                        n_unexposed=len(unexposed_values),
                        confounders=confounders,
                        method=(
                            f"Mann-Whitney U (two-sided) on next-day {outcome}; "
                            f"exposure = any '{behavior}' event on the prior local day; "
                            f"groups from actively-journaled days"
                        ),
                        note=(
                            f"keep logging — n={len(exposed_values)}/"
                            f"{min_group_days} exposed, {len(unexposed_values)}/"
                            f"{min_group_days} unexposed"
                        ),
                    )
                )
                continue

            p_value = mann_whitney_u_p(exposed_values, unexposed_values)
            median_exposed = statistics.median(exposed_values)
            median_unexposed = statistics.median(unexposed_values)
            row = BehaviorInsightRow(
                behavior=behavior,
                outcome=outcome,
                status="ok",
                n_exposed=len(exposed_values),
                n_unexposed=len(unexposed_values),
                median_exposed=median_exposed,
                median_unexposed=median_unexposed,
                median_difference=median_exposed - median_unexposed,
                p_value=p_value,
                confounders=confounders,
                algorithm_version=BEHAVIOR_INSIGHT_ALGORITHM,
                method="",  # completed after FDR knows the executed family size
            )
            executed.append((row, p_value))
            rows.append(row)

    q_values = bh_qvalues([p for _, p in executed])
    for (row, _), q in zip(executed, q_values, strict=True):
        row.q_value = q
        row.method = (
            f"Mann-Whitney U (two-sided) on next-day {row.outcome}; "
            f"exposure = any '{row.behavior}' event on the prior local day; "
            f"groups from actively-journaled days; BH-FDR q over "
            f"{len(executed)} executed tests; association, not causation"
        )
    return rows
