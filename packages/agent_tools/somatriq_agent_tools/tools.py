"""Deterministic coach tools (spec §94, §99; ADR 0009) — the ONLY facts an
LLM ever sees.

Every tool is async, DB-backed through somatriq_db, and returns a plain
JSON-safe dict in the §99 envelope spirit (``{data, coverage, sources,
quality, caveats, generated_at}``): provenance (which tables answered),
coverage against the requested window, a quality fraction, and honest
caveats. Nothing here invents data — an insufficient window yields an
explicit "insufficient data" result, never zeros (spec §158/§221). No tool
computes anything an LLM could be blamed for: statistics stay in
deterministic Python (ADR 0009), reusing the frozen analytics math
(``somatriq_baseline_v1`` via somatriq_analytics.recovery.baseline).

Semantics:

* ``get_today`` — delegates to somatriq_analytics.today_data.assemble_today,
  so the coach, the /metrics/today endpoint and the morning brief all read
  the SAME numbers by construction.
* ``get_baselines`` / ``get_trends`` — daily values per metric from the same
  catalog the MCP server uses: vendor health.daily_observations metrics plus
  our computed derived.daily_features columns (ours win the duplicate
  "resting_hr" key; the resolved source table is reported in every result).
  Unlike the MCP tool this slice reads the cached feature store directly —
  no read-through recompute — so a cold cache simply yields fewer days,
  reported honestly in coverage.
* ``get_journal`` — the local day's health.journal_events verbatim (kind,
  ts, structured, text). Free text leaves only through this tool, and only
  at the ``local`` privacy level (see services/agent privacy chokepoint).
* ``get_data_quality`` — per-day coverage/quality rows from
  derived.daily_features; days without a row are explicit gaps.

``get_trends`` direction is the sign of an ordinary least-squares slope of
the metric's daily values against days-elapsed (stdlib only, no numpy):
"up" for a positive slope, "down" for negative, "flat" for exactly zero,
"insufficient" below two points. Gaps count as distance (x is the day
offset from the window start, not the row index).
"""

import statistics
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

from somatriq_analytics.recovery import baseline as baseline_v1
from somatriq_analytics.strength import StrengthSet, session_summary
from somatriq_analytics.today_data import assemble_today, day_bounds_utc
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.observations import VENDOR_DAILY_METRICS
from somatriq_contracts.recovery import RECOVERY_BASELINE_MIN_DAYS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ToolFn = Callable[..., Any]

BASELINE_ALGORITHM: Final[str] = "somatriq_baseline_v1"
TREND_ALGORITHM: Final[str] = "least-squares-sign-v1"
DEFAULT_BASELINE_DAYS: Final[int] = 28
DEFAULT_TREND_DAYS: Final[int] = 14
DEFAULT_QUALITY_DAYS: Final[int] = 14
MAX_WINDOW_DAYS: Final[int] = 365
MIN_TREND_POINTS: Final[int] = 2

INSUFFICIENT_BASELINE_CAVEAT: Final[str] = (
    f"insufficient data: a personal baseline needs at least {RECOVERY_BASELINE_MIN_DAYS} "
    "days of values inside the window"
)

# metric → (source table, column / metric key) — same catalog shape as the
# MCP server's baselines tool. Our computed resting_hr/hr_* take precedence
# over the vendor observation of the same name (union order below), and the
# resolved source is reported in every result so the distinction is never
# ambiguous.
_DAILY_FEATURE_METRICS: Final[dict[str, str]] = {
    "resting_hr": "resting_hr",
    "hr_min": "hr_min",
    "hr_mean": "hr_mean",
    "hr_max": "hr_max",
}
METRIC_SOURCES: Final[dict[str, tuple[str, str]]] = {
    k: ("health.daily_observations", k) for k in VENDOR_DAILY_METRICS
} | {k: ("derived.daily_features", v) for k, v in _DAILY_FEATURE_METRICS.items()}


class ToolValidationError(ValueError):
    """Bad tool arguments (unknown metric, out-of-range window)."""


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local day."""
    return datetime.now(UTC)


def envelope(
    *,
    data: dict[str, Any],
    coverage: dict[str, Any],
    sources: list[str],
    quality: float,
    caveats: list[str],
) -> dict[str, Any]:
    """Assemble the §99 response contract (spec §99; JSON-safe by construction)."""
    return {
        "data": data,
        "coverage": coverage,
        "sources": sources,
        "quality": round(quality, 3),
        "caveats": caveats,
        "generated_at": _now().isoformat(),
    }


def _require_metric(metric: str) -> tuple[str, str]:
    if metric not in METRIC_SOURCES:
        msg = f"unsupported metric {metric!r}; supported: {sorted(METRIC_SOURCES)}"
        raise ToolValidationError(msg)
    return METRIC_SOURCES[metric]


def _require_window(days: int) -> int:
    if not 1 <= days <= MAX_WINDOW_DAYS:
        msg = f"days must be between 1 and {MAX_WINDOW_DAYS}"
        raise ToolValidationError(msg)
    return days


def _local_today(tz: ZoneInfo, now: datetime | None) -> date:
    return (now or _now()).astimezone(tz).date()


async def daily_metric_values(
    session: AsyncSession, metric: str, first: date, last: date
) -> list[tuple[date, float]]:
    """The metric's daily values inside the window, date-ordered (deterministic).

    Caller validates the metric against METRIC_SOURCES first; the table and
    column below are frozen catalog constants, never user input.
    """
    source_table, source_key = METRIC_SOURCES[metric]
    if source_table == "derived.daily_features":
        # source_key is a pinned catalog column (validated against
        # METRIC_SOURCES above), never user input.
        result = await session.execute(
            text(
                f"SELECT date, {source_key} FROM derived.daily_features "
                "WHERE feature_set_version = :fsv AND date >= :first AND date <= :last "
                f"AND {source_key} IS NOT NULL "
                "ORDER BY date"
            ),
            {"fsv": FEATURE_SET_VERSION, "first": first, "last": last},
        )
    else:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (day) day, value "
                "FROM health.daily_observations "
                "WHERE metric = :metric AND day >= :first AND day <= :last "
                "ORDER BY day, received_at DESC"
            ),
            {"metric": source_key, "first": first, "last": last},
        )
    return [(cast(date, row[0]), float(row[1])) for row in result.all()]


# ── get_today ────────────────────────────────────────────────────────────


async def get_today(
    session: AsyncSession, *, tz: ZoneInfo, now: datetime | None = None
) -> dict[str, Any]:
    """Today's sleep, HRV, resting HR and explainable recovery (spec §76).

    Thin §99 envelope over somatriq_analytics.today_data.assemble_today —
    the same reading the /metrics/today endpoint and the morning brief use.
    """
    data = await assemble_today(session, tz=tz, now=now or _now())
    hrv_coverage = data.hrv.coverage if data.hrv is not None else None
    journal = {
        "caffeine_count": data.journal.caffeine_count,
        "journal_notes": data.journal.journal_notes,
        "caffeine_last_ts": (
            data.journal.caffeine_last_ts.isoformat()
            if data.journal.caffeine_last_ts is not None
            else None
        ),
    }
    return envelope(
        data={
            "date": data.date.isoformat(),
            "timezone": data.timezone,
            "recovery": data.recovery.model_dump(mode="json"),
            "hrv": data.hrv.model_dump(mode="json") if data.hrv is not None else None,
            "sleep": data.sleep.model_dump(mode="json") if data.sleep is not None else None,
            "resting_hr": data.resting_hr,
            "resting_hr_quality": data.resting_hr_quality,
            "data_freshness_minutes": data.data_freshness_minutes,
            "journal": journal,
        },
        coverage={
            "heart_rate": data.coverage_ratio,
            "hrv": hrv_coverage,
            "date": data.date.isoformat(),
        },
        sources=[
            "health.sleep_sessions",
            "timeseries.rr_interval",
            "timeseries.heart_rate",
            "derived.daily_features",
            "health.journal_events",
        ],
        quality=data.coverage_ratio,
        caveats=list(data.caveats),
    )


# ── get_baselines ────────────────────────────────────────────────────────


async def get_baselines(
    session: AsyncSession,
    *,
    metric: str = "resting_hr",
    days: int = DEFAULT_BASELINE_DAYS,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Rolling personal baseline (median + inclusive IQR) over daily values.

    Reuses the frozen ``somatriq_baseline_v1`` math from
    somatriq_analytics.recovery.baseline: fewer than RECOVERY_BASELINE_MIN_DAYS
    values is an explicit "insufficient data" result, never a faked baseline.
    """
    source_table, _ = _require_metric(metric)
    _require_window(days)
    today = _local_today(tz, now)
    first = today - timedelta(days=days - 1)

    values = await daily_metric_values(session, metric, first, today)
    n = len(values)
    if n < RECOVERY_BASELINE_MIN_DAYS:
        return envelope(
            data={
                "metric": metric,
                "status": "insufficient data",
                "days_available": n,
                "required_minimum": RECOVERY_BASELINE_MIN_DAYS,
                "window_days": days,
                "algorithm_version": BASELINE_ALGORITHM,
                "source": source_table,
            },
            coverage={"days_available": n, "window_days": days},
            sources=[source_table],
            quality=n / days,
            caveats=[f"{INSUFFICIENT_BASELINE_CAVEAT}; got {n}"],
        )

    pair = baseline_v1([value for _, value in values])
    assert pair is not None  # n >= RECOVERY_BASELINE_MIN_DAYS guarantees a pair
    median, iqr = pair
    ordered = sorted(value for _, value in values)
    q1, _q2, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    return envelope(
        data={
            "metric": metric,
            "status": "ok",
            "median": round(median, 2),
            "q1": round(q1, 2),
            "q3": round(q3, 2),
            "iqr": round(iqr, 2),
            "min": round(ordered[0], 2),
            "max": round(ordered[-1], 2),
            "n_days": n,
            "first_day": first.isoformat(),
            "last_day": today.isoformat(),
            "window_days": days,
            "algorithm_version": BASELINE_ALGORITHM,
            "source": source_table,
        },
        coverage={"days_available": n, "window_days": days},
        sources=[source_table],
        quality=n / days,
        caveats=([f"{days - n} of {days} window days have no {metric} values"] if n < days else []),
    )


# ── get_trends ───────────────────────────────────────────────────────────


def _least_squares_slope(points: list[tuple[float, float]]) -> float | None:
    """Slope of ordinary least squares over (x, y); None below two points.

    Stdlib only (no numpy). x is days elapsed from the window start, so
    missing days still count as distance.
    """
    n = len(points)
    if n < MIN_TREND_POINTS:
        return None
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return numerator / denominator


def _direction(slope: float | None) -> str:
    if slope is None:
        return "insufficient"
    if slope > 0:
        return "up"
    if slope < 0:
        return "down"
    return "flat"


async def get_trends(
    session: AsyncSession,
    *,
    metric: str = "resting_hr",
    days: int = DEFAULT_TREND_DAYS,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Daily values series plus a deterministic direction word.

    Direction is the sign of the least-squares slope of the daily values
    against days elapsed (see module docstring). The series carries ONLY
    daily aggregates — raw samples are never exposed by any tool here.
    """
    source_table, _ = _require_metric(metric)
    _require_window(days)
    today = _local_today(tz, now)
    first = today - timedelta(days=days - 1)

    values = await daily_metric_values(session, metric, first, today)
    n = len(values)
    slope = _least_squares_slope([(float((day - first).days), value) for day, value in values])

    caveats: list[str] = []
    if n < MIN_TREND_POINTS:
        caveats.append(
            f"insufficient data: a trend needs at least {MIN_TREND_POINTS} days of values; got {n}"
        )
    elif n < days:
        caveats.append(f"{days - n} of {days} window days have no {metric} values")

    return envelope(
        data={
            "metric": metric,
            "direction": _direction(slope),
            "slope_per_day": None if slope is None else round(slope, 6),
            "n_points": n,
            "series": [{"date": day.isoformat(), "value": value} for day, value in values],
            "window_days": days,
            "algorithm_version": TREND_ALGORITHM,
            "source": source_table,
        },
        coverage={"days_requested": days, "days_with_values": n},
        sources=[source_table],
        quality=n / days,
        caveats=caveats,
    )


# ── get_journal ──────────────────────────────────────────────────────────


async def get_journal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    day: date | None = None,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Journal events for one local day, verbatim (kind, ts, structured, text).

    ``day`` defaults to the local day of ``now``. The events are returned
    exactly as stored — no interpretation. Free text is the most sensitive
    field the coach touches; the privacy chokepoint (services/agent) drops
    it at every level except ``local``.
    """
    effective_day = day if day is not None else _local_today(tz, now)
    start, end = day_bounds_utc(effective_day, tz)
    result = await session.execute(
        text(
            "SELECT kind, ts, structured, text FROM health.journal_events "
            "WHERE user_id = :user_id AND ts >= :start AND ts < :end "
            "ORDER BY ts"
        ),
        {"user_id": user_id, "start": start, "end": end},
    )
    events = [
        {
            "kind": cast(str, row[0]),
            "ts": cast(datetime, row[1]).isoformat(),
            "structured": cast("dict[str, Any] | None", row[2]),
            "text": cast("str | None", row[3]),
        }
        for row in result.all()
    ]
    n = len(events)
    return envelope(
        data={"day": effective_day.isoformat(), "events": events},
        coverage={"day": effective_day.isoformat(), "events": n},
        sources=["health.journal_events"],
        quality=1.0 if n else 0.0,
        caveats=[] if n else [f"no journal events on {effective_day.isoformat()}"],
    )


# ── get_data_quality ─────────────────────────────────────────────────────


async def get_data_quality(
    session: AsyncSession,
    *,
    days: int = DEFAULT_QUALITY_DAYS,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per-day coverage/quality from derived.daily_features (spec §99, §158).

    Days without a cached feature row are explicit gaps (zero samples,
    "insufficient"), never interpolated (spec §174).
    """
    _require_window(days)
    today = _local_today(tz, now)
    first = today - timedelta(days=days - 1)
    result = await session.execute(
        text(
            "SELECT date, sample_count, coverage_ratio, data_quality "
            "FROM derived.daily_features "
            "WHERE feature_set_version = :fsv AND date >= :first AND date <= :last"
        ),
        {"fsv": FEATURE_SET_VERSION, "first": first, "last": today},
    )
    by_day: dict[date, tuple[int | None, float | None, str | None]] = {
        cast(date, row[0]): (
            cast("int | None", row[1]),
            cast("float | None", row[2]),
            cast("str | None", row[3]),
        )
        for row in result.all()
    }

    day_rows: list[dict[str, Any]] = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        samples, coverage, quality = by_day.get(day, (None, None, None))
        day_rows.append(
            {
                "date": day.isoformat(),
                "sample_count": samples if samples is not None else 0,
                "coverage_ratio": coverage if coverage is not None else 0.0,
                "data_quality": quality or "insufficient",
            }
        )
    insufficient = sum(1 for row in day_rows if row["data_quality"] == "insufficient")
    days_with_rows = len(by_day)
    mean_coverage = sum(cast(float, row["coverage_ratio"]) for row in day_rows) / days

    return envelope(
        data={
            "days": day_rows,
            "feature_set_version": FEATURE_SET_VERSION,
            "timezone": tz.key,
        },
        coverage={"days_requested": days, "days_with_rows": days_with_rows},
        sources=["derived.daily_features"],
        quality=mean_coverage,
        caveats=(
            [f"{insufficient} of {days} days have insufficient heart-rate data"]
            if insufficient
            else []
        ),
    )


# ── get_training ──────────────────────────────────────────────────────────


DEFAULT_TRAINING_DAYS: Final[int] = 7

_TRAINING_SQL = """
    SELECT s.id, s.ts, s.source, t.exercise, t.weight_kg, t.reps, t.rir, t.rpe
    FROM health.training_sessions s
    JOIN health.training_sets t ON t.session_id = s.id
    WHERE s.user_id = :user_id AND s.ts >= :first_start
    ORDER BY s.ts, t.exercise, t.set_index
"""


async def get_training(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    days: int = DEFAULT_TRAINING_DAYS,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Strength sessions over the trailing window with their §79 summaries.

    M12 data finally reaches the coach (it post-dated the M8 toolset): each
    session carries its exercises, set count, tonnage and hard sets —
    computed by the same frozen session_summary the training API uses, so
    the coach can never disagree with the app's numbers.
    """
    _require_window(days)
    today = _local_today(tz, now)
    first = today - timedelta(days=days - 1)
    first_start = datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC)

    rows = (
        await session.execute(text(_TRAINING_SQL), {"user_id": user_id, "first_start": first_start})
    ).all()

    sessions: dict[uuid.UUID, dict[str, Any]] = {}
    sets_by_session: dict[uuid.UUID, list[StrengthSet]] = {}
    for session_id, session_ts, source, exercise, weight_kg, reps, rir, rpe in rows:
        sets_by_session.setdefault(session_id, []).append(
            StrengthSet(
                exercise=cast(str, exercise),
                weight_kg=cast("float | None", weight_kg),
                reps=int(reps),
                rir=cast("int | None", rir),
                rpe=cast("float | None", rpe),
            )
        )
        local_day = cast(datetime, session_ts).astimezone(tz).date()
        sessions[session_id] = {
            "date": local_day.isoformat(),
            "source": cast(str, source),
        }

    session_rows: list[dict[str, Any]] = []
    for session_id, header in sessions.items():
        summary = session_summary(sets_by_session[session_id])
        session_rows.append(
            {
                **header,
                "exercises": summary.exercises,
                "sets": summary.set_count,
                "tonnage_kg": summary.tonnage_kg,
                "hard_sets": summary.hard_sets,
            }
        )

    n = len(session_rows)
    return envelope(
        data={"days": days, "timezone": tz.key, "sessions": session_rows},
        coverage={"window_days": days, "sessions": n},
        sources=["health.training_sessions", "health.training_sets"],
        quality=1.0 if n else 0.0,
        caveats=[] if n else [f"no strength sessions in the last {days} days"],
    )
