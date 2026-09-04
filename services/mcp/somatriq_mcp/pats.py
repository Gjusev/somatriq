"""Personal access tokens for the health MCP (spec §123, §97; ADR 0010).

Mirrors the device-token security invariants of somatriq_api.accounts (which
this service cannot import — separate deployment unit): the raw token is
``sqt_pat_`` + 43 base64url chars (32 random bytes), only its sha256 digest
is stored, and the plaintext value exists exactly once at mint time.

All tools require the ``health.read`` scope (spec §97 read-only default;
write scopes additionally need the per-connection grant gate of ADR 0010,
which ships with the OAuth increment).
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from somatriq_db.models import PersonalAccessToken, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

PAT_TOKEN_PREFIX = "sqt_pat_"
PAT_DEFAULT_SCOPES = ("health.read",)
HEALTH_READ_SCOPE = "health.read"


class PatInvalid(Exception):
    """Unknown token or past expiry — both answer a flat 401, no info leak."""


class PatRevoked(Exception):
    """Revoked credential — 403, distinguishable so the UI can say 'revoked'."""


class PatScopeMissing(Exception):
    """Token is live but lacks a required scope — 403."""

    def __init__(self, required_scope: str) -> None:
        self.required_scope = required_scope
        super().__init__(f"token lacks required scope {required_scope!r}")


def mint_pat_value() -> str:
    """sqt_pat_ + 43 base64url chars (32 random bytes), same shape as sqt_dev_."""
    return PAT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_pat(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class MintedPat:
    row: PersonalAccessToken
    token: str  # plaintext — exists only in this return value


async def earliest_user_id(session: AsyncSession) -> uuid.UUID:
    """The seeded single local user (same resolution order as the api CLI)."""
    user_id = (
        await session.execute(select(User.id).order_by(User.created_at).limit(1))
    ).scalar_one_or_none()
    if user_id is None:
        msg = "no identity.users row: run migrations before minting (spec §122)"
        raise RuntimeError(msg)
    return user_id


async def mint_pat(
    session: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    scopes: tuple[str, ...] = PAT_DEFAULT_SCOPES,
    expires_days: int | None = None,
) -> MintedPat:
    """Issue a PAT; the plaintext token is returned ONCE and never logged."""
    token = mint_pat_value()
    row = PersonalAccessToken(
        user_id=user_id,
        name=name,
        token_hash=hash_pat(token),
        scopes=list(scopes),
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
        ),
    )
    session.add(row)
    await session.commit()
    logger.info("PAT issued: pat_id=%s user_id=%s name=%s scopes=%s", row.id, user_id, name, scopes)
    return MintedPat(row=row, token=token)


@dataclass(frozen=True)
class VerifiedPat:
    row: PersonalAccessToken
    user_id: uuid.UUID
    scopes: tuple[str, ...]


async def verify_pat(
    session: AsyncSession, token: str, required_scope: str = HEALTH_READ_SCOPE
) -> VerifiedPat:
    """Hash lookup + revoked/expired checks + scope enforcement (ADR 0010).

    Raises :class:`PatInvalid` (401), :class:`PatRevoked` or
    :class:`PatScopeMissing` (403). ``last_used_at`` is bumped on success —
    best-effort, never blocking the authenticated call.
    """
    row = (
        await session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.token_hash == hash_pat(token)
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or (row.expires_at is not None and row.expires_at < now):
        raise PatInvalid
    if row.revoked_at is not None:
        raise PatRevoked
    if required_scope not in row.scopes:
        raise PatScopeMissing(required_scope)
    row.last_used_at = now
    await session.commit()
    return VerifiedPat(row=row, user_id=row.user_id, scopes=tuple(row.scopes))


__all__ = [
    "HEALTH_READ_SCOPE",
    "MintedPat",
    "PAT_DEFAULT_SCOPES",
    "PAT_TOKEN_PREFIX",
    "PatInvalid",
    "PatRevoked",
    "PatScopeMissing",
    "VerifiedPat",
    "earliest_user_id",
    "hash_pat",
    "mint_pat",
    "mint_pat_value",
    "verify_pat",
]
