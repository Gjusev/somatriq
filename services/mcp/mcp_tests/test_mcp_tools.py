"""Tool behavior against seeded data (spec §94, §99; ADR 0009/0010).

Deterministic math is the system under test: heart-rate bucketing mirrors the
api endpoint, daily summaries mirror /api/v1/metrics/daily, baselines are
exact median/quartile computations over a crafted 28-day fixture, and
insufficient data yields explicit results — never zeros (spec §158/§221).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from helpers import call_tool
from helpers import requires_db as _helpers_requires_db
from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_db.models import DailyFeature, DailyObservation, HeartRate, SleepSession
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette

# mypy cannot resolve the non-package helpers module (same trick as api tests).
requires_db = cast(pytest.MarkDecorator, _helpers_requires_db)


def _grid_5m(now: datetime) -> datetime:
    """Floor to the 5-minute grid so time_bucket alignment is deterministic."""
    return now.replace(minute=now.minute - (now.minute % 5), second=0, microsecond=0)


async def _insert_heart_rate(
    db: AsyncSession,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    samples: list[tuple[datetime, float]],
) -> None:
    db.add_all(
        [
            HeartRate(
                user_id=user_id,
                device_id=device_id,
                source_record_id=f"mcp-test-{uuid.uuid4().hex}",
                ts=ts,
                bpm=bpm,
            )
            for ts, bpm in samples
        ]
    )
    await db.commit()


# ── get_heart_rate_summary ───────────────────────────────────────────────


@requires_db
async def test_heart_rate_bucket_5m_math(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import tools

    user_id, device_id = user_device_ids
    base = _grid_5m(datetime.now(UTC))
    t1 = base - timedelta(minutes=10)
    t2 = t1 + timedelta(minutes=1)  # same 5m bucket as t1
    t3 = base - timedelta(minutes=5)  # its own bucket
    await _insert_heart_rate(db, user_id, device_id, [(t1, 60.0), (t2, 80.0), (t3, 90.0)])

    result = await tools.get_heart_rate_summary(last_hours=1, bucket="5m")

    points = result["data"]["points"]
    assert points == [
        {
            "ts": t1.isoformat(),
            "bpm": 70.0,  # mean of 60 + 80, rounded to 2 decimals
            "sample_count": 2,
        },
        {"ts": t3.isoformat(), "bpm": 90.0, "sample_count": 1},
    ]
    assert result["data"]["count"] == 2
    assert result["coverage"]["observed_points"] == 2
    assert result["coverage"]["expected_points"] == 12.0  # 1h / 5m buckets
    assert result["coverage"]["ratio"] == round(2 / 12, 3)


@requires_db
async def test_heart_rate_raw_points_ordered(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import tools

    user_id, device_id = user_device_ids
    base = _grid_5m(datetime.now(UTC))
    await _insert_heart_rate(
        db,
        user_id,
        device_id,
        [(base - timedelta(minutes=2), 61.0), (base - timedelta(minutes=1), 60.0)],
    )

    result = await tools.get_heart_rate_summary(last_hours=1, bucket="none")

    assert result["data"]["count"] == 2
    bpms = [p["bpm"] for p in result["data"]["points"]]
    assert bpms == [61.0, 60.0]  # ts-ascending
    assert all(p["sample_count"] == 1 for p in result["data"]["points"])
    assert result["caveats"] == []


@requires_db
async def test_heart_rate_no_data_is_explicit(db: AsyncSession) -> None:
    from somatriq_mcp import tools

    result = await tools.get_heart_rate_summary(last_hours=1, bucket="1h")
    assert result["data"]["count"] == 0
    assert "no data in range" in result["caveats"]
    assert result["coverage"]["ratio"] == 0.0


@requires_db
async def test_heart_rate_validation_errors() -> None:
    from somatriq_mcp import tools

    bad_bucket = await tools.get_heart_rate_summary(last_hours=1, bucket="2h")
    assert bad_bucket["error_code"] == "VALIDATION"

    bad_hours = await tools.get_heart_rate_summary(last_hours=0, bucket="1h")
    assert bad_hours["error_code"] == "VALIDATION"


# ── get_daily_summary ────────────────────────────────────────────────────


@requires_db
async def test_daily_summary_shape_and_math(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Envelope shape + exact per-day math on a fixed mid-day UTC window."""
    from somatriq_mcp import tools

    user_id, device_id = user_device_ids
    # A fixed day three days back, samples 12:00-15:15 UTC — never crosses
    # midnight, so attribution is deterministic regardless of wall clock.
    target = datetime.now(UTC).date() - timedelta(days=3)
    start = datetime(target.year, target.month, target.day, 12, 0, tzinfo=UTC)
    # 40 five-minute buckets; bpm cycles 60..64 → lowest-3 medians are 60.
    samples = [(start + timedelta(minutes=5 * i), 60.0 + (i % 5)) for i in range(40)]
    await _insert_heart_rate(db, user_id, device_id, samples)

    result = await tools.get_daily_summary(days=5)

    assert set(result) == {"data", "coverage", "sources", "quality", "caveats", "generated_at"}
    assert result["coverage"]["days_requested"] == 5
    assert result["coverage"]["feature_set_version"] == FEATURE_SET_VERSION
    assert result["sources"] == ["timeseries.heart_rate", "derived.daily_features"]

    days = result["data"]["days"]
    assert len(days) == 5
    by_date = {date.fromisoformat(d["date"]): d for d in days}
    target_day = by_date[target]
    assert target_day["hr_min"] == 60.0
    assert target_day["hr_max"] == 64.0
    assert target_day["hr_mean"] == 62.0
    assert target_day["resting_hr"] == 60.0  # mean of the lowest 3 bucket medians
    assert target_day["algorithm_version"] == "somatriq_rhr_v1"
    assert target_day["sample_count"] == 40

    # Read-through persisted the computed day (spec §72 cache, ADR 0012).
    cached = (
        await db.execute(
            select(DailyFeature).where(
                DailyFeature.date == target, DailyFeature.feature_set_version == FEATURE_SET_VERSION
            )
        )
    ).scalar_one()
    assert cached.resting_hr == 60.0

    # A zero-sample day inside the window stays an explicit gap (spec §174).
    empty_day = by_date[target + timedelta(days=1)]
    assert empty_day["sample_count"] == 0
    assert empty_day["hr_min"] is None
    assert empty_day["data_quality"] == "insufficient"
    assert result["caveats"], "insufficient days must be surfaced as caveats"


@requires_db
async def test_daily_summary_bounds_validation() -> None:
    from somatriq_mcp import tools

    result = await tools.get_daily_summary(days=0)
    assert result["error_code"] == "VALIDATION"


# ── get_baselines ────────────────────────────────────────────────────────


async def _seed_daily_features(
    db: AsyncSession, values_by_offset: dict[int, float]
) -> None:
    today = datetime.now(UTC).date()
    db.add_all(
        [
            DailyFeature(
                date=today - timedelta(days=offset),
                feature_set_version=FEATURE_SET_VERSION,
                timezone="UTC",
                resting_hr=value,
                hr_min=value - 5,
                hr_mean=value,
                hr_max=value + 5,
                sample_count=1000,
                coverage_ratio=0.9,
                data_quality="good",
                algorithm_version="somatriq_rhr_v1",
            )
            for offset, value in values_by_offset.items()
        ]
    )
    await db.commit()


@requires_db
async def test_baselines_median_iqr_exact_28_day_fixture(db: AsyncSession) -> None:
    """Crafted fixture: resting_hr 50.0..77.0 over exactly the trailing 28 days."""
    from somatriq_mcp import tools

    # Hand-computed inclusive quartiles (numpy-linear / statistics inclusive):
    # values 50..77 → median 63.5, q1 56.75, q3 70.25, iqr 13.5.
    await _seed_daily_features(db, {offset: 50.0 + offset for offset in range(28)})

    result = await tools.get_baselines(metric="resting_hr")

    data = result["data"]
    assert data["metric"] == "resting_hr"
    assert data["algorithm_version"] == "somatriq_baseline_v1"
    assert data["source"] == "derived.daily_features"
    assert data["median"] == 63.5
    assert data["q1"] == 56.75
    assert data["q3"] == 70.25
    assert data["iqr"] == 13.5
    assert data["min"] == 50.0
    assert data["max"] == 77.0
    assert data["n_days"] == 28
    assert data["window_days"] == 28
    assert result["coverage"] == {"days_available": 28, "window_days": 28}
    assert result["caveats"] == []


@requires_db
async def test_baselines_insufficient_data_is_explicit(db: AsyncSession) -> None:
    from somatriq_mcp import tools

    await _seed_daily_features(db, {0: 60.0, 1: 61.0, 2: 59.0})  # 3 days only

    result = await tools.get_baselines(metric="resting_hr")

    data = result["data"]
    assert data["status"] == "insufficient data"
    assert data["days_available"] == 3
    assert data["required_minimum"] == 7
    assert "median" not in data  # never zeros (spec §158)
    assert result["coverage"]["days_available"] == 3
    assert any("insufficient data" in c for c in result["caveats"])


@requires_db
async def test_baselines_unknown_metric_is_validation_error() -> None:
    from somatriq_mcp import tools

    result = await tools.get_baselines(metric="vo2max")
    assert result["error_code"] == "VALIDATION"
    assert "supported" in result["message"]


@requires_db
async def test_baselines_vendor_observation_metric(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """avg_hrv resolves to health.daily_observations (M6 catalog, §74 HRV)."""
    from somatriq_mcp import tools

    user_id, device_id = user_device_ids
    today = datetime.now(UTC).date()
    values = [40.0, 42.0, 44.0, 46.0, 48.0, 50.0, 52.0, 54.0]
    db.add_all(
        [
            DailyObservation(
                user_id=user_id,
                device_id=device_id,
                day=today - timedelta(days=offset),
                metric="avg_hrv",
                value=value,
            )
            for offset, value in enumerate(values)
        ]
    )
    await db.commit()

    result = await tools.get_baselines(metric="avg_hrv")

    data = result["data"]
    assert data["source"] == "health.daily_observations"
    assert data["n_days"] == 8
    # Inclusive quartiles of 8 sorted values: median 47, q1 43.5, q3 50.5.
    assert data["median"] == 47.0
    assert data["q1"] == 43.5
    assert data["q3"] == 50.5
    assert data["iqr"] == 7.0


# ── get_sleep_sessions ───────────────────────────────────────────────────


@requires_db
async def test_sleep_sessions_returns_recent_window(
    db: AsyncSession, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import tools

    user_id, device_id = user_device_ids
    now = datetime.now(UTC)
    recent_start = now - timedelta(days=1)
    db.add_all(
        [
            SleepSession(
                user_id=user_id,
                device_id=device_id,
                source_record_id="mcp-recent",
                start_ts=recent_start,
                end_ts=recent_start + timedelta(hours=8),
                efficiency=0.95,
                resting_hr=52.0,
                avg_hrv=61.0,
            ),
            SleepSession(
                user_id=user_id,
                device_id=device_id,
                source_record_id="mcp-old",
                start_ts=now - timedelta(days=40),
                end_ts=now - timedelta(days=40) + timedelta(hours=8),
            ),
        ]
    )
    await db.commit()

    result = await tools.get_sleep_sessions(days=7)

    sessions = result["data"]["sessions"]
    assert len(sessions) == 1  # the 40-day-old session is outside the window
    assert sessions[0]["source_record_id"] == "mcp-recent"
    assert sessions[0]["duration_min"] == 480.0
    assert sessions[0]["efficiency"] == 0.95
    assert result["sources"] == ["health.sleep_sessions"]


@requires_db
async def test_sleep_sessions_empty_is_explicit(db: AsyncSession) -> None:
    from somatriq_mcp import tools

    result = await tools.get_sleep_sessions(days=7)
    assert result["data"]["sessions"] == []
    assert "no data in range" in result["caveats"]


@requires_db
async def test_sleep_sessions_guarded_when_model_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge-order guard: no SleepSession model → empty result + caveat, no 500."""
    from somatriq_mcp import tools

    monkeypatch.setattr(tools, "SleepSession", None)
    result = await tools.get_sleep_sessions(days=7)
    assert result["data"]["sessions"] == []
    assert any("not available" in c for c in result["caveats"])


# ── full-stack: tools through the MCP protocol ───────────────────────────


@requires_db
async def test_tool_result_over_mcp_protocol(
    db: AsyncSession, app: Starlette, user_device_ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from somatriq_mcp import pats

    user_id, _ = user_device_ids
    minted = await pats.mint_pat(db, user_id, "protocol-test")

    heart = await call_tool(minted.token, "get_heart_rate_summary", {"last_hours": 1})
    heart_data = cast("dict[str, object]", heart["data"])
    assert heart_data["metric"] == "heart_rate"

    baselines = await call_tool(minted.token, "get_baselines", {"metric": "resting_hr"})
    baseline_data = cast("dict[str, object]", baselines["data"])
    assert baseline_data["algorithm_version"] == "somatriq_baseline_v1"
