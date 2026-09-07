"""CLI mint → server verify round trip (spec §123: shown once, hashed at rest).

The handler coroutine runs on the test's event loop (cli.main would wrap it
in a fresh asyncio.run loop while the shared engine pools connections on this
test's loop); argparse wiring is covered by parsing the same argv first.
"""

import re
from typing import cast

import pytest
from helpers import requires_db as _helpers_requires_db
from somatriq_mcp import cli
from sqlalchemy.ext.asyncio import AsyncSession

requires_db = cast(pytest.MarkDecorator, _helpers_requires_db)

_TOKEN_LINE_RE = re.compile(r"^(sqt_pat_[A-Za-z0-9_-]{43})$", re.MULTILINE)


async def _mint(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    args = cli.build_parser().parse_args(argv)
    assert await cli._pat_mint(args) == 0
    out = capsys.readouterr().out
    match = _TOKEN_LINE_RE.search(out)
    assert match, f"raw token line not printed exactly once in:\n{out}"
    assert len(_TOKEN_LINE_RE.findall(out)) == 1
    return match.group(1)


@requires_db
async def test_pat_mint_prints_token_once_and_verifies(
    db: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    from somatriq_mcp import pats

    token = await _mint(["pat", "mint", "--name", "laptop-claude"], capsys)

    verified = await pats.verify_pat(db, token)
    assert verified.row.name == "laptop-claude"
    assert verified.scopes == ("health.read",)  # §97 read-only default


@requires_db
async def test_pat_mint_custom_scopes_and_expiry(
    db: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    from somatriq_mcp import pats

    token = await _mint(
        [
            "pat",
            "mint",
            "--name",
            "scoped",
            "--scopes",
            "health.read,other.read",
            "--expires-days",
            "30",
        ],
        capsys,
    )

    verified = await pats.verify_pat(db, token)
    assert set(verified.scopes) == {"health.read", "other.read"}
    assert verified.row.expires_at is not None
