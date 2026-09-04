"""Pure HRV math: somatriq_hrv_rmssd_v1 (spec §75; ADR 0012/0017).

DB-free by design (ADR 0009 — deterministic Python, never an LLM): the API
layer does the sleep-window SQL and hands the ts-ordered RR list here. The
artifact threshold comes from the frozen recovery contract so the algorithm
and its constants cannot drift apart.

Algorithm (frozen — changes bump the version): successive pair deltas over
the ts-ordered RR list; a pair whose |ΔRR| EXCEEDS 400 ms is an artifact PAIR
(dropped from RMSSD/pNN50) and marks BOTH its intervals as artifacts (dropped
from SDNN/mean RR, which run over intervals belonging to no artifact pair).
A clean pair adjacent to a spike still counts for RMSSD — the pair set is
fixed by the original sequence, and only offending pairs leave it. Fewer
than two surviving pairs is insufficient — None, never zeros.
"""

from datetime import date
from statistics import fmean, pstdev

from somatriq_contracts.recovery import HRV_ARTIFACT_DELTA_MS, HrvSummary

# pNN50 threshold (50 ms) is part of the frozen v1 name/definition; the
# contract pins the 400 ms filter constant but has no constant for it.
_PNN50_DELTA_MS = 50

# rmssd_stats is pure over the RR sequence; day/session_count are metadata
# the caller owns. The epoch default only exists so the documented pure call
# rmssd_stats(rr) stays valid; every API caller passes the real day.
_EPOCH_DAY = date(1970, 1, 1)


def rmssd_stats(
    rr_ms: list[int], *, day: date = _EPOCH_DAY, session_count: int = 1
) -> HrvSummary | None:
    """One somatriq_hrv_rmssd_v1 pass over a ts-ordered RR sequence.

    ``None`` when fewer than two pairs survive the delta-400 filter — the
    sequence says "insufficient", and insufficient is never reported as zeros.
    """
    n = len(rr_ms)

    # Pass 1: the pair set. An offending pair (|Δ| > 400) is an artifact
    # pair and marks BOTH its intervals as artifacts, so one spike costs its
    # two neighbouring intervals their place in the interval statistics.
    artifact = [False] * n
    for i in range(n - 1):
        if abs(rr_ms[i + 1] - rr_ms[i]) > HRV_ARTIFACT_DELTA_MS:
            artifact[i] = True
            artifact[i + 1] = True

    valid = [rr for rr, bad in zip(rr_ms, artifact, strict=True) if not bad]

    # Pass 2: statistics over survivors. Surviving PAIRS are the pairs that
    # are not themselves artifact pairs (RMSSD/pNN50); surviving INTERVALS
    # are those marked by no artifact pair (SDNN/mean RR).
    deltas = [
        rr_ms[i + 1] - rr_ms[i]
        for i in range(n - 1)
        if abs(rr_ms[i + 1] - rr_ms[i]) <= HRV_ARTIFACT_DELTA_MS
    ]
    if len(deltas) < 2:
        return None

    squared = [d * d for d in deltas]
    rmssd = (sum(squared) / len(squared)) ** 0.5
    pnn50 = sum(1 for d in deltas if abs(d) > _PNN50_DELTA_MS) / len(deltas)

    return HrvSummary(
        day=day,
        rmssd_ms=round(rmssd, 2),
        sdnn_ms=round(pstdev(valid), 2),
        pnn50=round(pnn50, 4),
        mean_rr_ms=round(fmean(valid), 2),
        samples=n,
        valid_samples=len(valid),
        artifact_count=n - len(valid),
        coverage=round(len(valid) / n, 4),
        session_count=session_count,
    )
