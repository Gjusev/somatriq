"""Pure recovery math: somatriq_recovery_v1 + baselines (spec §74/§76).

Every score below is hand-computed from the frozen formula
score = 100·clamp(0.5 + Σ wᵢ·tanh(z'ᵢ)/2, 0, 1) with weights .4/.3/.3 and
z'_rhr sign-flipped.
"""

import math
from datetime import date

import pytest
from somatriq_analytics.recovery import baseline, recovery_v1, robust_z

DAY = date(2026, 9, 3)


def test_robust_z_exact() -> None:
    assert robust_z(50.0, 40.0, 10.0) == pytest.approx(2.0)
    assert robust_z(30.0, 40.0, 10.0) == pytest.approx(-2.0)
    assert robust_z(40.0, 40.0, 10.0) == 0.0


def test_robust_z_zero_iqr_saturates() -> None:
    """iqr == 0: equal -> 0.0; any deviation -> ±10.0 (documented saturation)."""
    assert robust_z(60.0, 60.0, 0.0) == 0.0
    assert robust_z(62.0, 60.0, 0.0) == 10.0
    assert robust_z(58.0, 60.0, 0.0) == -10.0


def test_baseline_inclusive_quartiles_exact() -> None:
    """[90,95,100,100,105,110,115]: median 100, q1 97.5, q3 107.5 -> iqr 10."""
    assert baseline([90.0, 95.0, 100.0, 100.0, 105.0, 110.0, 115.0]) == (100.0, 10.0)


def test_baseline_below_min_days_is_none() -> None:
    assert baseline([60.0] * 6) is None
    assert baseline([]) is None


def test_baseline_exactly_min_days_and_degenerate_iqr() -> None:
    assert baseline([60.0] * 7) == (60.0, 0.0)


def test_recovery_exact_score_hand_computed() -> None:
    """z' = (hrv +1, rhr 0, sleep +1) -> 100·(0.5 + 0.7·tanh(1)/2) = 76.66."""
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": 105.0, "rhr": 60.0, "sleep": 65.0},
        baselines={
            "hrv": (100.0, 10.0),  # z = (105-100)/5 = +1
            "rhr": (60.0, 1.0),  # z = 0
            "sleep": (60.0, 10.0),  # z = (65-60)/5 = +1
        },
    )
    expected = 100.0 * (0.5 + (0.4 + 0.3) * math.tanh(1.0) / 2)
    assert result.score == pytest.approx(expected, abs=0.005)
    assert result.score == 76.66
    assert result.missing_inputs == []
    assert result.caveats == []
    by_input = {c.input: c for c in result.contributions}
    assert by_input["hrv"].contribution == "positive"
    assert by_input["hrv"].robust_z == 1.0
    assert by_input["hrv"].baseline_median == 100.0
    assert by_input["hrv"].baseline_iqr == 10.0
    assert by_input["hrv"].value == 105.0
    assert by_input["rhr"].contribution == "neutral"
    assert by_input["sleep"].contribution == "positive"


def test_recovery_all_at_baseline_is_exactly_50() -> None:
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": 100.0, "rhr": 60.0, "sleep": 60.0},
        baselines={
            "hrv": (100.0, 10.0),
            "rhr": (60.0, 1.0),
            "sleep": (60.0, 10.0),
        },
    )
    assert result.score == 50.0
    scored = [c for c in result.contributions if c.input in ("hrv", "rhr", "sleep")]
    assert all(c.contribution == "neutral" for c in scored)


def test_recovery_neutral_band_edges() -> None:
    """|z'| <= 0.5 is neutral (edges inclusive); one tick outside flips."""
    base = {"hrv": (100.0, 4.0), "rhr": (60.0, 1.0), "sleep": (60.0, 10.0)}
    at_edge = recovery_v1(
        day=DAY,
        inputs={"hrv": 101.0, "rhr": 60.0, "sleep": 60.0},  # z' = +0.5
        baselines=base,
    )
    assert at_edge.score == pytest.approx(100 * (0.5 + 0.4 * math.tanh(0.5) / 2), abs=0.005)
    assert at_edge.score == pytest.approx(59.24, abs=0.005)
    by_input = {c.input: c for c in at_edge.contributions}
    assert by_input["hrv"].contribution == "neutral"
    below_edge = recovery_v1(
        day=DAY,
        inputs={"hrv": 99.0, "rhr": 60.0, "sleep": 60.0},  # z' = -0.5
        baselines=base,
    )
    assert {c.input: c for c in below_edge.contributions}["hrv"].contribution == "neutral"
    outside = recovery_v1(
        day=DAY,
        inputs={"hrv": 102.0, "rhr": 60.0, "sleep": 60.0},  # z' = +1
        baselines=base,
    )
    assert {c.input: c for c in outside.contributions}["hrv"].contribution == "positive"


def test_recovery_rhr_orientation_is_sign_flipped() -> None:
    """RHR above baseline drags the score down and reads negative."""
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": 100.0, "rhr": 62.0, "sleep": 60.0},
        baselines={"hrv": (100.0, 10.0), "rhr": (60.0, 4.0), "sleep": (60.0, 10.0)},
    )
    by_input = {c.input: c for c in result.contributions}
    assert by_input["rhr"].robust_z == -1.0  # z raw +1, oriented -1
    assert by_input["rhr"].contribution == "negative"
    assert result.score is not None
    assert result.score < 50.0


def test_recovery_missing_input_listed_never_imputed() -> None:
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": None, "rhr": 60.0, "sleep": 65.0},
        baselines={"hrv": (100.0, 10.0), "rhr": (60.0, 1.0), "sleep": (60.0, 10.0)},
    )
    assert result.score is None
    assert result.missing_inputs == ["hrv"]
    by_input = {c.input: c for c in result.contributions}
    assert by_input["hrv"].value is None
    assert by_input["hrv"].note == "input missing"
    assert by_input["sleep"].contribution == "positive"  # present inputs still explain


def test_recovery_insufficient_baseline_caveat() -> None:
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": 105.0, "rhr": 60.0, "sleep": 65.0},
        baselines={"hrv": None, "rhr": (60.0, 1.0), "sleep": (60.0, 10.0)},
    )
    assert result.score is None
    assert result.missing_inputs == []  # the input exists; its baseline does not
    assert result.caveats == ["baseline insufficient for hrv: needs >= 7 days"]
    by_input = {c.input: c for c in result.contributions}
    assert by_input["hrv"].note == "baseline insufficient"


def test_recovery_iqr_zero_saturation_score() -> None:
    """Degenerate baselines: only hrv deviates -> tanh(10) ≈ 1 -> exactly 70."""
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": 42.0, "rhr": 60.0, "sleep": 60.0},
        baselines={"hrv": (40.0, 0.0), "rhr": (60.0, 0.0), "sleep": (60.0, 0.0)},
    )
    assert result.score == pytest.approx(100 * (0.5 + 0.4 * math.tanh(10.0) / 2), abs=0.005)
    assert result.score == 70.0


def test_recovery_clamps_at_0_and_100() -> None:
    top = recovery_v1(
        day=DAY,
        inputs={"hrv": 42.0, "rhr": 58.0, "sleep": 62.0},
        baselines={"hrv": (40.0, 0.0), "rhr": (60.0, 0.0), "sleep": (60.0, 0.0)},
    )
    assert top.score == 100.0
    bottom = recovery_v1(
        day=DAY,
        inputs={"hrv": 38.0, "rhr": 62.0, "sleep": 58.0},
        baselines={"hrv": (40.0, 0.0), "rhr": (60.0, 0.0), "sleep": (60.0, 0.0)},
    )
    assert bottom.score == 0.0


def test_recovery_monotonic_in_hrv() -> None:
    """Higher HRV against a fixed baseline never lowers the score."""
    scores = []
    for hrv in range(95, 116, 2):
        result = recovery_v1(
            day=DAY,
            inputs={"hrv": float(hrv), "rhr": 60.0, "sleep": 60.0},
            baselines={"hrv": (100.0, 10.0), "rhr": (60.0, 1.0), "sleep": (60.0, 10.0)},
        )
        assert result.score is not None
        scores.append(result.score)
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)


def test_recovery_always_lists_five_contributions() -> None:
    result = recovery_v1(
        day=DAY,
        inputs={"hrv": None, "rhr": None, "sleep": None},
        baselines={"hrv": None, "rhr": None, "sleep": None},
    )
    assert [c.input for c in result.contributions] == [
        "hrv",
        "rhr",
        "sleep",
        "temperature",
        "training_load",
    ]
    for c in result.contributions[3:]:
        assert c.contribution == "neutral"
        assert c.note == "input missing in v1"
    assert result.missing_inputs == ["hrv", "rhr", "sleep"]
    assert result.score is None
    assert result.algorithm_version == "somatriq_recovery_v1"
