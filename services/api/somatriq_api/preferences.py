"""GET/PUT /api/v1/preferences — the typed preference store (ADR 0019).

Registered keys are explicit optional fields on the wire models; unknown
keys are rejected (extra="forbid"). Values persist as jsonb in
identity.user_preferences — shapeless at rest, typed at this edge. Only
fields provided in the PUT are upserted; clearing a key is a later concern
(not v1). Account JWT only: preferences are the owner's declarations, not
device-writable state.
"""

import contextlib
import json
import uuid
from datetime import time as time_type
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.plan import PreferencesResponse, PreferenceUpdate
from somatriq_db.engine import get_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError

router = APIRouter(prefix="/api/v1/preferences", tags=["preferences"])

_PREFERENCE_SQL = """
    SELECT key, value FROM identity.user_preferences WHERE user_id = :user_id
"""

_UPSERT_SQL = """
    INSERT INTO identity.user_preferences (user_id, key, value)
    VALUES (:user_id, :key, CAST(:value AS jsonb))
    ON CONFLICT (user_id, key) DO UPDATE
    SET value = EXCLUDED.value, updated_at = now()
"""


async def _stored(user_id: uuid.UUID, session: AsyncSession) -> dict[str, object]:
    result = await session.execute(text(_PREFERENCE_SQL), {"user_id": user_id})
    stored: dict[str, object] = {}
    for key, value in result.all():
        parsed: object = value
        if isinstance(parsed, (bytes, bytearray)):
            parsed = parsed.decode()
        if isinstance(parsed, str):
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(parsed)
        stored[str(key)] = parsed
    return stored


@router.get("", response_model=PreferencesResponse)
async def read_preferences(
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PreferencesResponse:
    """The owner's declared preferences (unset keys are absent)."""
    try:
        return PreferencesResponse.model_validate(await _stored(user_id, session))
    except ValidationError:
        # A stored value that no longer validates degrades to unset here;
        # the plan falls back to the documented default the same way.
        return PreferencesResponse()


@router.put("", response_model=PreferencesResponse)
async def update_preferences(
    payload: PreferenceUpdate,
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PreferencesResponse:
    """Upsert the provided keys; others stay untouched. An empty body is a
    no-op read — nothing is ever cleared by this endpoint."""
    provided = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not provided:
        return await read_preferences(user_id, session)

    for key, value in provided.items():
        wire = value.isoformat() if isinstance(value, time_type) else value
        await session.execute(
            text(_UPSERT_SQL),
            {"user_id": user_id, "key": key, "value": json.dumps(wire)},
        )
    await session.commit()
    return PreferencesResponse.model_validate(await _stored(user_id, session))


__all__ = ["router", "ApiError", "ErrorCode"]
