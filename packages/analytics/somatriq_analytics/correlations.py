"""Pure correlation math: pearson, spearman, lagged pairs (spec §80-82; M10
§203; ADR 0009 — deterministic Python, never an LLM).

Stdlib only (``statistics`` + ``math``) per the dependency policy — no
scipy/numpy. Every result carries its sample size, a two-tailed p-value WITH
the method that produced it (``p_method``), and a magnitude band. The band
thresholds (|r| ≥ 0.3 / 0.5 / 0.7 → weak / moderate / strong) are published
rule-of-thumb conventions for describing co-movement strength — they are not
clinical claims and say nothing about causation (spec §82).

Honesty floors, by construction:

* ``n < MIN_OVERLAP_DAYS`` (14 shared days) → ``None``. Two weeks of overlap
  is the minimum before any coefficient is reported; below that the answer
  is "insufficient overlap", never a made-up r.
* A series that does not vary has no co-movement to measure → ``None``.
* ``p_method`` names the exact computation. Pearson and Spearman both use
  the Student-t two-tailed approximation on ``df = n - 2`` via the
  regularized incomplete beta (exact t-distribution tail). For Spearman the
  t-approximation applied to ranks is a documented approximation — ties are
  averaged in the ranks, but no tie-corrected variance is applied — and the
  label says so.

Everything here is DB-free; :mod:`somatriq_analytics.correlation_data`
loads dated daily series and feeds these functions.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

# The hard floor: fewer shared days than this never yields a coefficient.
MIN_OVERLAP_DAYS: Final = 14

INSUFFICIENT_OVERLAP_REASON: Final = (
    "insufficient overlap — need at least 14 shared days"
)
ZERO_VARIANCE_REASON: Final = (
    "one series does not vary over the shared days — correlation undefined"
)

# Spec §82: every surface that shows a coefficient shows this framing.
CAUSAL_LANGUAGE_NOTE: Final = (
    "Correlation is not causation — this describes co-movement in "
    "your personal data, not an effect."
)

# Family-wise error budget for multiple comparisons (spec §88 honesty).
FAMILY_ALPHA: Final = 0.05

Band = Literal["none", "weak", "moderate", "strong"]

P_METHOD_PEARSON: Final = "student-t two-tailed (incomplete beta), df = n - 2"
P_METHOD_SPEARMAN: Final = (
    "student-t two-tailed on ranks (approximation: average tie ranks, "
    "no tie-corrected variance), df = n - 2"
)


@dataclass(frozen=True)
class CorrelationResult:
    """One coefficient with its evidence attached (spec §81: never a bare r)."""

    r: float
    n: int
    p_value: float
    p_method: str
    band: Band


def band(strength_r: float) -> Band:
    """Magnitude band on |r|: 0.3 / 0.5 / 0.7 (inclusive lower edges).

    Published rule-of-thumb conventions for co-movement strength — labels
    for reading a table, not effect claims.
    """
    magnitude = abs(strength_r)
    if magnitude >= 0.7:
        return "strong"
    if magnitude >= 0.5:
        return "moderate"
    if magnitude >= 0.3:
        return "weak"
    return "none"


def bonferroni_alpha(n_tests: int) -> float:
    """Family-wise threshold: 0.05 / n_tests (nominal 0.05 when none ran)."""
    if n_tests <= 1:
        return FAMILY_ALPHA
    return FAMILY_ALPHA / n_tests


# ── regularized incomplete beta (Student-t tails without scipy) ─────────


_BETA_MAX_ITERATIONS: Final = 200
_BETA_EPS: Final = 3e-15
_BETA_FPMIN: Final = 1e-300


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (modified Lentz)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _BETA_FPMIN:
        d = _BETA_FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _BETA_MAX_ITERATIONS + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETA_FPMIN:
            d = _BETA_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETA_FPMIN:
            c = _BETA_FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETA_FPMIN:
            d = _BETA_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETA_FPMIN:
            c = _BETA_FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETA_EPS:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b) — exact via symmetry + continued fraction, lgamma frontend."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    # Use the fraction that converges quickly for the given x.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_tailed_p(t: float, df: int) -> float:
    """Two-tailed Student-t p-value: p = I_{df/(df+t²)}(df/2, 1/2)."""
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    x = df / (df + t * t)
    p = _regularized_incomplete_beta(df / 2.0, 0.5, x)
    return min(max(p, 0.0), 1.0)


# ── the estimators ───────────────────────────────────────────────────────


def pearson(xs: Sequence[float], ys: Sequence[float]) -> CorrelationResult | None:
    """Pearson r over paired samples; None below MIN_OVERLAP_DAYS or when
    either series has zero variance (see module docstring)."""
    return _correlate(xs, ys, P_METHOD_PEARSON)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> CorrelationResult | None:
    """Spearman ρ: Pearson on ranks (average ranks for ties)."""
    return _correlate(rankdata(xs), rankdata(ys), P_METHOD_SPEARMAN)


def _correlate(
    xs: Sequence[float], ys: Sequence[float], p_method: str
) -> CorrelationResult | None:
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    n = len(xs)
    if n < MIN_OVERLAP_DAYS:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    sum_xy = 0.0
    sum_xx = 0.0
    sum_yy = 0.0
    for x, y in zip(xs, ys, strict=True):
        dx = x - mean_x
        dy = y - mean_y
        sum_xy += dx * dy
        sum_xx += dx * dx
        sum_yy += dy * dy
    if sum_xx == 0.0 or sum_yy == 0.0:
        return None
    r = sum_xy / math.sqrt(sum_xx * sum_yy)
    # Floating point can land a hair outside [-1, 1]; clamp before the t
    # transform so |r| = 1 maps to p = 0 instead of a sqrt of a negative.
    r = max(-1.0, min(1.0, r))
    df = n - 2
    if df <= 0:  # unreachable given MIN_OVERLAP_DAYS; kept for honesty
        return None
    t = r * math.sqrt(df / (1.0 - r * r)) if r * r < 1.0 else math.inf
    return CorrelationResult(
        r=r,
        n=n,
        p_value=t_two_tailed_p(t, df),
        p_method=p_method,
        band=band(r),
    )


def rankdata(values: Sequence[float]) -> list[float]:
    """1-based ranks; tied values receive the average of their positions."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while (
            j + 1 < len(order)
            and values[order[j + 1]] == values[order[i]]
        ):
            j += 1
        average_rank = (i + j) / 2.0 + 1.0  # positions i..j (0-based) → 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1
    return ranks


# ── dated-series alignment (same-day and lagged) ────────────────────────


def join_lagged(
    series_a: Sequence[tuple[date, float]],
    series_b: Sequence[tuple[date, float]],
    lag_days: int,
) -> tuple[list[float], list[float]]:
    """Inner-join a[t] with b[t + lag_days] on calendar dates.

    Alignment is by date value, never by list index — missing days drop
    their pairs instead of shifting everything after them. ``lag_days = 1``
    answers "a today vs b tomorrow" (spec §80); ``0`` is the same-day join.
    """
    by_date_b = {day: value for day, value in series_b}
    xs: list[float] = []
    ys: list[float] = []
    for day, value in series_a:
        partner = by_date_b.get(day + timedelta(days=lag_days))
        if partner is not None:
            xs.append(value)
            ys.append(partner)
    return xs, ys


def lagged(
    series_a: Sequence[tuple[date, float]],
    series_b: Sequence[tuple[date, float]],
    lag_days: int,
) -> CorrelationResult | None:
    """Pearson r of a[t] vs b[t + lag_days] over the shared dates."""
    xs, ys = join_lagged(series_a, series_b, lag_days)
    return pearson(xs, ys)
