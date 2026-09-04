"""Pure HRV math: somatriq_hrv_rmssd_v1 (spec §75; ADR 0012/0017).

Every expectation below is hand-computed from the frozen definition: pair
deltas, both-sides artifact dropping above 400 ms, RMSSD/SDNN/pNN50/mean RR
over survivors, and the never-zeros insufficient rule.
"""

import math
from datetime import date

from somatriq_analytics.hrv import rmssd_stats

DAY = date(2026, 9, 3)


def test_exact_rmssd_on_small_vector() -> None:
    """[900, 950, 905]: deltas +50/-45 -> RMSSD sqrt(2262.5), SDNN sqrt(505.5̅)."""
    summary = rmssd_stats([900, 950, 905], day=DAY)
    assert summary is not None
    assert summary.rmssd_ms == round(math.sqrt((50**2 + 45**2) / 2), 2) == 47.57
    mean = (900 + 950 + 905) / 3
    var = ((900 - mean) ** 2 + (950 - mean) ** 2 + (905 - mean) ** 2) / 3
    assert summary.sdnn_ms == round(math.sqrt(var), 2) == 22.48
    assert summary.pnn50 == 0.0  # neither |50| nor |45| is > 50
    assert summary.mean_rr_ms == 918.33
    assert summary.samples == 3
    assert summary.valid_samples == 3
    assert summary.artifact_count == 0
    assert summary.coverage == 1.0
    assert summary.day == DAY
    assert summary.session_count == 1
    assert summary.filter_version == "simple-delta400"
    assert summary.algorithm_version == "somatriq_hrv_rmssd_v1"


def test_alternating_vector_exact() -> None:
    """[800, 900, 800, 900]: every delta ±100 -> RMSSD 100, SDNN 50, pNN50 1."""
    summary = rmssd_stats([800, 900, 800, 900], day=DAY)
    assert summary is not None
    assert summary.rmssd_ms == 100.0
    assert summary.sdnn_ms == 50.0
    assert summary.pnn50 == 1.0
    assert summary.mean_rr_ms == 850.0
    assert summary.coverage == 1.0


def test_artifact_spike_drops_both_sides_exactly() -> None:
    """One spike at index 3: artifact pairs (2,3) and (3,4) drop, marking
    intervals 2,3,4 for the interval stats (SDNN/mean over
    [900, 950, 920, 915]), while RMSSD keeps every clean pair of the
    original sequence: +50/-45/+10/-5 -> sqrt(4650/4).
    """
    summary = rmssd_stats([900, 950, 905, 1500, 910, 920, 915], day=DAY)
    assert summary is not None
    deltas = [50, -45, 10, -5]
    expected_rmssd = math.sqrt(sum(d * d for d in deltas) / len(deltas))
    assert summary.rmssd_ms == round(expected_rmssd, 2) == 34.1
    valid = [900.0, 950.0, 920.0, 915.0]
    mean = sum(valid) / 4
    expected_sdnn = math.sqrt(sum((v - mean) ** 2 for v in valid) / 4)
    assert summary.sdnn_ms == round(expected_sdnn, 2) == 18.16
    assert summary.mean_rr_ms == 921.25
    assert summary.pnn50 == 0.0
    assert summary.samples == 7
    assert summary.valid_samples == 4
    assert summary.artifact_count == 3
    assert summary.coverage == round(4 / 7, 4)


def test_delta_exactly_400_is_not_artifact() -> None:
    """The frozen filter drops |Δ| EXCEEDING 400: exactly 400 survives."""
    summary = rmssd_stats([800, 1200, 800], day=DAY)
    assert summary is not None
    assert summary.rmssd_ms == 400.0
    assert summary.pnn50 == 1.0
    assert summary.artifact_count == 0


def test_delta_401_drops_everything() -> None:
    """[800, 1201, 800]: both pairs offend, all intervals drop -> None."""
    assert rmssd_stats([800, 1201, 800], day=DAY) is None


def test_all_artifact_returns_none() -> None:
    assert rmssd_stats([800, 1500, 800], day=DAY) is None


def test_empty_returns_none() -> None:
    assert rmssd_stats([], day=DAY) is None


def test_single_interval_returns_none() -> None:
    assert rmssd_stats([900], day=DAY) is None


def test_one_surviving_pair_is_insufficient() -> None:
    """Two clean intervals give exactly one pair — below the minimum of two."""
    assert rmssd_stats([800, 900], day=DAY) is None


def test_two_surviving_pairs_compute() -> None:
    """[800, 900, 800]: two surviving pairs -> RMSSD 100."""
    summary = rmssd_stats([800, 900, 800], day=DAY)
    assert summary is not None
    assert summary.rmssd_ms == 100.0


def test_pnn50_boundary_exclusive() -> None:
    """|Δ| = 50 is NOT > 50: pNN50 counts it out."""
    summary = rmssd_stats([800, 850, 800, 850], day=DAY)
    assert summary is not None
    assert summary.pnn50 == 0.0
    assert summary.rmssd_ms == 50.0


def test_default_call_uses_epoch_day() -> None:
    """The documented pure call rmssd_stats(rr) works; day defaults to epoch."""
    summary = rmssd_stats([800, 900, 800])
    assert summary is not None
    assert summary.day == date(1970, 1, 1)
    assert summary.session_count == 1
