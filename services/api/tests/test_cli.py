"""Bootstrap CLI (ADR 0015): the CLI mints the same credential type the
routers issue, through the same shared service functions — verified by
feeding the printed token straight back into ingest.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import httpx
import pytest
from conftest import requires_db as _untyped_requires_db
from somatriq_api.cli import _device_token_mint, _pairing_session_create, build_parser
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound=Callable[..., object])
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

INGEST = "/api/v1/ingest/batches"
BASE_TS = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


@requires_db
async def test_cli_minted_token_authorizes_ingest(
    api: httpx.AsyncClient, db: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    args = build_parser().parse_args(["device-token", "mint", "--name", "cli-device"])
    assert await _device_token_mint(args) == 0

    out = capsys.readouterr().out
    assert "device created" in out
    token_lines = [line for line in out.splitlines() if line.startswith("sqt_dev_")]
    assert len(token_lines) == 1  # printed exactly once
    token = token_lines[0]

    body = {
        "batch_id": str(uuid.uuid4()),
        "schema_version": "1",
        "records": [
            {
                "source_record_id": f"cli-{uuid.uuid4().hex[:6]}",
                "ts": (BASE_TS + timedelta(seconds=1)).isoformat(),
                "bpm": 61.0,
            }
        ],
    }
    response = await api.post(INGEST, json=body, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text


@requires_db
async def test_cli_pairing_session_needs_account(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(["pairing-session", "create"])
    try:
        assert await _pairing_session_create(args) == 1
        assert "no account exists" in capsys.readouterr().out
    finally:
        # The handler opens its own session on this test's loop; drain the
        # pool so the loop-bound connection never reaches the next test.
        await get_engine().dispose(close=False)
