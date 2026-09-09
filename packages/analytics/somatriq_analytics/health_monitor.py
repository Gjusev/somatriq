"""Pure health-monitor math: somatriq_health_monitor_v1 (Block 3; ADR 0009).

DB-free; every constant lives in the frozen contract. One vital at a
time: the period median against the personal baseline as a robust z
(somatriq_baseline_v1), with warmup and coverage states that never
fabricate a number and never borrow clinical language.
"""

import statistics

from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_Z_THRESHOLD,
    VitalSummary,
)

from .recovery import robust_z

_SATURATION = 10.0  # degenerate baselines saturate (recovery.py precedent)


def health_monitor_v1(
    *,
    vital: str,
    period_values: list[float],
    baseline_values: list[float],
    expected_days: int,
    label: str | None = None,
    source: str = "",
) -> VitalSummary:
    """One vital's period-vs-baseline summary (frozen contract semantics)."""
    n_days = len(period_values)
    coverage = min(n_days / expected_days, 1.0) if expected_days > 0 else 0.0
    summary = VitalSummary(
        vital=vital,
        label=label or vital,
        source=source,
        n_days=n_days,
        coverage=coverage,
    )

    if n_days == 0:
        summary.status = "insufficient"
        summary.note = "no measured days in the period"
        return summary

    summary.period_median = statistics.median(period_values)

    if len(baseline_values) < 7:  # somatriq_baseline_v1 minimum
        summary.status = "building"
        summary.note = f"personal baseline still building ({len(baseline_values)}/7 days)"
        return summary

    baseline_median = statistics.median(baseline_values)
    ordered = sorted(baseline_values)
    midpoint = len(ordered) // 2
    lower = statistics.median(ordered[:midpoint])
    upper = statistics.median(ordered[midpoint:])
    baseline_iqr = upper - lower
    summary.baseline_median = baseline_median
    summary.baseline_iqr = baseline_iqr

    if baseline_iqr == 0:
        # Degenerate baseline: exactly-on median is 0.0, any deviation
        # saturates instead of dividing (recovery.py's documented z rule).
        deviation = summary.period_median - baseline_median
        z = 0.0 if deviation == 0 else _SATURATION if deviation > 0 else -_SATURATION
    else:
        z = robust_z(summary.period_median, baseline_median, baseline_iqr)
    summary.robust_z = round(z, 4)

    if z >= HEALTH_MONITOR_Z_THRESHOLD:
        summary.status = "elevated"
        summary.note = f"above your baseline (z={z:+.1f})"
    elif z <= -HEALTH_MONITOR_Z_THRESHOLD:
        summary.status = "reduced"
        summary.note = f"below your baseline (z={z:+.1f})"
    else:
        summary.status = "within"
        summary.note = f"in line with your baseline (z={z:+.1f})"
    return summary
