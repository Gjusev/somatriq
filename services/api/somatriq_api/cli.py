"""Bootstrap CLI (ADR 0015): ``python -m somatriq_api.cli``.

Mints the same credential types the routers issue, through the same shared
service functions (accounts.py) — never a parallel auth path. Secrets are
printed exactly once to stdout (tokens/pairing codes); passwords are never
echoed back.

Stdlib argparse only (spec §161 dependency policy); output uses
sys.stdout.write because repo lint flags bare ``print`` outside scripts/.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from somatriq_contracts.pairing import PAIRING_TTL_MINUTES
from somatriq_db.engine import get_session_factory

from somatriq_api import accounts


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="somatriq-api",
        description="Somatriq bootstrap CLI: local account, device tokens, pairing sessions.",
    )
    subcommands = parser.add_subparsers(dest="group", required=True)

    account = subcommands.add_parser("account", help="local account (spec §122)")
    account_actions = account.add_subparsers(dest="action", required=True)
    register = account_actions.add_parser("register", help="register the single local account")
    register.add_argument("--username", required=True)
    register.add_argument("--password", required=True)
    account_actions.add_parser("status", help="whether an account exists")

    device_token = subcommands.add_parser("device-token", help="device credentials (ADR 0015)")
    token_actions = device_token.add_subparsers(dest="action", required=True)
    mint = token_actions.add_parser("mint", help="bootstrap: mint a device token directly")
    mint.add_argument("--name", required=True, help="device name, e.g. pixel-9")
    mint.add_argument("--model", default=accounts.BOOTSTRAP_DEVICE_MODEL)

    pairing = subcommands.add_parser("pairing-session", help="pairing sessions (spec §43)")
    pairing_actions = pairing.add_subparsers(dest="action", required=True)
    create = pairing_actions.add_parser("create", help="issue a single-use pairing code")
    create.add_argument("--ttl-minutes", type=int, default=PAIRING_TTL_MINUTES)

    return parser


async def _account_register(args: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        if await accounts.account_exists(session):
            _emit("an account already exists — registration is closed (spec §122)")
            return 1
        credential = await accounts.register_account(session, args.username, args.password)
        _emit(f"account registered: user_id={credential.user_id} username={credential.username}")
        return 0


async def _account_status(_: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        exists = await accounts.account_exists(session)
        _emit(f"has_account={str(exists).lower()}")
        return 0


async def _device_token_mint(args: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        user_id = await accounts.earliest_user_id(session)
        device, token = await accounts.mint_device_direct(
            session, user_id, args.name, args.model
        )
        _emit(f"device created: device_id={device.id} name={device.name} model={device.model}")
        _emit("device token (shown ONCE — store it in Android keystore-backed storage now):")
        _emit(token)
        return 0


async def _pairing_session_create(args: argparse.Namespace) -> int:
    async with get_session_factory()() as session:
        if not await accounts.account_exists(session):
            _emit("no account exists — register one first: account register")
            return 1
        user_id = await accounts.earliest_user_id(session)
        pairing, code = await accounts.create_pairing_session(
            session, user_id, ttl_minutes=args.ttl_minutes
        )
        _emit(
            f"pairing session: session_id={pairing.id} "
            f"expires_at={pairing.expires_at.isoformat()}"
        )
        _emit("pairing code (shown ONCE — render it as QR/manual entry now):")
        _emit(code)
        return 0


_Handler = Callable[[argparse.Namespace], Coroutine[Any, Any, int]]

_HANDLERS: dict[tuple[str, str], _Handler] = {
    ("account", "register"): _account_register,
    ("account", "status"): _account_status,
    ("device-token", "mint"): _device_token_mint,
    ("pairing-session", "create"): _pairing_session_create,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _HANDLERS[(args.group, args.action)]
    return asyncio.run(handler(args))


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
