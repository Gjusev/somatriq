"""Owner read of the notifications outbox (Fase 4; spec Q15b).

The ntfy container is internal-only (private Docker network, ADR 0011):
the paired phone cannot subscribe to it, so the Android app POLLS the API
instead. This endpoint lists the owner's recent outbox rows — the same
messages the notifications service delivers — behind the shared read
principal (account JWT or data.read device token, spec §122).

Strictly read-only: delivery state stays the notifications service's job
(spec §105), so nothing here marks rows sent/consumed, and no delivery
internals (attempts, last_error) are exposed — only what the owner
should see.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from somatriq_db.engine import get_session
from somatriq_db.models import NotificationChannel, NotificationOutbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .security import ReadUserDep

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


class NotificationItem(BaseModel):
    """One recent notification for the owner."""

    id: uuid.UUID
    created_at: datetime
    kind: str  # morning_brief | sync_warning | anomaly | test
    channel: str  # telegram | ntfy
    message: str  # payload["text"] — the exact body the sender delivers
    status: str  # pending | sent | failed
    sent_at: datetime | None


def _message(payload: object) -> str:
    """Outbox payload jsonb -> message text ("" when absent; mirrors the
    sender's payload_text — never crashes on an odd shape)."""
    if isinstance(payload, dict):
        return str(payload.get("text", ""))
    return ""


@router.get("/recent", response_model=list[NotificationItem])
@router.get("/recent/", response_model=list[NotificationItem])
async def recent_notifications(
    user_id: ReadUserDep, session: SessionDep, limit: int = Query(default=_DEFAULT_LIMIT)
) -> list[NotificationItem]:
    """The owner's recent notifications, newest first — the phone's poll
    replacement for the unreachable ntfy subscribe (Q15b). ``limit`` is
    clamped to [1, 100]; no outbox row is ever marked consumed here."""
    effective = max(1, min(limit, _MAX_LIMIT))
    rows = (
        await session.execute(
            select(NotificationOutbox, NotificationChannel.kind)
            .join(NotificationChannel, NotificationOutbox.channel_id == NotificationChannel.id)
            .where(NotificationChannel.user_id == user_id)
            .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
            .limit(effective)
        )
    ).all()
    return [
        NotificationItem(
            id=outbox.id,
            created_at=outbox.created_at,
            kind=outbox.kind,
            channel=channel_kind,
            message=_message(outbox.payload),
            status=outbox.status,
            sent_at=outbox.sent_at,
        )
        for outbox, channel_kind in rows
    ]
