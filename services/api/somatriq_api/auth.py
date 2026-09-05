"""Local account auth router (spec §122, §180; ADR 0015).

Single local account: register is open only while zero accounts exist; login
mints a 12h HS256 web-session JWT that the pairing/devices surfaces require
and ingest never accepts. Errors render flat (spec §157).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import AuthStatus, LoginRequest, RegisterRequest, TokenResponse
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import (
    AccountJwtDep,
    account_exists,
    authenticate,
    create_access_token,
    register_account,
)
from somatriq_api.accounts import (
    change_password as change_account_password,
)
from somatriq_api.errors import ApiError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class ChangePasswordRequest(BaseModel):
    """POST /api/v1/auth/change-password — local model, not contracts.

    The wire shape is frozen (current + new, new >= 12 chars like
    RegisterRequest); it moves to somatriq_contracts.pairing with the next
    contracts release rather than forcing one for a single endpoint.
    """

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.get("/status", response_model=AuthStatus)
async def auth_status(session: SessionDep) -> AuthStatus:
    """Drives the first-run wizard branch (§180): register vs login."""
    return AuthStatus(has_account=await account_exists(session))


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, session: SessionDep) -> TokenResponse:
    if await account_exists(session):
        raise ApiError(
            status_code=409,
            code=ErrorCode.ACCOUNT_EXISTS,
            message="an account already exists; registration is closed (spec §122)",
        )
    credential = await register_account(session, body.username, body.password)
    token, expires_at = create_access_token(credential.user_id)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    credential = await authenticate(session, body.username, body.password)
    if credential is None:
        raise ApiError(
            status_code=401,
            code=ErrorCode.INVALID_CREDENTIALS,
            message="invalid username or password",
        )
    token, expires_at = create_access_token(credential.user_id)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, user_id: AccountJwtDep, session: SessionDep
) -> Response:
    """Rotate the local account passphrase (incident follow-up to ADR 0015).

    The current passphrase is verified first — a mismatch answers 401
    INVALID_CREDENTIALS exactly like login, and the stored hash is left
    untouched. Device tokens and MCP PATs stay valid across the change:
    they are independent credentials (random secrets stored hashed, never
    derived from the passphrase), so rotating them is the separate,
    revocation-based path — revoke the device or PAT and re-pair. There is
    no password-derived credential to rotate with it.
    """
    changed = await change_account_password(
        session, user_id, body.current_password, body.new_password
    )
    if not changed:
        raise ApiError(
            status_code=401,
            code=ErrorCode.INVALID_CREDENTIALS,
            message="current password is incorrect",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
