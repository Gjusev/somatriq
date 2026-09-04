"""GET /api/v1/metrics/today — the day's recovery + inputs (spec §76, §181;
ADR 0012/0017). Mounted under the metrics prefix with the other unauthenticated
reads (session auth lands with the web app, spec §122).

M7: the DB assembly lives in somatriq_analytics.today_data.assemble_today so
the HTTP surface and the Telegram morning brief read the SAME numbers by
construction. This endpoint maps TodayData onto TodayResponse — the wire
behavior is unchanged (its integration tests are the contract); the
assembler's semantics are documented there.
"""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from somatriq_analytics.today_data import assemble_today
from somatriq_contracts.recovery import TodayResponse
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .settings import get_settings

router = APIRouter(prefix="/api/v1/metrics", tags=["today"])


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local day."""
    return datetime.now(UTC)


@router.get("/today", response_model=TodayResponse)
async def read_today(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TodayResponse:
    """Today's sleep, HRV, resting HR and explainable recovery (spec §76).

    Unauthenticated like the other reads. The recovery result is always
    present — with missing_inputs listed and a null score when the day has
    not earned one — because the explainability surface is the point.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    data = await assemble_today(session, tz=tz, now=_now())
    return TodayResponse(
        date=data.date,
        timezone=data.timezone,
        recovery=data.recovery,
        hrv=data.hrv,
        sleep=data.sleep,
        resting_hr=data.resting_hr,
        resting_hr_quality=data.resting_hr_quality,
        data_freshness_minutes=data.data_freshness_minutes,
        caveats=data.caveats,
    )
