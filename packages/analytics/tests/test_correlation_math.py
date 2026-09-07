"""Pure correlation math: pearson/spearman/lagged (spec §80-82, §203 M10).

Every exact-value vector below is hand-computed. The shared construction for
the n=16 exact cases: xs = 1..16 has dx = xs - 8.5 and Σdx² = 340, so
choosing dy to be dx on a subset with Σdy² = 85 (and 0 elsewhere) gives

    r = Σdxdy / sqrt(Σdx² · Σdy²) = 85 / sqrt(340 · 85) = 85/170 = 0.5

exactly — the "half the days co-move, half sit on the mean" vector.
"""

import math
from datetime import date, timedelta

import pytest
from somatriq_analytics.correlations import (
    CAUSAL_LANGUAGE_NOTE,
    INSUFFICIENT_OVERLAP_REASON,
    MIN_OVERLAP_DAYS,
    ZERO_VARIANCE_REASON,
    band,
    bonferroni_alpha,
    lagged,
    pearson,
    rankdata,
    spearman,
)

DAY0 = date(2026, 8, 1)


def _days(n: int) -> list[date]:
    return [DAY0 + timedelta(days=i) for i in range(n)]


def _exact_half_vector() -> tuple[list[float], list[float]]:
    """dy = dx on the symmetric subset xs ∈ {2, 8, 9, 15} (Σdy² = 85), dy = 0
    elsewhere → r = 85/sqrt(340·85) = 0.5 exactly.

    The subset must be symmetric around the mean so that Σdy = 0 and the
    flat value 8.5 IS mean(ys) — otherwise the flat days contribute their
    own deviation and r drifts off 0.5.
    """
    xs = [float(i) for i in range(1, 17)]
    ys = [x if x in (2.0, 8.0, 9.0, 15.0) else 8.5 for x in xs]
    return xs, ys


# ── exact r on hand-computed vectors ─────────────────────────────────────


def test_pearson_perfect_positive() -> None:
    xs = [float(i) for i in range(1, 21)]
    result = pearson(xs, [2.0 * x + 3.0 for x in xs])
    assert result is not None
    assert result.n == 20
    assert result.r == pytest.approx(1.0, abs=1e-12)
    assert result.band == "strong"
    assert result.p_value == pytest.approx(0.0, abs=1e-12)


def test_pearson_perfect_negative() -> None:
    xs = [float(i) for i in range(1, 21)]
    result = pearson(xs, [3.0 - 2.0 * x for x in xs])
    assert result is not None
    assert result.r == pytest.approx(-1.0, abs=1e-12)
    assert result.band == "strong"


def test_pearson_exact_half() -> None:
    """Hand construction above: exactly r = 0.5 (band: moderate)."""
    xs, ys = _exact_half_vector()
    result = pearson(xs, ys)
    assert result is not None
    assert result.n == 16
    assert result.r == pytest.approx(0.5, abs=1e-9)
    assert result.band == "moderate"


def test_pearson_exact_zero_symmetric_halves() -> None:
    """ys = xs for the first 8 days, ys = 17-xs for the last 8: the two
    halves' Σdxdy contributions cancel (170 - 170) → r = 0 exactly."""
    xs = [float(i) for i in range(1, 17)]
    ys = [x if x <= 8.0 else 17.0 - x for x in xs]
    result = pearson(xs, ys)
    assert result is not None
    assert result.r == pytest.approx(0.0, abs=1e-9)
    assert result.band == "none"


# ── the honest floor: n < 14 never produces an r ────────────────────────


def test_below_min_overlap_returns_none() -> None:
    xs = [float(i) for i in range(MIN_OVERLAP_DAYS - 1)]
    assert pearson(xs, xs) is None
    assert spearman(xs, xs) is None
    assert MIN_OVERLAP_DAYS == 14
    assert INSUFFICIENT_OVERLAP_REASON == ("insufficient overlap — need at least 14 shared days")


def test_at_min_overlap_returns_result() -> None:
    xs = [float(i) for i in range(MIN_OVERLAP_DAYS)]
    result = pearson(xs, xs)
    assert result is not None
    assert result.n == MIN_OVERLAP_DAYS


def test_zero_variance_returns_none() -> None:
    """A flat series has no co-movement to measure — never a made-up r."""
    assert pearson([5.0] * 20, [float(i) for i in range(20)]) is None
    assert ZERO_VARIANCE_REASON.startswith("one series does not vary")


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        pearson([1.0] * 20, [1.0] * 19)


# ── ranks: average ties ──────────────────────────────────────────────────


def test_rankdata_average_ties() -> None:
    assert rankdata([1.0, 2.0, 2.0, 3.0]) == [1.0, 2.5, 2.5, 4.0]
    assert rankdata([7.0, 7.0, 7.0]) == [2.0, 2.0, 2.0]
    assert rankdata([3.0, 1.0, 2.0]) == [3.0, 1.0, 2.0]


def test_spearman_monotone_nonlinear_is_perfect() -> None:
    """Rank correlation is invariant to any strictly monotone transform."""
    xs = [float(i) for i in range(1, 17)]
    result = spearman(xs, [x**3 for x in xs])
    assert result is not None
    assert result.r == pytest.approx(1.0, abs=1e-12)
    assert result.p_value == pytest.approx(0.0, abs=1e-12)
    assert "rank" in result.p_method


def test_spearman_pearson_diverge_on_outliers() -> None:
    """One wild day drags pearson below spearman (robustness, spec §88)."""
    xs = [float(i) for i in range(1, 17)]
    ys = [x + (90.0 if x == 16.0 else 0.0) for x in xs]
    pr = pearson(xs, ys)
    sr = spearman(xs, ys)
    assert pr is not None and sr is not None
    assert sr.r > pr.r


def test_spearman_with_ties_still_perfect_on_identity() -> None:
    xs = [1.0, 2.0, 2.0, 3.0] * 4 + [4.0, 5.0, 5.0, 6.0]
    result = spearman(xs, list(xs))
    assert result is not None
    assert result.r == pytest.approx(1.0, abs=1e-12)


# ── p-value honesty: t-distribution, cross-checked independently ────────


def _t_density(t: float, df: int) -> float:
    """Closed-form Student-t density — the independent check, not the code."""
    norm = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    return float(norm * (1 + t * t / df) ** (-(df + 1) / 2))


def _two_tailed_p_by_simpson(t: float, df: int) -> float:
    """2·∫_t^∞ density du via Simpson on [t, 60] with 20000 steps."""
    upper, steps = 60.0, 20_000
    h = (upper - t) / steps
    total = _t_density(t, df) + _t_density(upper, df)
    for i in range(1, steps):
        total += (4 if i % 2 else 2) * _t_density(t + i * h, df)
    return min(2.0 * total * h / 3.0, 1.0)


def test_p_value_matches_independent_t_integration() -> None:
    """r = 0.5, n = 16 → df 14, t = 0.5·sqrt(14/0.75) ≈ 2.1602, p ≈ 0.04858.

    Expected value integrated numerically from the closed-form density in
    this test — an independent method, not a rerun of the implementation.
    """
    xs, ys = _exact_half_vector()
    result = pearson(xs, ys)
    assert result is not None
    t = 0.5 * math.sqrt(14.0 / 0.75)
    assert result.p_value == pytest.approx(_two_tailed_p_by_simpson(t, 14), abs=1e-6)
    assert result.p_value == pytest.approx(0.048580, abs=5e-6)


def _series_with_exact_r(n: int, target: float) -> tuple[list[float], list[float]]:
    """xs linear 1..n; ys built so pearson(xs, ys) == target exactly.

    dy = target·dx̂ + sqrt(1-target²)·q̂ where dx̂ is the centered linear
    direction and q̂ the centered quadratic — the two are exactly orthogonal
    over equally spaced points (Σ dx³ = Σ dx = 0 on the symmetric range),
    so the mixture's r is target by construction.
    """
    xs = [float(i) for i in range(1, n + 1)]
    mean = sum(xs) / n
    dx = [x - mean for x in xs]
    q = [d * d - sum(v * v for v in dx) / n for d in dx]
    sx = math.sqrt(sum(v * v for v in dx))
    sq = math.sqrt(sum(v * v for v in q))
    ys = [
        mean + target * (d / sx) + math.sqrt(1 - target * target) * (v / sq)
        for d, v in zip(dx, q, strict=True)
    ]
    return xs, ys


def test_p_value_orderings() -> None:
    """Stronger |r| → smaller p at fixed n; same |r| shrinks as n grows."""
    p40 = pearson(*_series_with_exact_r(24, 0.4))
    p60 = pearson(*_series_with_exact_r(24, 0.6))
    p90 = pearson(*_series_with_exact_r(24, 0.9))
    assert p40 is not None and p60 is not None and p90 is not None
    assert p40.r == pytest.approx(0.4, abs=1e-9)
    assert p60.r == pytest.approx(0.6, abs=1e-9)
    assert p40.p_value > p60.p_value > p90.p_value

    p_short = pearson(*_series_with_exact_r(24, 0.4))
    p_long = pearson(*_series_with_exact_r(60, 0.4))
    assert p_long is not None and p_short is not None
    assert p_long.r == pytest.approx(p_short.r, abs=1e-9)
    assert p_long.p_value < p_short.p_value


def test_p_method_labels_the_method() -> None:
    xs = [float(i) for i in range(1, 21)]
    pr = pearson(xs, xs)
    sr = spearman(xs, xs)
    assert pr is not None and sr is not None
    assert "student-t" in pr.p_method
    assert "two-tailed" in pr.p_method
    assert "df = n - 2" in pr.p_method
    # Spearman's approximation is labeled as one — no silent pretense.
    assert "approximation" in sr.p_method


# ── magnitude bands (documented thresholds) ──────────────────────────────


def test_band_thresholds_documented() -> None:
    assert band(0.0) == "none"
    assert band(0.29) == "none"
    assert band(0.3) == "weak"  # inclusive lower edge
    assert band(0.49) == "weak"
    assert band(-0.5) == "moderate"
    assert band(0.69) == "moderate"
    assert band(0.7) == "strong"
    assert band(-0.99) == "strong"


# ── lag alignment (spec §80: "HRV today vs RHR tomorrow") ───────────────


def test_lagged_perfect_next_day_relation() -> None:
    """b[t] = a[t-1]² ; a[t] vs b[t+1] re-pairs identical values → r = 1.

    Only lag=+1 (a[t] against b[t+lag], the documented direction) is exact;
    lag=0 pairs a[t] with (t-1)² and cannot reach 1 — the off-by-one pin.
    """
    days = _days(20)
    a = [(d, float((i + 1) ** 2)) for i, d in enumerate(days)]
    b = [(d, float(i**2)) for i, d in enumerate(days)]  # b[t] = (t-1)²

    plus1 = lagged(a, b, 1)
    assert plus1 is not None
    assert plus1.n == 19
    assert plus1.r == pytest.approx(1.0, abs=1e-12)

    same = lagged(a, b, 0)
    assert same is not None
    assert same.n == 20
    assert same.r < 0.9999

    minus1 = lagged(a, b, -1)
    assert minus1 is not None
    assert minus1.n == 19
    assert minus1.r < 0.9999


def test_lagged_alignment_is_by_date_not_index() -> None:
    """A missing day in b breaks that pair only — gaps never shift indices."""
    days = _days(16)
    a = [(d, float(i + 1)) for i, d in enumerate(days)]
    b_days = days[:-1]  # last day absent from b entirely
    b = [(d, float(i + 1)) for i, d in enumerate(b_days)]
    result = lagged(a, b, 0)
    assert result is not None
    assert result.n == 15
    assert result.r == pytest.approx(1.0, abs=1e-12)

    sparse_b = [(days[i], float(i + 1)) for i in (0, 2, 5, 9, 14, 15)]
    # Six shared days is below MIN_OVERLAP_DAYS — the honest answer is
    # "insufficient", not a coefficient over a handful of points.
    assert lagged(a, sparse_b, 0) is None


def test_lagged_below_min_overlap_returns_none() -> None:
    days = _days(10)
    a = [(d, float(i + 1)) for i, d in enumerate(days)]
    b = [(d, float(i + 1)) for i, d in enumerate(days)]
    assert lagged(a, b, 0) is None


# ── causal-language note + multiple-comparisons honesty ─────────────────


def test_causal_language_note_verbatim() -> None:
    assert CAUSAL_LANGUAGE_NOTE == (
        "Correlation is not causation — this describes co-movement in "
        "your personal data, not an effect."
    )


def test_bonferroni_alpha() -> None:
    assert bonferroni_alpha(1) == pytest.approx(0.05)
    assert bonferroni_alpha(20) == pytest.approx(0.0025)
    assert bonferroni_alpha(0) == pytest.approx(0.05)  # no tests → nominal
