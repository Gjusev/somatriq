"""Shared TODAY data assembly (spec §76, §102, §114; ADR 0009/0012/0017).

Extracted from the api's today endpoint (M6) so the HTTP surface and the
Telegram morning brief read the SAME numbers by construction. The api
endpoint maps TodayData onto TodayResponse; the brief builder renders it as
text. Per the M7 slice contract, the read-through resting-HR day compute is
REPLICATED minimally here (services/api helpers are private to that package
and the api's tests are frozen) — the SQL below is kept character-identical
to services/api/somatriq_api/metrics.py so drift is diffable; a future slice
may hoist those helpers into this package properly.

Semantics are unchanged from the endpoint this was extracted from:

* Sleep — health.sleep_sessions with end_ts inside [local-day start, now]
  (wake-date attribution, ADR 0017), cross-device deduped, durations summed,
  efficiency / vendor resting_hr / avg_hrv duration-weighted.
* HRV — timeseries.rr_interval rows inside the sleep windows, one
  range-bounded SQL pass, per-wake-date concatenated single
  somatriq_hrv_rmssd_v1 pass (the documented deterministic reading).
* RHR — today's derived.daily_features row (somatriq_rhr_v1), read-through
  computed when absent; null resting_hr is an input missing, never imputed.
* Baselines (somatriq_baseline_v1) — trailing 28 local days ENDING
  YESTERDAY; today never sits in its own baseline. Fewer than
  RECOVERY_BASELINE_MIN_DAYS values is a caveat, never a fake baseline.
* Journal (M7) — the local day's health.journal_events: caffeine count +
  last ts, journal note count (spec §103).

Everything is deterministic; no LLM anywhere in the path (ADR 0009).
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final, cast
from zoneinfo import ZoneInfo

from somatriq_contracts.catalog import HEART_RATE_CADENCE_SECONDS
from somatriq_contracts.daily import FEATURE_SET_VERSION, RHR_BUCKET_MINUTES
from somatriq_contracts.recovery import (
    RECOVERY_BASELINE_DAYS,
    HrvSummary,
    RecoveryResult,
    SleepSummary,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_analytics.daily import summarize_day
from somatriq_analytics.hrv import rmssd_stats
from somatriq_analytics.recovery import Baseline, baseline, recovery_v1

_SECONDS_PER_DAY: Final = 86400

_SESSIONS_SQL: Final[str] = """
SELECT DISTINCT ON (source_record_id, start_ts)
       source_record_id, start_ts, end_ts, efficiency, resting_hr, avg_hrv
FROM health.sleep_sessions
WHERE end_ts >= :first_end AND end_ts <= :last_end
ORDER BY source_record_id, start_ts, received_at DESC, device_id
"""

# Minimal single-day replica of the api's day-compute (see module docstring):
# local-day attribution in SQL, 5-minute bucket medians for RHR v1, and the
# day's min/avg/max/count — kept identical to metrics.py's _DAY_COMPUTE_SQL.
_DAY_COMPUTE_SQL: Final[str] = """
WITH day_samples AS (
    SELECT ts, bpm, (ts AT TIME ZONE :tz)::date AS local_date
    FROM timeseries.heart_rate
    WHERE ts >= :range_start AND ts < :range_end
),
buckets AS (
    SELECT local_date,
           time_bucket(:bucket_interval, ts) AS bucket_ts,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY bpm) AS median_bpm
    FROM day_samples
    GROUP BY local_date, time_bucket(:bucket_interval, ts)
),
bucket_days AS (
    SELECT local_date, array_agg(median_bpm ORDER BY median_bpm) AS bucket_medians
    FROM buckets
    GROUP BY local_date
),
raw_days AS (
    SELECT local_date,
           min(bpm) AS hr_min,
           avg(bpm) AS hr_mean,
           max(bpm) AS hr_max,
           count(*) AS sample_count
    FROM day_samples
    GROUP BY local_date
)
SELECT b.local_date, b.bucket_medians, r.hr_min, r.hr_mean, r.hr_max, r.sample_count
FROM bucket_days b
JOIN raw_days r USING (local_date)
ORDER BY 1
"""

# Same-version upsert (ADR 0012): refreshing inside a pinned
# feature_set_version never silently rewrites history. Identical to
# metrics.py's _DAILY_UPSERT_SQL.
_DAILY_UPSERT_SQL: Final[str] = """
INSERT INTO derived.daily_features (
    date, feature_set_version, timezone, resting_hr, hr_min, hr_mean, hr_max,
    sample_count, coverage_ratio, data_quality, algorithm_version
) VALUES (
    :date, :feature_set_version, :timezone, :resting_hr, :hr_min, :hr_mean, :hr_max,
    :sample_count, :coverage_ratio, :data_quality, :algorithm_version
)
ON CONFLICT (date, feature_set_version) DO UPDATE SET
    timezone = EXCLUDED.timezone,
    resting_hr = EXCLUDED.resting_hr,
    hr_min = EXCLUDED.hr_min,
    hr_mean = EXCLUDED.hr_mean,
    hr_max = EXCLUDED.hr_max,
    sample_count = EXCLUDED.sample_count,
    coverage_ratio = EXCLUDED.coverage_ratio,
    data_quality = EXCLUDED.data_quality,
    algorithm_version = EXCLUDED.algorithm_version,
    computed_at = now()
"""

_JOURNAL_SQL: Final[str] = """
SELECT kind, count(*) AS n, max(ts) AS last_ts
FROM health.journal_events
WHERE ts >= :day_start AND ts <= :now
  AND kind IN ('caffeine', 'journal', 'note')
GROUP BY kind
"""


@dataclass(frozen=True)
class JournalToday:
    """The local day's quick-log events (spec §103) for /status and the brief."""

    caffeine_count: int = 0
    caffeine_last_ts: datetime | None = None
    journal_notes: int = 0


@dataclass(frozen=True)
class TodayData:
    """Everything TODAY holds, shared by the api response and the brief."""

    date: date
    timezone: str  # the day's effective timezone key (ADR 0017)
    recovery: RecoveryResult
    hrv: HrvSummary | None = None
    sleep: SleepSummary | None = None
    resting_hr: float | None = None
    resting_hr_quality: str | None = None
    coverage_ratio: float = 0.0  # today's heart-sample coverage (spec §99)
    data_freshness_minutes: float | None = None
    caveats: list[str] = field(default_factory=list)
    journal: JournalToday = field(default_factory=JournalToday)


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


def _duration_weighted(pairs: list[tuple[float, float | None]]) -> float | None:
    """Duration-weighted mean over sessions that report the value, else None."""
    total = 0.0
    weight = 0.0
    for duration, value in pairs:
        if value is not None:
            total += duration * value
            weight += duration
    return round(total / weight, 2) if weight > 0 else None


def day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) in UTC — DST-correct via zoneinfo.

    A fall-back day spans 25 UTC hours, a spring-forward day 23; both fall
    out of the astimezone conversion (ADR 0017 golden-fixture semantics).
    """
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end_local = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _coverage_ratio(sample_count: int, expected_per_day: float) -> float:
    """sample_count / expected samples per day, capped at 1.0 (spec §99)."""
    if expected_per_day <= 0:
        return 1.0 if sample_count else 0.0
    return min(sample_count / expected_per_day, 1.0)


async def _expected_cadence(session: AsyncSession) -> int:
    """heart_rate cadence from system.metrics; the contract constant as fallback."""
    result = await session.execute(
        text("SELECT expected_cadence_seconds FROM system.metrics WHERE name = 'heart_rate'")
    )
    cadence = result.scalar_one_or_none()
    return cadence if cadence and cadence > 0 else HEART_RATE_CADENCE_SECONDS


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
) -> tuple[float | None, str | None, float]:
    """Today's resting_hr + data_quality + coverage, read-through computed.

    Reuses the exact /metrics/daily compute (one-query day aggregates +
    same-version upsert) so every surface agrees by construction. Today is
    by definition an OPEN day: never answer from its cached snapshot —
    always recompute (grill decision: emit with marker + silent recompute).
    The cached read only serves CLOSED baseline days.
    """
    expected_per_day = _SECONDS_PER_DAY / await _expected_cadence(session)

    range_start, range_end = day_bounds_utc(day, tz)
    result = await session.execute(
        text(_DAY_COMPUTE_SQL),
        {
            "tz": tz.key,
            "bucket_interval": timedelta(minutes=RHR_BUCKET_MINUTES),
            "range_start": range_start,
            "range_end": range_end,
        },
    )
    rows = result.all()
    bucket_medians: list[float] = (
        list(cast("list[float]", rows[0][1])) if rows else []
    )
    summary = summarize_day(
        date=day,
        timezone=tz.key,
        bucket_medians=bucket_medians,
        hr_min=cast("float | None", rows[0][2]) if rows else None,
        hr_mean=cast("float | None", rows[0][3]) if rows else None,
        hr_max=cast("float | None", rows[0][4]) if rows else None,
        sample_count=cast(int, rows[0][5]) if rows else 0,
        coverage_ratio=_coverage_ratio(
            cast(int, rows[0][5]) if rows else 0, expected_per_day
        ),
    )
    await session.execute(
        text(_DAILY_UPSERT_SQL),
        {
            "date": summary.date,
            "feature_set_version": FEATURE_SET_VERSION,
            "timezone": summary.timezone,
            "resting_hr": summary.resting_hr,
            "hr_min": summary.hr_min,
            "hr_mean": summary.hr_mean,
            "hr_max": summary.hr_max,
            "sample_count": summary.sample_count,
            "coverage_ratio": summary.coverage_ratio,
            "data_quality": summary.data_quality,
            "algorithm_version": summary.algorithm_version,
        },
    )
    await session.commit()
    return summary.resting_hr, summary.data_quality, summary.coverage_ratio


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


async def _journal_today(
    session: AsyncSession, day_start: datetime, now: datetime
) -> JournalToday:
    """The local day's caffeine events and journal notes (spec §103)."""
    result = await session.execute(
        text(_JOURNAL_SQL), {"day_start": day_start, "now": now}
    )
    journal = JournalToday()
    for kind, count, last_ts in result.all():
        if kind == "caffeine":
            journal = JournalToday(
                caffeine_count=cast(int, count),
                caffeine_last_ts=cast("datetime | None", last_ts),
                journal_notes=journal.journal_notes,
            )
        else:  # journal | note
            journal = JournalToday(
                caffeine_count=journal.caffeine_count,
                caffeine_last_ts=journal.caffeine_last_ts,
                journal_notes=journal.journal_notes + cast(int, count),
            )
    return journal


async def assemble_today(
    session: AsyncSession, *, tz: ZoneInfo, now: datetime
) -> TodayData:
    """Assemble TODAY in the effective timezone — the single shared reading.

    Identical semantics to the M6 GET /metrics/today assembly this was
    extracted from (its integration tests are the wire-behavior contract),
    plus the day's journal events (M7).
    """
    today = now.astimezone(tz).date()
    day_start, next_day_start = day_bounds_utc(today, tz)
    baseline_first = today - timedelta(days=RECOVERY_BASELINE_DAYS)
    baseline_start = day_bounds_utc(baseline_first, tz)[0]

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

    resting_hr, resting_hr_quality, coverage_ratio = await _today_resting_hr(
        session, tz, today
    )

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

    journal = await _journal_today(session, day_start, now)

    return TodayData(
        date=today,
        timezone=tz.key,
        recovery=recovery,
        hrv=hrv_today,
        sleep=sleep_summary,
        resting_hr=resting_hr,
        resting_hr_quality=resting_hr_quality,
        coverage_ratio=coverage_ratio,
        data_freshness_minutes=freshness,
        caveats=caveats,
        journal=journal,
    )
