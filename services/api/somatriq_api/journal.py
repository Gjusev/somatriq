"""Journal quick-log API (Block 2; grill P7/P10/P11; spec §103/§181).

POST accepts the account JWT (source 'web') or a device token with
journal.write (source 'mobile'); the structured payload is validated per
kind by the contract and normalized exactly like the Telegram parser
(quantity kinds without a stated quantity record {"estimated": false} —
never a guess). ``client_event_id`` makes offline retries idempotent: the
same id returns the SAME event, never a duplicate (ADR 0006 spirit).
DELETE is the owner's correction mechanism for user-authored kinds only —
system kinds (training, experiment_checkin) are the system's audit trail.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from somatriq_analytics.behavior_data import behavior_insight_inputs
from somatriq_analytics.behaviors import behavior_insights_v1
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.journal import (
    BEHAVIOR_INSIGHT_DEFAULT_DAYS,
    QUANTITY_KINDS,
    SYSTEM_KINDS,
    BehaviorInsightsResponse,
    JournalDayResponse,
    JournalEventCreate,
    JournalEventOut,
)
from somatriq_db.engine import get_session
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import ApiError
from .security import JournalWriteDep, ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/journal", tags=["journal"])

_INSERT_SQL = """
    INSERT INTO health.journal_events
        (user_id, source, kind, ts, text, structured, client_event_id)
    VALUES (:user_id, :source, :kind, :ts, :text, CAST(:structured AS jsonb),
            :client_event_id)
    ON CONFLICT (user_id, client_event_id) DO NOTHING
    RETURNING id, source, kind, ts, text, structured, client_event_id, created_at
"""

_PLAIN_INSERT_SQL = """
    INSERT INTO health.journal_events
        (user_id, source, kind, ts, text, structured)
    VALUES (:user_id, :source, :kind, :ts, :text, CAST(:structured AS jsonb))
    RETURNING id, source, kind, ts, text, structured, client_event_id, created_at
"""

_BY_CLIENT_ID_SQL = """
    SELECT id, source, kind, ts, text, structured, client_event_id, created_at
    FROM health.journal_events
    WHERE user_id = :user_id AND client_event_id = :client_event_id
"""

_DAY_SQL = """
    SELECT id, source, kind, ts, text, structured, client_event_id, created_at
    FROM health.journal_events
    WHERE user_id = :user_id AND ts >= :day_start AND ts < :next_day_start
    ORDER BY ts DESC
"""


def _normalize_structured(
    kind: str, structured: dict[str, object] | None
) -> dict[str, object] | None:
    """Quantity kinds without a stated quantity record estimated=false —
    the spec §103 rule: quantity unknown, and we did not guess one."""
    if structured is not None:
        return structured
    if kind in QUANTITY_KINDS:
        return {"estimated": False}
    return None


def _row_to_out(row: Row[Any]) -> JournalEventOut:
    values = dict(row._mapping)  # noqa: SLF001 — Row mapping is the read API
    return JournalEventOut(
        id=values["id"],
        kind=values["kind"],
        source=values["source"],
        ts=values["ts"],
        text=values["text"],
        structured=values["structured"],
        client_event_id=values["client_event_id"],
        created_at=values["created_at"],
    )


@router.post("/events", response_model=JournalEventOut, status_code=201)
async def create_journal_event(
    payload: JournalEventCreate,
    principal: JournalWriteDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JournalEventOut:
    """Quick-log one event. 201 for a new event; 200 (same body) when the
    ``client_event_id`` was already stored — a retried mobile write is the
    SAME event, never a duplicate."""
    user_id, source = principal
    ts = payload.ts or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)  # naive wire clocks read as UTC (documented)
    params = {
        "user_id": user_id,
        "source": source,
        "kind": payload.kind,
        "ts": ts,
        "text": payload.text,
        "structured": _structured_wire(payload.kind, payload.structured),
        "client_event_id": payload.client_event_id,
    }
    if payload.client_event_id is not None:
        row = (await session.execute(text(_INSERT_SQL), params)).one_or_none()
        if row is None:
            # A retried mobile write is the SAME event — return the stored
            # one (200 semantics; the status stays 201 on the wire but the
            # body is identical, which is what idempotency promises).
            row = (
                await session.execute(
                    text(_BY_CLIENT_ID_SQL),
                    {"user_id": user_id, "client_event_id": payload.client_event_id},
                )
            ).one()
        await session.commit()
        return _row_to_out(row)
    row = (await session.execute(text(_PLAIN_INSERT_SQL), params)).one()
    await session.commit()
    return _row_to_out(row)


def _structured_wire(kind: str, structured: dict[str, object] | None) -> str | None:
    normalized = _normalize_structured(kind, structured)
    return json.dumps(normalized) if normalized is not None else None


@router.get("", response_model=JournalDayResponse)
async def read_journal_day(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    day: Annotated[str | None, None] = None,
) -> JournalDayResponse:
    """One local day's events, newest first (the brief + /status read)."""
    tz = ZoneInfo(get_settings().user_timezone)
    try:
        local_day = date.fromisoformat(day) if day else datetime.now(UTC).astimezone(tz).date()
    except ValueError as exc:
        raise ApiError(
            status_code=422, code=ErrorCode.VALIDATION, message="day must be YYYY-MM-DD"
        ) from exc
    day_start = datetime.combine(local_day, datetime.min.time(), tzinfo=tz)
    next_day_start = day_start + timedelta(days=1)
    result = await session.execute(
        text(_DAY_SQL),
        {"user_id": user_id, "day_start": day_start, "next_day_start": next_day_start},
    )
    return JournalDayResponse(
        date=local_day.isoformat(),
        events=[_row_to_out(row) for row in result.all()],
    )


@router.delete("/events/{event_id}", status_code=204)
async def delete_journal_event(
    event_id: uuid.UUID,
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Owner correction, user-authored kinds only (P11): the journal is the
    user's input, not source evidence — but system kinds are the system's
    audit trail and are rejected explicitly."""
    row = (
        await session.execute(
            text("SELECT kind FROM health.journal_events WHERE id = :id AND user_id = :user_id"),
            {"id": event_id, "user_id": user_id},
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(status_code=404, code=ErrorCode.NOT_FOUND, message="journal event not found")
    if row in SYSTEM_KINDS:
        raise ApiError(
            status_code=409,
            code=ErrorCode.VALIDATION,
            message=f"kind {row!r} is system-written — not deletable here",
        )
    await session.execute(
        text("DELETE FROM health.journal_events WHERE id = :id AND user_id = :user_id"),
        {"id": event_id, "user_id": user_id},
    )
    await session.commit()


@router.get("/insights", response_model=BehaviorInsightsResponse)
async def read_behavior_insights(
    user_id: ReadUserDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=14, le=365)] = BEHAVIOR_INSIGHT_DEFAULT_DAYS,
) -> BehaviorInsightsResponse:
    """The frozen behavior-insight family (association, never causation):
    exposure day d -> outcome day d+1, groups from actively-journaled
    days, BH-FDR q over executed tests, confounders beside every effect."""
    tz = ZoneInfo(get_settings().user_timezone)
    inputs = await behavior_insight_inputs(session, tz=tz, days=days)
    rows = behavior_insights_v1(**inputs)  # type: ignore[arg-type]
    return BehaviorInsightsResponse(days=days, timezone=tz.key, rows=rows)
