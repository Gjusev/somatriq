"""POST /api/v1/coach/ask — the AI coach surface (M8, spec §89-94; ADR 0009).

Account-JWT-guarded (the web session audience, ADR 0015). The endpoint is a
thin shim: deterministic tool selection, the privacy chokepoint and the
provider call all live in somatriq_agent (the coach engine); nothing here
touches health math.

Rate limiting is a small in-memory per-user token bucket (10 asks/min) —
single-user deployment, no shared state needed (spec §161: only real needs).
A 429 carries the contract's RETRYABLE code so clients back off.
"""

import threading
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from somatriq_agent.coach import build_engine
from somatriq_agent.providers import ExternalProviderRefused, ProviderError
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError

router = APIRouter(prefix="/api/v1/coach", tags=["coach"])

QUESTION_MAX_LENGTH = 500

# Token bucket: burst of 10 asks, refilled at 10 per minute.
RATE_CAPACITY = 10.0
RATE_REFILL_PER_SECOND = RATE_CAPACITY / 60.0


class CoachAskRequest(BaseModel):
    """One coach question (spec §90): bounded, plain text."""

    question: str = Field(min_length=1, max_length=QUESTION_MAX_LENGTH)


class CoachAskResponse(BaseModel):
    """The coach answer plus full provenance (§99 spirit): which deterministic
    tools answered, through which provider, at which privacy level."""

    answer: str
    provider: str
    privacy_level: str
    tools_used: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class _Bucket:
    __slots__ = ("tokens", "updated_at")

    def __init__(self, tokens: float, updated_at: float) -> None:
        self.tokens = tokens
        self.updated_at = updated_at


_buckets: dict[uuid.UUID, _Bucket] = {}
_lock = threading.Lock()


def _consume(user_id: uuid.UUID, now: float) -> float | None:
    """Take one token; None when allowed, else seconds until the next one."""
    with _lock:
        bucket = _buckets.get(user_id)
        if bucket is None:
            _buckets[user_id] = _Bucket(RATE_CAPACITY - 1.0, now)
            return None
        elapsed = max(now - bucket.updated_at, 0.0)
        tokens = min(RATE_CAPACITY, bucket.tokens + elapsed * RATE_REFILL_PER_SECOND)
        bucket.tokens = tokens
        bucket.updated_at = now
        if tokens >= 1.0:
            bucket.tokens = tokens - 1.0
            return None
        return (1.0 - tokens) / RATE_REFILL_PER_SECOND


def reset_coach_state() -> None:
    """Test hook: clear the rate buckets and the cached engine."""
    with _lock:
        _buckets.clear()
    build_engine.cache_clear()


@router.post("/ask", response_model=CoachAskResponse)
async def ask(
    body: CoachAskRequest,
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CoachAskResponse:
    """Answer one health question from deterministic tools + one LLM call."""
    retry_after = _consume(user_id, time.monotonic())
    if retry_after is not None:
        raise ApiError(
            status_code=429,
            code=ErrorCode.RETRYABLE,
            message=f"coach rate limit exceeded; retry in {retry_after:.0f}s",
        )

    try:
        engine = build_engine()
    except ExternalProviderRefused as exc:
        raise ApiError(
            status_code=503, code=ErrorCode.PERMANENT, message=str(exc)
        ) from None

    try:
        result = await engine.answer(body.question, user_id, session)
    except ProviderError as exc:
        raise ApiError(
            status_code=503, code=ErrorCode.RETRYABLE, message=f"AI provider unavailable: {exc}"
        ) from None

    return CoachAskResponse(
        answer=result.answer,
        provider=result.provider,
        privacy_level=result.privacy_level,
        tools_used=result.tools_used,
        caveats=result.caveats,
    )
