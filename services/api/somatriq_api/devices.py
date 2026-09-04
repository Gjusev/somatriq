"""Device management router (spec §44-45, §123-124; ADR 0015).

Lists the account's devices with the newest active token's usage/expiry
fields (devices with no tokens still appear, with nulls) and revokes every
active token of one device — revocation is immediate and unexplained to the
device beyond DEVICE_REVOKED on its next write.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import DeviceInfo
from somatriq_db.engine import get_session
from somatriq_db.models import Device, DeviceToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import AccountJwtDep
from somatriq_api.errors import ApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[DeviceInfo])
@router.get("/", response_model=list[DeviceInfo])
async def list_devices(user_id: AccountJwtDep, session: SessionDep) -> list[DeviceInfo]:
    """All devices under the account, oldest first (web renders them all)."""
    devices = (
        (
            await session.execute(
                select(Device).where(Device.user_id == user_id).order_by(Device.active_from)
            )
        )
        .scalars()
        .all()
    )
    infos: list[DeviceInfo] = []
    for device in devices:
        tokens = (
            (
                await session.execute(
                    select(DeviceToken)
                    .where(DeviceToken.device_id == device.id)
                    .order_by(DeviceToken.created_at)
                )
            )
            .scalars()
            .all()
        )
        active = [token for token in tokens if token.revoked_at is None]
        # Newest active token carries the usage/expiry fields; a revoked-only
        # device surfaces its newest (revoked) token so the web can show why.
        chosen = active[-1] if active else (tokens[-1] if tokens else None)
        infos.append(
            DeviceInfo(
                device_id=device.id,
                name=device.name,
                model=device.model,
                created_at=device.active_from,
                last_used_at=chosen.last_used_at if chosen is not None else None,
                token_expires_at=chosen.expires_at if chosen is not None else None,
                revoked_at=chosen.revoked_at if chosen is not None else None,
            )
        )
    return infos


@router.post("/{device_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: UUID, user_id: AccountJwtDep, session: SessionDep
) -> Response:
    """Revoke ALL active tokens of the device (§124 audit event)."""
    device = (
        await session.execute(
            select(Device).where(Device.id == device_id, Device.user_id == user_id)
        )
    ).scalar_one_or_none()
    if device is None:
        raise ApiError(
            status_code=404,
            code=ErrorCode.DEVICE_NOT_FOUND,
            message="no such device under this account",
        )
    now = datetime.now(UTC)
    await session.execute(
        update(DeviceToken)
        .where(DeviceToken.device_id == device_id, DeviceToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.commit()
    logger.info("device revoked: device_id=%s user_id=%s", device_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
