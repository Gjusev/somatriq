"""GET /api/v1/metrics/today — the day's recovery + inputs (spec §76, §181;
ADR 0012/0017). Mounted under the metrics prefix with the other unauthenticated
reads (session auth lands with the web app, spec §122).

Assembles TODAY in the effective timezone (USER_TIMEZONE, DST-correct via
zoneinfo) from the M6 observation families and the M5 daily feature store:

* Sleep — health.sleep_sessions with end_ts inside [local-day start, now]
  (wake-date attribution, ADR 0017). Duplicate reports across devices resolve
  to the most recently received copy; multiple sessions sum durations, and
  efficiency / vendor resting_hr / avg_hrv are duration-weighted means over
  the sessions that report them.
* HRV — timeseries.rr_interval rows INSIDE those sleep windows, fetched in
  ONE range-bounded SQL pass, then each day's windows concatenated in ts
  order into a single somatriq_hrv_rmssd_v1 pass. Concatenation (rather than
  a sample-weighted combination of per-session RMSSDs) is the documented
  choice: RMSSD is a quadratic functional of the sequence, so one pass over
  the full ordered sequence is the defensible deterministic reading; the
  delta-400 filter simply sees one extra pair at each session boundary.
* RHR — today's derived.daily_features row (somatriq_rhr_v1), read-through
  computed via the exact metrics.py path when absent; a null resting_hr is an
  input missing, never imputed.
* Baselines (somatriq_baseline_v1) — trailing 28 local days ENDING YESTERDAY;
  today never sits in its own baseline. resting_hr comes from daily_features
  (read-only — only today's row is read-through computed), RMSSD is
  recomputed per wake-date over that date's sleep windows with the same
  single-pass rule (same one range-bounded SQL), and sleep minutes sum the
  sessions per wake-date. Fewer than RECOVERY_BASELINE_MIN_DAYS values is a
  caveat, never a fake baseline.

RR rows reach Python only inside sleep windows — RMSSD needs the ordered
sequence — bounded to ~8 h at 1 Hz ≈ 29 k rows per night worst case, which is
the accepted cost of exact RMSSD. Session windows are assumed non-overlapping
after the cross-device dedup (vendor sessions do not overlap themselves), so
a row landing in two windows is not expected. Everything is deterministic; no
LLM anywhere in the path (ADR 0009).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Final, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from somatriq_analytics.daily import summarize_day
from somatriq_analytics.hrv import rmssd_stats
from somatriq_analytics.recovery import Baseline, baseline, recovery_v1
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.recovery import (
    RECOVERY_BASELINE_DAYS,
    HrvSummary,
    SleepSummary,
    TodayResponse,
)
from somatriq_db.engine import get_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The frozen M5 compute path: metrics.py is read-only for this slice, so the
# read-through helpers behind /metrics/daily are imported (not duplicated) —
# today's resting_hr can never drift from the daily card's math.
from .metrics import (
    _SECONDS_PER_DAY,
    _coverage_ratio,
    _day_aggregates,
    _day_bounds_utc,
    _expected_cadence,
    _persist_days,
)
from .settings import get_settings

router = APIRouter(prefix="/api/v1/metrics", tags=["today"])

_SESSIONS_SQL: Final[str] = """
SELECT DISTINCT ON (source_record_id, start_ts)
       source_record_id, start_ts, end_ts, efficiency, resting_hr, avg_hrv
FROM health.sleep_sessions
WHERE end_ts >= :first_end AND end_ts <= :last_end
ORDER BY source_record_id, start_ts, received_at DESC, device_id
"""


class _SessionWindow:
    """One deduped sleep session: its window plus the RR it captured."""

    __slots__ = (
        "avg_hrv",
        "efficiency",
        "end_ts",
        "resting_hr",
        "rr",
        "source_record_id",
        "start_ts",
        "wake_date",
    )

    def __init__(
        self,
        source_record_id: str,
        start_ts: datetime,
        end_ts: datetime,
        efficiency: float | None,
        resting_hr: float | None,
        avg_hrv: float | None,
        wake_date: date,
    ) -> None:
        self.source_record_id = source_record_id
        self.start_ts = start_ts
        self.end_ts = end_ts
        self.efficiency = efficiency
        self.resting_hr = resting_hr
        self.avg_hrv = avg_hrv
        self.wake_date = wake_date
        self.rr: list[int] = []

    @property
    def minutes(self) -> float:
        return (self.end_ts - self.start_ts).total_seconds() / 60


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local day."""
    return datetime.now(UTC)


def _duration_weighted(pairs: list[tuple[float, float | None]]) -> float | None:
    """Duration-weighted mean over sessions that report the value, else None."""
    total = 0.0
    weight = 0.0
    for duration, value in pairs:
        if value is not None:
            total += duration * value
            weight += duration
    return round(total / weight, 2) if weight > 0 else None


async def _sleep_window_rr(
    session: AsyncSession,
    windows: list[_SessionWindow],
    range_start: datetime,
    range_end: datetime,
) -> list[tuple[datetime, int]]:
    """RR rows inside any sleep window, one range-bounded pass, ts-ordered.

    The OR-range predicate keeps the scan bounded to the union of windows
    (never 28 days of raw RR). Bound names embed loop indices only — no user
    data ever reaches the SQL text.
    """
    clauses: list[str] = []
    params: dict[str, object] = {"range_start": range_start, "range_end": range_end}
    for i, window in enumerate(windows):
        clauses.append(f"(ts >= :s{i} AND ts <= :e{i})")
        params[f"s{i}"] = window.start_ts
        params[f"e{i}"] = window.end_ts
    sql = (
        "SELECT ts, rr_ms FROM timeseries.rr_interval "
        f"WHERE ts >= :range_start AND ts <= :range_end "
        f"AND ({' OR '.join(clauses)}) "
        "ORDER BY ts"
    )
    result = await session.execute(text(sql), params)
    return [(cast(datetime, row[0]), cast(int, row[1])) for row in result.all()]


def _group_by_date(windows: list[_SessionWindow]) -> dict[date, list[_SessionWindow]]:
    grouped: dict[date, list[_SessionWindow]] = {}
    for window in windows:
        grouped.setdefault(window.wake_date, []).append(window)
    for sessions in grouped.values():
        sessions.sort(key=lambda w: w.start_ts)
    return grouped


def _hrv_for_days(windows: list[_SessionWindow]) -> tuple[dict[date, HrvSummary], list[float]]:
    """Per-wake-date HRV over that date's concatenated sleep-window RR.

    Returns the per-day summaries (for today) and the flat rmssd value list
    (for baselines; days with insufficient RR simply contribute nothing).
    """
    summaries: dict[date, HrvSummary] = {}
    values: list[float] = []
    for wake, sessions in sorted(_group_by_date(windows).items()):
        day_rr = [rr for window in sessions for rr in window.rr]
        summary = rmssd_stats(day_rr, day=wake, session_count=len(sessions))
        if summary is not None:
            summaries[wake] = summary
            if summary.rmssd_ms is not None:
                values.append(summary.rmssd_ms)
    return summaries, values


async def _today_resting_hr(
    session: AsyncSession, tz: ZoneInfo, day: date
) -> tuple[float | None, str | None]:
    """Today's resting_hr + data_quality, read-through computed when absent.

    Reuses the exact /metrics/daily compute (one-query day aggregates +
    same-version upsert) so both surfaces agree by construction. Today is
    by definition an OPEN day: never answer from its cached snapshot —
    always recompute (grill decision 7: emit with marker + silent
    recompute). The cached read only serves CLOSED baseline days.
    """
    expected_per_day = _SECONDS_PER_DAY / await _expected_cadence(session)

    range_start, range_end = _day_bounds_utc(day, tz)
    aggregates = await _day_aggregates(session, tz, range_start, range_end)
    agg = aggregates.get(day)
    summary = summarize_day(
        date=day,
        timezone=tz.key,
        bucket_medians=list(agg.bucket_medians) if agg else [],
        hr_min=agg.hr_min if agg else None,
        hr_mean=agg.hr_mean if agg else None,
        hr_max=agg.hr_max if agg else None,
        sample_count=agg.sample_count if agg else 0,
        coverage_ratio=_coverage_ratio(agg.sample_count if agg else 0, expected_per_day),
    )
    await _persist_days(session, tz.key, [summary])
    return summary.resting_hr, summary.data_quality


async def _baseline_resting_hr(
    session: AsyncSession, first: date, last: date
) -> list[float]:
    """resting_hr values from daily_features over [first, last] (read-only)."""
    result = await session.execute(
        text(
            "SELECT resting_hr FROM derived.daily_features "
            "WHERE feature_set_version = :fsv AND date >= :first AND date <= :last "
            "AND resting_hr IS NOT NULL"
        ),
        {"fsv": FEATURE_SET_VERSION, "first": first, "last": last},
    )
    return [cast(float, row[0]) for row in result.all()]


@router.get("/today", response_model=TodayResponse)
async def read_today(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TodayResponse:
    """Today's sleep, HRV, resting HR and explainable recovery (spec §76).

    Unauthenticated like the other reads. The recovery result is always
    present — with missing_inputs listed and a null score when the day has
    not earned one — because the explainability surface is the point.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    now = _now()
    today = now.astimezone(tz).date()
    day_start, next_day_start = _day_bounds_utc(today, tz)
    baseline_first = today - timedelta(days=RECOVERY_BASELINE_DAYS)
    baseline_start = _day_bounds_utc(baseline_first, tz)[0]

    # Sleep sessions whose wake-date is today (ADR 0017) or inside the
    # trailing baseline window, in one query, cross-device deduped.
    result = await session.execute(
        text(_SESSIONS_SQL), {"first_end": baseline_start, "last_end": now}
    )
    today_sessions: list[_SessionWindow] = []
    baseline_sessions: list[_SessionWindow] = []
    baseline_sleep_minutes: dict[date, float] = {}
    for srid, start_ts, end_ts, efficiency, resting_hr, avg_hrv in result.all():
        window = _SessionWindow(
            cast(str, srid),
            cast(datetime, start_ts),
            cast(datetime, end_ts),
            cast("float | None", efficiency),
            cast("float | None", resting_hr),
            cast("float | None", avg_hrv),
            cast(datetime, end_ts).astimezone(tz).date(),
        )
        if window.wake_date == today:
            today_sessions.append(window)
        elif window.wake_date >= baseline_first:
            baseline_sessions.append(window)
            minutes = baseline_sleep_minutes.get(window.wake_date, 0.0) + window.minutes
            baseline_sleep_minutes[window.wake_date] = minutes
    today_sessions.sort(key=lambda w: w.start_ts)
    baseline_sessions.sort(key=lambda w: w.start_ts)

    # One range-bounded RR pass covers today's and the baseline windows.
    all_windows = today_sessions + baseline_sessions
    if all_windows:
        rr_rows = await _sleep_window_rr(session, all_windows, baseline_start, now)
        for ts, rr_ms in rr_rows:
            for window in all_windows:
                if window.start_ts <= ts <= window.end_ts:
                    window.rr.append(rr_ms)

    # Per-day HRV summaries (today's for the response) and the baseline value
    # list — computed over the BASELINE windows only, so today's own RMSSD can
    # never leak into the baseline it is scored against.
    hrv_summaries, _ = _hrv_for_days(all_windows)
    _, hrv_baseline_values = _hrv_for_days(baseline_sessions)
    hrv_today = hrv_summaries.get(today)

    sleep_summary: SleepSummary | None = None
    today_sleep_minutes: float | None = None
    if today_sessions:
        today_sleep_minutes = round(sum(w.minutes for w in today_sessions), 2)
        sleep_summary = SleepSummary(
            day=today,
            duration_minutes=today_sleep_minutes,
            efficiency=_duration_weighted([(w.minutes, w.efficiency) for w in today_sessions]),
            resting_hr=_duration_weighted([(w.minutes, w.resting_hr) for w in today_sessions]),
            avg_hrv=_duration_weighted([(w.minutes, w.avg_hrv) for w in today_sessions]),
            source_record_ids=sorted(w.source_record_id for w in today_sessions),
        )

    resting_hr, resting_hr_quality = await _today_resting_hr(session, tz, today)

    baseline_last = today - timedelta(days=1)
    hrv_baseline = baseline(hrv_baseline_values)
    rhr_baseline: Baseline | None = baseline(
        await _baseline_resting_hr(session, baseline_first, baseline_last)
    )
    sleep_baseline: Baseline | None = baseline(
        [baseline_sleep_minutes[d] for d in sorted(baseline_sleep_minutes)]
    )

    recovery = recovery_v1(
        day=today,
        inputs={
            "hrv": hrv_today.rmssd_ms if hrv_today is not None else None,
            "rhr": resting_hr,
            "sleep": today_sleep_minutes,
        },
        baselines={
            "hrv": hrv_baseline,
            "rhr": rhr_baseline,
            "sleep": sleep_baseline,
        },
    )

    newest_hr = cast(
        "datetime | None",
        (
            await session.execute(text("SELECT max(ts) FROM timeseries.heart_rate"))
        ).scalar_one_or_none(),
    )
    freshness = (
        round((now - newest_hr).total_seconds() / 60, 2) if newest_hr is not None else None
    )

    caveats: list[str] = []
    day_hours = (next_day_start - day_start).total_seconds() / 3600
    if day_hours != 24.0:
        caveats.append(f"DST transition day: local day spans {day_hours:g} hours")
    for caveat in recovery.caveats:
        if caveat not in caveats:
            caveats.append(caveat)

    return TodayResponse(
        date=today,
        timezone=tz.key,
        recovery=recovery,
        hrv=hrv_today,
        sleep=sleep_summary,
        resting_hr=resting_hr,
        resting_hr_quality=resting_hr_quality,
        data_freshness_minutes=freshness,
        caveats=caveats,
    )
