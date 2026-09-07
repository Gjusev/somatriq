"""M13 import surface (spec §129) + connector registry (§126).

Preview shows the exact §129 fields before anything is written; commit is
bound to the previewed content by an HMAC token, drives the EXISTING
daily-observations ingest path, and is idempotent by content hash (same
file → same deterministic batch UUID → replay, all duplicates).
"""

import base64
import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.imports import PREVIEW_TTL_SECONDS, sign_preview_token
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound="Callable[..., object]")
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

PREVIEW = "/api/v1/imports/preview"
COMMIT = "/api/v1/imports/commit"
CONNECTORS = "/api/v1/connectors"
REGISTER = "/api/v1/auth/register"

CSV = "date,weight_kg,steps,mood\n2026-01-05,80.0,9000,fine\n2026-01-06,80.4,9500,fine\n"
CSV_WITH_DUP = "date,weight_kg\n2026-01-05,80.0\n2026-01-05,81.0\n2026-01-06,82.0\n"


def _b64(csv_text: str) -> str:
    return base64.b64encode(csv_text.encode()).decode()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _dispose_engine_pool() -> AsyncIterator[None]:
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
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


async def _preview(api: httpx.AsyncClient, token: str, csv_text: str) -> httpx.Response:
    return await api.post(
        PREVIEW,
        json={"filename": "scale-export.csv", "content_b64": _b64(csv_text)},
        headers=_auth(token),
    )


# ── auth: imports are owner operations ────────────────────────────────────


@requires_db
async def test_preview_requires_account_jwt(api: httpx.AsyncClient) -> None:
    response = await api.post(PREVIEW, json={"content_b64": _b64(CSV)})
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


@requires_db
async def test_commit_requires_account_jwt(api: httpx.AsyncClient) -> None:
    response = await api.post(COMMIT, json={"content_b64": _b64(CSV), "preview_token": "1.abc"})
    assert response.status_code == 401


@requires_db
async def test_connectors_requires_account_jwt(api: httpx.AsyncClient) -> None:
    response = await api.get(CONNECTORS)
    assert response.status_code == 401


# ── preview: the exact §129 fields ───────────────────────────────────────


@requires_db
async def test_preview_carries_the_spec129_fields(api: httpx.AsyncClient) -> None:
    token = await _register(api)
    response = await _preview(api, token, CSV_WITH_DUP)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "filename",
        "decoder_version",
        "date_range",
        "metrics",
        "record_count",
        "duplicates",
        "validation_warnings",
        "validation_warning_count",
        "skipped_columns",
        "preview_token",
        "expires_at",
    }
    assert body["filename"] == "scale-export.csv"
    assert body["decoder_version"] == "csv-import/1"
    assert body["date_range"] == {"first_day": "2026-01-05", "last_day": "2026-01-06"}
    assert body["metrics"] == [{"metric": "weight_kg", "count": 2}]
    assert body["record_count"] == 2
    assert body["duplicates"] == {"in_file": 1, "already_present": 0}
    assert body["validation_warning_count"] == 1
    assert body["validation_warnings"] == [
        {"line": 3, "reason": "duplicate (2026-01-05, weight_kg) — last value wins"}
    ]
    assert body["skipped_columns"] == []
    assert len(body["preview_token"].split(".")) == 2
    expires = datetime.fromisoformat(body["expires_at"])
    assert expires - datetime.now(UTC) <= timedelta(seconds=PREVIEW_TTL_SECONDS + 5)


@requires_db
async def test_preview_counts_unknown_columns_and_pairs_already_in_db(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    token = await _register(api)
    user_id, device_id = (
        await db.execute(
            text(
                "SELECT (SELECT id FROM identity.users ORDER BY created_at LIMIT 1), "
                "(SELECT id FROM identity.devices ORDER BY active_from LIMIT 1)"
            )
        )
    ).one()
    await db.execute(
        text(
            "INSERT INTO health.daily_observations "
            "(user_id, device_id, day, metric, value, decoder_version) "
            "VALUES (:u, :d, '2026-01-05', 'weight_kg', 79.0, 'noop-android/6')"
        ),
        {"u": user_id, "d": device_id},
    )
    await db.commit()

    response = await _preview(api, token, CSV)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped_columns"] == ["mood"]
    assert body["duplicates"] == {"in_file": 0, "already_present": 1}


@requires_db
async def test_preview_rejects_empty_garbage_and_bad_base64(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    empty = await api.post(PREVIEW, json={"content_b64": _b64("   \n")}, headers=_auth(token))
    assert empty.status_code == 422
    assert "empty" in empty.json()["message"]

    garbage = await api.post(
        PREVIEW, json={"content_b64": _b64("no header at all")}, headers=_auth(token)
    )
    assert garbage.status_code == 422
    assert "date" in garbage.json()["message"]

    bad_b64 = await api.post(
        PREVIEW, json={"content_b64": "!!!not-base64!!!"}, headers=_auth(token)
    )
    assert bad_b64.status_code == 422


# ── commit: content-bound token, reused ingest path, idempotency ─────────


@requires_db
async def test_commit_imports_through_the_ingest_path(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    token = await _register(api)
    preview = (await _preview(api, token, CSV)).json()

    response = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": preview["preview_token"]},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ack"]["accepted"] is True
    assert body["ack"]["records_received"] == 4
    assert body["ack"]["records_inserted"] == 4
    assert body["ack"]["records_duplicate"] == 0
    assert body["validation_warnings"] == [{"line": 1, "reason": "unknown columns skipped: mood"}]

    rows = (
        await db.execute(
            text(
                "SELECT day, metric, value, decoder_version FROM health.daily_observations "
                "ORDER BY day, metric"
            )
        )
    ).all()
    assert [(str(r[0]), r[1], r[2], r[3]) for r in rows] == [
        ("2026-01-05", "steps", 9000.0, "csv-import/1"),
        ("2026-01-05", "weight_kg", 80.0, "csv-import/1"),
        ("2026-01-06", "steps", 9500.0, "csv-import/1"),
        ("2026-01-06", "weight_kg", 80.4, "csv-import/1"),
    ]


@requires_db
async def test_recommit_of_the_same_file_is_all_duplicates(
    api: httpx.AsyncClient, db: AsyncSession
) -> None:
    token = await _register(api)
    preview = (await _preview(api, token, CSV)).json()
    first = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": preview["preview_token"]},
        headers=_auth(token),
    )
    assert first.status_code == 200

    # Fresh preview (new token, same content) — the deterministic batch UUID
    # replays the original batch instead of writing a second copy.
    second_preview = (await _preview(api, token, CSV)).json()
    second = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": second_preview["preview_token"]},
        headers=_auth(token),
    )
    assert second.status_code == 200
    ack = second.json()["ack"]
    assert ack["records_inserted"] == 0
    assert ack["records_duplicate"] == 4
    assert "duplicate batch replay" in ack["warnings"]

    count = (await db.execute(text("SELECT count(*) FROM health.daily_observations"))).scalar_one()
    assert count == 4


@requires_db
async def test_commit_rejects_token_bound_to_different_content(api: httpx.AsyncClient) -> None:
    token = await _register(api)
    other_preview = (await _preview(api, token, "date,steps\n2026-02-01,1000\n")).json()

    response = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": other_preview["preview_token"]},
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert "does not match" in response.json()["message"]


@requires_db
async def test_commit_rejects_expired_and_malformed_tokens(api: httpx.AsyncClient) -> None:
    token = await _register(api)

    expired = sign_preview_token(_sha(CSV), datetime.now(UTC) - timedelta(seconds=1))
    expired_response = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": expired},
        headers=_auth(token),
    )
    assert expired_response.status_code == 422
    assert "expired" in expired_response.json()["message"]

    malformed = await api.post(
        COMMIT,
        json={"content_b64": _b64(CSV), "preview_token": "not-a-token"},
        headers=_auth(token),
    )
    assert malformed.status_code == 422
    assert "malformed" in malformed.json()["message"]


@requires_db
async def test_commit_rejects_file_with_no_importable_rows(api: httpx.AsyncClient) -> None:
    token = await _register(api)
    csv_all_bad = "date,weight_kg\nnot-a-date,80.0\n"
    preview = (await _preview(api, token, csv_all_bad)).json()
    response = await api.post(
        COMMIT,
        json={"content_b64": _b64(csv_all_bad), "preview_token": preview["preview_token"]},
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert "no importable rows" in response.json()["message"]


# ── connector registry ───────────────────────────────────────────────────


@requires_db
async def test_connectors_lists_registry_with_honest_health(api: httpx.AsyncClient) -> None:
    token = await _register(api)
    response = await api.get(CONNECTORS, headers=_auth(token))
    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()["connectors"]}
    assert set(entries) == {"csv", "withings", "garmin", "oura", "health_connect"}
    assert entries["csv"] == {"name": "csv", "status": "available", "reason": None}
    for name in ("withings", "garmin", "oura", "health_connect"):
        assert entries[name]["status"] == "not_configured"
        assert "credentials not provided" in entries[name]["reason"]
        assert "§206" in entries[name]["reason"]


# ── helpers ──────────────────────────────────────────────────────────────


def _sha(csv_text: str) -> str:
    return hashlib.sha256(csv_text.encode()).hexdigest()
