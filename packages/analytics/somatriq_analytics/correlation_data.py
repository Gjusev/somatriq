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

Everything below is deterministic SQL + Python (ADR 0009); the coefficient
math lives in :mod:`somatriq_analytics.correlations` and is fed the
aligned float lists this module returns.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.observations import VENDOR_DAILY_METRICS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_analytics.correlations import join_lagged

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
    return {
        metric: await daily_metric_series(session, metric, days, tz)
        for metric in metrics
    }


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
        {"tz": tz.key, "kind": kind, "first_start": datetime(
            first.year, first.month, first.day, tzinfo=tz
        ).astimezone(UTC)},
    )
    return [(day, float(value)) for day, value in result.all()]
