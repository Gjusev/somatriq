"""Deterministic read-only health tools for the MCP server (spec §94-99, ADR 0009/0010).

Every tool:

- is read-only (spec §97: default connection scope is ``health.read`` only);
- computes deterministically in Python/SQL — never LLM math (ADR 0009);
- queries PostgreSQL directly through somatriq_db (spec §95 stack), never
  over HTTP to the api;
- answers with the §99 envelope ``{data, coverage, sources, quality,
  caveats, generated_at}`` so AI interpretation stays grounded;
- reports data-quality honestly: insufficient input yields an explicit
  ``insufficient data`` result, never zeros (spec §158/§221).

Parallel-workstream guards: the sleep model (migration 0006) may or may not
be present at merge time — the tool degrades to an empty result plus a
caveat, and never raises a 500 (spec §158).
"""

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.observations import VENDOR_DAILY_METRICS
from somatriq_db.engine import get_session_factory
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from . import queries
from .errors import tool_error, validation_error
from .settings import get_settings

# The sleep model lands with the parallel M6 workstream (migration 0006):
# code against the model name, degrade gracefully on any import order.
try:  # pragma: no cover - exercised by whichever merge order misses it
    from somatriq_db.models import SleepSession
except ImportError:  # pragma: no cover
    SleepSession = None  # type: ignore[assignment,misc]

BASELINE_ALGORITHM: Final = "somatriq_baseline_v1"
BASELINE_WINDOW_DAYS: Final = 28
MIN_BASELINE_DAYS: Final = 7
INSUFFICIENT_CAVEAT: Final = (
    f"insufficient data: a personal baseline needs at least {MIN_BASELINE_DAYS} days "
    f"of values inside the trailing {BASELINE_WINDOW_DAYS}-day window"
)

# metric → (source table, column/metric key). "resting_hr" exists in both
# worlds; ours (derived.daily_features, computed deterministically per ADR
# 0009) takes precedence over the vendor observation of the same name — the
# union below puts our features last so they win the duplicate key. The
# resolved source is reported in every baseline response, so the distinction
# is never ambiguous.
_DAILY_FEATURE_METRICS: Final[dict[str, str]] = {
    "resting_hr": "resting_hr",
    "hr_min": "hr_min",
    "hr_mean": "hr_mean",
    "hr_max": "hr_max",
}
BASELINE_METRICS: Final[dict[str, tuple[str, str]]] = {
    k: ("health.daily_observations", k) for k in VENDOR_DAILY_METRICS
} | {k: ("derived.daily_features", v) for k, v in _DAILY_FEATURE_METRICS.items()}


def _envelope(
    *,
    data: dict[str, Any],
    coverage: dict[str, Any],
    sources: list[str],
    quality: float,
    caveats: list[str],
) -> dict[str, Any]:
    """Assemble the §99 response contract."""
    return {
        "data": data,
        "coverage": coverage,
        "sources": sources,
        "quality": round(quality, 3),
        "caveats": caveats,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().user_timezone)


@tool_error
async def get_heart_rate_summary(last_hours: int = 24, bucket: str = "none") -> dict[str, Any]:
    """Heart-rate series for the last N hours, mirroring GET /api/v1/metrics/heart_rate.

    bucket: "none" (raw, capped at 5000 points), "1m", "5m" or "1h"
    (database-side time_bucket averages). The response carries coverage
    against the expected sampling cadence and truncation/no-data caveats.
    """
    if not 1 <= last_hours <= 24 * 400:
        raise validation_error("last_hours must be between 1 and 9600")
    now = datetime.now(UTC)
    start = now - timedelta(hours=last_hours)
    window_seconds = (now - start).total_seconds()

    async with get_session_factory()() as session:
        if bucket == "none":
            points, caveats = await queries.raw_points(session, start, now)
            expected = window_seconds / await queries.expected_cadence(session)
        elif bucket in queries.BUCKET_INTERVALS:
            points, caveats = await queries.bucketed_points(
                session, start, now, queries.BUCKET_INTERVALS[bucket]
            )
            expected = window_seconds / queries.BUCKET_SECONDS[bucket]
        else:
            raise validation_error("bucket must be one of: none, 1m, 5m, 1h")

    count = len(points)
    if count == 0:
        caveats.append(queries.NO_DATA_CAVEAT)
    ratio = _coverage(count, expected)
    return _envelope(
        data={"metric": "heart_rate", "unit": "bpm", "points": points, "count": count},
        coverage={
            "ratio": ratio,
            "observed_points": count,
            "expected_points": round(expected, 1),
        },
        sources=["timeseries.heart_rate"],
        # For a series read, quality IS the share of expected samples present.
        quality=ratio,
        caveats=caveats,
    )


def _coverage(observed: int, expected: float) -> float:
    """Proportion of expected data present, clamped to [0, 1] (spec §99)."""
    if expected <= 0:
        return 1.0 if observed else 0.0
    return round(min(observed / expected, 1.0), 3)


@tool_error
async def get_daily_summary(days: int = 14) -> dict[str, Any]:
    """Daily heart summaries for the last N local days, mirroring GET /api/v1/metrics/daily.

    Read-through over derived.daily_features (ADR 0012): missing days are
    computed deterministically from the heart-rate hypertable first. Days
    with no samples are returned with nulls + data_quality "insufficient" —
    explicit gaps, never interpolated zeros (spec §174).
    """
    if not 1 <= days <= 120:
        raise validation_error("days must be between 1 and 120")
    async with get_session_factory()() as session:
        summaries = await queries.daily_summaries(session, _tz(), days)

    days_json = [s.model_dump(mode="json") for s in summaries]
    days_with_data = sum(1 for s in summaries if s.sample_count > 0)
    mean_coverage = sum(s.coverage_ratio for s in summaries) / len(summaries)
    insufficient = sum(1 for s in summaries if s.data_quality == "insufficient")
    caveats = (
        [f"{insufficient} of {days} days have insufficient heart-rate data"] if insufficient else []
    )
    return _envelope(
        data={"days": days_json},
        coverage={
            "days_requested": days,
            "days_with_samples": days_with_data,
            "feature_set_version": FEATURE_SET_VERSION,
        },
        sources=["timeseries.heart_rate", "derived.daily_features"],
        quality=mean_coverage,
        caveats=caveats,
    )


@tool_error
async def get_sleep_sessions(days: int = 7) -> dict[str, Any]:
    """Vendor sleep sessions starting within the last N days (health.sleep_sessions).

    Sessions are returned verbatim (start/end, efficiency, resting HR, average
    HRV, computed duration); no scoring or interpretation is invented here.
    """
    if not 1 <= days <= 90:
        raise validation_error("days must be between 1 and 90")
    caveat = "sleep sessions are not available yet (sleep storage lands with migration 0006)"
    if SleepSession is None:
        return _envelope(
            data={"sessions": []},
            coverage={"days_requested": days, "days_with_sessions": 0},
            sources=[],
            quality=0.0,
            caveats=[caveat],
        )

    cutoff = datetime.now(UTC) - timedelta(days=days)
    async with get_session_factory()() as session:
        try:
            rows = (
                (
                    await session.execute(
                        select(SleepSession)
                        .where(SleepSession.start_ts >= cutoff)
                        .order_by(SleepSession.start_ts.desc())
                    )
                )
                .scalars()
                .all()
            )
        except ProgrammingError:  # table present in models but not migrated
            await session.rollback()
            return _envelope(
                data={"sessions": []},
                coverage={"days_requested": days, "days_with_sessions": 0},
                sources=[],
                quality=0.0,
                caveats=[caveat],
            )

    sessions = [
        {
            "source_record_id": row.source_record_id,
            "start_ts": row.start_ts.isoformat(),
            "end_ts": row.end_ts.isoformat(),
            "duration_min": round((row.end_ts - row.start_ts).total_seconds() / 60, 1),
            "efficiency": row.efficiency,
            "resting_hr": row.resting_hr,
            "avg_hrv": row.avg_hrv,
            "user_edited": row.user_edited,
        }
        for row in rows
    ]
    days_with_sessions = len({r.start_ts.astimezone(_tz()).date() for r in rows})
    return _envelope(
        data={"sessions": sessions},
        coverage={"days_requested": days, "days_with_sessions": days_with_sessions},
        sources=["health.sleep_sessions"],
        quality=days_with_sessions / days,
        caveats=[] if sessions else [queries.NO_DATA_CAVEAT],
    )


@tool_error
async def get_baselines(metric: str = "resting_hr") -> dict[str, Any]:
    """Rolling personal baseline for a metric over the trailing 28 days (spec §74).

    Deterministic robust statistics (somatriq_baseline_v1): median and
    quartiles (IQR) of the metric's daily values. Fewer than 7 days of data
    returns an explicit "insufficient data" result — never zeros.
    Supported metrics: our computed resting_hr/hr_min/hr_mean/hr_max plus the
    vendor daily observation catalog (avg_hrv, recovery, strain,
    total_sleep_min, spo2_pct, ...).
    """
    source = BASELINE_METRICS.get(metric)
    if source is None:
        raise validation_error(
            f"unsupported metric {metric!r}; supported: {sorted(BASELINE_METRICS)}"
        )
    source_table, source_key = source

    tz = _tz()
    today = datetime.now(UTC).astimezone(tz).date()
    first = today - timedelta(days=BASELINE_WINDOW_DAYS - 1)

    async with get_session_factory()() as session:
        values = await _baseline_values(session, tz, metric, source_table, source_key, first, today)

    n = len(values)
    if n < MIN_BASELINE_DAYS:
        return _envelope(
            data={
                "metric": metric,
                "status": "insufficient data",
                "days_available": n,
                "required_minimum": MIN_BASELINE_DAYS,
                "algorithm_version": BASELINE_ALGORITHM,
                "source": source_table,
            },
            coverage={"days_available": n, "window_days": BASELINE_WINDOW_DAYS},
            sources=[source_table],
            quality=n / BASELINE_WINDOW_DAYS,
            caveats=[INSUFFICIENT_CAVEAT + f"; got {n}"],
        )

    ordered = sorted(values)
    q1, _median, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    median = statistics.median(ordered)
    return _envelope(
        data={
            "metric": metric,
            "median": round(median, 2),
            "q1": round(q1, 2),
            "q3": round(q3, 2),
            "iqr": round(q3 - q1, 2),
            "min": round(ordered[0], 2),
            "max": round(ordered[-1], 2),
            "n_days": n,
            "window_days": BASELINE_WINDOW_DAYS,
            "algorithm_version": BASELINE_ALGORITHM,
            "source": source_table,
        },
        coverage={"days_available": n, "window_days": BASELINE_WINDOW_DAYS},
        sources=[source_table],
        quality=n / BASELINE_WINDOW_DAYS,
        caveats=[],
    )


async def _baseline_values(
    session: AsyncSession,
    tz: ZoneInfo,
    metric: str,
    source_table: str,
    source_key: str,
    first: date,
    last: date,
) -> list[float]:
    """The metric's daily values inside the window, deterministic order."""
    if source_table == "derived.daily_features":
        # Read-through: compute missing days first so a baseline works on a
        # cold cache (same semantics as the daily-summary tool).
        summaries = await queries.daily_summaries(session, tz, BASELINE_WINDOW_DAYS)
        return [v for s in summaries if (v := getattr(s, source_key)) is not None]

    result = await session.execute(
        text(
            "SELECT DISTINCT ON (day) day, value "
            "FROM health.daily_observations "
            "WHERE metric = :metric AND day BETWEEN :first AND :last "
            "ORDER BY day, received_at DESC"
        ),
        {"metric": source_key, "first": first, "last": last},
    )
    return [float(row[1]) for row in result.all()]


TOOL_NAMES: Final[tuple[str, ...]] = (
    "get_heart_rate_summary",
    "get_daily_summary",
    "get_sleep_sessions",
    "get_baselines",
)

__all__ = [
    "BASELINE_ALGORITHM",
    "BASELINE_METRICS",
    "BASELINE_WINDOW_DAYS",
    "MIN_BASELINE_DAYS",
    "TOOL_NAMES",
    "get_baselines",
    "get_daily_summary",
    "get_heart_rate_summary",
    "get_sleep_sessions",
]
