"""Pure recovery math: somatriq_recovery_v1 + somatriq_baseline_v1
(spec §74/§76; ADR 0012/0017).

DB-free by design (ADR 0009 — deterministic Python, never an LLM); every
constant comes from the frozen recovery contract. The formula (frozen —
changes bump the version):

    score = 100 * clamp( 0.5 + Σ wᵢ·tanh(z'ᵢ)/2 , 0, 1 )
    z'_hrv = +z_hrv ; z'_rhr = -z_rhr ; z'_sleep = +z_sleep

weights {hrv: 0.4, rhr: 0.3, sleep: 0.3}; a contribution is positive when
z' > +0.5, negative when z' < -0.5, else neutral. Missing inputs are LISTED,
never imputed; the score is null unless all three inputs exist AND carry a
usable baseline. Temperature and training load are neutral in v1 (spec §76
lists five contributions; two are noted "input missing in v1").
"""

import math
import statistics
from collections.abc import Mapping
from datetime import date
from typing import Literal

from somatriq_contracts.recovery import (
    RECOVERY_BASELINE_MIN_DAYS,
    RECOVERY_NEUTRAL_Z,
    RECOVERY_WEIGHTS,
    Contribution,
    RecoveryContribution,
    RecoveryResult,
)

# somatriq_baseline_v1 shape: (median, inclusive IQR).
Baseline = tuple[float, float]

# A degenerate baseline (iqr == 0) cannot divide. An input equal to the
# median is exactly on it (0.0); any deviation saturates at ±10.0 instead of
# ±inf — tanh(±10) ≈ ±1 to 8 decimals, so the score is numerically identical
# to infinity while every emitted number stays a finite float.
Z_SATURATION = 10.0

RecoveryInput = Literal["hrv", "rhr", "sleep"]

_V1_INPUTS: tuple[RecoveryInput, ...] = ("hrv", "rhr", "sleep")
_V1_NEUTRAL_INPUTS: tuple[Literal["temperature", "training_load"], ...] = (
    "temperature",
    "training_load",
)
# Orientation: higher HRV and more sleep are good; a LOWER resting HR is
# good, so its robust z enters the frozen formula sign-flipped.
_ORIENTATION = {"hrv": 1.0, "rhr": -1.0, "sleep": 1.0}
_V1_MISSING_NOTE = "input missing in v1"
_MISSING_NOTE = "input missing"


def robust_z(value: float, baseline_median: float, baseline_iqr: float) -> float:
    """(value - median) / (iqr / 2); ±Z_SATURATION when iqr == 0 (see above)."""
    if baseline_iqr == 0:
        if value == baseline_median:
            return 0.0
        return Z_SATURATION if value > baseline_median else -Z_SATURATION
    return (value - baseline_median) / (baseline_iqr / 2)


def baseline(values: list[float]) -> Baseline | None:
    """(median, inclusive-IQR) personal baseline (somatriq_baseline_v1).

    Inclusive quartiles (the statistics "inclusive" method). ``None`` when
    fewer than RECOVERY_BASELINE_MIN_DAYS values exist — an insufficient
    baseline is reported as missing, never faked as a zero-width one.
    """
    if len(values) < RECOVERY_BASELINE_MIN_DAYS:
        return None
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return (median, q3 - q1)


def recovery_v1(
    *,
    day: date,
    inputs: Mapping[str, float | None],
    baselines: Mapping[str, Baseline | None],
) -> RecoveryResult:
    """Explainable somatriq_recovery_v1 over today's inputs and baselines.

    ``inputs``/``baselines`` are keyed hrv/rhr/sleep; a missing key, a None
    value, or a None baseline is never imputed: missing values land in
    missing_inputs, insufficient baselines in caveats, and either forces the
    score to null. The robust_z stored on each contribution row is the
    ORIENTED z' (sign-flipped for rhr) so the row's value matches its
    positive/negative/neutral label exactly as the formula sees it.
    """
    contributions: list[RecoveryContribution] = []
    caveats: list[str] = []
    missing_inputs: list[str] = []
    oriented: dict[str, float] = {}

    for key in _V1_INPUTS:
        value = inputs.get(key)
        if value is None:
            missing_inputs.append(key)
            contributions.append(RecoveryContribution(input=key, note=_MISSING_NOTE))
            continue
        pair = baselines.get(key)
        if pair is None:
            caveats.append(
                f"baseline insufficient for {key}: needs >= {RECOVERY_BASELINE_MIN_DAYS} days"
            )
            contributions.append(
                RecoveryContribution(input=key, value=value, note="baseline insufficient")
            )
            continue
        median, iqr = pair
        z_prime = _ORIENTATION[key] * robust_z(value, median, iqr)
        oriented[key] = z_prime
        if z_prime > RECOVERY_NEUTRAL_Z:
            kind: Contribution = "positive"
        elif z_prime < -RECOVERY_NEUTRAL_Z:
            kind = "negative"
        else:
            kind = "neutral"
        contributions.append(
            RecoveryContribution(
                input=key,
                value=value,
                baseline_median=median,
                baseline_iqr=iqr,
                robust_z=round(z_prime, 4),
                contribution=kind,
            )
        )

    # Spec §76 lists five contributions; temperature and training load have
    # no v1 input and are reported neutral with the frozen note.
    for neutral_key in _V1_NEUTRAL_INPUTS:
        contributions.append(RecoveryContribution(input=neutral_key, note=_V1_MISSING_NOTE))

    score: float | None = None
    if len(oriented) == len(_V1_INPUTS):
        inner = (
            0.5 + sum(RECOVERY_WEIGHTS[key] * math.tanh(oriented[key]) for key in _V1_INPUTS) / 2
        )
        score = round(100.0 * min(max(inner, 0.0), 1.0), 2)

    return RecoveryResult(
        day=day,
        score=score,
        contributions=contributions,
        missing_inputs=missing_inputs,
        caveats=caveats,
    )
