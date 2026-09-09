"""GET /api/v1/health-monitor (Block 3; CONTEXT "Vital").

Vitals vs the personal baseline with coverage and provenance — wellness
analytics, never a diagnosis; the disclaimer rides every response.
"""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from somatriq_analytics.health_monitor_data import assemble_health_monitor
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_ALLOWED_PERIODS,
    HealthMonitorResponse,
)
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.health_report import render_health_report

from .errors import ApiError
from .security import ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/health-monitor", tags=["health-monitor"])


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("", response_model=HealthMonitorResponse)
async def read_health_monitor(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query()] = 30,
) -> HealthMonitorResponse:
    del user_id  # owner-only guard has run
    if days not in HEALTH_MONITOR_ALLOWED_PERIODS:
        allowed = ", ".join(str(value) for value in HEALTH_MONITOR_ALLOWED_PERIODS)
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=f"days must be one of {allowed}",
        )
    tz = ZoneInfo(get_settings().user_timezone)
    assembled = await assemble_health_monitor(session, tz=tz, days=days, now=_now())
    return HealthMonitorResponse(**assembled)


@router.get("/report.pdf")
async def read_health_monitor_report(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query()] = 30,
) -> Response:
    """The shareable report: deterministic bytes rendered from the
    assembler's structured output (no model text, permanent disclaimer)."""
    del user_id
    if days not in HEALTH_MONITOR_ALLOWED_PERIODS:
        allowed = ", ".join(str(value) for value in HEALTH_MONITOR_ALLOWED_PERIODS)
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=f"days must be one of {allowed}",
        )
    tz = ZoneInfo(get_settings().user_timezone)
    assembled = await assemble_health_monitor(session, tz=tz, days=days, now=_now())
    body = render_health_report(HealthMonitorResponse(**assembled))
    filename = f"somatriq-health-monitor-{days}d.pdf"
    return Response(
        content=body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
