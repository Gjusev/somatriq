"""Read-path SQL for MCP tools — direct database access, never HTTP to the api.

Mirrors ``services/api/somatriq_api/metrics.py`` query-for-query (spec §71,
§72, §99; ADR 0002: aggregation happens in the database via time_bucket;
ADR 0017: local-day attribution with the day's effective timezone). The MCP
server sits beside the api, not behind it (spec §95: AI client → Somatriq MCP
→ domain service → analytics → PostgreSQL), so the read model is replicated
here rather than fetched — same shapes, same caveats, same determinism.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Final, cast
from zoneinfo import ZoneInfo

from somatriq_analytics.daily import summarize_day
from somatriq_contracts.catalog import HEART_RATE_CADENCE_SECONDS
from somatriq_contracts.daily import (
    FEATURE_SET_VERSION,
    RHR_BUCKET_MINUTES,
    DailyHeartSummary,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── heart-rate series (mirror of metrics.py /api/v1/metrics/heart_rate) ──

RAW_POINT_LIMIT: Final = 5000
TRUNCATION_CAVEAT: Final = "result truncated to 5000 points; use a coarser bucket"
NO_DATA_CAVEAT: Final = "no data in range"

BUCKET_INTERVALS: Final[dict[str, timedelta]] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
}
BUCKET_SECONDS: Final[dict[str, int]] = {"1m": 60, "5m": 300, "1h": 3600}

SECONDS_PER_DAY: Final = 86400


async def raw_points(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[list[dict[str, object]], list[str]]:
    """Raw samples capped at the raw limit; the cap is reported, never silent."""
    result = await session.execute(
        text(
            "SELECT ts, bpm FROM timeseries.heart_rate "
            "WHERE ts BETWEEN :start AND :end "
            "ORDER BY ts "
            "LIMIT :limit"
        ),
        {"start": start, "end": end, "limit": RAW_POINT_LIMIT + 1},
    )
    rows = result.all()
    caveats: list[str] = []
    if len(rows) > RAW_POINT_LIMIT:
        rows = rows[:RAW_POINT_LIMIT]
        caveats.append(TRUNCATION_CAVEAT)
    points = [
        {"ts": cast(datetime, row[0]).isoformat(), "bpm": cast(float, row[1]), "sample_count": 1}
        for row in rows
    ]
    return points, caveats


async def bucketed_points(
    session: AsyncSession, start: datetime, end: datetime, interval: timedelta
) -> tuple[list[dict[str, object]], list[str]]:
    """Per-bucket averages via time_bucket (ADR 0002)."""
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
        {
            "ts": cast(datetime, row[0]).isoformat(),
            "bpm": round(cast(float, row[1]), 2),
            "sample_count": cast(int, row[2]),
        }
        for row in result.all()
    ]
    return points, []


async def expected_cadence(session: AsyncSession) -> int:
    """heart_rate cadence from system.metrics; the contract constant as fallback."""
    result = await session.execute(
        text("SELECT expected_cadence_seconds FROM system.metrics WHERE name = 'heart_rate'")
    )
    cadence = result.scalar_one_or_none()
    return cadence if cadence and cadence > 0 else HEART_RATE_CADENCE_SECONDS


# ── daily heart summaries (mirror of metrics.py /api/v1/metrics/daily) ───
#
# Same read-through cache over derived.daily_features keyed (date,
# feature_set_version) per ADR 0012: uncached days are computed from the
# hypertable in one SQL round per request and persisted before responding.

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


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local-day window."""
    return datetime.now(UTC)


def _day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) in UTC — DST-correct (ADR 0017)."""
    start_local = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end_local = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def coverage_ratio(sample_count: int, expected_per_day: float) -> float:
    """sample_count / expected samples per day, capped at 1.0 (spec §99)."""
    if expected_per_day <= 0:
        return 1.0 if sample_count else 0.0
    return min(sample_count / expected_per_day, 1.0)


async def _cached_days(
    session: AsyncSession, first: date, last: date
) -> dict[date, DailyHeartSummary]:
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
) -> dict[date, dict[str, object]]:
    result = await session.execute(
        text(_DAY_COMPUTE_SQL),
        {
            "tz": tz.key,
            "bucket_interval": timedelta(minutes=RHR_BUCKET_MINUTES),
            "range_start": range_start,
            "range_end": range_end,
        },
    )
    return {cast(date, row[0]): {"row": row} for row in result.all()}


async def _persist_days(
    session: AsyncSession, tz_key: str, summaries: Iterable[DailyHeartSummary]
) -> None:
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


async def daily_summaries(
    session: AsyncSession, tz: ZoneInfo, days: int
) -> list[DailyHeartSummary]:
    """Last ``days`` local-day summaries, computing + caching missing days.

    Identical semantics to GET /api/v1/metrics/daily: days with zero samples
    are included with nulls + ``insufficient`` so gaps stay explicit (spec
    §174) instead of interpolated.
    """
    today = _now().astimezone(tz).date()
    requested = [today - timedelta(days=days - 1 - i) for i in range(days)]

    cached = await _cached_days(session, requested[0], requested[-1])
    missing = [day for day in requested if day not in cached]

    computed: dict[date, DailyHeartSummary] = {}
    if missing:
        expected_per_day = SECONDS_PER_DAY / await expected_cadence(session)
        range_start = _day_bounds_utc(missing[0], tz)[0]
        range_end = _day_bounds_utc(missing[-1], tz)[1]
        aggregates = await _day_aggregates(session, tz, range_start, range_end)
        for day in missing:
            agg = aggregates.get(day)
            if agg is None:
                bucket_medians: list[float] = []
                hr_min = hr_mean = hr_max = None
                sample_count = 0
            else:
                row = cast("tuple[object, ...]", agg["row"])
                bucket_medians = list(cast("list[float]", row[1]))
                hr_min = cast("float | None", row[2])
                hr_mean = cast("float | None", row[3])
                hr_max = cast("float | None", row[4])
                sample_count = cast(int, row[5])
            computed[day] = summarize_day(
                date=day,
                timezone=tz.key,
                bucket_medians=bucket_medians,
                hr_min=hr_min,
                hr_mean=hr_mean,
                hr_max=hr_max,
                sample_count=sample_count,
                coverage_ratio=coverage_ratio(sample_count, expected_per_day),
            )
        await _persist_days(session, tz.key, computed.values())

    return [cached.get(day) or computed[day] for day in requested]
