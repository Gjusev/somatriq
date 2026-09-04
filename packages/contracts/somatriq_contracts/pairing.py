"""Local account, pairing and device-credential wire contracts (ADR 0015).

Flow (spec §43, §180):

    web (first run)          collector (first run)
    ───────────────          ─────────────────────
    register account
    login → account JWT
    create pairing session
    show code/QR      ───►   confirm code
    poll status              receive device token
                            (scopes: ingest.write device.read sync.read)

The device token authorizes ingest; it never grants admin, arbitrary
health reads, other-user access, or database access (spec §44).
"""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,62}[a-z0-9])?$")

# Unambiguous alphabet: no 0/O/1/I. 8 chars, no separators.
PAIRING_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PAIRING_CODE_LENGTH = 8
PAIRING_TTL_MINUTES = 10

DEVICE_SCOPES = ("ingest.write", "device.read", "sync.read")

# Device token wire format: sqt_dev_ + 43 base64url chars (32 random bytes).
DEVICE_TOKEN_PREFIX = "sqt_dev_"


class AuthStatus(BaseModel):
    """GET /api/v1/auth/status — drives the first-run wizard branch (§180)."""

    has_account: bool


class RegisterRequest(BaseModel):
    """POST /api/v1/auth/register — allowed only while no account exists."""

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username")
    @classmethod
    def _username_shape(cls, value: str) -> str:
        if not USERNAME_RE.match(value):
            msg = "username: 3-64 chars, lowercase letters/digits/._-, not starting/ending with punctuation"
            raise ValueError(msg)
        return value


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    """Account session token (JWT, HS256, 12h) for the web app."""

    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class PairingSessionResponse(BaseModel):
    """POST /api/v1/pairing/sessions (account JWT).

    pairing_code is returned ONCE, at creation; only its hash is stored.
    """

    session_id: UUID
    pairing_code: str
    expires_at: datetime

    @field_validator("pairing_code")
    @classmethod
    def _code_shape(cls, value: str) -> str:
        if len(value) != PAIRING_CODE_LENGTH or any(
            c not in PAIRING_CODE_ALPHABET for c in value
        ):
            msg = f"pairing_code must be {PAIRING_CODE_LENGTH} chars from the unambiguous alphabet"
            raise ValueError(msg)
        return value


class PairedDeviceInfo(BaseModel):
    device_id: UUID
    name: str
    model: str | None = None


class PairingStatus(BaseModel):
    """GET /api/v1/pairing/sessions/{session_id} (account JWT) — wizard polling.

    Only the code's hash is stored, so the full code travels solely in the
    creation response; the hint (first 4 chars) keeps the polling UI legible.
    """

    session_id: UUID
    pairing_code_hint: str = Field(min_length=4, max_length=4)
    status: Literal["pending", "consumed", "expired"]
    created_at: datetime
    expires_at: datetime
    device: PairedDeviceInfo | None = None


class PairingConfirmRequest(BaseModel):
    """POST /api/v1/pairing/confirm — possession of the live code is the proof."""

    pairing_code: str = Field(min_length=8, max_length=8)
    device_name: str = Field(min_length=1, max_length=100)


class PairingConfirmResponse(BaseModel):
    device_id: UUID
    token: str  # shown once; store in Android keystore-backed storage
    token_type: str = "device"
    scopes: list[str]
    issued_at: datetime
    expires_at: datetime | None = None


class DeviceInfo(BaseModel):
    """GET /api/v1/devices (account JWT)."""

    device_id: UUID
    name: str
    model: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    token_expires_at: datetime | None = None
    revoked_at: datetime | None = None
