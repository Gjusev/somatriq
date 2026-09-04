"""/experiment command (M11, spec §85, §204): deterministic parser,
bound-chat auth, listing with today's check-in state, and the check-in
upsert itself. DB-backed like the other command tests; the HTTP layer is
not involved — handlers return reply text; the loop owns sending.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from somatriq_db.testing import requires_db
from somatriq_telegram.bot import (
    EXPERIMENT_USAGE,
    RunningExperiment,
    experiment_handle,
    handle_command,
    handle_update,
    match_experiment_handle,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UTC_TZ = ZoneInfo("UTC")
NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
OWNER_CHAT = 4242


# ── parser (pure) ─────────────────────────────────────────────────────────


def _parse(argument: str) -> Any:
    from somatriq_telegram.parser import parse_experiment_argument

    return parse_experiment_argument(argument)


def test_parse_experiment_list() -> None:
    parsed = _parse("")
    assert parsed.kind == "list"
    assert parsed.id_token is None and parsed.complied is None and parsed.note is None


def test_parse_experiment_checkin_forms() -> None:
    yes = _parse("3fa85f64 yes")
    assert (yes.kind, yes.id_token, yes.complied, yes.note) == (
        "checkin",
        "3fa85f64",
        True,
        None,
    )
    no = _parse("3FA85F6457174562B3FC2C963F66AFA6 no")
    assert (no.kind, no.complied) == ("checkin", False)  # id case tolerant
    noted = _parse("3fa85f64 no had an espresso at 16:00")
    assert (noted.kind, noted.complied, noted.note) == (
        "checkin",
        False,
        "had an espresso at 16:00",
    )


def test_parse_experiment_usage() -> None:
    assert _parse("3fa85f64").kind == "usage"  # missing yes/no
    assert _parse("3fa85f64 maybe").kind == "usage"  # neither yes nor no
    assert _parse("yes").kind == "usage"  # missing the id


# ── handle matching (pure) ────────────────────────────────────────────────


def _experiment(experiment_id: uuid.UUID, name: str = "seed") -> RunningExperiment:
    return RunningExperiment(
        id=experiment_id,
        name=name,
        started_at=datetime(2026, 8, 21, tzinfo=UTC),
        baseline_days=7,
        intervention_days=7,
    )


def test_match_experiment_handle_prefix_and_rejection() -> None:
    a = _experiment(uuid.UUID("3fa85f6457174562b3fc2c963f66afa6"))
    b = _experiment(uuid.UUID("3fa85f64000000000000000000000000"))
    unrelated = _experiment(uuid.UUID("abcdefabcdefabcdefabcdefabcdef12"))

    # Exact-prefix match on one experiment.
    assert match_experiment_handle("3fa85f6457174562b3fc2c963f66afa6", [a, unrelated]) is a
    # Shared prefix across two running experiments is ambiguous → None.
    assert match_experiment_handle("3fa85f64", [a, b]) is None
    # Too short to be safe, or matching nothing → None.
    assert match_experiment_handle("3f", [a]) is None
    assert match_experiment_handle("abcdef12", [a]) is None


def test_experiment_handle_is_short_and_stable() -> None:
    experiment_id = uuid.UUID("3fa85f6457174562b3fc2c963f66afa6")
    assert experiment_handle(experiment_id) == "3fa85f64"


# ── auth: the bound chat ──────────────────────────────────────────────────


@requires_db
async def test_experiment_requires_a_bound_chat(db: AsyncSession) -> None:
    reply = await handle_command(db, "experiment", "", OWNER_CHAT, NOW, UTC_TZ)
    assert reply == "This chat is not bound yet. Use /start first."

    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)  # first chat wins
    other = await handle_command(db, "experiment", "", 999, NOW, UTC_TZ)
    assert other == "This bot is already bound to another chat."


# ── listing ───────────────────────────────────────────────────────────────


async def _seed_experiment(
    db: AsyncSession,
    *,
    started: date,
    baseline_days: int = 7,
    intervention_days: int = 7,
    name: str = "Caffeine cutoff",
) -> uuid.UUID:
    user_id = (
        await db.execute(text("SELECT id FROM identity.users LIMIT 1"))
    ).scalar_one()
    experiment_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO research.experiments "
            "(id, user_id, name, hypothesis, intervention, metric, direction, "
            " baseline_days, intervention_days, started_at) "
            "VALUES (:id, :user_id, :name, 'h', 'i', 'avg_hrv', 'increase', "
            "        :baseline_days, :intervention_days, :started_at)"
        ),
        {
            "id": experiment_id,
            "user_id": user_id,
            "name": name,
            "baseline_days": baseline_days,
            "intervention_days": intervention_days,
            "started_at": datetime(
                started.year, started.month, started.day, tzinfo=UTC
            ),
        },
    )
    await db.commit()
    return experiment_id


@requires_db
async def test_experiment_list_empty(db: AsyncSession) -> None:
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    reply = await handle_command(db, "experiment", "", OWNER_CHAT, NOW, UTC_TZ)
    assert reply == "No running experiments."


@requires_db
async def test_experiment_list_shows_phase_progress_and_today(
    db: AsyncSession,
) -> None:
    """Started 5 days ago (7/7): today is intervention day 5. The ledger's
    default is complied=true (silence counts as kept — a check-in is how
    you say no), so a freshly materialized today reads "complied"."""
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    experiment_id = await _seed_experiment(
        db, started=NOW.date() - timedelta(days=5)
    )

    reply = await handle_command(db, "experiment", "", OWNER_CHAT, NOW, UTC_TZ)

    assert reply.startswith("Experiments")
    assert f"Caffeine cutoff [{experiment_handle(experiment_id)}]" in reply
    assert "intervention day 5/7" in reply  # materialized through today
    assert "5/5 complied" in reply  # unchecked days default to kept
    assert "today: complied" in reply  # default-kept, not an explicit check-in


# ── check-in ──────────────────────────────────────────────────────────────


async def _today_row(
    db: AsyncSession, experiment_id: uuid.UUID
) -> tuple[str, bool, str | None]:
    result = await db.execute(
        text(
            "SELECT phase, complied, note FROM research.experiment_days "
            "WHERE experiment_id = :id AND day = :today"
        ),
        {"id": experiment_id, "today": NOW.date()},
    )
    row = result.one()
    return (str(row[0]), bool(row[1]), cast("str | None", row[2]))


@requires_db
async def test_experiment_checkin_yes_no_note(db: AsyncSession) -> None:
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    experiment_id = await _seed_experiment(
        db, started=NOW.date() - timedelta(days=5)
    )
    handle = experiment_handle(experiment_id)

    reply = await handle_command(
        db, "experiment", f"{handle} no had an espresso", OWNER_CHAT, NOW, UTC_TZ
    )
    assert reply == "Check-in saved: Caffeine cutoff — today not complied (had an espresso)"
    assert await _today_row(db, experiment_id) == (
        "intervention",
        False,
        "had an espresso",
    )

    # Upsert: the same day flips to yes and the note clears (last word wins).
    reply = await handle_command(db, "experiment", f"{handle} yes", OWNER_CHAT, NOW, UTC_TZ)
    assert reply == "Check-in saved: Caffeine cutoff — today complied"
    assert await _today_row(db, experiment_id) == ("intervention", True, None)

    listed = await handle_command(db, "experiment", "", OWNER_CHAT, NOW, UTC_TZ)
    assert "today: complied" in listed


@requires_db
async def test_experiment_checkin_unknown_handle_lists_candidates(
    db: AsyncSession,
) -> None:
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    experiment_id = await _seed_experiment(
        db, started=NOW.date() - timedelta(days=5)
    )
    reply = await handle_command(
        db, "experiment", "deadbeef yes", OWNER_CHAT, NOW, UTC_TZ
    )
    assert reply.startswith("No running experiment matches that id")
    assert experiment_handle(experiment_id) in reply  # candidates for retyping


@requires_db
async def test_experiment_checkin_outside_window_is_refused(
    db: AsyncSession,
) -> None:
    """Started 20 days ago (7/7): the window closed 13 days ago — nothing
    to check in, and no row is written."""
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    experiment_id = await _seed_experiment(
        db, started=NOW.date() - timedelta(days=20)
    )
    handle = experiment_handle(experiment_id)

    listed = await handle_command(db, "experiment", "", OWNER_CHAT, NOW, UTC_TZ)
    assert "intervention day 7/7" in listed  # window fully materialized
    reply = await handle_command(db, "experiment", f"{handle} yes", OWNER_CHAT, NOW, UTC_TZ)
    assert "outside the experiment window" in reply
    rows = await db.execute(
        text("SELECT count(*) FROM research.experiment_days")
    )
    assert rows.scalar_one() == 14  # the list materialized the window; checkin added 0


@requires_db
async def test_experiment_usage_reply(db: AsyncSession) -> None:
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    reply = await handle_command(db, "experiment", "abc", OWNER_CHAT, NOW, UTC_TZ)
    assert reply == EXPERIMENT_USAGE


# ── handle_update dispatch ────────────────────────────────────────────────


def _update(message_text: str, chat_id: int = OWNER_CHAT) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"message_id": 2, "chat": {"id": chat_id}, "text": message_text},
    }


@requires_db
async def test_handle_update_dispatches_experiment(db: AsyncSession) -> None:
    await handle_command(db, "start", "", OWNER_CHAT, NOW, UTC_TZ)
    reply = await handle_update(db, _update("/experiment"), NOW, UTC_TZ)
    assert reply is not None
    assert reply.chat_id == OWNER_CHAT
    assert reply.text == "No running experiments."
