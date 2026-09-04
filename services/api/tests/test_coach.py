"""Coach endpoint behavior: POST /api/v1/coach/ask (M8, spec §89-94; ADR 0009).

JWT guard, request validation, the CoachAnswer wire shape, honest empty-data
answers, numbers flowing from seeded data, and the per-user rate limit.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.coach import reset_coach_state
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

ASK = "/api/v1/coach/ask"
REGISTER = "/api/v1/auth/register"

_AI_ENV_KEYS = (
    "AI_PRIVACY_LEVEL",
    "AI_ALLOW_EXTERNAL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_coach_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No ambient AI configuration; fresh engine + empty rate buckets."""
    for key in _AI_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_coach_state()
    yield
    reset_coach_state()


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client sharing the test's event loop (asyncpg pools are loop-bound).

    Same pattern as test_auth: the global engine pool is drained on both
    ends so connections never cross loops.
    """
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


async def _register(api: httpx.AsyncClient) -> str:
    response = await api.post(
        REGISTER, json={"username": "local", "password": "correct-horse-battery"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@requires_db
async def test_ask_requires_account_jwt(api: httpx.AsyncClient) -> None:
    response = await api.post(ASK, json={"question": "How is my recovery today?"})
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


@requires_db
async def test_ask_honest_answer_on_empty_data(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    response = await api.post(
        ASK, json={"question": "How is my recovery today?"}, headers=_auth(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"answer", "provider", "privacy_level", "tools_used", "caveats"}
    assert body["provider"] == "deterministic"  # nothing configured → no LLM
    assert body["privacy_level"] == "local"
    assert body["tools_used"] == ["get_today"]
    assert "no data yet" in body["answer"]
    assert "deterministic summary (no LLM configured)" in body["answer"]
    assert body["caveats"] == []


@requires_db
async def test_ask_no_tool_question_is_honest(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    response = await api.post(
        ASK, json={"question": "What is the capital of France?"}, headers=_auth(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tools_used"] == []
    assert body["answer"].startswith("I have no data for that.")
    assert any("no coach tool matched" in caveat for caveat in body["caveats"])


@requires_db
async def test_ask_carries_seeded_numbers(
    api: httpx.AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: heart samples → deterministic tools → the answer text."""
    frozen = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr("somatriq_agent.coach._now", lambda: frozen)
    row = (
        await db.execute(
            text(
                "SELECT u.id, d.id FROM identity.users u "
                "JOIN identity.devices d ON d.user_id = u.id "
                "WHERE d.name = 'synthetic-01' LIMIT 1"
            )
        )
    ).one()
    user_id, device_id = row[0], row[1]
    base = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)
    await db.execute(
        text(
            "INSERT INTO timeseries.heart_rate "
            "(user_id, device_id, source_record_id, ts, bpm) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :bpm)"
        ),
        [
            {
                "user_id": user_id,
                "device_id": device_id,
                "source_record_id": f"coach-api-{i}",
                "ts": base + timedelta(minutes=i),
                "bpm": 60.0,
            }
            for i in range(150)
        ],
    )
    await db.commit()
    token = await _register(api)

    response = await api.post(
        ASK, json={"question": "How is my recovery today?"}, headers=_auth(token)
    )

    body = response.json()
    assert response.status_code == 200, body
    assert body["tools_used"] == ["get_today"]
    assert "resting HR 60 bpm" in body["answer"]


@requires_db
async def test_ask_validates_question_length(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    empty = await api.post(ASK, json={"question": ""}, headers=_auth(token))
    assert empty.status_code == 422
    long = await api.post(ASK, json={"question": "x" * 501}, headers=_auth(token))
    assert long.status_code == 422
    missing = await api.post(ASK, json={}, headers=_auth(token))
    assert missing.status_code == 422


@requires_db
async def test_ask_rate_limited_after_burst(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    statuses = [
        (
            await api.post(ASK, json={"question": "meaning of life?"}, headers=_auth(token))
        ).status_code
        for _ in range(10)
    ]
    assert statuses == [200] * 10

    limited = await api.post(ASK, json={"question": "meaning of life?"}, headers=_auth(token))
    assert limited.status_code == 429
    body = limited.json()
    assert body["error_code"] == "RETRYABLE"
    assert "retry" in body["message"]
