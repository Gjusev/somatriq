"""/start owner binding (spec §101): first chat claims, others rejected.

The pure decision and the DB path are tested separately — the decision is
deterministic over the existing bindings, the DB layer only applies it and
is protected by UNIQUE(user, kind, target) even under races.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from conftest import requires_db as _conftest_requires_db
from somatriq_telegram.bot import bind_chat, decide_binding
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# mypy cannot resolve the non-package conftest module, so its imports arrive
# as Any; re-bind the marker with its runtime type to keep strict mode honest.
requires_db = cast(pytest.MarkDecorator, _conftest_requires_db)


# ── pure decision ─────────────────────────────────────────────────────────


def test_first_chat_binds() -> None:
    decision = decide_binding([], "111")
    assert decision.action == "bind"
    assert "Bound" in decision.reply


def test_same_chat_is_already_bound() -> None:
    decision = decide_binding(["111"], "111")
    assert decision.action == "already"
    assert decision.reply == "This chat is already bound."


def test_different_chat_is_rejected() -> None:
    decision = decide_binding(["111"], "222")
    assert decision.action == "rejected"
    assert decision.reply == "This bot is already bound to another chat."


# ── DB application ────────────────────────────────────────────────────────


async def _channel_targets(db: AsyncSession, kind: str = "telegram") -> list[str]:
    result = await db.execute(
        text("SELECT target FROM notifications.channels WHERE kind = :kind"),
        {"kind": kind},
    )
    return [str(row[0]) for row in result.all()]


@requires_db
async def test_bind_chat_writes_channel_once(db: AsyncSession) -> None:
    first = await bind_chat(db, 111)
    assert first.action == "bind"
    assert await _channel_targets(db) == ["111"]

    again = await bind_chat(db, 111)
    assert again.action == "already"
    assert await _channel_targets(db) == ["111"]  # no duplicate row


@requires_db
async def test_bind_chat_rejects_takeover(db: AsyncSession) -> None:
    await bind_chat(db, 111)
    takeover = await bind_chat(db, 222)
    assert takeover.action == "rejected"
    assert await _channel_targets(db) == ["111"]  # binding unchanged


@requires_db
async def test_bound_channel_is_the_scheduler_target(db: AsyncSession) -> None:
    """The /start binding is exactly what the scheduler enqueues against."""
    await bind_chat(db, 111)
    result = await db.execute(
        text(
            "SELECT c.kind, c.target FROM notifications.channels c "
            "JOIN identity.users u ON u.id = c.user_id "
            "ORDER BY u.created_at LIMIT 1"
        )
    )
    row = result.first()
    assert row is not None and row[0] == "telegram" and row[1] == "111"


@requires_db
async def test_journal_event_row_shape(db: AsyncSession) -> None:
    """journal_events carries the verbatim text + validated payload (§103)."""
    from somatriq_telegram.bot import _owner_user_id, record_journal_event
    from somatriq_telegram.parser import parse_quick_log

    user_id = await _owner_user_id(db)
    assert user_id is not None
    ts = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)
    await record_journal_event(db, user_id, parse_quick_log("coffee"), "coffee", ts)
    await record_journal_event(
        db, user_id, parse_quick_log("coffee 2"), "coffee 2", ts + timedelta(minutes=5)
    )
    await record_journal_event(db, user_id, parse_quick_log("tired"), "tired", ts)

    result = await db.execute(
        text(
            "SELECT kind, source, ts::date, text, structured "
            "FROM health.journal_events ORDER BY ts"
        )
    )
    rows = result.all()
    assert len(rows) == 3
    caffeine_plain, caffeine_qty, journal = rows
    assert caffeine_plain[0] == "caffeine"
    assert caffeine_plain[1] == "telegram"
    assert caffeine_plain[2] == date(2026, 8, 21)
    assert caffeine_plain[3] == "coffee"
    assert caffeine_plain[4] == {"estimated": False}
    assert caffeine_qty[4] == {"quantity": 2}
    assert journal[0] == "journal"
    assert journal[4] is None


@requires_db
async def test_journal_user_fk_enforced(db: AsyncSession) -> None:
    """journal_events.user_id is a real FK — a bogus user cannot log."""
    bogus = uuid.uuid4()
    with pytest.raises(Exception):  # noqa: B017 - FK violation surfaces as DBAPIError
        await db.execute(
            text(
                "INSERT INTO health.journal_events (user_id, source, kind) "
                "VALUES (:u, 'telegram', 'journal')"
            ),
            {"u": bogus},
        )
