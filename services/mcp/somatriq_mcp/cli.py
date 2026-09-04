"""MCP bootstrap CLI (ADR 0010, spec §123): ``python -m somatriq_mcp.cli``.

Mints the credential the MCP server itself verifies — through the same
service functions the request path uses (pats.py), never a parallel auth
path. The raw token is printed exactly once to stdout; only its sha256 is
stored (spec §123). Mirrors the argparse/stdout style of somatriq_api.cli.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from somatriq_db.engine import get_session_factory

from . import pats


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="somatriq-mcp",
        description="Somatriq MCP CLI: personal access tokens for AI clients (ADR 0010).",
    )
    subcommands = parser.add_subparsers(dest="group", required=True)

    pat = subcommands.add_parser("pat", help="personal access tokens (spec §123)")
    pat_actions = pat.add_subparsers(dest="action", required=True)
    mint = pat_actions.add_parser("mint", help="mint a PAT; the token is shown ONCE")
    mint.add_argument("--name", required=True, help="token name, e.g. laptop-claude")
    mint.add_argument(
        "--scopes",
        default=",".join(pats.PAT_DEFAULT_SCOPES),
        help="comma-separated scopes (default: health.read — spec §97 read-only default)",
    )
    mint.add_argument(
        "--expires-days",
        type=int,
        default=None,
        help="optional expiry in days (default: no expiry)",
    )

    return parser


async def _pat_mint(args: argparse.Namespace) -> int:
    scopes = tuple(s.strip() for s in args.scopes.split(",") if s.strip())
    if not scopes:
        _emit("at least one scope is required (e.g. health.read)")
        return 1
    async with get_session_factory()() as session:
        user_id = await pats.earliest_user_id(session)
        minted = await pats.mint_pat(
            session,
            user_id,
            args.name,
            scopes=scopes,
            expires_days=args.expires_days,
        )
    _emit(f"personal access token created: pat_id={minted.row.id} name={minted.row.name}")
    _emit(f"scopes={','.join(minted.row.scopes)} user_id={user_id}")
    _emit("token (shown ONCE — configure your MCP client's Authorization header now):")
    _emit(minted.token)
    return 0


_Handler = Callable[[argparse.Namespace], Coroutine[Any, Any, int]]

_HANDLERS: dict[tuple[str, str], _Handler] = {
    ("pat", "mint"): _pat_mint,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _HANDLERS[(args.group, args.action)]
    return asyncio.run(handler(args))


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
