"""Shared TODAY-PLAN data assembly (Block 1 plan; ADR 0012/0017/0018/0019).

The plan's recovery/sleep inputs come from ``assemble_today`` — the same
shared reading the Today endpoint and the morning brief use, so the plan
can never disagree with the numbers the user already saw (same-numbers-by-
construction). On top it reads: the 28-day sleep history (sessions
canonical, ``total_sleep_min`` fallback for session-less wake dates —
grill P2), the 28-day vendor strain distribution (grill P6), and the
``wake_time`` User Preference (ADR 0019; documented default 07:00).

Windows (ADR 0017 — wake-date attribution everywhere):
- sleep baseline: wake dates [today-28, today-1] (baselines end yesterday,
  matching the recovery convention — today's values are inputs, never
  baseline members);
- sleep debt: the 7 wake dates ending TODAY (last night is the most recent
  measured night and counts, grill P3);
- strain: [today-28, today-1]; recent_load = yesterday's strain.

Results are persisted to ``derived.daily_derived`` with same-version
upsert (latest-state, ADR 0018).
"""

import contextlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from somatriq_contracts.plan import (
    DAY_PLAN_ALGORITHM,
    SLEEP_NEED_ALGORITHM,
    SLEEP_NEED_BASELINE_DAYS,
    SLEEP_NEED_DEBT_WINDOW_DAYS,
    STRAIN_HISTORY_DAYS,
    DayPlanResult,
    SleepNeedResult,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .day_plan import day_plan_v1
from .recovery import Baseline, baseline
from .sleep_need import SleepDebt, sleep_debt, sleep_need_v1
from .today_data import TodayData, assemble_today, day_bounds_utc

DEFAULT_WAKE_TIME = time(7, 0)  # ADR 0019: documented default when unset

# Vendor-daily sleep source vs measured sessions: beyond this many minutes
# the disagreement is surfaced as a caveat, never silently resolved.
SLEEP_SOURCE_DISCREPANCY_MIN = 45.0

_WAKE_PREF_SQL = """
    SELECT value FROM identity.user_preferences WHERE key = 'wake_time'
"""

# Cross-device deduped vendor dailies (same DISTINCT ON discipline as the
# correlation reads) for the two metrics the plan consumes.
_VENDOR_DAILY_SQL = """
    SELECT DISTINCT ON (day, metric) day, metric, value
    FROM health.daily_observations
    WHERE metric IN ('strain', 'total_sleep_min')
      AND day >= :first AND day <= :last
    ORDER BY day, metric, received_at DESC, device_id
"""

_SESSION_MINUTES_SQL = """
    SELECT DISTINCT ON (source_record_id) start_ts, end_ts
    FROM health.sleep_sessions
    WHERE end_ts >= :first_end AND end_ts <= :last_end
    ORDER BY source_record_id, start_ts, received_at DESC, device_id
"""

_DERIVED_UPSERT_SQL = """
    INSERT INTO derived.daily_derived
        (date, algorithm_version, timezone, payload, computed_at)
    VALUES (:day, :algorithm, :tz, CAST(:payload AS jsonb), now())
    ON CONFLICT (date, algorithm_version) DO UPDATE
    SET timezone = EXCLUDED.timezone,
        payload = EXCLUDED.payload,
        computed_at = now()
"""


@dataclass(frozen=True)
class PlanData:
    """Everything the plan holds, shared by the API response and the brief."""

    date: date
    timezone: str
    wake_time: time
    wake_source: Literal["preference", "default"]
    sleep_baseline: Baseline | None
    sleep_debt: SleepDebt
    sleep_need: SleepNeedResult
    plan: DayPlanResult
    coverage_ratio: float = 0.0
    caveats: list[str] = field(default_factory=list)


async def _wake_time(session: AsyncSession) -> tuple[time, Literal["preference", "default"]]:
    """The wake_time preference, or the documented default (ADR 0019).

    The jsonb column may arrive already deserialized (SQLAlchemy asyncpg
    dialect) or as raw JSON text — parse both defensively; any malformed
    value degrades to the default rather than failing the plan.
    """
    raw = (await session.execute(text(_WAKE_PREF_SQL))).scalar_one_or_none()
    if raw is None:
        return DEFAULT_WAKE_TIME, "default"
    value: object = raw
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    if isinstance(value, str):
        # A bare "07:30" is not JSON — keep it as-is when loads() rejects it.
        with contextlib.suppress(json.JSONDecodeError):
            value = json.loads(value)
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError:
        return DEFAULT_WAKE_TIME, "default"
    return parsed, "preference"


async def _vendor_dailies(
    session: AsyncSession, first: date, last: date
) -> dict[tuple[date, str], float]:
    result = await session.execute(text(_VENDOR_DAILY_SQL), {"first": first, "last": last})
    return {(day, metric): float(value) for day, metric, value in result.all()}


async def _session_minutes(
    session: AsyncSession, first_end: datetime, last_end: datetime, tz: ZoneInfo
) -> dict[date, float]:
    """Summed session minutes per wake date with end_ts in [first, last]."""
    result = await session.execute(
        text(_SESSION_MINUTES_SQL), {"first_end": first_end, "last_end": last_end}
    )
    per_date: dict[date, float] = {}
    for start_ts, end_ts in result.all():
        wake = end_ts.astimezone(tz).date()
        minutes = (end_ts - start_ts).total_seconds() / 60
        per_date[wake] = per_date.get(wake, 0.0) + minutes
    return per_date


def _measured_sleep(
    session_minutes: dict[date, float],
    vendor_sleep: dict[date, float],
    days: list[date],
) -> tuple[dict[date, float], int, int]:
    """Per-date measured minutes: sessions canonical, vendor fallback (P2).

    Returns (measured, fallback_days, discrepancy_days) — honest counts for
    the caveats, never silent resolution.
    """
    measured: dict[date, float] = {}
    fallback = 0
    discrepancy = 0
    for day in days:
        sessions = session_minutes.get(day)
        vendor = vendor_sleep.get(day, 0.0) if vendor_sleep.get(day) is not None else None
        if sessions is not None:
            measured[day] = sessions
            if vendor is not None and abs(sessions - vendor) > SLEEP_SOURCE_DISCREPANCY_MIN:
                discrepancy += 1
        elif day in vendor_sleep:
            measured[day] = vendor_sleep[day]
            fallback += 1
    return measured, fallback, discrepancy


async def _persist(
    session: AsyncSession, day: date, tz_key: str, results: dict[str, object]
) -> None:
    for algorithm, result in results.items():
        payload = getattr(result, "model_dump", lambda **_: None)(mode="json")
        await session.execute(
            text(_DERIVED_UPSERT_SQL),
            {"day": day, "algorithm": algorithm, "tz": tz_key, "payload": json.dumps(payload)},
        )


async def assemble_plan(
    session: AsyncSession,
    *,
    tz: ZoneInfo,
    now: datetime,
    today_data: TodayData | None = None,
) -> PlanData:
    """Assemble the TODAY PLAN in the effective timezone (single shared read).

    ``today_data`` may carry a pre-assembled TODAY (the brief/scheduler path
    already holds it) — same numbers by construction, one DB pass saved.
    """
    today = now.astimezone(tz).date()

    # The plan's recovery/sleep inputs are TODAY's shared reading.
    data = today_data if today_data is not None else await assemble_today(session, tz=tz, now=now)

    baseline_first = today - timedelta(days=SLEEP_NEED_BASELINE_DAYS)
    vendor = await _vendor_dailies(session, baseline_first, today)
    vendor_sleep = {d: v for (d, m), v in vendor.items() if m == "total_sleep_min"}
    strain_by_day = {d: v for (d, m), v in vendor.items() if m == "strain"}

    # Upper bound is NOW (not today's midnight): last night's session ends
    # this morning and must count (assemble_today uses the same bound).
    session_min = await _session_minutes(session, day_bounds_utc(baseline_first, tz)[0], now, tz)

    baseline_days_list = [today - timedelta(days=i) for i in range(SLEEP_NEED_BASELINE_DAYS, 0, -1)]
    measured, fallback_days, discrepancy_days = _measured_sleep(
        session_min, vendor_sleep, baseline_days_list
    )
    baseline_values = [measured[d] for d in baseline_days_list if d in measured]
    sleep_baseline: Baseline | None = baseline(baseline_values)

    debt_days = [today - timedelta(days=i) for i in range(SLEEP_NEED_DEBT_WINDOW_DAYS - 1, -1, -1)]
    debt_measured, _, _ = _measured_sleep(session_min, vendor_sleep, debt_days)
    debt = sleep_debt(
        [(d, debt_measured.get(d)) for d in debt_days],
        sleep_baseline[0] if sleep_baseline is not None else 0.0,
    )

    strain_history = [
        strain_by_day[d]
        for d in (today - timedelta(days=i) for i in range(STRAIN_HISTORY_DAYS, 0, -1))
        if d in strain_by_day
    ]
    recent_load = strain_by_day.get(today - timedelta(days=1))

    wake_time, wake_source = await _wake_time(session)

    need = sleep_need_v1(
        today,
        baseline_sleep_min=sleep_baseline[0] if sleep_baseline is not None else None,
        sleep_debt_min=debt.debt_min,
        recent_load=recent_load,
        recovery=data.recovery.score if data.recovery is not None else None,
    )
    plan = day_plan_v1(
        today,
        recovery_score=data.recovery.score if data.recovery is not None else None,
        sleep_debt_min=debt.debt_min,
        sleep_need_minutes=need.minutes,
        strain_history=strain_history,
        wake_time=wake_time,
        baseline_days=len(baseline_values),
    )

    caveats: list[str] = []
    if fallback_days:
        caveats.append(
            f"sleep source: vendor daily fallback on {fallback_days} of "
            f"{len(baseline_days_list)} baseline days"
        )
    if discrepancy_days:
        caveats.append(
            f"sessions vs vendor daily sleep disagree >{SLEEP_SOURCE_DISCREPANCY_MIN:.0f} min "
            f"on {discrepancy_days} day(s)"
        )
    for caveat in data.caveats + plan.caveats + need.caveats:
        if caveat not in caveats:
            caveats.append(caveat)

    await _persist(
        session,
        today,
        tz.key,
        {SLEEP_NEED_ALGORITHM: need, DAY_PLAN_ALGORITHM: plan},
    )

    return PlanData(
        date=today,
        timezone=tz.key,
        wake_time=wake_time,
        wake_source=wake_source,
        sleep_baseline=sleep_baseline,
        sleep_debt=debt,
        sleep_need=need,
        plan=plan,
        coverage_ratio=data.coverage_ratio,
        caveats=caveats,
    )
