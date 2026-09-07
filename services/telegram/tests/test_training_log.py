"""Training-line interception in /log (spec §104, §205 M12).

The §104 training line must land as training_sessions + training_sets (one
transaction), answered with the deterministic summary — and everything else
about /log is unchanged: the caffeine line still parses as caffeine, a
plain note still parses as a journal note.
"""

from datetime import UTC, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from somatriq_db.testing import requires_db
from somatriq_telegram.bot import handle_command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC_TZ = ZoneInfo("UTC")
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)


async def _training_rows(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(
        text(
            "SELECT s.ts, s.source, s.raw_text, t.exercise, t.muscle_group, "
            "       t.weight_kg, t.reps, t.rir, t.rpe, t.set_index "
            "FROM health.training_sessions s "
            "JOIN health.training_sets t ON t.session_id = s.id "
            "ORDER BY s.ts, t.exercise, t.set_index"
        )
    )
    return [
        {
            "ts": row[0],
            "source": row[1],
            "raw_text": row[2],
            "exercise": row[3],
            "muscle_group": row[4],
            "weight_kg": row[5],
            "reps": row[6],
            "rir": row[7],
            "rpe": row[8],
            "set_index": row[9],
        }
        for row in result.all()
    ]


async def _session_count(db: AsyncSession) -> int:
    return cast(
        int,
        (await db.execute(text("SELECT count(*) FROM health.training_sessions"))).scalar_one(),
    )


# ── the §104 example through /log ─────────────────────────────────────────


@requires_db
async def test_log_training_line_stores_session_and_sets(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "Chest press 180x8 170x9 160x10", 1, NOW, UTC_TZ)
    # Deterministic summary of the parsed session (e1RM 180*(1+8/30)=228).
    assert reply == "Chest press: 3 sets, tonnage 4,570 kg, best e1RM 228"

    rows = await _training_rows(db)
    assert len(rows) == 3
    assert await _session_count(db) == 1
    assert rows[0]["source"] == "telegram"
    assert rows[0]["raw_text"] == "Chest press 180x8 170x9 160x10"
    assert rows[0]["exercise"] == "chest press"
    assert rows[0]["muscle_group"] == "chest"
    assert [(r["weight_kg"], r["reps"], r["set_index"]) for r in rows] == [
        (180.0, 8, 0),
        (170.0, 9, 1),
        (160.0, 10, 2),
    ]
    # A training line is NOT also a journal event.
    journal = await db.execute(text("SELECT count(*) FROM health.journal_events"))
    assert journal.scalar_one() == 0


@requires_db
async def test_log_bodyweight_training_line(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "pull-ups 8 8 7", 1, NOW, UTC_TZ)
    assert reply == "Pull ups: 3 sets, bodyweight"
    rows = await _training_rows(db)
    assert len(rows) == 3
    assert rows[0]["exercise"] == "pull ups"
    assert rows[0]["muscle_group"] == "back"
    assert all(r["weight_kg"] is None for r in rows)


@requires_db
async def test_log_training_with_rir_stores_effort(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "Squat 140x5 rir=2 130x8 rir=4", 1, NOW, UTC_TZ)
    assert "Squat: 2 sets" in reply
    rows = await _training_rows(db)
    assert [(r["weight_kg"], r["reps"], r["rir"]) for r in rows] == [
        (140.0, 5, 2),  # ORDER BY exercise, set_index
        (130.0, 8, 4),
    ]


@requires_db
async def test_log_unknown_exercise_grouped_as_other(db: AsyncSession) -> None:
    await handle_command(db, "log", "Fliegel press 100x8", 1, NOW, UTC_TZ)
    rows = await _training_rows(db)
    assert rows[0]["muscle_group"] == "other"


# ── /log behavior preserved ───────────────────────────────────────────────


@requires_db
async def test_log_caffeine_line_still_works(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "I drank a coffee now", 1, NOW, UTC_TZ)
    assert reply == "Logged: caffeine (quantity not stated — not guessed)"
    assert await _session_count(db) == 0
    stored = await db.execute(text("SELECT kind, text FROM health.journal_events"))
    rows = stored.all()
    assert len(rows) == 1
    assert rows[0][0] == "caffeine"


@requires_db
async def test_log_caffeine_with_number_still_works(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "coffee 2", 1, NOW, UTC_TZ)
    assert reply == "Logged: caffeine (quantity 2)"
    assert await _session_count(db) == 0


@requires_db
async def test_log_plain_note_still_works(db: AsyncSession) -> None:
    reply = await handle_command(db, "log", "tired today", 1, NOW, UTC_TZ)
    assert reply == "Logged: journal note"
    assert await _session_count(db) == 0
