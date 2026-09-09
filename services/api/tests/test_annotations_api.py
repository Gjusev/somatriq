"""Annotation CRUD behavior (Block 3; grill P13): the owner's mutable
narrative — create/edit/delete, explicit null clears the range, foreign
ids are 404, blank titles and inverted ranges are 422, no auth is 401.
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db
from sqlalchemy.ext.asyncio import AsyncSession

ANNOTATIONS = "/api/v1/annotations"


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: httpx.AsyncClient) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@requires_db
async def test_crud_roundtrip_with_range_clearing(api: httpx.AsyncClient) -> None:
    headers = await _register(api)

    created = await api.post(
        ANNOTATIONS,
        json={
            "date_from": "2026-08-01",
            "date_to": "2026-08-07",
            "title": "Beach week",
            "note": "travel, heat, poor sleep",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    annotation = created.json()
    assert annotation["date_to"] == "2026-08-07"
    annotation_id = annotation["id"]

    listed = await api.get(ANNOTATIONS, headers=headers)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [annotation_id]

    windowed = await api.get(
        ANNOTATIONS,
        params={"from_date": "2026-09-01", "to_date": "2026-09-30"},
        headers=headers,
    )
    assert windowed.json() == []  # filter is on the anchor date

    edited = await api.patch(
        f"{ANNOTATIONS}/{annotation_id}",
        json={"title": "Beach week (edited)"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Beach week (edited)"
    assert edited.json()["date_to"] == "2026-08-07"  # untouched field

    cleared = await api.patch(
        f"{ANNOTATIONS}/{annotation_id}", json={"date_to": None}, headers=headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["date_to"] is None  # explicit null clears the range

    removed = await api.delete(f"{ANNOTATIONS}/{annotation_id}", headers=headers)
    assert removed.status_code == 204, removed.text
    gone = await api.delete(f"{ANNOTATIONS}/{annotation_id}", headers=headers)
    assert gone.status_code == 404, gone.text


@requires_db
async def test_validation_and_ownership(api: httpx.AsyncClient) -> None:
    headers = await _register(api)

    inverted = await api.post(
        ANNOTATIONS,
        json={"date_from": "2026-08-07", "date_to": "2026-08-01", "title": "x"},
        headers=headers,
    )
    assert inverted.status_code == 422, inverted.text

    blank = await api.post(
        ANNOTATIONS, json={"date_from": "2026-08-01", "title": "  "}, headers=headers
    )
    assert blank.status_code == 422, blank.text

    foreign = await api.patch(f"{ANNOTATIONS}/{uuid.uuid4()}", json={"title": "y"}, headers=headers)
    assert foreign.status_code == 404, foreign.text

    unauthenticated = await api.get(ANNOTATIONS)
    assert unauthenticated.status_code == 401, unauthenticated.text
