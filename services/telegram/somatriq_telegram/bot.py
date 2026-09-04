"""Bot command handlers + long-poll loop (spec §101-104; grill decision on
partial data: the brief always carries markers).

Command surface (M7):

* /start — claim the owner binding. The FIRST chat writes the telegram
  notification channel for the seeded user; any DIFFERENT chat is rejected
  (single-owner system; UNIQUE(user, kind, target) makes takeover
  impossible even under races).
* /brief — immediate morning brief over the shared TODAY assembler.
* /status — freshness + coverage summary.
* /log <text> — deterministic quick-log (parser.py; quantity never invented).
* /help — command list.

Privacy: log lines never contain the token or chat ids at info level (spec
§45, §221) — the logger records commands and outcomes only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_analytics.brief import build_morning_brief
from somatriq_analytics.today_data import assemble_today
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .parser import ParsedLog, parse_quick_log
from .telegram_client import TelegramClient, TelegramError

log = logging.getLogger("somatriq_telegram.bot")

HELP_TEXT = (
    "Commands:\n"
    "/start — bind this chat to Somatriq (first chat wins)\n"
    "/brief — morning brief now\n"
    "/status — data freshness and coverage\n"
    "/log <text> — quick log (e.g. /log coffee, /log coffee 2)\n"
    "/help — this message"
)

_MAX_MESSAGE_LEN = 4000  # Telegram hard limit 4096; keep headroom


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class UpdateReply:
    """A reply the loop should send (chat id kept out of every log line)."""

    chat_id: int | str
    text: str


@dataclass(frozen=True)
class BindingDecision:
    """Pure /start decision over the existing telegram bindings."""

    action: str  # "bind" | "already" | "rejected"
    reply: str


def decide_binding(existing_targets: list[str], chat_id: str) -> BindingDecision:
    """First chat claims the owner binding; a different chat is rejected."""
    if not existing_targets:
        return BindingDecision(
            "bind",
            "Bound. This chat now receives the morning brief.\n" + HELP_TEXT,
        )
    if chat_id in existing_targets:
        return BindingDecision("already", "This chat is already bound.")
    return BindingDecision("rejected", "This bot is already bound to another chat.")


def parse_command(message_text: str) -> tuple[str, str] | None:
    """/command@botname argument -> (command, argument); None when not a command."""
    if not message_text.startswith("/"):
        return None
    parts = message_text.split(maxsplit=1)
    command = parts[0][1:].split("@")[0].lower()
    if not command:
        return None
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument


# ── DB helpers (thin SQL; the assembler owns the heavy reads) ─────────────


async def _owner_user_id(session: AsyncSession) -> uuid.UUID | None:
    result = await session.execute(
        text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1")
    )
    return result.scalar_one_or_none()


async def _telegram_targets(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    result = await session.execute(
        text(
            "SELECT target FROM notifications.channels "
            "WHERE user_id = :user_id AND kind = 'telegram'"
        ),
        {"user_id": user_id},
    )
    return [str(row[0]) for row in result.all()]


async def bind_chat(session: AsyncSession, chat_id: int | str) -> BindingDecision:
    """Apply the /start binding decision for the seeded (owner) user."""
    user_id = await _owner_user_id(session)
    if user_id is None:
        return BindingDecision(
            "rejected", "No user account found. Initialize the server first."
        )
    targets = await _telegram_targets(session, user_id)
    decision = decide_binding(targets, str(chat_id))
    if decision.action == "bind":
        await session.execute(
            text(
                "INSERT INTO notifications.channels (user_id, kind, target) "
                "VALUES (:user_id, 'telegram', :target) "
                "ON CONFLICT (user_id, kind, target) DO NOTHING"
            ),
            {"user_id": user_id, "target": str(chat_id)},
        )
        await session.commit()
    return decision


async def record_journal_event(
    session: AsyncSession,
    user_id: uuid.UUID,
    parsed: ParsedLog,
    event_text: str,
    ts: datetime,
) -> None:
    """Persist one quick-log event (source telegram, spec §103)."""
    await session.execute(
        text(
            "INSERT INTO health.journal_events (user_id, source, kind, ts, text, structured) "
            "VALUES (:user_id, 'telegram', :kind, :ts, :text, CAST(:structured AS jsonb))"
        ),
        {
            "user_id": user_id,
            "kind": parsed.kind,
            "ts": ts,
            "text": event_text,
            "structured": (
                None if parsed.structured is None else json.dumps(parsed.structured)
            ),
        },
    )
    await session.commit()


# ── command handlers ──────────────────────────────────────────────────────


async def _status_text(session: AsyncSession, tz: ZoneInfo, now: datetime) -> str:
    data = await assemble_today(session, tz=tz, now=now)
    lines = ["Status"]
    if data.data_freshness_minutes is None:
        lines.append("Newest observation: no data yet")
    else:
        minutes = round(data.data_freshness_minutes)
        unit = "minute" if minutes == 1 else "minutes"
        lines.append(f"Newest observation: {minutes} {unit} ago")
    percent = round(data.coverage_ratio * 100)
    lines.append(f"Coverage today: {data.resting_hr_quality} — {percent} percent")
    caffeine = f"Caffeine today: {data.journal.caffeine_count}"
    if data.journal.caffeine_last_ts is not None:
        caffeine += f" (last {data.journal.caffeine_last_ts.astimezone(tz):%H:%M})"
    lines.append(caffeine)
    lines.append(f"Notes today: {data.journal.journal_notes}")
    return "\n".join(lines)


async def handle_command(
    session: AsyncSession,
    command: str,
    argument: str,
    chat_id: int | str,
    now: datetime,
    tz: ZoneInfo,
) -> str:
    """One command -> its reply text. Raises nothing on bad input."""
    if command == "start":
        return (await bind_chat(session, chat_id)).reply
    if command == "brief":
        data = await assemble_today(session, tz=tz, now=now)
        return build_morning_brief(data, data.coverage_ratio)
    if command == "status":
        return await _status_text(session, tz, now)
    if command == "log":
        if not argument:
            return "Usage: /log <text> — e.g. /log coffee or /log coffee 2"
        user_id = await _owner_user_id(session)
        if user_id is None:
            return "No user account found. Initialize the server first."
        parsed = parse_quick_log(argument)
        await record_journal_event(session, user_id, parsed, argument, now)
        return parsed.reply
    if command == "help":
        return HELP_TEXT
    return "Unknown command.\n" + HELP_TEXT


async def handle_update(
    session: AsyncSession, update: dict[str, Any], now: datetime, tz: ZoneInfo
) -> UpdateReply | None:
    """One Telegram update -> its reply (None: nothing to answer)."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    message_text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(message_text, str):
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    parsed = parse_command(message_text)
    if parsed is None:
        return UpdateReply(chat_id, "Use /help to see what I can do.")
    command, argument = parsed
    reply = await handle_command(session, command, argument, chat_id, now, tz)
    return UpdateReply(chat_id, reply[:_MAX_MESSAGE_LEN])


# ── long-poll loop ────────────────────────────────────────────────────────


async def run_bot(
    client: TelegramClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tz: ZoneInfo,
    now: Callable[[], datetime] = _utc_now,
    poll_timeout: int = 25,
    poll_error_wait: float = 5.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Outbound-only long-poll loop (spec §27).

    ``now`` is the request clock (tests inject a fixed one); one update is
    handled at a time and a failure in one update never kills the loop.
    """
    offset = 0
    log.info("bot polling started")
    while stop is None or not stop.is_set():
        try:
            updates = await asyncio.to_thread(
                client.get_updates, offset, timeout=poll_timeout
            )
        except TelegramError as exc:
            # exc carries method/status/description only — never the token.
            log.warning("poll failed, retrying: %s", exc)
            await asyncio.sleep(poll_error_wait)
            continue
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            await _handle_one(client, session_factory, update, tz, now)
        if stop is not None:
            # Let a test-set stop event take effect even with instant polls.
            await asyncio.sleep(0)


async def _handle_one(
    client: TelegramClient,
    session_factory: async_sessionmaker[AsyncSession],
    update: dict[str, Any],
    tz: ZoneInfo,
    now: Callable[[], datetime],
) -> None:
    try:
        async with session_factory() as session:
            reply = await handle_update(session, update, now(), tz)
    except Exception:  # noqa: BLE001 - the loop must survive any handler failure
        log.exception("update handling failed")
        return
    if reply is None:
        return
    try:
        await asyncio.to_thread(client.send_message, reply.chat_id, reply.text)
    except TelegramError as exc:
        log.warning("send failed: %s", exc)
