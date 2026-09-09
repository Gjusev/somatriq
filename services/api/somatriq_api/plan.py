"""GET /api/v1/plan/today — the day's plan (Block 1; ADR 0012/0017/0018).

The DB assembly lives in somatriq_analytics.plan_data.assemble_plan so the
HTTP surface, the brief and (later) the mobile science views read the SAME
numbers by construction. The response is always present — degraded plans
(null tier / null bedtime with caveats) are the explainability surface,
mirroring today.py's always-present recovery.
"""

from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from somatriq_analytics.plan_data import assemble_plan
from somatriq_contracts.plan import PlanTodayResponse, SleepDebtSummary
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .security import ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/plan", tags=["plan"])


def _now() -> datetime:
    """Request clock, isolated so tests can anchor the local day."""
    return datetime.now(UTC)


@router.get("/today", response_model=PlanTodayResponse)
async def read_plan_today(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanTodayResponse:
    """Today's guidance: training tier, target strain, bedtime window —
    each with contributions and caveats (spec §76 explainability pattern).

    Account JWT or data.read device token (spec §122) — the owner's read.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    data = await assemble_plan(session, tz=tz, now=_now())
    return PlanTodayResponse(
        date=data.date,
        timezone=data.timezone,
        wake_time=data.wake_time,
        wake_source=data.wake_source,
        sleep_need=data.sleep_need,
        plan=data.plan,
        sleep_debt=SleepDebtSummary(
            debt_min=data.sleep_debt.debt_min,
            measured_days=data.sleep_debt.measured_days,
            unmeasured_days=data.sleep_debt.unmeasured_days,
        ),
        coverage_ratio=data.coverage_ratio,
        caveats=data.caveats,
    )
