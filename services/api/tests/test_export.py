"""M13 data export (spec §130): everything, structured, no lock-in.

Seeded multi-source data must come out complete: the JSON document carries
every section with exact counts and stable ascending order; the flat CSV
unions computed dailies with latest-received observations; the raw-batches
registry exposes metadata + the volume note. Every endpoint is an owner
operation (account JWT).
"""

import csv
import io
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from typing import TypeVar, cast

import httpx
import pytest
from somatriq_api.main import app
from somatriq_db.engine import get_engine
from somatriq_db.models import (
    DailyFeature,
    DailyObservation,
    Experiment,
    ExperimentDay,
    IngestBatch,
    JournalEvent,
    RawBatch,
    TrainingSession,
    TrainingSet,
)
from somatriq_db.testing import requires_db as _untyped_requires_db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_F = TypeVar("_F", bound="Callable[..., object]")
requires_db = cast("Callable[[_F], _F]", _untyped_requires_db)

DAILY_JSON = "/api/v1/export/daily.json"
DAILY_CSV = "/api/v1/export/daily.csv"
RAW_REGISTRY = "/api/v1/export/raw-batches.json"
REGISTER = "/api/v1/auth/register"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _dispose_engine_pool() -> AsyncIterator[None]:
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client
    await get_engine().dispose(close=False)


@pytest.fixture()
async def seeded(db: AsyncSession) -> dict[str, object]:
    """Multi-source fixture: two feature versions, observations from two
    devices (latest wins), training + sets, an experiment + day ledger, a
    journal event, and a raw batch registry row."""
    user_id, device_id = (
        await db.execute(
            text(
                "SELECT (SELECT id FROM identity.users ORDER BY created_at LIMIT 1), "
                "(SELECT id FROM identity.devices ORDER BY active_from LIMIT 1)"
            )
        )
    ).one()
    second_device = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO identity.devices (id, user_id, name, model) "
            "VALUES (:id, :u, 'scale-02', 'withings')"
        ),
        {"id": second_device, "u": user_id},
    )

    db.add_all(
        [
            DailyFeature(
                date=date(2026, 1, 5),
                feature_set_version="heart.v1",
                timezone="UTC",
                resting_hr=52.0,
                hr_min=44.0,
                hr_mean=58.2,
                hr_max=121.0,
                sample_count=8640,
                coverage_ratio=0.9,
                data_quality="good",
                algorithm_version="somatriq_daily_v1",
            ),
            DailyFeature(  # second version same day — both must survive
                date=date(2026, 1, 5),
                feature_set_version="heart.v2",
                timezone="UTC",
                resting_hr=51.5,
                hr_min=44.0,
                hr_mean=58.0,
                hr_max=119.0,
                sample_count=8640,
                coverage_ratio=0.9,
                data_quality="good",
                algorithm_version="somatriq_daily_v2",
            ),
            DailyFeature(
                date=date(2026, 1, 6),
                feature_set_version="heart.v1",
                timezone="UTC",
                resting_hr=None,
                hr_min=None,
                hr_mean=None,
                hr_max=None,
                sample_count=0,
                coverage_ratio=0.0,
                data_quality="insufficient",
                algorithm_version="somatriq_daily_v1",
            ),
        ]
    )
    early = datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
    late = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)
    db.add_all(
        [
            DailyObservation(
                user_id=user_id,
                device_id=device_id,
                day=date(2026, 1, 5),
                metric="weight_kg",
                value=80.0,
                decoder_version="noop-android/6",
                received_at=early,
            ),
            DailyObservation(  # same (day, metric), newer copy — wins the CSV
                user_id=user_id,
                device_id=second_device,
                day=date(2026, 1, 5),
                metric="weight_kg",
                value=80.4,
                decoder_version="csv-import/1",
                received_at=late,
            ),
            DailyObservation(
                user_id=user_id,
                device_id=device_id,
                day=date(2026, 1, 6),
                metric="resting_hr",
                value=51.0,
                decoder_version="noop-android/6",
                received_at=early,
            ),
        ]
    )

    training = TrainingSession(
        user_id=user_id,
        ts=datetime(2026, 1, 5, 17, 0, tzinfo=UTC),
        source="api",
        raw_text="squat 100x5x5",
    )
    db.add(training)
    await db.flush()
    db.add_all(
        [
            TrainingSet(
                session_id=training.id,
                exercise="squat",
                muscle_group="legs",
                weight_kg=100.0,
                reps=5,
                set_index=0,
            ),
            TrainingSet(
                session_id=training.id,
                exercise="squat",
                muscle_group="legs",
                weight_kg=102.5,
                reps=5,
                set_index=1,
            ),
        ]
    )

    experiment = Experiment(
        user_id=user_id,
        name="Caffeine window",
        hypothesis="No caffeine after 14:00 raises next-day recovery",
        intervention="caffeine cutoff 14:00",
        metric="recovery",
        direction="increase",
        baseline_days=2,
        intervention_days=2,
    )
    db.add(experiment)
    await db.flush()
    db.add_all(
        [
            ExperimentDay(
                experiment_id=experiment.id,
                day=date(2026, 1, 5),
                phase="baseline",
                complied=True,
            ),
            ExperimentDay(
                experiment_id=experiment.id,
                day=date(2026, 1, 6),
                phase="intervention",
                complied=False,
                note="one espresso at 15:00",
            ),
        ]
    )

    db.add(
        JournalEvent(
            user_id=user_id,
            source="web",
            kind="journal",
            ts=datetime(2026, 1, 6, 20, 0, tzinfo=UTC),
            text="slept great",
            structured={"note": "slept great"},
        )
    )

    batch_id = uuid.uuid4()
    db.add(
        IngestBatch(
            batch_id=batch_id,
            user_id=user_id,
            device_id=device_id,
            schema_version="1",
            content_hash="0" * 64,
            records_received=10,
            records_inserted=10,
            records_duplicate=0,
            raw_frame_count=10,
            raw_bytes_stored=1024,
        )
    )
    await db.flush()
    db.add(
        RawBatch(
            batch_id=batch_id,
            device_id=device_id,
            codec="zstd",
            journal_version=1,
            frame_count=10,
            payload_sha256="a" * 64,
            byte_size=1024,
            blob_path="/var/lib/somatriq/raw/2026/01/05/test.bin",
        )
    )
    await db.commit()
    return {"user_id": user_id}


async def _register(api: httpx.AsyncClient) -> str:
    response = await api.post(
        REGISTER, json={"username": "local", "password": "correct-horse-battery"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


# ── auth: exports are owner operations ───────────────────────────────────


@requires_db
async def test_export_endpoints_require_account_jwt(api: httpx.AsyncClient) -> None:
    for path in (DAILY_JSON, DAILY_CSV, RAW_REGISTRY):
        response = await api.get(path)
        assert response.status_code == 401, path
        assert response.json()["error_code"] == "AUTHENTICATION"


# ── JSON export ──────────────────────────────────────────────────────────


@requires_db
async def test_daily_json_contains_every_section_with_exact_counts(
    api: httpx.AsyncClient, seeded: dict[str, object]
) -> None:
    token = await _register(api)
    response = await api.get(DAILY_JSON, headers=_auth(token))
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {
        "generated_at",
        "days",
        "daily_features",
        "daily_observations",
        "training_sessions",
        "experiments",
        "journal_events",
    }
    assert body["days"] is None  # default: all history
    assert len(body["daily_features"]) == 3  # both versions of Jan 5 + Jan 6
    assert len(body["daily_observations"]) == 3  # every raw row, both devices
    assert len(body["training_sessions"]) == 1
    assert len(body["training_sessions"][0]["sets"]) == 2
    assert len(body["experiments"]) == 1
    assert len(body["experiments"][0]["days"]) == 2
    assert len(body["journal_events"]) == 1

    # Stable ascending ordering.
    feature_days = [f["date"] for f in body["daily_features"]]
    assert feature_days == sorted(feature_days)
    observation_days = [o["day"] for o in body["daily_observations"]]
    assert observation_days == sorted(observation_days)
    session_ts = [t["ts"] for t in body["training_sessions"]]
    assert session_ts == sorted(session_ts)
    experiment_days = [d["day"] for d in body["experiments"][0]["days"]]
    assert experiment_days == sorted(experiment_days)

    # The day ledger keeps its honesty fields verbatim.
    intervention = body["experiments"][0]["days"][1]
    assert intervention["complied"] is False
    assert intervention["note"] == "one espresso at 15:00"


@requires_db
async def test_daily_json_days_window_excludes_old_data(
    api: httpx.AsyncClient, seeded: dict[str, object]
) -> None:
    token = await _register(api)
    response = await api.get(DAILY_JSON, params={"days": 1}, headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 1
    assert body["daily_features"] == []
    assert body["daily_observations"] == []
    assert body["training_sessions"] == []
    assert body["journal_events"] == []  # seeded 2026-01-06, outside a 1-day window
    # Experiments export whole (small table, full history — the day ledger
    # is dated and self-describing).
    assert len(body["experiments"]) == 1


# ── CSV export ───────────────────────────────────────────────────────────


@requires_db
async def test_daily_csv_unions_computed_and_observed_long_form(
    api: httpx.AsyncClient, seeded: dict[str, object]
) -> None:
    token = await _register(api)
    response = await api.get(DAILY_CSV, headers=_auth(token))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["date", "metric", "value", "source"]
    body = [tuple(row) for row in rows[1:]]

    # Jan 6 feature row: sample_count 0 and coverage_ratio 0.0 survive (they
    # are values, not NULLs); the NULL hr columns do not.
    jan6_features = [row for row in body if row[0] == "2026-01-06" and row[3] == "daily_features"]
    assert [row[1] for row in jan6_features] == ["coverage_ratio", "sample_count"]

    # Jan 5 features from BOTH versions export (versioned rows preserved).
    jan5_features = [row for row in body if row[0] == "2026-01-05" and row[3] == "daily_features"]
    assert len(jan5_features) == 12  # 6 numeric columns × 2 feature versions

    # Observations: latest-received wins the double-reported weight_kg.
    observations = [row for row in body if row[3] == "daily_observations"]
    assert sorted(observations) == [
        ("2026-01-05", "weight_kg", "80.4", "daily_observations"),
        ("2026-01-06", "resting_hr", "51.0", "daily_observations"),
    ]

    # Whole file ascends by date.
    dates = [row[0] for row in body]
    assert dates == sorted(dates)


# ── raw batch registry ───────────────────────────────────────────────────


@requires_db
async def test_raw_batches_registry_is_metadata_plus_volume_note(
    api: httpx.AsyncClient, seeded: dict[str, object]
) -> None:
    token = await _register(api)
    response = await api.get(RAW_REGISTRY, headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert "volume" in body["note"]
    assert "backups" in body["note"]
    assert len(body["batches"]) == 1
    entry = body["batches"][0]
    assert entry["codec"] == "zstd"
    assert entry["frame_count"] == 10
    assert entry["byte_size"] == 1024
    assert entry["payload_sha256"] == "a" * 64
    assert entry["blob_path"].endswith("test.bin")
    assert "blob" not in entry  # never the blob bytes themselves
