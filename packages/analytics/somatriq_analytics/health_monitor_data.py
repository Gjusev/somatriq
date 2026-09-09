"""Health Monitor data assembly (Block 3; CONTEXT "Vital"; ADR 0009/0012).

Reads each vital's report window and the frozen trailing baseline window
(HEALTH_MONITOR_BASELINE_DAYS before the period) with the SAME honest
per-day series the Explore surface reads — absent days stay absent, so
coverage and n are the truth. HRV prefers our computed RMSSD
(somatriq_hrv_rmssd_v1 over sleep-window RR) and falls back to the
vendor's avg_hrv with the source recorded on the row.
"""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_BASELINE_DAYS,
    HEALTH_MONITOR_DISCLAIMER,
    VitalSummary,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .correlation_data import rmssd_daily_series
from .explore_data import explore_series
from .health_monitor import health_monitor_v1

# Frozen vital set (order is the wire order): name, label, metric family.
_VITALS: tuple[tuple[str, str, str], ...] = (
    ("hrv", "HRV", "avg_hrv"),
    ("resting_hr", "Resting HR", "resting_hr"),
    ("resp_rate_bpm", "Respiratory rate", "resp_rate_bpm"),
    ("spo2_pct", "SpO2", "spo2_pct"),
    ("skin_temp_dev_c", "Skin temp deviation", "skin_temp_dev_c"),
)


async def _window_values(
    session: AsyncSession, metric: str, first: date, last: date, tz: ZoneInfo
) -> list[float]:
    series = await explore_series(session, metrics=[metric], first=first, last=last, tz=tz)
    return [point.value for point in series[0].days]


async def assemble_health_monitor(
    session: AsyncSession, *, tz: ZoneInfo, days: int, now: datetime
) -> dict[str, Any]:
    """Per-vital period-vs-baseline summaries + caveats + disclaimer."""
    today = now.astimezone(tz).date()
    period_first = today - timedelta(days=days - 1)
    baseline_last = period_first - timedelta(days=1)
    baseline_first = period_first - timedelta(days=HEALTH_MONITOR_BASELINE_DAYS)

    vitals: list[VitalSummary] = []
    caveats: list[str] = []

    for vital, label, metric in _VITALS:
        period_values = await _window_values(session, metric, period_first, today, tz)
        baseline_values = await _window_values(session, metric, baseline_first, baseline_last, tz)
        source = "vendor daily"

        if vital == "hrv":
            # Our own RMSSD first (sleep-window RR), vendor avg_hrv fallback.
            combined = await rmssd_daily_series(
                session, days=days + HEALTH_MONITOR_BASELINE_DAYS, tz=tz
            )
            computed_period = [value for day, value in combined if day >= period_first]
            computed_baseline = [
                value for day, value in combined if baseline_first <= day <= baseline_last
            ]
            if computed_period:
                period_values = computed_period
                baseline_values = computed_baseline
                source = "computed somatriq_hrv_rmssd_v1"
            else:
                caveats.append("hrv: vendor avg_hrv — no computed RMSSD in the period")

        vitals.append(
            health_monitor_v1(
                vital=vital,
                label=label,
                source=source,
                period_values=period_values,
                baseline_values=baseline_values,
                expected_days=days,
            )
        )

    return {
        "days": days,
        "timezone": tz.key,
        "vitals": vitals,
        "caveats": caveats,
        "disclaimer": HEALTH_MONITOR_DISCLAIMER,
    }
