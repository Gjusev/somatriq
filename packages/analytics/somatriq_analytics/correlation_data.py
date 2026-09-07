"""Daily metric-pair loading for the correlation engine (spec §80-82; M10
§203).

One name → one dated daily series, unified across the three sources the
product already keeps:

* our computed metrics — ``derived.daily_features`` under the pinned
  ``FEATURE_SET_VERSION`` (resting_hr, hr_min, hr_mean, hr_max,
  coverage_ratio);
* vendor observations — ``health.daily_observations`` catalog-governed by
  the frozen VENDOR_DAILY_METRICS contract (latest received value wins per
  (day, metric), mirroring the observations read);
* derived journal counts — ``caffeine_count`` per local day from
  ``health.journal_events`` kind='caffeine' (spec §103).

Name precedence is explicit: ``resting_hr`` ALWAYS resolves to our
``somatriq_rhr_v1`` output, shadowing the vendor's parallel restingHr
observation — the same "our algorithm over the vendor's copy" stance as the
HRV pipeline. Exposing the vendor value under its own name is a later
slice's decision, not a silent fallback here.

M12 adds two BUILDERS (not catalog names — MATRIX_METRICS stays frozen):
the daily muscular-load series from health.training_sessions (spec §78-80)
and the sleep-window RMSSD per wake-date, both consumed only by the §80
personal-response surface.

Everything below is deterministic SQL + Python (ADR 0009); the coefficient
math lives in :mod:`somatriq_analytics.correlations` and is fed the
aligned float lists this module returns.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Final, cast
from zoneinfo import ZoneInfo

from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.observations import VENDOR_DAILY_METRICS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_analytics.correlations import join_lagged
from somatriq_analytics.hrv import rmssd_stats
from somatriq_analytics.strength import StrengthSet, session_summary

# Our computed daily metrics: metric name → derived.daily_features column.
# Fixed whitelist — column names never reach SQL from user input.
COMPUTED_DAILY_METRICS: Final[dict[str, str]] = {
    "resting_hr": "resting_hr",
    "hr_min": "hr_min",
    "hr_mean": "hr_mean",
    "hr_max": "hr_max",
    "coverage_ratio": "coverage_ratio",
}

# Vendor names we deliberately shadow (our computed metric owns the name).
_SHADOWED_VENDOR_METRICS: Final[frozenset[str]] = frozenset(
    {"resting_hr"} & set(VENDOR_DAILY_METRICS)
)

# Derived per-day counts from the journal (spec §103 quick-log events).
JOURNAL_COUNT_METRICS: Final[dict[str, str]] = {"caffeine_count": "caffeine"}


class UnknownMetricError(ValueError):
    """The requested metric name is not in the correlation catalog."""

    def __init__(self, metric: str) -> None:
        known = sorted(
            set(COMPUTED_DAILY_METRICS)
            | (set(VENDOR_DAILY_METRICS) - _SHADOWED_VENDOR_METRICS)
            | set(JOURNAL_COUNT_METRICS)
        )
        super().__init__(f"unknown metric {metric!r}; catalog: {known}")
        self.metric = metric


def is_known_metric(metric: str) -> bool:
    return (
        metric in COMPUTED_DAILY_METRICS
        or metric in JOURNAL_COUNT_METRICS
        or (metric in VENDOR_DAILY_METRICS and metric not in _SHADOWED_VENDOR_METRICS)
    )


# The curated matrix catalog (spec §203): our 4 physiological computed
# metrics (coverage_ratio stays pair-queryable but is a data-quality
# measure, not a physiology variable), a curated vendor set, and the
# caffeine count — 12 distinct names, 66 pairs, order fixed for
# deterministic wire output.
MATRIX_METRICS: Final[tuple[str, ...]] = (
    "resting_hr",
    "hr_mean",
    "hr_min",
    "hr_max",
    "avg_hrv",
    "recovery",
    "strain",
    "total_sleep_min",
    "spo2_pct",
    "skin_temp_dev_c",
    "resp_rate_bpm",
    "caffeine_count",
)


async def daily_metric_series(
    session: AsyncSession, metric: str, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    """One metric's (local day, value) pairs over the last ``days`` days.

    Values are absent-day-honest: only days that actually have the value
    appear — no filler zeros, no interpolation (spec §158).
    """
    if metric in COMPUTED_DAILY_METRICS:
        return await _computed_series(session, metric, days, tz)
    if metric in JOURNAL_COUNT_METRICS:
        return await _journal_count_series(session, metric, days, tz)
    if metric in VENDOR_DAILY_METRICS and metric not in _SHADOWED_VENDOR_METRICS:
        return await _vendor_series(session, metric, days, tz)
    raise UnknownMetricError(metric)


async def metric_pair(
    session: AsyncSession,
    metric_a: str,
    metric_b: str,
    days: int,
    tz: ZoneInfo,
) -> tuple[int, list[float], list[float]]:
    """Same-day inner join of two metrics: (shared n, xs, ys) — unaligned
    days drop out rather than pair by index."""
    series_a = await daily_metric_series(session, metric_a, days, tz)
    series_b = await daily_metric_series(session, metric_b, days, tz)
    xs, ys = join_lagged(series_a, series_b, 0)
    return len(xs), xs, ys


async def lagged_pair(
    session: AsyncSession,
    metric_a: str,
    metric_b: str,
    days: int,
    tz: ZoneInfo,
    lag_days: int,
) -> tuple[int, list[float], list[float]]:
    """Lagged join: a[t] paired with b[t + lag_days] (spec §80)."""
    series_a = await daily_metric_series(session, metric_a, days, tz)
    series_b = await daily_metric_series(session, metric_b, days, tz)
    xs, ys = join_lagged(series_a, series_b, lag_days)
    return len(xs), xs, ys


async def matrix_series(
    session: AsyncSession, metrics: tuple[str, ...], days: int, tz: ZoneInfo
) -> dict[str, list[tuple[date, float]]]:
    """Load every matrix metric's series once — pairs then join in Python."""
    return {metric: await daily_metric_series(session, metric, days, tz) for metric in metrics}


def _first_day(days: int, tz: ZoneInfo) -> date:
    """Inclusive first local day of the window ending today."""
    today = datetime.now(UTC).astimezone(tz).date()
    return today - timedelta(days=days - 1)


async def _computed_series(
    session: AsyncSession, metric: str, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    column = COMPUTED_DAILY_METRICS[metric]
    result = await session.execute(
        text(
            f"SELECT date, {column} AS value "  # noqa: S608 — fixed whitelist
            "FROM derived.daily_features "
            "WHERE feature_set_version = :fsv AND date >= :first "
            f"AND {column} IS NOT NULL "  # noqa: S608 — fixed whitelist
            "ORDER BY date"
        ),
        {"fsv": FEATURE_SET_VERSION, "first": _first_day(days, tz)},
    )
    return [(day, float(value)) for day, value in result.all()]


async def _vendor_series(
    session: AsyncSession, metric: str, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    result = await session.execute(
        text(
            "SELECT DISTINCT ON (day) day, value "
            "FROM health.daily_observations "
            "WHERE metric = :metric AND day >= :first "
            "ORDER BY day, received_at DESC, device_id"
        ),
        {"metric": metric, "first": _first_day(days, tz)},
    )
    return [(day, float(value)) for day, value in result.all()]


async def _journal_count_series(
    session: AsyncSession, metric: str, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    kind = JOURNAL_COUNT_METRICS[metric]
    first = _first_day(days, tz)
    # Local-day bucketing in SQL (ADR 0017) — the tz key arrives as a bound
    # parameter, and the count is a float because correlation math is float.
    result = await session.execute(
        text(
            "SELECT (ts AT TIME ZONE :tz)::date AS day, count(*)::float AS value "
            "FROM health.journal_events "
            "WHERE kind = :kind AND ts >= :first_start "
            "GROUP BY 1 ORDER BY 1"
        ),
        {
            "tz": tz.key,
            "kind": kind,
            "first_start": datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC),
        },
    )
    return [(day, float(value)) for day, value in result.all()]


# ── M12 muscular-load daily series (spec §78-80, §205) ────────────────────
# Additive builders for the §80 personal-response read: daily tonnage /
# hard-set counts from health.training_sessions × training_sets, and the
# sleep-window RMSSD per wake-date (our own HRV, somatriq_hrv_rmssd_v1 —
# the same deterministic reading the TODAY assembler performs). These are
# NOT added to the frozen MATRIX_METRICS catalog; the §80 surface is the
# only consumer today.

TRAINING_LOAD_METRICS: Final[tuple[str, ...]] = ("training_tonnage", "training_hard_sets")

# Recovery inputs paired against the load features in the §80 read.
RESPONSE_RECOVERY_METRICS: Final[tuple[str, ...]] = ("resting_hr", "rmssd")


async def training_load_series(
    session: AsyncSession, metric: str, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    """One muscular-load feature per local day: tonnage (kg) or hard sets.

    Days are session-local (ADR 0017) and absent-day-honest: a day without
    training simply does not appear — the §80 lagged join then correlates
    training days' load with next-day recovery, never zero-filled rest days
    (spec §158). Hard sets are counted per SESSION (the hard-set fallback
    is session-scoped) and summed into the day.
    """
    if metric not in TRAINING_LOAD_METRICS:
        raise ValueError(f"unknown training-load metric {metric!r}")
    first = _first_day(days, tz)
    result = await session.execute(
        text(
            "SELECT s.id, s.ts, t.exercise, t.weight_kg, t.reps, t.rir, t.rpe "
            "FROM health.training_sessions s "
            "JOIN health.training_sets t ON t.session_id = s.id "
            "WHERE s.ts >= :first_start "
            "ORDER BY s.ts, t.exercise, t.set_index"
        ),
        {"first_start": datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC)},
    )
    sessions: dict[uuid.UUID, tuple[date, list[StrengthSet]]] = {}
    for row_id, row_ts, exercise, weight_kg, reps, rir, rpe in result.all():
        day = cast("datetime", row_ts).astimezone(tz).date()
        _, sets = sessions.setdefault(row_id, (day, []))
        sets.append(
            StrengthSet(
                exercise=str(exercise),
                weight_kg=cast("float | None", weight_kg),
                reps=int(reps),
                rir=cast("int | None", rir),
                rpe=cast("float | None", rpe),
            )
        )
    by_day: dict[date, float] = {}
    for day, sets in sessions.values():
        summary = session_summary(sets)
        value = summary.tonnage_kg if metric == "training_tonnage" else float(summary.hard_sets)
        by_day[day] = by_day.get(day, 0.0) + value
    return [(day, by_day[day]) for day in sorted(by_day)]


async def rmssd_daily_series(
    session: AsyncSession, days: int, tz: ZoneInfo
) -> list[tuple[date, float]]:
    """Sleep-window RMSSD (ms) per wake-date over the last ``days`` days.

    Mirrors the TODAY assembler's deterministic reading: deduped sleep
    sessions (latest received wins per source identity), one range-bounded
    RR pass over their windows, then one somatriq_hrv_rmssd_v1 pass per
    wake-date over that date's concatenated RR. Dates whose RR is
    insufficient do not appear — never zeros.
    """
    first = _first_day(days, tz)
    first_end = datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC)
    result = await session.execute(
        text(
            "SELECT DISTINCT ON (source_record_id, start_ts) "
            "       source_record_id, start_ts, end_ts "
            "FROM health.sleep_sessions "
            "WHERE end_ts >= :first_end AND end_ts <= now() "
            "ORDER BY source_record_id, start_ts, received_at DESC, device_id"
        ),
        {"first_end": first_end},
    )
    windows = [
        (
            cast("datetime", row[1]),
            cast("datetime", row[2]),
            cast("datetime", row[2]).astimezone(tz).date(),
        )
        for row in result.all()
    ]
    if not windows:
        return []

    clauses: list[str] = []
    params: dict[str, object] = {
        "range_start": min(w[0] for w in windows),
        "range_end": max(w[1] for w in windows),
    }
    for i, (start, end, _) in enumerate(windows):
        clauses.append(f"(ts >= :s{i} AND ts <= :e{i})")
        params[f"s{i}"] = start
        params[f"e{i}"] = end
    rr_result = await session.execute(
        text(
            "SELECT ts, rr_ms FROM timeseries.rr_interval "
            "WHERE ts >= :range_start AND ts <= :range_end "
            f"AND ({' OR '.join(clauses)}) "
            "ORDER BY ts"
        ),
        params,
    )
    rr_by_date: dict[date, list[int]] = {}
    for row_ts, rr_ms in rr_result.all():
        for start, end, wake in windows:
            if start <= cast("datetime", row_ts) <= end:
                rr_by_date.setdefault(wake, []).append(int(rr_ms))
                break

    series: list[tuple[date, float]] = []
    for wake in sorted(rr_by_date):
        summary = rmssd_stats(rr_by_date[wake], day=wake)
        if summary is not None and summary.rmssd_ms is not None:
            series.append((wake, summary.rmssd_ms))
    return series
