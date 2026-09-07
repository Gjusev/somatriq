"""Local account auth: status, register, login, JWT guards (ADR 0015, §122).

Single local account: register only while zero accounts exist; the account
JWT (HS256, 12h, aud=somatriq-web) guards the web surfaces and is never
accepted by ingest.
"""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import jwt as pyjwt
import pytest
from somatriq_api.main import app
from somatriq_api.settings import get_settings
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

STATUS = "/api/v1/auth/status"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
CHANGE_PASSWORD = "/api/v1/auth/change-password"
DEVICES = "/api/v1/devices"


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI client sharing the test's event loop (asyncpg pools are loop-bound).

    The engine pool is drained on both ends: connections opened on this
    test's loop must not leak into the next test's loop.
    """
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


async def _register(
    api: httpx.AsyncClient, username: str = "local", password: str = "correct-horse-battery"
) -> dict[str, object]:
    response = await api.post(REGISTER, json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _auth(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@requires_db
async def test_status_false_before_any_account(api: httpx.AsyncClient) -> None:
    assert (await api.get(STATUS)).json() == {"has_account": False}


@requires_db
async def test_register_login_roundtrip(api: httpx.AsyncClient) -> None:
    registered = await _register(api)
    assert registered["token_type"] == "bearer"
    assert registered["access_token"]
    # 12h session expiry (ADR 0015)
    expires_at = datetime.fromisoformat(str(registered["expires_at"]))
    delta = expires_at - datetime.now(UTC)
    assert timedelta(hours=11, minutes=55) < delta <= timedelta(hours=12)

    assert (await api.get(STATUS)).json() == {"has_account": True}

    login = await api.post(LOGIN, json={"username": "local", "password": "correct-horse-battery"})
    assert login.status_code == 200
    assert login.json()["access_token"]


@requires_db
async def test_double_register_conflicts(api: httpx.AsyncClient) -> None:
    await _register(api)
    again = await api.post(
        REGISTER, json={"username": "second", "password": "another-long-password"}
    )
    assert again.status_code == 409
    assert again.json()["error_code"] == "ACCOUNT_EXISTS"


@requires_db
async def test_bad_login_rejected(api: httpx.AsyncClient) -> None:
    await _register(api, password="correct-horse-battery")
    for username, password in (
        ("local", "wrong-password-entirely"),
        ("ghost", "correct-horse-battery"),
    ):
        response = await api.post(LOGIN, json={"username": username, "password": password})
        assert response.status_code == 401
        assert response.json()["error_code"] == "INVALID_CREDENTIALS"


@requires_db
async def test_account_jwt_guards_web_endpoints(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    no_auth = await api.get(DEVICES)
    assert no_auth.status_code == 401
    assert no_auth.json()["error_code"] == "AUTHENTICATION"

    authorized = await api.get(DEVICES, headers=_auth(token["access_token"]))
    assert authorized.status_code == 200


@requires_db
async def test_wrong_audience_jwt_rejected(api: httpx.AsyncClient) -> None:
    """A JWT minted for another audience never authorizes web surfaces."""
    await _register(api)
    secret = get_settings().secret_key or "dev-secret"
    forged = pyjwt.encode(
        {
            "sub": "00000000-0000-4000-8000-000000000001",
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "aud": "someone-else",
        },
        secret,
        algorithm="HS256",
    )
    response = await api.get(DEVICES, headers=_auth(forged))
    assert response.status_code == 401


@requires_db
async def test_expired_jwt_rejected(api: httpx.AsyncClient) -> None:
    await _register(api)
    secret = get_settings().secret_key or "dev-secret"
    expired = pyjwt.encode(
        {
            "sub": "00000000-0000-4000-8000-000000000001",
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "aud": "somatriq-web",
        },
        secret,
        algorithm="HS256",
    )
    response = await api.get(DEVICES, headers=_auth(expired))
    assert response.status_code == 401


@requires_db
async def test_change_password_happy_path(api: httpx.AsyncClient) -> None:
    """Old passphrase stops working, new one signs in, live JWTs stay valid."""
    token = await _register(api, password="correct-horse-battery")

    changed = await api.post(
        CHANGE_PASSWORD,
        headers=_auth(token["access_token"]),
        json={
            "current_password": "correct-horse-battery",
            "new_password": "staple-hummingbird-42",
        },
    )
    assert changed.status_code == 204

    old_login = await api.post(
        LOGIN, json={"username": "local", "password": "correct-horse-battery"}
    )
    assert old_login.status_code == 401

    new_login = await api.post(
        LOGIN, json={"username": "local", "password": "staple-hummingbird-42"}
    )
    assert new_login.status_code == 200
    assert new_login.json()["access_token"]

    # Session JWTs are independent of the passphrase (ADR 0015): the one
    # minted before the change keeps authorizing web surfaces.
    assert (await api.get(DEVICES, headers=_auth(token["access_token"]))).status_code == 200


@requires_db
async def test_change_password_wrong_current_401(api: httpx.AsyncClient) -> None:
    token = await _register(api, password="correct-horse-battery")
    response = await api.post(
        CHANGE_PASSWORD,
        headers=_auth(token["access_token"]),
        json={
            "current_password": "wrong-password-entirely",
            "new_password": "staple-hummingbird-42",
        },
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_CREDENTIALS"
    # Nothing changed: the original passphrase still signs in.
    again = await api.post(LOGIN, json={"username": "local", "password": "correct-horse-battery"})
    assert again.status_code == 200


@requires_db
async def test_change_password_short_new_422(api: httpx.AsyncClient) -> None:
    token = await _register(api, password="correct-horse-battery")
    response = await api.post(
        CHANGE_PASSWORD,
        headers=_auth(token["access_token"]),
        json={"current_password": "correct-horse-battery", "new_password": "short"},
    )
    assert response.status_code == 422


@requires_db
async def test_change_password_requires_account_jwt(api: httpx.AsyncClient) -> None:
    await _register(api, password="correct-horse-battery")
    response = await api.post(
        CHANGE_PASSWORD,
        json={
            "current_password": "correct-horse-battery",
            "new_password": "staple-hummingbird-42",
        },
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


def test_jwt_secret_production_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """No SECRET_KEY in production refuses to mint; dev falls back explicitly."""
    from somatriq_api.accounts import jwt_secret

    monkeypatch.setenv("SOMATRIQ_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            jwt_secret()

        monkeypatch.setenv("SOMATRIQ_ENV", "development")
        get_settings.cache_clear()
        assert jwt_secret() == "dev-secret"
    finally:
        get_settings.cache_clear()
