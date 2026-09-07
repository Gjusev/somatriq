"""Ingest authentication guard (M2 per ADR 0015; M1 stopgap preserved).

Two credential paths, both resolving to the same request.state principal
(user_id, device_id, device_name):

1. ``Authorization: Bearer sqt_dev_…`` — a hashed device token from
   identity.device_tokens (must be unrevoked, unexpired, and carry the
   ``ingest.write`` scope). Revoked → 403 DEVICE_REVOKED; anything else
   unknown → 401 AUTHENTICATION.
2. The transitional M1 global INGEST_TOKEN — accepted either via the legacy
   ``X-Somatriq-Token`` header (scripts/e2e_smoke.py and deployed senders)
   or as a non-device ``Authorization: Bearer`` — resolving to the seeded
   single-user identity (accounts.resolve_single_user_target).

The M1 production guard stays: no credentials presented at all and no
INGEST_TOKEN configured in production → 503, never silent open writes. In
development with no INGEST_TOKEN, unauthenticated writes keep the M1
behavior of resolving the seeded single user.

require_read_principal is the READ-side sibling: the account JWT (spec
§122) or a data.read-scoped device token — the Android dashboard client —
resolving to the owner's user_id for the owner-only read endpoints.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import DEVICE_TOKEN_PREFIX
from somatriq_db.engine import get_session
from somatriq_db.models import Device, DeviceToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import (
    hash_device_token,
    require_account_jwt,
    resolve_single_user_target,
)
from somatriq_api.errors import ApiError
from somatriq_api.settings import get_settings

INGEST_WRITE_SCOPE = "ingest.write"
DATA_READ_SCOPE = "data.read"


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer ") :].strip()


async def require_ingest_principal(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_somatriq_token: Annotated[str | None, Header()] = None,
) -> None:
    """Resolve the ingest principal onto request.state, or reject the write."""
    bearer = _bearer_token(authorization)

    if bearer is not None and bearer.startswith(DEVICE_TOKEN_PREFIX):
        await _authenticate_device_token(request, session, bearer)
        return

    settings = get_settings()
    if settings.ingest_token is not None:
        if x_somatriq_token == settings.ingest_token or bearer == settings.ingest_token:
            await _apply_single_user_context(request, session)
            return
        raise ApiError(
            status_code=401,
            code=ErrorCode.AUTHENTICATION,
            message="invalid or missing ingest token",
        )

    if bearer is not None:
        # A presented credential that is neither a device token nor the
        # global token is a hard 401 — never an anonymous dev write.
        raise ApiError(
            status_code=401,
            code=ErrorCode.AUTHENTICATION,
            message="invalid or missing ingest token",
        )

    if settings.somatriq_env == "production":
        # M1 guard (kept): a misconfigured production deployment must reject
        # writes rather than accept them silently.
        raise ApiError(
            status_code=503,
            code=ErrorCode.AUTHENTICATION,
            message="INGEST_TOKEN not configured; refusing unauthenticated writes in production",
        )

    # Development with no INGEST_TOKEN: M1 behavior — resolve the seeded user.
    await _apply_single_user_context(request, session)


async def _verified_device_token(
    session: AsyncSession, token: str, required_scope: str
) -> DeviceToken:
    """Hash-lookup + revoked/expired/scope gate, shared by every device-token
    guard (ingest write, data read). Unknown/expired → 401 AUTHENTICATION;
    revoked → 403 DEVICE_REVOKED; missing scope → 403 AUTHORIZATION."""
    token_row = (
        await session.execute(
            select(DeviceToken).where(DeviceToken.token_hash == hash_device_token(token))
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if token_row is None:
        raise ApiError(
            status_code=401, code=ErrorCode.AUTHENTICATION, message="unknown device token"
        )
    if token_row.revoked_at is not None:
        raise ApiError(
            status_code=403, code=ErrorCode.DEVICE_REVOKED, message="device token has been revoked"
        )
    if token_row.expires_at is not None and token_row.expires_at <= now:
        raise ApiError(
            status_code=401, code=ErrorCode.AUTHENTICATION, message="expired device token"
        )
    if required_scope not in (token_row.scopes or []):
        raise ApiError(
            status_code=403,
            code=ErrorCode.AUTHORIZATION,
            message=f"device token lacks the {required_scope} scope",
        )
    return token_row


async def _touch_device_token(session: AsyncSession, token_row: DeviceToken) -> None:
    """Fire-and-forget bookkeeping; nothing else is pending on this session."""
    token_row.last_used_at = datetime.now(UTC)
    await session.commit()


async def _authenticate_device_token(
    request: Request, session: AsyncSession, token: str
) -> None:
    token_row = await _verified_device_token(session, token, INGEST_WRITE_SCOPE)

    device = (
        await session.execute(select(Device).where(Device.id == token_row.device_id))
    ).scalar_one()
    request.state.user_id = device.user_id
    request.state.device_id = device.id
    request.state.device_name = device.name
    await _touch_device_token(session, token_row)


async def require_read_principal(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> UUID:
    """Owner-read guard: the account JWT (spec §122, unchanged) OR a device
    token (Authorization: Bearer sqt_dev_…, same convention as ingest) whose
    scopes include data.read — resolved to the token owner's user_id."""
    bearer = _bearer_token(authorization)

    if bearer is not None and bearer.startswith(DEVICE_TOKEN_PREFIX):
        token_row = await _verified_device_token(session, bearer, DATA_READ_SCOPE)
        device = (
            await session.execute(select(Device).where(Device.id == token_row.device_id))
        ).scalar_one()
        await _touch_device_token(session, token_row)
        return device.user_id

    # Byte-identical account-JWT path — including its flat 401s.
    return await require_account_jwt(authorization)


ReadUserDep = Annotated[UUID, Depends(require_read_principal)]


async def _apply_single_user_context(request: Request, session: AsyncSession) -> None:
    user_id, device_id = await resolve_single_user_target(session)
    device = (
        await session.execute(select(Device).where(Device.id == device_id))
    ).scalar_one()
    request.state.user_id = user_id
    request.state.device_id = device_id
    request.state.device_name = device.name
