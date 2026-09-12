"""Coach engine tests (spec §90; ADR 0009): keyword routing, deterministic
answers over seeded data, privacy end-to-end (the provider's prompt is
inspected), provider clamping, and per-tool failure degradation.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from coach_helpers import requires_db as _untyped_requires_db
from somatriq_agent.coach import (
    NO_TOOL_ANSWER,
    CoachEngine,
    ToolCall,
    select_tools,
)
from somatriq_agent.privacy import PrivacyLevel
from somatriq_agent.providers import DeterministicProvider, ProviderError
from somatriq_agent_tools import TOOLS, ToolSpec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# mypy cannot resolve the non-package coach_helpers module; re-bind with its
# runtime type to keep strict mode honest (api/mcp test precedent).
requires_db = cast(pytest.MarkDecorator, _untyped_requires_db)

FROZEN_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 8, 21)


class RecordingProvider:
    """Test double that records exactly what the engine tried to send."""

    name = "recording-external"
    is_local = False
    max_privacy_level: PrivacyLevel = "aggregates"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
        self.prompts.append(prompt)
        return "recorded answer"


# ── keyword routing (pure) ───────────────────────────────────────────────


def test_select_tools_today_question() -> None:
    assert select_tools("How is my recovery today?") == [ToolCall("get_today")]


def test_select_tools_baseline_resolves_metric() -> None:
    calls = select_tools("What is my hrv baseline?")
    assert calls == [ToolCall("get_baselines", {"metric": "avg_hrv", "days": 28})]


def test_select_tools_trend_resolves_metric_and_window() -> None:
    calls = select_tools("Is my resting heart rate trending down?")
    assert calls == [ToolCall("get_trends", {"metric": "resting_hr", "days": 14})]


def test_select_tools_journal_and_quality_and_none() -> None:
    assert select_tools("What did I log in my journal?") == [ToolCall("get_journal")]
    assert select_tools("How reliable is my data coverage?") == [
        ToolCall("get_data_quality", {"days": 14})
    ]
    assert select_tools("What is the meaning of life?") == []


def test_select_tools_baseline_word_hits_multiple_metrics() -> None:
    calls = select_tools("my sleep baseline")
    assert calls == [ToolCall("get_baselines", {"metric": "total_sleep_min", "days": 28})]


# ── engine over seeded data ──────────────────────────────────────────────


async def _ids(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    result = await db.execute(
        text(
            "SELECT u.id, d.id FROM identity.users u "
            "JOIN identity.devices d ON d.user_id = u.id "
            "WHERE d.name = 'synthetic-01' LIMIT 1"
        )
    )
    row = result.one()
    return uuid.UUID(str(row[0])), uuid.UUID(str(row[1]))


async def _seed_sleep(db: AsyncSession, srid: str, start: datetime, end: datetime) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.sleep_sessions "
            "(user_id, device_id, source_record_id, start_ts, end_ts) "
            "VALUES (:user_id, :device_id, :srid, :start, :end)"
        ),
        {"user_id": user_id, "device_id": device_id, "srid": srid, "start": start, "end": end},
    )
    await db.commit()


async def _seed_rr(db: AsyncSession, points: list[tuple[datetime, int]]) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO timeseries.rr_interval "
            "(user_id, device_id, source_record_id, ts, rr_ms, seq) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :rr_ms, :seq)"
        ),
        [
            {
                "user_id": user_id,
                "device_id": device_id,
                "source_record_id": f"coach-rr-{i}",
                "ts": ts,
                "rr_ms": rr_ms,
                "seq": i,
            }
            for i, (ts, rr_ms) in enumerate(points)
        ],
    )
    await db.commit()


async def _seed_hr(db: AsyncSession, samples: list[tuple[datetime, float]]) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO timeseries.heart_rate "
            "(user_id, device_id, source_record_id, ts, bpm) "
            "VALUES (:user_id, :device_id, :source_record_id, :ts, :bpm)"
        ),
        [
            {
                "user_id": user_id,
                "device_id": device_id,
                "source_record_id": f"coach-hr-{i}",
                "ts": ts,
                "bpm": bpm,
            }
            for i, (ts, bpm) in enumerate(samples)
        ],
    )
    await db.commit()


async def _seed_feature(db: AsyncSession, day: date, resting_hr: float) -> None:
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, data_quality, "
            "sample_count, coverage_ratio, algorithm_version) "
            "VALUES (:day, 'daily_heart/v1', 'UTC', :rhr, 'good', 86400, 1.0, "
            "'somatriq_rhr_v1')"
        ),
        {"day": day, "rhr": resting_hr},
    )
    await db.commit()


async def _seed_journal(db: AsyncSession, ts: datetime, kind: str, value: str) -> None:
    user_id, _ = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.journal_events (user_id, source, kind, ts, text) "
            "VALUES (:user_id, 'api', :kind, :ts, :text)"
        ),
        {"user_id": user_id, "kind": kind, "ts": ts, "text": value},
    )
    await db.commit()


async def _seed_full_day(db: AsyncSession) -> None:
    """The frozen full day: recovery score 76.66, RHR 60.0, plus a journal note."""
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
        day = TODAY - timedelta(days=7 - i)
        start = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
        await _seed_sleep(db, f"s-{day.isoformat()}", start, start + timedelta(minutes=sleep_min))
        await _seed_rr(
            db,
            [
                (start + timedelta(minutes=10, seconds=j), rr)
                for j, rr in enumerate([800, 800 + rmssd, 800, 800 + rmssd])
            ],
        )
        await _seed_feature(db, day, float(rhr))
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    await _seed_sleep(db, "s-today", start, start + timedelta(minutes=65))
    await _seed_rr(
        db,
        [
            (start + timedelta(minutes=10, seconds=j), rr)
            for j, rr in enumerate([800, 905, 800, 905])
        ],
    )
    await _seed_hr(
        db,
        [
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)
        ],
    )
    await _seed_journal(db, datetime(2026, 8, 21, 10, 0, tzinfo=UTC), "journal", "slept badly")


@requires_db
async def test_engine_deterministic_answer_with_seeded_data(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    await _seed_full_day(db)
    engine = CoachEngine(DeterministicProvider(), privacy_level="local")

    result = await engine.answer("How is my recovery today?", user_id, db, now=FROZEN_NOW)

    assert result.provider == "deterministic"
    assert result.privacy_level == "local"
    assert result.tools_used == ["get_today"]
    assert "recovery 76.66 of 100" in result.answer
    assert "resting HR 60 bpm" in result.answer
    assert result.caveats == []


@requires_db
async def test_engine_no_tool_answer_is_honest(db: AsyncSession, user_id: uuid.UUID) -> None:
    engine = CoachEngine(DeterministicProvider(), privacy_level="local")

    result = await engine.answer("What is the capital of France?", user_id, db, now=FROZEN_NOW)

    assert result.answer == NO_TOOL_ANSWER
    assert result.tools_used == []
    assert "no coach tool matched" in " ".join(result.caveats)


@requires_db
async def test_engine_sends_only_privacy_filtered_payload(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    """End-to-end chokepoint proof: the provider prompt cannot contain journal
    free text below local, and reports the effective level."""
    await _seed_full_day(db)
    provider = RecordingProvider()
    engine = CoachEngine(provider, privacy_level="aggregates")

    result = await engine.answer(
        "How am I doing today and what did I journal?", user_id, db, now=FROZEN_NOW
    )

    assert result.privacy_level == "aggregates"
    assert "get_today" in result.tools_used
    assert "get_journal" in result.tools_used
    assert result.answer == "recorded answer"
    sent = provider.prompts[0]
    assert "slept badly" not in sent
    bundle = json.loads(sent)
    assert bundle["tool_results"]["get_journal"]["data"]["events"][0]["text"] is None
    assert bundle["tool_results"]["get_journal"]["data"]["events"][0]["kind"] == "journal"


@requires_db
async def test_engine_clamps_level_to_provider_ceiling(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    provider = RecordingProvider()  # external-style ceiling: aggregates
    engine = CoachEngine(provider, privacy_level="detailed")

    result = await engine.answer("How is my recovery today?", user_id, db, now=FROZEN_NOW)

    assert result.privacy_level == "aggregates"
    assert any("clamped" in caveat for caveat in result.caveats)
    assert "privacy level clamped from 'detailed' to 'aggregates'" in " ".join(result.caveats)


@requires_db
async def test_engine_tool_failure_degrades_instead_of_failing(
    db: AsyncSession, user_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def raiser(*args: Any, **kwargs: Any) -> dict[str, Any]:
        msg = "database exploded"
        raise RuntimeError(msg)

    monkeypatch.setitem(
        TOOLS,
        "get_today",
        ToolSpec(name="get_today", description="broken on purpose", fn=raiser),
    )
    engine = CoachEngine(DeterministicProvider(), privacy_level="local")

    result = await engine.answer("How is my recovery today?", user_id, db, now=FROZEN_NOW)

    assert result.tools_used == ["get_today"]
    assert any("get_today failed" in caveat for caveat in result.caveats)
    assert "database exploded" in result.answer


@requires_db
async def test_engine_propagates_provider_errors(db: AsyncSession, user_id: uuid.UUID) -> None:
    class ExplodingProvider(RecordingProvider):
        async def complete(self, prompt: str, *, privacy_level: PrivacyLevel) -> str:
            msg = "provider down"
            raise ProviderError(msg)

    engine = CoachEngine(ExplodingProvider(), privacy_level="local")
    with pytest.raises(ProviderError, match="provider down"):
        await engine.answer("How is my recovery today?", user_id, db, now=FROZEN_NOW)


# ── get_training (the workouts fix: M12 data finally reaches the coach) ───


def test_select_tools_training_questions_route_to_get_training() -> None:
    for question in (
        "What workouts did I do this week?",
        "my training lately",
        "did I lift on tuesday?",
        "how much tonnage did I move?",
        "gym summary",
        "any strength work recently?",
    ):
        assert select_tools(question) == [ToolCall("get_training", {"days": 7})], question


def test_select_tools_spanish_questions_route() -> None:
    """The keyword router is bilingual (accented AND unaccented spellings),
    so the owner's natural phrasing never falls through to NO_TOOL_ANSWER."""
    for question in (
        "¿qué entrené esta semana?",
        "qué entrenamiento hice",
        "resumen del gimnasio",
        "cuánto pesas moví",  # unaccented keyboard
    ):
        assert select_tools(question) == [ToolCall("get_training", {"days": 7})], question
    assert select_tools("cómo estoy hoy") == [ToolCall("get_today")]
    assert select_tools("como estoy") == [ToolCall("get_today")]
    assert select_tools("tendencia de mi variabilidad") == [
        ToolCall("get_trends", {"metric": "avg_hrv", "days": 14})
    ]
    assert select_tools("mi pulso en reposo habitual") == [
        ToolCall("get_baselines", {"metric": "resting_hr", "days": 28})
    ]
    assert select_tools("qué calidad tienen los datos") == [
        ToolCall("get_data_quality", {"days": 14})
    ]
    assert select_tools("qué he registrado en el diario") == [ToolCall("get_journal")]


def test_workout_question_with_log_word_keeps_training_first() -> None:
    """'log' is a journal keyword — the journal may join as context, but the
    TRAINING tool must lead, never be displaced by the journal."""
    calls = select_tools("What workouts did I log this week?")
    assert calls[0] == ToolCall("get_training", {"days": 7})
    assert ToolCall("get_journal") in calls


async def _seed_training_session(db: AsyncSession) -> None:
    from somatriq_analytics.strength import muscle_group_for, parse_training_line

    owner, _ = await _ids(db)
    parsed = parse_training_line("Bench press 80x8 85x6 90x4")
    assert parsed is not None, "deterministic parser never rejects this line"
    session_id = (
        await db.execute(
            text(
                "INSERT INTO health.training_sessions (user_id, ts, source, raw_text) "
                "VALUES (:user_id, :ts, 'api', :raw) RETURNING id"
            ),
            {
                "user_id": owner,
                "ts": datetime(2026, 8, 20, 18, 0, 0, tzinfo=UTC),
                "raw": "Bench press 80x8 85x6 90x4",
            },
        )
    ).scalar_one()
    for index, parsed_set in enumerate(parsed.to_strength_sets()):
        await db.execute(
            text(
                "INSERT INTO health.training_sets "
                "(session_id, set_index, exercise, muscle_group, weight_kg, reps) "
                "VALUES (:session_id, :set_index, :exercise, :muscle_group, :weight, :reps)"
            ),
            {
                "session_id": session_id,
                "set_index": index,
                "exercise": parsed_set.exercise,
                "muscle_group": muscle_group_for(parsed_set.exercise),
                "weight": parsed_set.weight_kg,
                "reps": parsed_set.reps,
            },
        )
    await db.commit()


@requires_db
async def test_engine_answers_workout_question_with_seeded_session(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    await _seed_training_session(db)
    provider = RecordingProvider()
    engine = CoachEngine(provider, privacy_level="local")

    result = await engine.answer("What workouts did I do?", user_id, db, now=FROZEN_NOW)

    assert result.tools_used[0] == "get_training"
    # Exercises arrive normalized (lowercase surface names, spec §79).
    assert "bench press" in provider.prompts[0]
    # The session facts reached the provider: 80·8 + 85·6 + 90·4 = 1510 kg.
    assert "1510" in provider.prompts[0]


@requires_db
async def test_engine_workout_question_without_sessions_is_honest(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    provider = RecordingProvider()
    engine = CoachEngine(provider, privacy_level="local")
    result = await engine.answer("my training lately", user_id, db, now=FROZEN_NOW)
    assert result.tools_used == ["get_training"]
    assert "no strength sessions" in provider.prompts[0]


@requires_db
async def test_engine_training_hard_sets_honor_explicit_rir(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    """get_training must carry rir/rpe through to the frozen §79 summary.

    An explicitly easy session (RIR 4) has ``hard_sets == 0`` by the
    explicit-effort rule; before the fix the columns were dropped from the
    SQL and the 85%-of-best fallback counted the set hard — the coach
    disagreeing with the training API on the same stored rows.
    """
    owner, _ = await _ids(db)
    session_id = (
        await db.execute(
            text(
                "INSERT INTO health.training_sessions (user_id, ts, source, raw_text) "
                "VALUES (:user_id, :ts, 'api', :raw) RETURNING id"
            ),
            {
                "user_id": owner,
                "ts": datetime(2026, 8, 20, 18, 0, 0, tzinfo=UTC),
                "raw": "Bench press 100x10 rir=4",
            },
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO health.training_sets "
            "(session_id, set_index, exercise, muscle_group, weight_kg, reps, rir) "
            "VALUES (:session_id, 0, 'bench press', 'chest', 100.0, 10, 4)"
        ),
        {"session_id": session_id},
    )
    await db.commit()

    provider = RecordingProvider()
    engine = CoachEngine(provider, privacy_level="local")
    result = await engine.answer("my training lately", user_id, db, now=FROZEN_NOW)

    assert result.tools_used == ["get_training"]
    payload = json.loads(provider.prompts[0])["tool_results"]["get_training"]
    session_row = payload["data"]["sessions"][0]
    assert session_row["sets"] == 1
    assert session_row["tonnage_kg"] == 1000.0
    # RIR 4 travelled through: explicitly easy, never the 85% fallback's "hard".
    assert session_row["hard_sets"] == 0
