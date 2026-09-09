"""Bot command handlers + long-poll loop (spec §101-104; grill decision on
partial data: the brief always carries markers).

Command surface (M7 + M11):

* /start — claim the owner binding. The FIRST chat writes the telegram
  notification channel for the seeded user; any DIFFERENT chat is rejected
  (single-owner system; UNIQUE(user, kind, target) makes takeover
  impossible even under races).
* /brief — immediate morning brief over the shared TODAY assembler.
* /status — freshness + coverage summary.
* /log <text> — deterministic quick-log (parser.py; quantity never
  invented). A §104 training line ("Chest press 180x8 170x9 160x10") is
  intercepted before the caffeine check and stored as a training session
  (M12; somatriq_analytics.strength) with a deterministic summary reply.
* /experiment — running experiments with today's check-in state;
  /experiment <id> yes|no [note] — today's compliance check-in (spec §85).
  Auth is the bound chat: an unbound or different chat gets nothing.
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
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_analytics.brief import build_morning_brief
from somatriq_analytics.plan_data import assemble_plan
from somatriq_analytics.strength import (
    ParsedTraining,
    muscle_group_for,
    parse_training_line,
    session_summary,
)
from somatriq_analytics.today_data import assemble_today
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .parser import (
    ParsedExperimentCommand,
    ParsedLog,
    parse_experiment_argument,
    parse_quick_log,
)
from .telegram_client import TelegramClient, TelegramError

log = logging.getLogger("somatriq_telegram.bot")

HELP_TEXT = (
    "Commands:\n"
    "/start — bind this chat to Somatriq (first chat wins)\n"
    "/brief — morning brief now\n"
    "/status — data freshness and coverage\n"
    "/log <text> — quick log (e.g. /log coffee, /log coffee 2,\n"
    "/log Chest press 180x8 170x9 160x10)\n"
    "/experiment — running experiments and today's check-in\n"
    "/experiment <id> yes|no [note] — today's compliance check-in\n"
    "/help — this message"
)

EXPERIMENT_USAGE = (
    "Usage:\n"
    "/experiment — list running experiments with today's check-in\n"
    "/experiment <id> yes|no [note] — record today's compliance\n"
    "The <id> is the short handle shown by /experiment."
)

# Short typing handle: the first 8 hex chars of the experiment id.
EXPERIMENT_HANDLE_LENGTH = 8

_MIN_HANDLE_CHARS = 4

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
        return BindingDecision("rejected", "No user account found. Initialize the server first.")
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
            "structured": (None if parsed.structured is None else json.dumps(parsed.structured)),
        },
    )
    await session.commit()


# ── /log training lines (M12, spec §104, §205) ────────────────────────────


async def record_training_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    parsed: ParsedTraining,
    raw_text: str,
    ts: datetime,
) -> str:
    """Persist one parsed training session + its sets in one transaction
    (both inserts commit together) and return the deterministic summary
    reply (spec §104)."""
    summary = session_summary(parsed.to_strength_sets())
    session_id = await session.execute(
        text(
            "INSERT INTO health.training_sessions (user_id, source, ts, raw_text) "
            "VALUES (:user_id, 'telegram', :ts, :raw_text) RETURNING id"
        ),
        {"user_id": user_id, "ts": ts, "raw_text": raw_text},
    )
    session_uuid = session_id.scalar_one()
    await session.execute(
        text(
            "INSERT INTO health.training_sets "
            "(session_id, exercise, muscle_group, weight_kg, reps, rir, rpe, set_index) "
            "VALUES (:session_id, :exercise, :muscle_group, :weight_kg, :reps, "
            "        :rir, :rpe, :set_index)"
        ),
        [
            {
                "session_id": session_uuid,
                "exercise": parsed.exercise,
                "muscle_group": muscle_group_for(parsed.exercise),
                "weight_kg": one_set.weight_kg,
                "reps": one_set.reps,
                "rir": one_set.rir,
                "rpe": one_set.rpe,
                "set_index": index,
            }
            for index, one_set in enumerate(parsed.sets)
        ],
    )
    await session.commit()
    display = parsed.exercise.capitalize()  # stored name stays lowercase
    if summary.tonnage_kg > 0.0 and summary.exercises:
        best = max(summary.best_e1rm_by_exercise.values())
        return (
            f"{display}: {summary.set_count} sets, "
            f"tonnage {summary.tonnage_kg:,.0f} kg, best e1RM {best:,.0f}"
        )
    return f"{display}: {summary.set_count} sets, bodyweight"


# ── /experiment (M11, spec §85, §204) ─────────────────────────────────────


@dataclass(frozen=True)
class RunningExperiment:
    """The fields /experiment needs from research.experiments."""

    id: uuid.UUID
    name: str
    started_at: datetime
    baseline_days: int
    intervention_days: int


def experiment_handle(experiment_id: uuid.UUID) -> str:
    """Short, typable handle (first hex chars of the id)."""
    return experiment_id.hex[:EXPERIMENT_HANDLE_LENGTH]


def match_experiment_handle(
    id_token: str, experiments: list[RunningExperiment]
) -> RunningExperiment | None:
    """Unique prefix match (dashes ignored, >= 4 chars); None when the
    token matches nothing or more than one running experiment."""
    token = id_token.replace("-", "").lower()
    if len(token) < _MIN_HANDLE_CHARS:
        return None
    matches = [experiment for experiment in experiments if experiment.id.hex.startswith(token)]
    return matches[0] if len(matches) == 1 else None


def experiment_window(experiment: RunningExperiment, tz: ZoneInfo) -> tuple[date, date, date]:
    """(first_day, start_day, last_day) — same math as the API window."""
    start_day = experiment.started_at.astimezone(tz).date()
    first = start_day - timedelta(days=experiment.baseline_days - 1)
    last = start_day + timedelta(days=experiment.intervention_days)
    return first, start_day, last


async def _roll_experiment_days_forward(
    session: AsyncSession,
    experiment: RunningExperiment,
    today: date,
    tz: ZoneInfo,
) -> None:
    """Materialize missing day rows through today (same statement the API
    uses; kept local so the services stay decoupled)."""
    first, start_day, last = experiment_window(experiment, tz)
    through = min(today, last)
    if through < first:
        return
    await session.execute(
        text(
            "INSERT INTO research.experiment_days (experiment_id, day, phase) "
            "SELECT :experiment_id, d::date, "
            "       CASE WHEN d::date <= :start_day THEN 'baseline' "
            "            ELSE 'intervention' END "
            "FROM generate_series(CAST(:first AS date), CAST(:through AS date), "
            "     INTERVAL '1 day') AS d "
            "ON CONFLICT (experiment_id, day) DO NOTHING"
        ),
        {
            "experiment_id": experiment.id,
            "start_day": start_day,
            "first": first,
            "through": through,
        },
    )
    await session.commit()


async def _running_experiments(
    session: AsyncSession, user_id: uuid.UUID
) -> list[RunningExperiment]:
    result = await session.execute(
        text(
            "SELECT id, name, started_at, baseline_days, intervention_days "
            "FROM research.experiments "
            "WHERE user_id = :user_id AND status = 'running' "
            "ORDER BY created_at, id"
        ),
        {"user_id": user_id},
    )
    return [
        RunningExperiment(
            id=row[0],
            name=str(row[1]),
            started_at=row[2],
            baseline_days=int(row[3]),
            intervention_days=int(row[4]),
        )
        for row in result.all()
    ]


async def _experiment_list_text(
    session: AsyncSession, user_id: uuid.UUID, now: datetime, tz: ZoneInfo
) -> str:
    experiments = await _running_experiments(session, user_id)
    if not experiments:
        return "No running experiments."
    today = now.astimezone(tz).date()
    lines = ["Experiments"]
    for experiment in experiments:
        await _roll_experiment_days_forward(session, experiment, today, tz)
        first, start_day, last = experiment_window(experiment, tz)
        phase = "baseline" if today <= start_day else "intervention"
        phase_total = (
            experiment.baseline_days if phase == "baseline" else experiment.intervention_days
        )
        day_rows = await session.execute(
            text(
                "SELECT phase, count(*) FILTER (WHERE complied), count(*) "
                "FROM research.experiment_days WHERE experiment_id = :id "
                "GROUP BY phase"
            ),
            {"id": experiment.id},
        )
        complied = {str(row[0]): (int(row[1]), int(row[2])) for row in day_rows.all()}
        today_row = await session.execute(
            text(
                "SELECT complied FROM research.experiment_days "
                "WHERE experiment_id = :id AND day = :today"
            ),
            {"id": experiment.id, "today": today},
        )
        today_complied = today_row.scalar_one_or_none()
        phase_complied, phase_total_materialized = complied.get(phase, (0, 0))
        today_state = (
            "not checked in yet"
            if today_complied is None
            else ("complied" if today_complied else "not complied")
        )
        lines.append(
            f"{experiment.name} [{experiment_handle(experiment.id)}]\n"
            f"{phase} day {phase_total_materialized}/{phase_total} · "
            f"{phase_complied}/{phase_total_materialized} complied\n"
            f"today: {today_state}"
        )
    return "\n".join(lines)


async def _experiment_checkin(
    session: AsyncSession,
    user_id: uuid.UUID,
    parsed: ParsedExperimentCommand,
    now: datetime,
    tz: ZoneInfo,
) -> str:
    assert parsed.id_token is not None and parsed.complied is not None
    experiments = await _running_experiments(session, user_id)
    experiment = match_experiment_handle(parsed.id_token, experiments)
    if experiment is None:
        lines = ["No running experiment matches that id (or it is ambiguous)."]
        lines.extend(f"{e.name} [{experiment_handle(e.id)}]" for e in experiments)
        return "\n".join(lines)
    today = now.astimezone(tz).date()
    first, start_day, last = experiment_window(experiment, tz)
    if today < first or today > last:
        return f"{experiment.name}: today is outside the experiment window — nothing to check in."
    phase = "baseline" if today <= start_day else "intervention"
    await session.execute(
        text(
            "INSERT INTO research.experiment_days "
            "(experiment_id, day, phase, complied, note) "
            "VALUES (:experiment_id, :day, :phase, :complied, :note) "
            "ON CONFLICT (experiment_id, day) DO UPDATE SET "
            "complied = EXCLUDED.complied, note = EXCLUDED.note"
        ),
        {
            "experiment_id": experiment.id,
            "day": today,
            "phase": phase,
            "complied": parsed.complied,
            "note": parsed.note,
        },
    )
    await session.commit()
    answer = "complied" if parsed.complied else "not complied"
    suffix = f" ({parsed.note})" if parsed.note else ""
    return f"Check-in saved: {experiment.name} — today {answer}{suffix}"


async def _handle_experiment_command(
    session: AsyncSession,
    argument: str,
    chat_id: int | str,
    now: datetime,
    tz: ZoneInfo,
) -> str:
    """/experiment — auth is the bound chat (spec §85 rapid check-ins)."""
    user_id = await _owner_user_id(session)
    if user_id is None:
        return "No user account found. Initialize the server first."
    targets = await _telegram_targets(session, user_id)
    if not targets:
        return "This chat is not bound yet. Use /start first."
    if str(chat_id) not in targets:
        return "This bot is already bound to another chat."
    parsed = parse_experiment_argument(argument)
    if parsed.kind == "usage":
        return EXPERIMENT_USAGE
    if parsed.kind == "list":
        return await _experiment_list_text(session, user_id, now, tz)
    return await _experiment_checkin(session, user_id, parsed, now, tz)


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
        plan = await assemble_plan(session, tz=tz, now=now, today_data=data)
        return build_morning_brief(data, data.coverage_ratio, plan)
    if command == "status":
        return await _status_text(session, tz, now)
    if command == "log":
        if not argument:
            return (
                "Usage: /log <text> — e.g. /log coffee, /log coffee 2, "
                "/log Chest press 180x8 170x9 160x10"
            )
        user_id = await _owner_user_id(session)
        if user_id is None:
            return "No user account found. Initialize the server first."
        # Training lines (spec §104) are intercepted BEFORE the caffeine
        # check: a §104 grammar match is a training session, never a note.
        training = parse_training_line(argument)
        if training is not None:
            return await record_training_session(session, user_id, training, argument, now)
        parsed = parse_quick_log(argument)
        await record_journal_event(session, user_id, parsed, argument, now)
        return parsed.reply
    if command == "experiment":
        return await _handle_experiment_command(session, argument, chat_id, now, tz)
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
            updates = await asyncio.to_thread(client.get_updates, offset, timeout=poll_timeout)
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
