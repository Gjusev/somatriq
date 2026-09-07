"""M13 data export (spec §130): everything, structured, no lock-in.

Three surfaces, all owner operations behind the account JWT:

* GET /api/v1/export/daily.json — one JSON document with every daily slice
  (derived daily features incl. every feature_set_version, raw vendor
  observations, training sessions with their sets, experiments with their
  day ledgers, journal events), stable ordering (dates/timestamps
  ascending). ``?days=N`` limits the window; the default is ALL history.
* GET /api/v1/export/daily.csv — the flat long-form union of the computed
  dailies and the vendor observations (``date,metric,value,source``) as a
  StreamingResponse attachment. Observations resolve latest-received per
  (day, metric) exactly like the read surface; the JSON export keeps every
  raw row.
* GET /api/v1/export/raw-batches.json — the raw_batches REGISTRY (metadata
  + blob sha/path) so the archive is auditable. The blobs themselves live
  on the server volume and are covered by volume backups — the response
  says so in ``note`` instead of silently omitting the distinction.
"""

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from somatriq_db.engine import get_session
from somatriq_db.models import (
    DailyFeature,
    DailyObservation,
    Experiment,
    ExperimentDay,
    JournalEvent,
    RawBatch,
    TrainingSession,
    TrainingSet,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .settings import get_settings

export_router = APIRouter(prefix="/api/v1/export", tags=["export"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Numeric daily-feature columns exported long-form in the CSV (the text
#: columns — data_quality, algorithm_version, timezone — ride the JSON only).
_FEATURE_COLUMNS: tuple[str, ...] = (
    "resting_hr",
    "hr_min",
    "hr_mean",
    "hr_max",
    "sample_count",
    "coverage_ratio",
)

_RAW_BLOBS_NOTE = (
    "This is the raw_batches registry only: metadata, payload sha256 and the "
    "blob path on the server volume. The blobs themselves are not served "
    "over HTTP; they live on the volume and are covered by volume backups."
)


# ── wire models ───────────────────────────────────────────────────────────


class DailyFeatureOut(BaseModel):
    date: date
    feature_set_version: str
    timezone: str
    resting_hr: float | None
    hr_min: float | None
    hr_mean: float | None
    hr_max: float | None
    sample_count: int
    coverage_ratio: float
    data_quality: str
    algorithm_version: str | None
    computed_at: datetime


class DailyObservationOut(BaseModel):
    day: date
    metric: str
    value: float
    decoder_version: str | None
    received_at: datetime


class TrainingSetOut(BaseModel):
    exercise: str
    muscle_group: str
    weight_kg: float | None
    reps: int
    rir: int | None
    rpe: float | None
    set_index: int


class TrainingSessionOut(BaseModel):
    id: uuid.UUID
    ts: datetime
    source: str
    raw_text: str | None
    created_at: datetime
    sets: list[TrainingSetOut]


class ExperimentDayOut(BaseModel):
    day: date
    phase: str
    complied: bool
    note: str | None


class ExperimentOut(BaseModel):
    id: uuid.UUID
    name: str
    hypothesis: str
    intervention: str
    metric: str
    direction: str
    baseline_days: int
    intervention_days: int
    status: str
    started_at: datetime
    created_at: datetime
    completed_at: datetime | None
    days: list[ExperimentDayOut]


class JournalEventOut(BaseModel):
    id: uuid.UUID
    ts: datetime
    source: str
    kind: str
    text: str | None
    structured: dict[str, object] | None


class DailyExportResponse(BaseModel):
    """One document, every daily slice, dates ascending (spec §130)."""

    generated_at: datetime
    days: int | None
    daily_features: list[DailyFeatureOut]
    daily_observations: list[DailyObservationOut]
    training_sessions: list[TrainingSessionOut]
    experiments: list[ExperimentOut]
    journal_events: list[JournalEventOut]


class RawBatchOut(BaseModel):
    batch_id: uuid.UUID
    device_id: uuid.UUID
    codec: str
    journal_version: int
    frame_count: int
    payload_sha256: str
    byte_size: int
    blob_path: str
    storage_state: str
    received_at: datetime


class RawRegistryResponse(BaseModel):
    generated_at: datetime
    note: str
    batches: list[RawBatchOut]


# ── windowing ─────────────────────────────────────────────────────────────


class _ExportWindow:
    """Optional trailing window: ``days=None`` exports ALL history."""

    def __init__(self, days: int | None) -> None:
        self.days = days
        tz = ZoneInfo(get_settings().user_timezone)
        now = datetime.now(UTC)
        self.first_day: date | None = (
            None if days is None else (now.astimezone(tz).date() - timedelta(days=days - 1))
        )
        self.ts_cutoff: datetime | None = None if days is None else now - timedelta(days=days)


# ── JSON export ───────────────────────────────────────────────────────────


async def _daily_features(session: AsyncSession, window: _ExportWindow) -> list[DailyFeature]:
    stmt = select(DailyFeature).order_by(DailyFeature.date, DailyFeature.feature_set_version)
    if window.first_day is not None:
        stmt = stmt.where(DailyFeature.date >= window.first_day)
    # Single-user derived table (no user column): versioned rows are all
    # preserved — ADR 0012 evolution writes new rows, never rewrites.
    return list((await session.execute(stmt)).scalars().all())


async def _daily_observations(
    session: AsyncSession, user_id: uuid.UUID, window: _ExportWindow
) -> list[DailyObservation]:
    stmt = (
        select(DailyObservation)
        .where(DailyObservation.user_id == user_id)
        .order_by(DailyObservation.day, DailyObservation.metric, DailyObservation.received_at)
    )
    if window.first_day is not None:
        stmt = stmt.where(DailyObservation.day >= window.first_day)
    # EVERY raw row (all devices, all decoder versions) — the JSON export
    # is the archival view; the CSV applies latest-wins resolution.
    return list((await session.execute(stmt)).scalars().all())


async def _training_sessions(
    session: AsyncSession, user_id: uuid.UUID, window: _ExportWindow
) -> list[tuple[TrainingSession, list[TrainingSet]]]:
    stmt = (
        select(TrainingSession)
        .where(TrainingSession.user_id == user_id)
        .order_by(TrainingSession.ts, TrainingSession.created_at)
    )
    if window.ts_cutoff is not None:
        stmt = stmt.where(TrainingSession.ts >= window.ts_cutoff)
    sessions = list((await session.execute(stmt)).scalars().all())
    if not sessions:
        return []
    sets = list(
        (
            await session.execute(
                select(TrainingSet)
                .where(TrainingSet.session_id.in_([s.id for s in sessions]))
                .order_by(TrainingSet.exercise, TrainingSet.set_index)
            )
        )
        .scalars()
        .all()
    )
    by_session: dict[uuid.UUID, list[TrainingSet]] = {}
    for one_set in sets:
        by_session.setdefault(one_set.session_id, []).append(one_set)
    return [(s, by_session.get(s.id, [])) for s in sessions]


async def _experiments(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Experiment, list[ExperimentDay]]]:
    experiments = list(
        (
            await session.execute(
                select(Experiment)
                .where(Experiment.user_id == user_id)
                .order_by(Experiment.started_at, Experiment.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not experiments:
        return []
    days = list(
        (
            await session.execute(
                select(ExperimentDay)
                .where(ExperimentDay.experiment_id.in_([e.id for e in experiments]))
                .order_by(ExperimentDay.day)
            )
        )
        .scalars()
        .all()
    )
    by_experiment: dict[uuid.UUID, list[ExperimentDay]] = {}
    for day in days:
        by_experiment.setdefault(day.experiment_id, []).append(day)
    return [(e, by_experiment.get(e.id, [])) for e in experiments]


async def _journal_events(
    session: AsyncSession, user_id: uuid.UUID, window: _ExportWindow
) -> list[JournalEvent]:
    stmt = (
        select(JournalEvent)
        .where(JournalEvent.user_id == user_id)
        .order_by(JournalEvent.ts, JournalEvent.created_at)
    )
    if window.ts_cutoff is not None:
        stmt = stmt.where(JournalEvent.ts >= window.ts_cutoff)
    return list((await session.execute(stmt)).scalars().all())


@export_router.get("/daily.json", response_model=DailyExportResponse)
async def export_daily_json(
    user_id: AccountJwtDep,
    session: SessionDep,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
) -> DailyExportResponse:
    """Everything daily in one JSON document, dates ascending (§130)."""
    window = _ExportWindow(days)
    features = await _daily_features(session, window)
    observations = await _daily_observations(session, user_id, window)
    training = await _training_sessions(session, user_id, window)
    experiments = await _experiments(session, user_id)
    journal = await _journal_events(session, user_id, window)

    return DailyExportResponse(
        generated_at=datetime.now(UTC),
        days=days,
        daily_features=[
            DailyFeatureOut(
                date=f.date,
                feature_set_version=f.feature_set_version,
                timezone=f.timezone,
                resting_hr=f.resting_hr,
                hr_min=f.hr_min,
                hr_mean=f.hr_mean,
                hr_max=f.hr_max,
                sample_count=f.sample_count,
                coverage_ratio=f.coverage_ratio,
                data_quality=f.data_quality,
                algorithm_version=f.algorithm_version,
                computed_at=f.computed_at,
            )
            for f in features
        ],
        daily_observations=[
            DailyObservationOut(
                day=o.day,
                metric=o.metric,
                value=o.value,
                decoder_version=o.decoder_version,
                received_at=o.received_at,
            )
            for o in observations
        ],
        training_sessions=[
            TrainingSessionOut(
                id=t.id,
                ts=t.ts,
                source=t.source,
                raw_text=t.raw_text,
                created_at=t.created_at,
                sets=[
                    TrainingSetOut(
                        exercise=s.exercise,
                        muscle_group=s.muscle_group,
                        weight_kg=s.weight_kg,
                        reps=s.reps,
                        rir=s.rir,
                        rpe=s.rpe,
                        set_index=s.set_index,
                    )
                    for s in session_sets
                ],
            )
            for t, session_sets in training
        ],
        experiments=[
            ExperimentOut(
                id=e.id,
                name=e.name,
                hypothesis=e.hypothesis,
                intervention=e.intervention,
                metric=e.metric,
                direction=e.direction,
                baseline_days=e.baseline_days,
                intervention_days=e.intervention_days,
                status=e.status,
                started_at=e.started_at,
                created_at=e.created_at,
                completed_at=e.completed_at,
                days=[
                    ExperimentDayOut(day=d.day, phase=d.phase, complied=d.complied, note=d.note)
                    for d in experiment_days
                ],
            )
            for e, experiment_days in experiments
        ],
        journal_events=[
            JournalEventOut(
                id=j.id,
                ts=j.ts,
                source=j.source,
                kind=j.kind,
                text=j.text,
                structured=j.structured,
            )
            for j in journal
        ],
    )


# ── CSV export ────────────────────────────────────────────────────────────


def _csv_line(*cells: object) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerow(cells)
    return buffer.getvalue()


async def _latest_observations(
    session: AsyncSession, user_id: uuid.UUID, window: _ExportWindow
) -> list[tuple[date, str, float]]:
    """Latest-received value per (day, metric) — same resolution as the
    daily observations read surface (received_at DESC, device_id tie-break)."""
    # DISTINCT ON stays in PostgreSQL (it owns the semantics); parameters
    # bind, the day filter is optional.
    day_filter = "" if window.first_day is None else "AND day >= :first_day"
    result = await session.execute(
        text(
            "SELECT DISTINCT ON (day, metric) day, metric, value "
            "FROM health.daily_observations "
            "WHERE user_id = :user_id " + day_filter + " "
            "ORDER BY day, metric, received_at DESC, device_id"
        ),
        {"user_id": user_id, "first_day": window.first_day},
    )
    return [(row[0], row[1], float(row[2])) for row in result.all()]


@export_router.get("/daily.csv")
async def export_daily_csv(
    user_id: AccountJwtDep,
    session: SessionDep,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
) -> StreamingResponse:
    """Flat long-form daily export: date, metric, value, source (§130).

    Sources: ``daily_features`` (the six numeric computed columns, one row
    per non-NULL feature) and ``daily_observations`` (latest-received per
    day+metric). Rows stream ascending by date, then source, then metric.
    """
    window = _ExportWindow(days)

    async def lines() -> AsyncIterator[str]:
        yield _csv_line("date", "metric", "value", "source")
        features = await _daily_features(session, window)
        feature_rows = [
            (f.date, column, getattr(f, column))
            for f in features
            for column in _FEATURE_COLUMNS
            if getattr(f, column) is not None
        ]
        observation_rows = await _latest_observations(session, user_id, window)
        combined = [
            (day, metric, value, "daily_features") for day, metric, value in feature_rows
        ] + [(day, metric, value, "daily_observations") for day, metric, value in observation_rows]
        combined.sort(key=lambda row: (row[0], row[3], row[1]))
        for day, metric, value, source in combined:
            yield _csv_line(day.isoformat(), metric, value, source)

    return StreamingResponse(
        lines(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="somatriq-daily.csv"'},
    )


# ── raw batch registry ────────────────────────────────────────────────────


@export_router.get("/raw-batches.json", response_model=RawRegistryResponse)
async def export_raw_batches(_: AccountJwtDep, session: SessionDep) -> RawRegistryResponse:
    """The raw blob registry (metadata only — see the note in the response)."""
    batches = list(
        (await session.execute(select(RawBatch).order_by(RawBatch.received_at))).scalars().all()
    )
    return RawRegistryResponse(
        generated_at=datetime.now(UTC),
        note=_RAW_BLOBS_NOTE,
        batches=[
            RawBatchOut(
                batch_id=b.batch_id,
                device_id=b.device_id,
                codec=b.codec,
                journal_version=b.journal_version,
                frame_count=b.frame_count,
                payload_sha256=b.payload_sha256,
                byte_size=b.byte_size,
                blob_path=b.blob_path,
                storage_state=b.storage_state,
                received_at=b.received_at,
            )
            for b in batches
        ],
    )
