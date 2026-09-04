"""Shared account / pairing / device-token logic (ADR 0015, spec §122-124).

One module consumed by the auth/pairing/devices routers AND the bootstrap
CLI, so the wire behavior and the CLI mint exactly the same credentials.

Security invariants:

- passwords are hashed with Argon2id and never logged;
- pairing codes and device tokens are stored as sha256 hex digests and the
  plaintext values are returned exactly once, never logged (spec §45, §124).
"""

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Header
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.pairing import (
    DEVICE_SCOPES,
    DEVICE_TOKEN_PREFIX,
    PAIRING_CODE_ALPHABET,
    PAIRING_CODE_LENGTH,
    PAIRING_TTL_MINUTES,
)
from somatriq_db.models import (
    AccountCredential,
    DataSource,
    Device,
    DeviceToken,
    PairingSession,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from somatriq_api.errors import ApiError
from somatriq_api.settings import get_settings

logger = logging.getLogger(__name__)

# Account JWT (ADR 0015): HS256, 12h, web-session audience only — never ingest.
JWT_ALGORITHM = "HS256"
JWT_AUDIENCE = "somatriq-web"
SESSION_LIFETIME = timedelta(hours=12)
_DEV_SECRET_FALLBACK = "dev-secret"

# M1 bootstrap: the synthetic device + collector identity reused by every
# directly-minted device (ADR 0015 — the CLI mints the same token type).
BOOTSTRAP_DEVICE_MODEL = "noop-android"
BOOTSTRAP_PROVIDER = "noop"
BOOTSTRAP_COLLECTOR = "somatriq-mobile"

_password_hasher = PasswordHasher()  # Argon2id defaults
_pairing_rng = secrets.SystemRandom()


# ── password hashing ────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ── account JWT ─────────────────────────────────────────────────────────


def jwt_secret() -> str:
    settings = get_settings()
    if settings.secret_key:
        return settings.secret_key
    if settings.somatriq_env == "production":
        msg = "SECRET_KEY must be set when SOMATRIQ_ENV=production (spec §209)"
        raise RuntimeError(msg)
    return _DEV_SECRET_FALLBACK


def create_access_token(user_id: uuid.UUID) -> tuple[str, datetime]:
    """Mint a web-session JWT; returns (token, expires_at)."""
    now = datetime.now(UTC)
    expires_at = now + SESSION_LIFETIME
    token = jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": expires_at, "aud": JWT_AUDIENCE},
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> uuid.UUID:
    """Verify signature + audience + expiry; raises jwt.PyJWTError otherwise."""
    payload = jwt.decode(
        token, jwt_secret(), algorithms=[JWT_ALGORITHM], audience=JWT_AUDIENCE
    )
    return uuid.UUID(str(payload["sub"]))


def _http_unauthenticated(message: str) -> ApiError:
    return ApiError(status_code=401, code=ErrorCode.AUTHENTICATION, message=message)


async def require_account_jwt(
    authorization: Annotated[str | None, Header()] = None,
) -> uuid.UUID:
    """Web-surface guard: Authorization: Bearer <account JWT> → user_id."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise _http_unauthenticated("account JWT required")
    try:
        return decode_access_token(authorization[len("Bearer ") :].strip())
    except jwt.PyJWTError:
        raise _http_unauthenticated("invalid or expired account JWT") from None


AccountJwtDep = Annotated[uuid.UUID, Depends(require_account_jwt)]


# ── local account (§122) ────────────────────────────────────────────────


async def account_exists(session: AsyncSession) -> bool:
    found = (
        await session.execute(select(AccountCredential.user_id).limit(1))
    ).scalar_one_or_none()
    return found is not None


async def earliest_user_id(session: AsyncSession) -> uuid.UUID:
    """The seeded single local user (register attaches credentials to it)."""
    user_id = (
        await session.execute(select(User.id).order_by(User.created_at).limit(1))
    ).scalar_one_or_none()
    if user_id is None:
        msg = "no identity.users row: run migrations before registering (spec §122)"
        raise RuntimeError(msg)
    return user_id


async def register_account(
    session: AsyncSession, username: str, password: str
) -> AccountCredential:
    """Attach credentials to the earliest user; caller checks account_exists."""
    credential = AccountCredential(
        user_id=await earliest_user_id(session),
        username=username,
        password_hash=hash_password(password),
        last_login_at=datetime.now(UTC),
    )
    session.add(credential)
    await session.commit()
    logger.info("account registered: user_id=%s username=%s", credential.user_id, username)
    return credential


async def authenticate(
    session: AsyncSession, username: str, password: str
) -> AccountCredential | None:
    """None on unknown username or wrong password — indistinguishable by design."""
    credential = (
        await session.execute(
            select(AccountCredential).where(AccountCredential.username == username)
        )
    ).scalar_one_or_none()
    if credential is None or not verify_password(credential.password_hash, password):
        return None
    credential.last_login_at = datetime.now(UTC)
    await session.commit()
    logger.info("account login: user_id=%s", credential.user_id)
    return credential


# ── pairing sessions (§43, ADR 0015) ────────────────────────────────────


class PairingCodeInvalid(Exception):
    """Unknown or already-consumed code — both answer 404, no info leak."""


class PairingCodeExpired(Exception):
    """Live-shaped code past its TTL — 410 lets the wizard explain the retry."""


def generate_pairing_code() -> str:
    return "".join(
        _pairing_rng.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH)
    )


def hash_pairing_code(code: str) -> str:
    """Codes are compared normalized: strip + upper (users type lowercase)."""
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


async def create_pairing_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    ttl_minutes: int = PAIRING_TTL_MINUTES,
) -> tuple[PairingSession, str]:
    """Issue a single-use session; the plaintext code exists only in the return."""
    code = generate_pairing_code()
    pairing = PairingSession(
        user_id=user_id,
        code_hash=hash_pairing_code(code),
        code_hint=code[:4],
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    session.add(pairing)
    await session.commit()
    logger.info(
        "pairing session issued: session_id=%s user_id=%s ttl_minutes=%s",
        pairing.id,
        user_id,
        ttl_minutes,
    )
    return pairing, code


PairingStatusValue = Literal["pending", "consumed", "expired"]


def pairing_session_status(pairing: PairingSession, now: datetime) -> PairingStatusValue:
    """Derived, never stored-as-truth: consumed beats expired beats pending."""
    if pairing.status == "consumed":
        return "consumed"
    if pairing.expires_at < now:
        return "expired"
    return "pending"


async def confirm_pairing(
    session: AsyncSession, raw_code: str, device_name: str
) -> tuple[PairingSession, Device, str]:
    """Exchange a live code for a device + token; the token is returned ONCE."""
    code_hash = hash_pairing_code(raw_code)
    pairing = (
        await session.execute(
            select(PairingSession).where(PairingSession.code_hash == code_hash)
        )
    ).scalar_one_or_none()
    if pairing is None or pairing.status == "consumed":
        raise PairingCodeInvalid
    if pairing.expires_at < datetime.now(UTC):
        raise PairingCodeExpired

    now = datetime.now(UTC)
    device = Device(user_id=pairing.user_id, name=device_name, model=BOOTSTRAP_DEVICE_MODEL)
    session.add(device)
    await session.flush()  # device.id for the source + token rows below
    session.add(
        DataSource(
            device_id=device.id, provider=BOOTSTRAP_PROVIDER, collector=BOOTSTRAP_COLLECTOR
        )
    )
    token = mint_device_token_value()
    session.add(
        DeviceToken(
            device_id=device.id, token_hash=hash_device_token(token), scopes=list(DEVICE_SCOPES)
        )
    )
    pairing.status = "consumed"
    pairing.consumed_at = now
    pairing.device_id = device.id
    await session.commit()
    logger.info(
        "pairing session consumed: session_id=%s device_id=%s user_id=%s",
        pairing.id,
        device.id,
        pairing.user_id,
    )
    return pairing, device, token


# ── device tokens (§44-45, §123-124) ────────────────────────────────────


def mint_device_token_value() -> str:
    """sqt_dev_ + 43 base64url chars (32 random bytes) — contracts §ADR 0015."""
    return DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint_device_direct(
    session: AsyncSession, user_id: uuid.UUID, name: str, model: str = BOOTSTRAP_DEVICE_MODEL
) -> tuple[Device, str]:
    """ADR 0015 bootstrap path: create device + source + token in one transaction."""
    device = Device(user_id=user_id, name=name, model=model)
    session.add(device)
    await session.flush()
    session.add(
        DataSource(
            device_id=device.id, provider=BOOTSTRAP_PROVIDER, collector=BOOTSTRAP_COLLECTOR
        )
    )
    token = mint_device_token_value()
    session.add(
        DeviceToken(
            device_id=device.id, token_hash=hash_device_token(token), scopes=list(DEVICE_SCOPES)
        )
    )
    await session.commit()
    logger.info(
        "device token issued via bootstrap: device_id=%s user_id=%s", device.id, user_id
    )
    return device, token


# ── M1 single-user fallback (kept for the global-token path) ────────────


async def resolve_single_user_target(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Single-user M1: resolve the seeded user and device by query, never by
    hardcoded UUID (real pairing landed with M2, ADR 0015)."""
    user_id = (
        await session.execute(select(User.id).order_by(User.created_at).limit(1))
    ).scalar_one_or_none()
    device_id = (
        await session.execute(select(Device.id).order_by(Device.active_from).limit(1))
    ).scalar_one_or_none()
    if user_id is None or device_id is None:
        msg = "single-user M1 requires seeded identity.users and identity.devices rows"
        raise RuntimeError(msg)
    return user_id, device_id


__all__ = [
    "AccountJwtDep",
    "BOOTSTRAP_COLLECTOR",
    "BOOTSTRAP_DEVICE_MODEL",
    "BOOTSTRAP_PROVIDER",
    "JWT_ALGORITHM",
    "JWT_AUDIENCE",
    "SESSION_LIFETIME",
    "PairingCodeExpired",
    "PairingCodeInvalid",
    "account_exists",
    "authenticate",
    "confirm_pairing",
    "create_access_token",
    "create_pairing_session",
    "decode_access_token",
    "earliest_user_id",
    "generate_pairing_code",
    "hash_device_token",
    "hash_pairing_code",
    "hash_password",
    "jwt_secret",
    "mint_device_direct",
    "mint_device_token_value",
    "pairing_session_status",
    "register_account",
    "require_account_jwt",
    "resolve_single_user_target",
    "verify_password",
]
