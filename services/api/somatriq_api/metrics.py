"""Heart-rate metric reads (spec §71, §99, §116, §174; ADR 0002).

The read API is intentionally unauthenticated for M1 — session auth lands
with the web app (spec §122). The web never fetches raw firehoses: raw
reads are capped and viewport aggregation goes through TimescaleDB
``time_bucket`` so the database, not the browser, does the folding.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from somatriq_contracts.catalog import HEART_RATE_CADENCE_SECONDS, HEART_RATE_UNIT
from somatriq_contracts.metrics import (
    HeartRatePoint,
    MetricSeriesRequest,
    MetricSeriesResponse,
)
from somatriq_db.engine import get_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
