"""Device pairing router (spec §43, §180; ADR 0015).

The web account creates a short-lived session (code shown/QR'd ONCE), the
collector proves possession of the live code, and receives a scoped device
token that is shown exactly once and stored server-side hashed. Errors
render flat (spec §157).
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import (
    DEVICE_SCOPES,
    PairedDeviceInfo,
    PairingConfirmRequest,
    PairingConfirmResponse,
    PairingSessionResponse,
    PairingStatus,
)
from somatriq_db.engine import get_session
from somatriq_db.models import Device, PairingSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import (
    AccountJwtDep,
    PairingCodeExpired,
    PairingCodeInvalid,
    confirm_pairing,
    create_pairing_session,
    pairing_session_status,
)
from somatriq_api.errors import ApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pairing", tags=["pairing"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/sessions", response_model=PairingSessionResponse)
async def create_session(user_id: AccountJwtDep, session: SessionDep) -> PairingSessionResponse:
    """Issue a single-use pairing code; the plaintext code returns ONCE."""
    pairing, code = await create_pairing_session(session, user_id)
    return PairingSessionResponse(
        session_id=pairing.id, pairing_code=code, expires_at=pairing.expires_at
    )


@router.get("/sessions/{session_id}", response_model=PairingStatus)
async def session_status(
    session_id: UUID, user_id: AccountJwtDep, session: SessionDep
) -> PairingStatus:
    """Wizard polling: hint + derived status + the paired device once consumed."""
    pairing = (
        await session.execute(
            select(PairingSession).where(PairingSession.id == session_id)
        )
    ).scalar_one_or_none()
    if pairing is None or pairing.user_id != user_id:
        raise ApiError(
            status_code=404,
            code=ErrorCode.PAIRING_SESSION_NOT_FOUND,
            message="no such pairing session for this account",
        )

    device_info: PairedDeviceInfo | None = None
    if pairing.status == "consumed" and pairing.device_id is not None:
        device = (
            await session.execute(select(Device).where(Device.id == pairing.device_id))
        ).scalar_one_or_none()
        if device is not None:
            device_info = PairedDeviceInfo(
                device_id=device.id, name=device.name, model=device.model
            )

    return PairingStatus(
        session_id=pairing.id,
        pairing_code_hint=pairing.code_hint,
        status=pairing_session_status(pairing, datetime.now(UTC)),
        created_at=pairing.created_at,
        expires_at=pairing.expires_at,
        device=device_info,
    )


@router.post("/confirm", response_model=PairingConfirmResponse)
async def confirm(body: PairingConfirmRequest, session: SessionDep) -> PairingConfirmResponse:
    """Possession of the live code is the proof (no auth header by design)."""
    try:
        pairing, device, token = await confirm_pairing(
            session, body.pairing_code, body.device_name
        )
    except PairingCodeInvalid:
        # Unknown and already-consumed answer identically — no info leak.
        raise ApiError(
            status_code=404,
            code=ErrorCode.PAIRING_CODE_INVALID,
            message="pairing code is unknown or no longer usable",
        ) from None
    except PairingCodeExpired:
        raise ApiError(
            status_code=410,
            code=ErrorCode.PAIRING_CODE_EXPIRED,
            message="pairing code expired; request a new session",
        ) from None
    issued_at = pairing.consumed_at or datetime.now(UTC)
    return PairingConfirmResponse(
        device_id=device.id,
        token=token,
        scopes=list(DEVICE_SCOPES),
        issued_at=issued_at,
    )
