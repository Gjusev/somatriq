"""Heart-rate metric reads (spec §71, §99, §116, §174; ADR 0002) and the
daily heart summary read-through cache (spec §72, §174; ADR 0012/0017).

The read API is intentionally unauthenticated for M1 — session auth lands
with the web app (spec §122). The web never fetches raw firehoses: raw
reads are capped and viewport aggregation goes through TimescaleDB
``time_bucket`` so the database, not the browser, does the folding.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Final, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from somatriq_analytics.daily import summarize_day
from somatriq_contracts.catalog import HEART_RATE_CADENCE_SECONDS, HEART_RATE_UNIT
from somatriq_contracts.daily import (
    FEATURE_SET_VERSION,
    RHR_BUCKET_MINUTES,
    DailyHeartSummary,
    DailySummaryResponse,
)
from somatriq_contracts.metrics import (
    HeartRatePoint,
    MetricSeriesRequest,
    MetricSeriesResponse,
)
from somatriq_db.engine import get_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .settings import get_settings

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

_RAW_POINT_LIMIT: Final = 5000
_TRUNCATION_CAVEAT: Final = "result truncated to 5000 points; use a coarser bucket"
_NO_DATA_CAVEAT: Final = "no data in range"

# Fixed whitelist: validated bucket codes -> TimescaleDB interval widths and
# widths in seconds for the coverage denominator (ADR 0002, spec §99). The
# interval reaches SQL as a bound timedelta parameter (asyncpg encodes it as
# an interval), never via string interpolation.
_BUCKET_INTERVALS: Final[dict[str, timedelta]] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
}
_BUCKET_SECONDS: Final[dict[str, int]] = {"1m": 60, "5m": 300, "1h": 3600}


@router.get("/heart_rate", response_model=MetricSeriesResponse)
async def read_heart_rate(
    request: Annotated[MetricSeriesRequest, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MetricSeriesResponse:
    """Viewport-appropriate heart-rate series (spec §71, §116)."""
    start, end = _resolve_window(request, datetime.now(UTC))
    window_seconds = (end - start).total_seconds()

    if request.bucket == "none":
        points, caveats = await _raw_points(session, start, end)
        expected = window_seconds / HEART_RATE_CADENCE_SECONDS
    else:
        interval = _BUCKET_INTERVALS.get(request.bucket)
        if interval is None:
            raise _validation_error("bucket must be one of: none, 1m, 5m, 1h")
        points, caveats = await _bucketed_points(session, start, end, interval)
        expected = window_seconds / _BUCKET_SECONDS[request.bucket]

    count = len(points)
    if count == 0:
        caveats.append(_NO_DATA_CAVEAT)

    return MetricSeriesResponse(
        metric="heart_rate",
        unit=HEART_RATE_UNIT,
        points=points,
        count=count,
        coverage=_coverage(count, expected),
        caveats=caveats,
        generated_at=datetime.now(UTC),
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error_code": "VALIDATION", "message": message},
    )


def _aware(dt: datetime) -> datetime:
    """Treat naive timestamps as UTC rather than failing deep in asyncpg."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _resolve_window(request: MetricSeriesRequest, now: datetime) -> tuple[datetime, datetime]:
    """Explicit [from_ts, to_ts] only when both are given; else last_hours back from now."""
    if request.from_ts is not None and request.to_ts is not None:
        start, end = _aware(request.from_ts), _aware(request.to_ts)
    else:
        end = now
        start = end - timedelta(hours=request.last_hours)
    if start >= end:
        raise _validation_error("from_ts must be before to_ts")
    return start, end


def _coverage(observed: int, expected: float) -> float:
    """Proportion of expected data present, clamped to the [0, 1] envelope (spec §99)."""
    if expected <= 0:
        return 1.0 if observed else 0.0
    return round(min(observed / expected, 1.0), 3)


async def _raw_points(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[list[HeartRatePoint], list[str]]:
    """Raw samples capped at the raw limit; the cap is reported, never silent (spec §71)."""
    result = await session.execute(
        text(
            "SELECT ts, bpm FROM timeseries.heart_rate "
            "WHERE ts BETWEEN :start AND :end "
            "ORDER BY ts "
            "LIMIT :limit"
        ),
        {"start": start, "end": end, "limit": _RAW_POINT_LIMIT + 1},
    )
    rows = result.all()
    caveats: list[str] = []
    if len(rows) > _RAW_POINT_LIMIT:
        rows = rows[:_RAW_POINT_LIMIT]
        caveats.append(_TRUNCATION_CAVEAT)
    points = [
        HeartRatePoint(ts=cast(datetime, row[0]), bpm=cast(float, row[1]), sample_count=1)
        for row in rows
    ]
    return points, caveats


async def _bucketed_points(
    session: AsyncSession, start: datetime, end: datetime, interval: timedelta
) -> tuple[list[HeartRatePoint], list[str]]:
    """Per-bucket averages via time_bucket — aggregation belongs in the database (ADR 0002)."""
    result = await session.execute(
        text(
            "SELECT time_bucket(:interval, ts) AS bucket_ts, "
            "avg(bpm) AS avg_bpm, count(*) AS sample_count "
            "FROM timeseries.heart_rate "
            "WHERE ts BETWEEN :start AND :end "
            "GROUP BY 1 "
            "ORDER BY 1"
        ),
        {"interval": interval, "start": start, "end": end},
    )
    points = [
        HeartRatePoint(
            ts=cast(datetime, row[0]),
            bpm=round(cast(float, row[1]), 2),
            sample_count=cast(int, row[2]),
        )
        for row in result.all()
    ]
    return points, []


# ── daily heart summary (spec §72 slice, §174; ADR 0012/0017) ────────────

_SECONDS_PER_DAY: Final = 86400

# One compute query per request (never per day): local-day attribution in
# SQL via (ts AT TIME ZONE :tz)::date — DST-correct because every sample
# maps to exactly one local date — plus 5-minute bucket medians for RHR v1
# and the day's min/avg/max/count. Grouping buckets by (local_date, bucket)
# splits a midnight-straddling 5-minute bucket at the day boundary, which is
# exactly "bucket the local day's samples" per the frozen contract.
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

# Same-version upsert is allowed under ADR 0012 because feature_set_version
# pins the semantics: refreshing a row inside its version never silently
# rewrites history — changed semantics MUST bump the version and write new
# rows. This covers the read-through cache (a day re-computed after its row
# was dropped or invalidated) without version churn.
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


class _DayAggregates:
    """Raw aggregates for one local day as returned by the compute query."""

    __slots__ = ("bucket_medians", "hr_max", "hr_mean", "hr_min", "sample_count")

    def __init__(
        self,
        bucket_medians: list[float],
        hr_min: float | None,
        hr_mean: float | None,
        hr_max: float | None,
        sample_count: int,
    ) -> None:
        self.bucket_medians = bucket_medians
        self.hr_min = hr_min
        self.hr_mean = hr_mean
        self.hr_max = hr_max
        self.sample_count = sample_count


_EMPTY_AGGREGATE = _DayAggregates([], None, None, None, 0)


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local-day window."""
    return datetime.now(UTC)


def _day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) in UTC — DST-correct via zoneinfo.

    A fall-back day spans 25 UTC hours, a spring-forward day 23; both fall
    out of the astimezone conversion (ADR 0017 golden-fixture semantics).
    """
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end_local = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _coverage_ratio(sample_count: int, expected_per_day: float) -> float:
    """sample_count / expected samples per day, capped at 1.0 (spec §99).

    The denominator is always 86400/cadence — coverage is reported against a
    nominal day even on 25-hour DST days, keeping the envelope simple and
    never hiding quality problems (spec §158).
    """
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


async def _cached_days(
    session: AsyncSession, first: date, last: date
) -> dict[date, DailyHeartSummary]:
    """Rows already materialized for this feature_set_version in the range."""
    result = await session.execute(
        text(
            "SELECT date, timezone, resting_hr, hr_min, hr_mean, hr_max, "
            "sample_count, coverage_ratio, data_quality, algorithm_version "
            "FROM derived.daily_features "
            "WHERE feature_set_version = :fsv AND date BETWEEN :first AND :last"
        ),
        {"fsv": FEATURE_SET_VERSION, "first": first, "last": last},
    )
    return {
        cast(date, row[0]): DailyHeartSummary.model_validate(
            {
                "date": row[0],
                "timezone": row[1],
                "resting_hr": row[2],
                "hr_min": row[3],
                "hr_mean": row[4],
                "hr_max": row[5],
                "sample_count": row[6],
                "coverage_ratio": row[7],
                "data_quality": row[8],
                "algorithm_version": row[9],
            }
        )
        for row in result.all()
    }


async def _day_aggregates(
    session: AsyncSession, tz: ZoneInfo, range_start: datetime, range_end: datetime
) -> dict[date, _DayAggregates]:
    """Per-local-day aggregates for the whole requested range in one query."""
    result = await session.execute(
        text(_DAY_COMPUTE_SQL),
        {
            "tz": tz.key,
            "bucket_interval": timedelta(minutes=RHR_BUCKET_MINUTES),
            "range_start": range_start,
            "range_end": range_end,
        },
    )
    return {
        cast(date, row[0]): _DayAggregates(
            bucket_medians=list(cast("list[float]", row[1])),
            hr_min=cast("float | None", row[2]),
            hr_mean=cast("float | None", row[3]),
            hr_max=cast("float | None", row[4]),
            sample_count=cast(int, row[5]),
        )
        for row in result.all()
    }


async def _persist_days(
    session: AsyncSession, tz_key: str, summaries: Iterable[DailyHeartSummary]
) -> None:
    """Upsert every computed day (empty days included) in one transaction."""
    rows = [
        {
            "date": summary.date,
            "feature_set_version": FEATURE_SET_VERSION,
            "timezone": tz_key,
            "resting_hr": summary.resting_hr,
            "hr_min": summary.hr_min,
            "hr_mean": summary.hr_mean,
            "hr_max": summary.hr_max,
            "sample_count": summary.sample_count,
            "coverage_ratio": summary.coverage_ratio,
            "data_quality": summary.data_quality,
            "algorithm_version": summary.algorithm_version,
        }
        for summary in summaries
    ]
    if rows:
        await session.execute(text(_DAILY_UPSERT_SQL), rows)
        await session.commit()


@router.get("/daily", response_model=DailySummaryResponse)
async def read_daily_summary(
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=120)] = 14,
) -> DailySummaryResponse:
    """Daily heart summaries for the last ``days`` local days (spec §72, §174).

    Read-through cache over derived.daily_features keyed (date,
    feature_set_version) per ADR 0012: uncached days are computed from the
    heart-rate hypertable in one SQL round per request and persisted before
    responding. Day boundaries are local days in the effective timezone
    (USER_TIMEZONE, ADR 0017). Days with zero samples are included with
    nulls + ``insufficient`` so charts render explicit gaps (spec §174)
    instead of interpolating.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    today = _now().astimezone(tz).date()
    requested = [today - timedelta(days=days - 1 - i) for i in range(days)]

    cached = await _cached_days(session, requested[0], requested[-1])
    missing = [day for day in requested if day not in cached]

    computed: dict[date, DailyHeartSummary] = {}
    if missing:
        expected_per_day = _SECONDS_PER_DAY / await _expected_cadence(session)
        range_start = _day_bounds_utc(missing[0], tz)[0]
        range_end = _day_bounds_utc(missing[-1], tz)[1]
        aggregates = await _day_aggregates(session, tz, range_start, range_end)
        for day in missing:
            agg = aggregates.get(day, _EMPTY_AGGREGATE)
            computed[day] = summarize_day(
                date=day,
                timezone=tz.key,
                bucket_medians=agg.bucket_medians,
                hr_min=agg.hr_min,
                hr_mean=agg.hr_mean,
                hr_max=agg.hr_max,
                sample_count=agg.sample_count,
                coverage_ratio=_coverage_ratio(agg.sample_count, expected_per_day),
            )
        await _persist_days(session, tz.key, computed.values())

    return DailySummaryResponse(days=[cached.get(day) or computed[day] for day in requested])
