"""Explore read API (Block 3; grill P12; ADR 0014).

Day-grain only, one honest contract: gaps stay gaps (days without a value
do not appear), coverage rides along where it exists (computed metrics),
and windows longer than the frozen 3-year cap are rejected with guidance
instead of silently truncated.
"""

from datetime import date as date_type
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from somatriq_analytics.correlation_data import UnknownMetricError, is_known_metric
from somatriq_analytics.explore_data import explore_context, explore_series
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.explore import (
    EXPLORE_MAX_SPAN_DAYS,
    ExploreContextResponse,
    ExploreSeriesResponse,
)
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import ApiError
from .security import ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/explore", tags=["explore"])

_MAX_METRICS = 8


def _validated_window(from_date: date_type, to_date: date_type) -> None:
    if from_date > to_date:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="from_date must not be after to_date",
        )
    span = (to_date - from_date).days + 1
    if span > EXPLORE_MAX_SPAN_DAYS:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=(
                f"window spans {span} days; the maximum is {EXPLORE_MAX_SPAN_DAYS} — "
                "split the request into smaller windows"
            ),
        )


def _validated_metrics(raw: str) -> list[str]:
    metrics = [metric.strip() for metric in raw.split(",") if metric.strip()]
    if not metrics or len(metrics) > _MAX_METRICS:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=f"provide 1..{_MAX_METRICS} metrics, comma-separated",
        )
    for metric in metrics:
        if not is_known_metric(metric):
            raise ApiError(
                status_code=422, code=ErrorCode.VALIDATION, message=str(UnknownMetricError(metric))
            )
    return metrics


@router.get("/series", response_model=ExploreSeriesResponse)
async def read_explore_series(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    metrics: Annotated[str, Query(min_length=1)],
    from_date: Annotated[date_type, Query()],
    to_date: Annotated[date_type, Query()],
) -> ExploreSeriesResponse:
    """Day-grain series over the window; week/month/year aggregation is a
    client concern (one honest contract, gaps visible)."""
    del user_id  # owner-only guard has run; single-user stores filter nothing
    _validated_window(from_date, to_date)
    requested = _validated_metrics(metrics)
    tz = ZoneInfo(get_settings().user_timezone)
    series = await explore_series(session, metrics=requested, first=from_date, last=to_date, tz=tz)
    return ExploreSeriesResponse(
        from_date=from_date, to_date=to_date, timezone=tz.key, metrics=series
    )


@router.get("/context", response_model=ExploreContextResponse)
async def read_explore_context(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_date: Annotated[date_type, Query()],
    to_date: Annotated[date_type, Query()],
) -> ExploreContextResponse:
    """The overlays: device boundaries, algorithm versions (registry +
    first-seen), journal kinds, training days, timezone change points."""
    del user_id
    _validated_window(from_date, to_date)
    tz = ZoneInfo(get_settings().user_timezone)
    context = await explore_context(session, first=from_date, last=to_date, tz=tz)
    return ExploreContextResponse(from_date=from_date, to_date=to_date, timezone=tz.key, **context)
