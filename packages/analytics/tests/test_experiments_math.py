"""N-of-1 experiment evaluation (spec §83-86, §82; M11 §204; ADR 0009).

Every exact vector below is hand-computed. The shared construction: the
7-point ramp [47..53] has mean 50 and sample variance 28/6, so shifting it
by +10 keeps the variance while moving the mean — giving Welch t, df and
Cohen's d in closed form:

    var/n        = (28/6)/7            = 2/3
    se           = sqrt(2/3 + 2/3)     = 2/sqrt(3)
    t            = 10 / (2/sqrt(3))    = 5*sqrt(3)   ≈ 8.66025
    welch df     = (4/3)^2 / (2*(2/3)^2/6) = 12       (exactly)
    pooled sd    = sqrt((6*28/6 + 6*28/6)/12) = sqrt(14/3)
    d            = 10 / sqrt(14/3)     ≈ 4.62910

Verdict wording is the system under test as much as the numbers (spec §82):
even a significant result says "consistent with effect" — never "proves".
"""

import math

import pytest
from somatriq_analytics.experiments import (
    ALPHA,
    CAVEAT,
    MIN_DAYS_PER_PHASE,
    P_METHOD_WELCH,
    Direction,
    evaluate,
)

RAMP = [float(v) for v in range(47, 54)]  # mean 50, sample variance 28/6
SHIFTED = [float(v) for v in range(57, 64)]  # mean 60, same variance


# ── exact statistics on the hand-computed shift ───────────────────────────


def test_known_shift_exact_t_df_d() -> None:
    result = evaluate(RAMP, SHIFTED, "increase")
    assert result.n_baseline == 7
    assert result.n_intervention == 7
    assert result.mean_baseline == pytest.approx(50.0)
    assert result.mean_intervention == pytest.approx(60.0)
    assert result.mean_difference == pytest.approx(10.0)
    assert result.welch_t == pytest.approx(5.0 * math.sqrt(3.0), abs=1e-9)
    assert result.welch_df == 12  # exact by the hand derivation above
    assert result.cohens_d == pytest.approx(10.0 / math.sqrt(14.0 / 3.0), abs=1e-9)
    assert result.p_value is not None
    assert result.p_value < 0.05
    assert result.verdict == "consistent with effect"


def test_known_shift_p_cross_checked_by_simpson() -> None:
    """p for t = 5*sqrt(3), df = 12 integrated numerically from the closed
    form Student-t density — an independent method, not a rerun."""
    t = 5.0 * math.sqrt(3.0)
    df = 12.0

    def density(u: float) -> float:
        norm = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
        return float(norm * (1 + u * u / df) ** (-(df + 1) / 2))

    upper, steps = 60.0, 20_000
    h = (upper - t) / steps
    total = density(t) + density(upper)
    for i in range(1, steps):
        total += (4 if i % 2 else 2) * density(t + i * h)
    p_simpson = 2.0 * total * h / 3.0

    result = evaluate(RAMP, SHIFTED, "increase")
    assert result.p_value == pytest.approx(p_simpson, abs=1e-6)


def test_same_distribution_is_inconclusive() -> None:
    result = evaluate(RAMP, RAMP, "increase")
    assert result.mean_difference == pytest.approx(0.0)
    assert result.cohens_d == pytest.approx(0.0, abs=1e-12)
    assert result.welch_t == pytest.approx(0.0, abs=1e-12)
    assert result.p_value == pytest.approx(1.0)
    assert result.verdict == "inconclusive"


def test_p_method_labels_the_method() -> None:
    result = evaluate(RAMP, SHIFTED, "any")
    assert result.p_method == P_METHOD_WELCH
    assert "welch" in result.p_method
    assert "two-tailed" in result.p_method


# ── direction handling (spec §82: the hypothesis is directional) ──────────


def test_increase_hypothesis_with_decrease_is_opposite() -> None:
    result = evaluate(SHIFTED, RAMP, "increase")  # dropped by 10
    assert result.mean_difference == pytest.approx(-10.0)
    assert result.p_value is not None
    assert result.p_value < ALPHA
    assert result.verdict == "opposite of hypothesis"


def test_decrease_hypothesis_with_decrease_is_consistent() -> None:
    result = evaluate(SHIFTED, RAMP, "decrease")
    assert result.verdict == "consistent with effect"


def test_any_direction_takes_either_way() -> None:
    assert evaluate(RAMP, SHIFTED, "any").verdict == "consistent with effect"
    assert evaluate(SHIFTED, RAMP, "any").verdict == "consistent with effect"


def test_non_significant_shift_is_inconclusive() -> None:
    """+1 shift on the same spread: t = 1/(2/sqrt(3)) ≈ 0.866, df = 12,
    p ≈ 0.403459 (verified against Simpson on the closed-form density)."""
    nudged = [v + 1.0 for v in RAMP]
    result = evaluate(RAMP, nudged, "increase")
    assert result.welch_t == pytest.approx(math.sqrt(3.0) / 2.0, abs=1e-9)
    assert result.p_value is not None
    assert result.p_value == pytest.approx(0.403459, abs=5e-6)
    assert result.p_value > ALPHA
    assert result.verdict == "inconclusive"  # right direction, not enough evidence


def test_invalid_direction_raises() -> None:
    with pytest.raises(ValueError, match="direction"):
        evaluate(RAMP, SHIFTED, "up")  # type: ignore[arg-type]


# ── the min-n floor: small samples never earn a verdict ───────────────────


def test_min_days_per_phase_is_seven() -> None:
    assert MIN_DAYS_PER_PHASE == 7


def test_below_min_n_is_inconclusive_even_with_a_huge_shift() -> None:
    baseline = [50.0, 50.5, 51.0, 51.5, 52.0, 52.5]
    intervention = [90.0, 90.5, 91.0, 91.5, 92.0, 92.5]
    result = evaluate(baseline, intervention, "increase")
    assert result.p_value is not None
    assert result.p_value < 1e-6  # statistics still computed and reported…
    assert result.verdict == "inconclusive"  # …but n=6 per phase earns no verdict


def test_empty_phase_is_inconclusive_with_null_means() -> None:
    result = evaluate([], SHIFTED, "increase")
    assert result.n_baseline == 0
    assert result.n_intervention == 7
    assert result.mean_baseline is None
    assert result.mean_intervention == pytest.approx(60.0)
    assert result.mean_difference is None
    assert result.cohens_d is None
    assert result.welch_t is None
    assert result.p_value is None
    assert result.verdict == "inconclusive"


# ── degenerate (zero-variance) phases stay honest ─────────────────────────


def test_flat_identical_phases_are_inconclusive() -> None:
    result = evaluate([50.0] * 7, [50.0] * 7, "increase")
    assert result.cohens_d == pytest.approx(0.0, abs=1e-12)
    assert result.p_value == pytest.approx(1.0)
    assert result.verdict == "inconclusive"


def test_flat_different_phases_refuse_a_verdict() -> None:
    """Zero within-phase variance with different means has no defined t —
    the honest answer is "undefined", never an invented p = 0."""
    result = evaluate([50.0] * 7, [60.0] * 7, "increase")
    assert result.cohens_d is None
    assert result.welch_t is None
    assert result.p_value is None
    assert result.verdict == "inconclusive"


# ── caveat wording (spec §82 humility — single-person experiment) ────────


def test_caveat_is_verbatim_and_always_attached() -> None:
    assert CAVEAT == (
        "N-of-1: your data only; replication requires repeating the experiment"
    )
    assert evaluate(RAMP, SHIFTED, "increase").caveat == CAVEAT
    assert evaluate([], [], "any").caveat == CAVEAT


def test_verdict_vocabulary_is_closed() -> None:
    """Exactly three verdicts exist and none of them claims proof (§82)."""
    cases: list[tuple[tuple[list[float], list[float]], Direction, str]] = [
        ((RAMP, SHIFTED), "increase", "consistent with effect"),
        ((SHIFTED, RAMP), "increase", "opposite of hypothesis"),
        ((RAMP, RAMP), "increase", "inconclusive"),
    ]
    for values, direction, verdict in cases:
        baseline, intervention = values
        assert evaluate(baseline, intervention, direction).verdict == verdict
