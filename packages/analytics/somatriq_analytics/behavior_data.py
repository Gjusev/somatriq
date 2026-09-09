"""DB inputs for somatriq_behavior_insight_v1 (Block 2; ADR 0009).

Assembles the exposure/outcome/confounder sets the pure engine consumes:
journal events become per-day exposure sets (plus the frozen
caffeine-after-14 windowed variant, CAFFEINE_AFTER_HOUR local), outcomes
and strain reuse the correlation catalog's honest per-day series
(absent-day-honest, cross-device deduped), and the active-day universe is
every day with at least one journal event — the compliance-honest choice
(a silent day joins no group).
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from somatriq_contracts.journal import (
    BEHAVIOR_KINDS,
    CAFFEINE_AFTER_HOUR,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .correlation_data import daily_metric_series

_EVENTS_SQL = """
    SELECT ts, kind FROM health.journal_events WHERE ts >= :first_ts
"""


def exposures_from_events(
    events: list[tuple[datetime, str]], tz: ZoneInfo
) -> tuple[dict[str, set[date]], set[date]]:
    """(exposures, active_days): local-day sets; caffeine events at/after
    CAFFEINE_AFTER_HOUR (local) also feed the windowed variant."""
    exposures: dict[str, set[date]] = {kind: set() for kind in BEHAVIOR_KINDS}
    exposures["caffeine_after_14"] = set()
    active_days: set[date] = set()
    for ts, kind in events:
        local = ts.astimezone(tz)
        day = local.date()
        active_days.add(day)
        if kind in BEHAVIOR_KINDS:
            exposures[kind].add(day)
            if kind == "caffeine" and local.hour >= CAFFEINE_AFTER_HOUR:
                exposures["caffeine_after_14"].add(day)
    return exposures, active_days


async def behavior_insight_inputs(
    session: AsyncSession, *, tz: ZoneInfo, days: int, now: datetime | None = None
) -> dict[str, object]:
    """Everything behavior_insights_v1 needs, read once."""
    moment = now or datetime.now(UTC)
    first_ts = datetime.combine(
        moment.astimezone(tz).date() - timedelta(days=days - 1),
        datetime.min.time(),
        tzinfo=tz,
    ).astimezone(UTC)
    events = [
        (ts, kind)
        for ts, kind in (await session.execute(text(_EVENTS_SQL), {"first_ts": first_ts})).all()
    ]
    exposures, active_days = exposures_from_events(events, tz)

    outcome_series = {
        metric: dict(await daily_metric_series(session, metric, days, tz))
        for metric in ("avg_hrv", "recovery", "total_sleep_min", "resting_hr")
    }
    strain_by_day = dict(await daily_metric_series(session, "strain", days, tz))

    return {
        "exposures": exposures,
        "outcomes": outcome_series,
        "strain_by_day": strain_by_day,
        "active_days": active_days,
    }
