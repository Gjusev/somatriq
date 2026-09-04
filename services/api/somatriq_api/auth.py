"""Local account auth router (spec §122, §180; ADR 0015).

Single local account: register is open only while zero accounts exist; login
mints a 12h HS256 web-session JWT that the pairing/devices surfaces require
and ingest never accepts. Errors render flat (spec §157).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import AuthStatus, LoginRequest, RegisterRequest, TokenResponse
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.accounts import (
    account_exists,
    authenticate,
    create_access_token,
    register_account,
)
from somatriq_api.errors import ApiError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
