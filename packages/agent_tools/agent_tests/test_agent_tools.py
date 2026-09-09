"""Deterministic coach tools against the migrated DB (spec §94/§99; ADR 0009).

Fixtures mirror services/api/tests/test_today.py: the frozen recovery math
(score 76.66) and the baseline-night seeding pattern, so the tool layer is
verified against the same seeded reality the endpoint tests use.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from somatriq_agent_tools import (
    TOOLS,
    ToolValidationError,
    get_baselines,
    get_data_quality,
    get_journal,
    get_today,
    get_trends,
)
from somatriq_agent_tools.tools import envelope
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tool_helpers import requires_db as _untyped_requires_db

# mypy cannot resolve the non-package tool_helpers module; re-bind with its
# runtime type to keep strict mode honest (api/mcp test precedent).
requires_db = cast(pytest.MarkDecorator, _untyped_requires_db)

UTC_TZ = ZoneInfo("UTC")
FROZEN_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
TODAY = date(2026, 8, 21)
ENVELOPE_KEYS = {"data", "coverage", "sources", "quality", "caveats", "generated_at"}

# Baseline nights from test_today.py: per-night (RMSSD ms, sleep min, RHR).
NIGHTS = [
    (90, 50, 59),
    (95, 55, 60),
    (100, 60, 60),
    (100, 60, 60),
    (105, 65, 61),
    (110, 70, 61),
    (115, 75, 62),
]


# ── seeding helpers (mirror services/api/tests/test_today.py) ────────────


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


async def _seed_rr(db: AsyncSession, prefix: str, points: list[tuple[datetime, int]]) -> None:
    user_id, device_id = await _ids(db)
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


async def _seed_hr(db: AsyncSession, samples: list[tuple[datetime, float]]) -> None:
    user_id, device_id = await _ids(db)
    prefix = uuid.uuid4().hex
    rows = [
        {
            "user_id": user_id,
            "device_id": device_id,
            "source_record_id": f"agent-tools-{prefix}-{i}",
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


async def _seed_feature(
    db: AsyncSession,
    day: date,
    resting_hr: float,
    *,
    coverage: float = 1.0,
    quality: str = "good",
    sample_count: int = 86_400,
) -> None:
    """Upsert (PK is date + feature_set_version) so tests can reseed a day."""
    await db.execute(
        text(
            "INSERT INTO derived.daily_features "
            "(date, feature_set_version, timezone, resting_hr, data_quality, "
            "sample_count, coverage_ratio, algorithm_version) "
            "VALUES (:day, 'daily_heart/v1', 'UTC', :rhr, :quality, "
            ":sample_count, :coverage, 'somatriq_rhr_v1') "
            "ON CONFLICT (date, feature_set_version) DO UPDATE SET "
            "resting_hr = EXCLUDED.resting_hr, data_quality = EXCLUDED.data_quality, "
            "sample_count = EXCLUDED.sample_count, coverage_ratio = EXCLUDED.coverage_ratio"
        ),
        {
            "day": day,
            "rhr": resting_hr,
            "quality": quality,
            "sample_count": sample_count,
            "coverage": coverage,
        },
    )
    await db.commit()


async def _seed_observation(db: AsyncSession, day: date, metric_key: str, value: float) -> None:
    user_id, device_id = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.daily_observations "
            "(user_id, device_id, day, metric, value) "
            "VALUES (:user_id, :device_id, :day, :metric, :value)"
        ),
        {
            "user_id": user_id,
            "device_id": device_id,
            "day": day,
            "metric": metric_key,
            "value": value,
        },
    )
    await db.commit()


async def _seed_journal(
    db: AsyncSession,
    ts: datetime,
    kind: str,
    text_value: str | None,
    structured: dict[str, object] | None,
) -> None:
    user_id, _ = await _ids(db)
    await db.execute(
        text(
            "INSERT INTO health.journal_events (user_id, source, kind, ts, text, structured) "
            "VALUES (:user_id, 'api', :kind, :ts, :text, CAST(:structured AS jsonb))"
        ),
        {
            "user_id": user_id,
            "kind": kind,
            "ts": ts,
            "text": text_value,
            "structured": None if structured is None else json.dumps(structured),
        },
    )
    await db.commit()


def _rr_pattern(base: datetime, d: int) -> list[tuple[datetime, int]]:
    """[800, 800+d, 800, 800+d] one second apart — RMSSD is exactly d."""
    return [(base + timedelta(seconds=i), rr) for i, rr in enumerate([800, 800 + d, 800, 800 + d])]


async def _seed_baseline_nights(db: AsyncSession, today: date, count: int) -> None:
    for i, (rmssd, sleep_min, rhr) in enumerate(NIGHTS[:count]):
        day = today - timedelta(days=count - i)  # oldest first, last = yesterday
        start = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
        await _seed_sleep(db, f"s-{day.isoformat()}", start, start + timedelta(minutes=sleep_min))
        pattern = _rr_pattern(start + timedelta(minutes=10), rmssd)
        await _seed_rr(db, f"rr-{day.isoformat()}", pattern)
        await _seed_feature(db, day, float(rhr))


async def _seed_full_today(db: AsyncSession) -> None:
    """The test_today.py full-day fixture: exact score 76.66, RHR 60.0."""
    start = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
    await _seed_sleep(db, "s-today", start, start + timedelta(minutes=65))
    await _seed_rr(db, "rr-today", _rr_pattern(start + timedelta(minutes=10), 105))
    await _seed_hr(
        db,
        [
            (datetime(2026, 8, 21, 9, 30, tzinfo=UTC) + timedelta(minutes=m), 60.0)
            for m in range(150)
        ],
    )
    await _seed_baseline_nights(db, TODAY, count=7)


# ── registry ─────────────────────────────────────────────────────────────


def test_registry_lists_the_six_coach_tools() -> None:
    assert set(TOOLS) == {
        "get_today",
        "get_baselines",
        "get_trends",
        "get_journal",
        "get_data_quality",
        "get_training",
    }
    for name, spec in TOOLS.items():
        assert spec.name == name
        assert spec.required_scope == "coach.read"
        assert spec.description
        assert callable(spec.fn)


def test_envelope_shape_is_the_section_99_contract() -> None:
    result = envelope(data={}, coverage={}, sources=[], quality=0.5, caveats=[])
    assert set(result) == ENVELOPE_KEYS
    assert isinstance(result["generated_at"], str)


# ── get_today ────────────────────────────────────────────────────────────


@requires_db
async def test_get_today_reuses_the_shared_assembly(db: AsyncSession) -> None:
    """Same seeded day as the endpoint test: exact score 76.66, RHR 60.0."""
    await _seed_full_today(db)

    result = await get_today(db, tz=UTC_TZ, now=FROZEN_NOW)

    assert set(result) == ENVELOPE_KEYS
    data = result["data"]
    assert data["date"] == "2026-08-21"
    assert data["timezone"] == "UTC"
    assert data["resting_hr"] == 60.0
    assert data["hrv"]["rmssd_ms"] == 105.0
    assert data["sleep"]["duration_minutes"] == 65.0
    assert data["recovery"]["score"] == pytest.approx(76.66, abs=0.005)
    assert data["recovery"]["algorithm_version"] == "somatriq_recovery_v1"
    assert data["journal"] == {"caffeine_count": 0, "journal_notes": 0, "caffeine_last_ts": None}
    assert set(result["sources"]) == {
        "health.sleep_sessions",
        "timeseries.rr_interval",
        "timeseries.heart_rate",
        "derived.daily_features",
        "health.journal_events",
    }
    assert result["quality"] == round(result["coverage"]["heart_rate"], 3)
    assert result["caveats"] == []


@requires_db
async def test_get_today_empty_day_lists_missing_inputs(db: AsyncSession) -> None:
    result = await get_today(db, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["hrv"] is None
    assert data["sleep"] is None
    assert data["resting_hr"] is None
    assert data["recovery"]["score"] is None
    assert data["recovery"]["missing_inputs"] == ["hrv", "rhr", "sleep"]
    assert result["quality"] == 0.0


# ── get_baselines ────────────────────────────────────────────────────────


@requires_db
async def test_get_baselines_daily_feature_median_and_iqr(db: AsyncSession) -> None:
    """7 nights ending yesterday: median 60, q1 60, q3 61 (inclusive quartiles)."""
    await _seed_baseline_nights(db, TODAY, count=7)

    result = await get_baselines(db, metric="resting_hr", days=28, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["status"] == "ok"
    assert data["metric"] == "resting_hr"
    assert data["median"] == 60.0
    assert data["q1"] == 60.0
    assert data["q3"] == 61.0
    assert data["iqr"] == 1.0
    assert data["min"] == 59.0
    assert data["max"] == 62.0
    assert data["n_days"] == 7
    assert data["algorithm_version"] == "somatriq_baseline_v1"
    assert data["source"] == "derived.daily_features"
    assert result["coverage"] == {"days_available": 7, "window_days": 28}
    assert result["quality"] == round(7 / 28, 3)
    assert result["caveats"] == ["21 of 28 window days have no resting_hr values"]


@requires_db
async def test_get_baselines_vendor_metric_reads_observations(db: AsyncSession) -> None:
    """avg_hrv reads the vendor table (metric column stores the snake key)."""
    values = [40, 42, 44, 46, 48, 50, 52, 54]
    for i, value in enumerate(values):
        day = TODAY - timedelta(days=7 - i)  # 8 days ending today
        await _seed_observation(db, day, "avg_hrv", float(value))

    result = await get_baselines(db, metric="avg_hrv", days=8, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["status"] == "ok"
    assert data["source"] == "health.daily_observations"
    assert data["median"] == 47.0
    assert data["q1"] == 43.5
    assert data["q3"] == 50.5
    assert data["iqr"] == 7.0
    assert data["n_days"] == 8
    assert result["quality"] == 1.0


@requires_db
async def test_get_baselines_insufficient_never_fakes_numbers(db: AsyncSession) -> None:
    for rhr, i in ((59.0, 1), (60.0, 2), (61.0, 3)):
        await _seed_feature(db, TODAY - timedelta(days=i), rhr)

    result = await get_baselines(db, metric="resting_hr", days=28, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["status"] == "insufficient data"
    assert data["days_available"] == 3
    assert data["required_minimum"] == 7
    assert "median" not in data
    assert any("insufficient data" in caveat for caveat in result["caveats"])


@requires_db
async def test_get_baselines_rejects_unknown_metric_and_window(db: AsyncSession) -> None:
    with pytest.raises(ToolValidationError, match="unsupported metric"):
        await get_baselines(db, metric="bogus", tz=UTC_TZ, now=FROZEN_NOW)
    with pytest.raises(ToolValidationError, match="between 1 and"):
        await get_baselines(db, metric="resting_hr", days=0, tz=UTC_TZ, now=FROZEN_NOW)


# ── get_trends ───────────────────────────────────────────────────────────


@requires_db
async def test_get_trends_up_direction_with_gap(db: AsyncSession) -> None:
    """Days 0,1,3,4 of [10,12,16,18]: least-squares slope is exactly 2/day."""
    for offset, value in ((0, 10.0), (1, 12.0), (3, 16.0), (4, 18.0)):
        await _seed_feature(db, TODAY - timedelta(days=4 - offset), value)

    result = await get_trends(db, metric="resting_hr", days=5, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["direction"] == "up"
    assert data["slope_per_day"] == 2.0
    assert data["n_points"] == 4
    assert [point["value"] for point in data["series"]] == [10.0, 12.0, 16.0, 18.0]
    assert data["algorithm_version"] == "least-squares-sign-v1"
    assert result["coverage"] == {"days_requested": 5, "days_with_values": 4}
    assert result["caveats"] == ["1 of 5 window days have no resting_hr values"]


@requires_db
async def test_get_trends_down_and_flat(db: AsyncSession) -> None:
    for offset, value in ((0, 60.0), (1, 58.0), (2, 56.0)):
        await _seed_feature(db, TODAY - timedelta(days=2 - offset), value)
    result = await get_trends(db, metric="resting_hr", days=3, tz=UTC_TZ, now=FROZEN_NOW)
    assert result["data"]["direction"] == "down"
    assert result["data"]["slope_per_day"] == -2.0

    for offset in range(3):
        await _seed_feature(db, TODAY - timedelta(days=2 - offset), 55.0)
    result = await get_trends(db, metric="resting_hr", days=3, tz=UTC_TZ, now=FROZEN_NOW)
    assert result["data"]["direction"] == "flat"
    assert result["data"]["slope_per_day"] == 0.0
    assert result["caveats"] == []


@requires_db
async def test_get_trends_insufficient_below_two_points(db: AsyncSession) -> None:
    await _seed_feature(db, TODAY, 60.0)

    result = await get_trends(db, metric="resting_hr", days=7, tz=UTC_TZ, now=FROZEN_NOW)

    data = result["data"]
    assert data["direction"] == "insufficient"
    assert data["slope_per_day"] is None
    assert data["series"] == [{"date": TODAY.isoformat(), "value": 60.0}]
    assert any("at least 2 days" in caveat for caveat in result["caveats"])


@requires_db
async def test_get_trends_rejects_unknown_metric(db: AsyncSession) -> None:
    with pytest.raises(ToolValidationError):
        await get_trends(db, metric="hrv", tz=UTC_TZ, now=FROZEN_NOW)


# ── get_journal ──────────────────────────────────────────────────────────


@requires_db
async def test_get_journal_returns_days_events_verbatim(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, _ = user_device_ids
    morning = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
    await _seed_journal(
        db, morning, "caffeine", "double espresso", {"quantity_mg": 80, "estimated": False}
    )
    await _seed_journal(db, morning + timedelta(hours=2), "journal", "slept badly", None)
    # Outside the local day (next day) — excluded.
    await _seed_journal(db, datetime(2026, 8, 22, 1, 0, tzinfo=UTC), "note", "next day", None)

    result = await get_journal(db, user_id=user_id, day=TODAY, tz=UTC_TZ)

    data = result["data"]
    assert data["day"] == "2026-08-21"
    events = data["events"]
    assert len(events) == 2
    assert events[0]["kind"] == "caffeine"
    assert events[0]["structured"] == {"quantity_mg": 80, "estimated": False}
    assert events[0]["text"] == "double espresso"
    assert events[1]["kind"] == "journal"
    assert events[1]["structured"] is None
    assert result["quality"] == 1.0
    assert result["sources"] == ["health.journal_events"]


@requires_db
async def test_get_journal_defaults_to_the_local_day_and_reports_empty(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, _ = user_device_ids

    result = await get_journal(db, user_id=user_id, tz=UTC_TZ, now=FROZEN_NOW)

    assert result["data"]["day"] == "2026-08-21"
    assert result["data"]["events"] == []
    assert result["quality"] == 0.0
    assert result["caveats"] == ["no journal events on 2026-08-21"]


# ── get_data_quality ─────────────────────────────────────────────────────


@requires_db
async def test_get_data_quality_explicit_gaps(db: AsyncSession) -> None:
    days = [TODAY - timedelta(days=i) for i in (4, 3, 2, 1, 0)]
    await _seed_feature(db, days[0], 60.0, coverage=0.6, quality="good")
    await _seed_feature(db, days[1], 60.0, coverage=0.3, quality="fair")
    await _seed_feature(db, days[3], 60.0, coverage=0.02, quality="insufficient", sample_count=10)

    result = await get_data_quality(db, days=5, tz=UTC_TZ, now=FROZEN_NOW)

    rows = result["data"]["days"]
    assert [row["date"] for row in rows] == [day.isoformat() for day in days]
    assert rows[0] == {
        "date": days[0].isoformat(),
        "sample_count": 86_400,
        "coverage_ratio": 0.6,
        "data_quality": "good",
    }
    # Days without a cached row are explicit gaps, never interpolated.
    assert rows[2] == {
        "date": days[2].isoformat(),
        "sample_count": 0,
        "coverage_ratio": 0.0,
        "data_quality": "insufficient",
    }
    assert rows[4]["data_quality"] == "insufficient"
    assert result["coverage"] == {"days_requested": 5, "days_with_rows": 3}
    assert result["caveats"] == ["3 of 5 days have insufficient heart-rate data"]
    assert result["sources"] == ["derived.daily_features"]


@requires_db
async def test_get_data_quality_empty_window(db: AsyncSession) -> None:
    result = await get_data_quality(db, days=7, tz=UTC_TZ, now=FROZEN_NOW)
    assert len(result["data"]["days"]) == 7
    assert all(row["data_quality"] == "insufficient" for row in result["data"]["days"])
    assert result["quality"] == 0.0


@requires_db
async def test_get_data_quality_rejects_bad_window(db: AsyncSession) -> None:
    with pytest.raises(ToolValidationError):
        await get_data_quality(db, days=400, tz=UTC_TZ, now=FROZEN_NOW)
