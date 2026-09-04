"""Command dispatch + handlers (spec §101-103). DB-backed for /brief,
/status, /log, /start; pure for parse_command. The HTTP layer is not
involved here — handlers return reply text; the loop owns sending.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from somatriq_db.testing import requires_db
from somatriq_telegram.bot import (
    HELP_TEXT,
    handle_command,
    handle_update,
    parse_command,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC_TZ = ZoneInfo("UTC")
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


# ── parse_command (pure) ──────────────────────────────────────────────────


def test_parse_command_basic() -> None:
    assert parse_command("/brief") == ("brief", "")
    assert parse_command("/log coffee 2") == ("log", "coffee 2")
    assert parse_command("/BRIEF@somatriq_bot now") == ("brief", "now")


def test_parse_command_non_command_is_none() -> None:
    assert parse_command("hello there") is None
    assert parse_command("/") is None


# ── /help and unknown commands (pure-ish; no DB reads) ────────────────────


@requires_db
async def test_help_and_unknown(db: AsyncSession) -> None:
    assert await handle_command(db, "help", "", 1, NOW, UTC_TZ) == HELP_TEXT
    unknown = await handle_command(db, "frobnicate", "", 1, NOW, UTC_TZ)
    assert unknown.startswith("Unknown command.")


# ── /start ────────────────────────────────────────────────────────────────


@requires_db
async def test_start_binds_and_replies(db: AsyncSession) -> None:
    reply = await handle_command(db, "start", "", 4242, NOW, UTC_TZ)
    assert reply.startswith("Bound.")
    assert "morning brief" in reply


# ── /brief on an empty day: honest markers ────────────────────────────────


@requires_db
async def test_brief_empty_day_is_honest(db: AsyncSession) -> None:
    reply = await handle_command(db, "brief", "", 1, NOW, UTC_TZ)
    assert reply.startswith("Good morning")
    assert "Recovery\nnot available yet" in reply
    assert "Missing inputs\nhrv, rhr, sleep" in reply
    assert "Data quality\ninsufficient — 0 percent coverage" in reply


@requires_db
async def test_brief_full_day_golden(db: AsyncSession) -> None:
    """End-to-end: seeded data -> assembler -> brief, same golden format."""
    user_id, device_id = await _ids(db)
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    await _seed(db, user_id, device_id, "s-today", start, timedelta(minutes=441))
    await _seed_rr(
        db,
        user_id,
        device_id,
        [
            (start + timedelta(minutes=10, seconds=i), rr)
            for i, rr in enumerate([800, 912, 800, 912])
        ],
    )
    await _seed_hr(
        db,
        user_id,
        device_id,
        (
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 56.0)
            for m in range(150)
        ),
    )
    nights = [
        (90, 50, 59),
        (95, 55, 60),
        (100, 60, 60),
        (100, 60, 60),
        (105, 65, 61),
        (110, 70, 61),
        (115, 75, 62),
    ]
    for i, (rmssd, sleep_min, rhr) in enumerate(nights):
        day = date(2026, 8, 21) - timedelta(days=7 - i)
        night = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
        await _seed(
            db, user_id, device_id, f"s-{day.isoformat()}", night, timedelta(minutes=sleep_min)
        )
        await _seed_rr(
            db,
            user_id,
            device_id,
            [
                (night + timedelta(minutes=10, seconds=j), rr)
                for j, rr in enumerate([800, 800 + rmssd, 800, 800 + rmssd])
            ],
        )
        await _seed_feature(db, day, float(rhr))

    reply = await handle_command(db, "brief", "", 1, NOW, UTC_TZ)

    # Sleep 441 min renders exactly; HRV rmssd of [800,912,800,912] is 112.
    assert "Sleep\n7 h 21 min" in reply
    assert "HRV\n112 ms" in reply
    assert "RHR\n56 bpm" in reply
    assert "Data quality\n" in reply
    assert "Main insight" in reply or "Missing inputs" in reply


# ── /log ──────────────────────────────────────────────────────────────────


@requires_db
async def test_log_caffeine_without_number(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "I drank a coffee now", 1, NOW, UTC_TZ)
    assert reply == "Logged: caffeine (quantity not stated — not guessed)"
    stored = await _journal_rows(db)
    assert stored == [("caffeine", "I drank a coffee now", {"estimated": False})]


@requires_db
async def test_log_caffeine_with_number(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "coffee 2", 1, NOW, UTC_TZ)
    assert reply == "Logged: caffeine (quantity 2)"
    stored = await _journal_rows(db)
    assert stored == [("caffeine", "coffee 2", {"quantity": 2})]


@requires_db
async def test_log_journal_note(db: AsyncSession) -> None:
    await handle_command(db, "log", "tired today", 1, NOW, UTC_TZ)
    assert await _journal_rows(db) == [("journal", "tired today", None)]


@requires_db
async def test_log_without_argument_is_usage(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "", 1, NOW, UTC_TZ)
    assert reply.startswith("Usage: /log")
    assert await _journal_rows(db) == []


# ── /status ───────────────────────────────────────────────────────────────


@requires_db
async def test_status_reports_freshness_coverage_and_caffeine(db: AsyncSession) -> None:
    user_id, device_id = await _ids(db)
    await _seed_hr(
        db,
        user_id,
        device_id,
        ((NOW - timedelta(minutes=m), 60.0) for m in range(1, 4)),
    )
    await handle_command(db, "log", "coffee", 1, NOW - timedelta(hours=1), UTC_TZ)
    await handle_command(db, "log", "felt good", 1, NOW - timedelta(minutes=30), UTC_TZ)

    reply = await handle_command(db, "status", "", 1, NOW, UTC_TZ)

    assert reply.startswith("Status")
    assert "Newest observation: 1 minute" in reply
    assert "Coverage today:" in reply
    assert "Caffeine today: 1" in reply
    assert "Notes today: 1" in reply


@requires_db
async def test_status_no_data(db: AsyncSession) -> None:
    reply = await handle_command(db, "status", "", 1, NOW, UTC_TZ)
    assert "Newest observation: no data yet" in reply
    assert "Caffeine today: 0" in reply


# ── handle_update dispatch ────────────────────────────────────────────────


def _update(message_text: str, chat_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": chat_id}, "text": message_text},
    }


@requires_db
async def test_handle_update_non_command_gets_hint(db: AsyncSession) -> None:
    reply = await handle_update(db, _update("hello"), NOW, UTC_TZ)
    assert reply is not None
    assert reply.text == "Use /help to see what I can do."


@requires_db
async def test_handle_update_without_message_is_ignored(db: AsyncSession) -> None:
    assert await handle_update(db, {"update_id": 1}, NOW, UTC_TZ) is None


@requires_db
async def test_handle_update_dispatches_log(db: AsyncSession) -> None:
    reply = await handle_update(db, _update("/log coffee", chat_id=77), NOW, UTC_TZ)
    assert reply is not None
    assert reply.chat_id == 77
    assert "Logged: caffeine" in reply.text


# ── seed helpers (same shapes as the api today tests) ─────────────────────


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users ORDER BY created_at LIMIT 1"))
    ).scalar_one()
    device_id = (
        await db.execute(text("SELECT id FROM identity.devices ORDER BY active_from LIMIT 1"))
    ).scalar_one()
    return user_id, device_id


async def _seed(
    db: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    srid: str,
    start: datetime,
    duration: timedelta,
) -> None:
    await db.execute(
        text(
            "INSERT INTO health.sleep_sessions "
            "(user_id, device_id, source_record_id, start_ts, end_ts) "
            "VALUES (:user_id, :device_id, :srid, :start, :end)"
        ),
        {
            "user_id": user_id,
            "device_id": device_id,
            "srid": srid,
            "start": start,
            "end": start + duration,
        },
    )
    await db.commit()


async def _seed_rr(
    db: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    points: list[tuple[datetime, int]],
) -> None:
    prefix = uuid.uuid4().hex
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"{prefix}-{i}",
            "ts": ts,
            "rr_ms": rr_ms,
            "seq": i,
        }
        for i, (ts, rr_ms) in enumerate(points)
    ]
    await db.execute(
        text(
            "INSERT INTO timeseries.rr_interval "
            "(user_id, device_id, source_record_id, ts, rr_ms, seq) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :rr_ms, :seq)"
        ),
        rows,
    )
    await db.commit()


async def _seed_hr(
    db: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    samples: Any,
) -> None:
    prefix = uuid.uuid4().hex
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"tg-test-{prefix}-{i}",
            "ts": ts,
            "bpm": bpm,
        }
        for i, (ts, bpm) in enumerate(samples)
    ]
    await db.execute(
        text(
            "INSERT INTO timeseries.heart_rate "
            "(user_id, device_id, source_record_id, ts, bpm) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :bpm)"
        ),
        rows,
    )
    await db.commit()


async def _seed_feature(db: AsyncSession, day: date, resting_hr: float) -> None:
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, data_quality, "
            "sample_count, coverage_ratio, algorithm_version) "
            "VALUES (:day, 'daily_heart/v1', 'UTC', :rhr, 'good', 86400, 1.0, 'somatriq_rhr_v1')"
        ),
        {"day": day, "rhr": resting_hr},
    )
    await db.commit()


async def _journal_rows(db: AsyncSession) -> list[tuple[str, str, dict[str, Any] | None]]:
    result = await db.execute(
        text("SELECT kind, text, structured FROM health.journal_events ORDER BY ts")
    )
    return [(str(r[0]), str(r[1]), cast("dict[str, Any] | None", r[2])) for r in result.all()]
