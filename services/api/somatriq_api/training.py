"""M12 strength training (spec §78-80, §104, §205): sessions, summaries and
the §80 personal response under /api/v1/training.

The parser and every §79 calculation live in
:mod:`somatriq_analytics.strength` (deterministic, pure, ADR 0009); the
daily muscular-load and sleep-window RMSSD series live in
:mod:`somatriq_analytics.correlation_data`. This module is the wire surface
only.

Honesty rules held here (spec §81-82):

* the §80 response is CORRELATIONAL — every response embeds
  CAUSAL_LANGUAGE_NOTE verbatim, exactly like the correlations matrix;
* insufficient overlap (< 14 shared days) is a valid skipped row with its
  reason, never an error and never an invented r;
* Spearman is the default method, as on the matrix (spec §88 robustness);
* summaries are computed LIVE on every read — nothing derived is persisted
  (ADR 0012 spirit).

Auth: writes require the account JWT (spec §122); reads accept the account
JWT or a data.read-scoped device token (the owner's dashboard client).
Strength data answers the owner only, never the bare URL.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from somatriq_analytics.correlation_data import (
    RESPONSE_RECOVERY_METRICS,
    TRAINING_LOAD_METRICS,
    daily_metric_series,
    rmssd_daily_series,
    training_load_series,
)
from somatriq_analytics.correlations import (
    CAUSAL_LANGUAGE_NOTE,
    MIN_OVERLAP_DAYS,
    ZERO_VARIANCE_REASON,
    bonferroni_alpha,
    join_lagged,
    pearson,
    spearman,
)
from somatriq_analytics.strength import (
    StrengthSet,
    muscle_group_for,
    normalize_exercise_name,
    session_summary,
)
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session
from somatriq_db.models import TrainingSession, TrainingSet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError
from .security import ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/training", tags=["training"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

Method = Literal["pearson", "spearman"]
_ESTIMATORS = {"pearson": pearson, "spearman": spearman}

_R_DISPLAY_DIGITS = 4
_P_DISPLAY_DIGITS = 4

_RESPONSE_LAG_DAYS = 1  # §80: session today → recovery tomorrow

_DEFAULT_DAYS = 28


# ── request / response models ─────────────────────────────────────────────


class TrainingSetInput(BaseModel):
    """One set; weight omitted = bodyweight (no external load)."""

    exercise: str = Field(min_length=1, max_length=200)
    weight_kg: float | None = Field(default=None, gt=0)
    reps: int = Field(ge=1, le=1000)
    rir: int | None = Field(default=None, ge=0, le=30)
    rpe: float | None = Field(default=None, ge=1, le=10)


class TrainingSessionCreate(BaseModel):
    ts: datetime | None = None
    raw_text: str | None = Field(default=None, max_length=4000)
    sets: list[TrainingSetInput] = Field(min_length=1, max_length=200)


class StrengthSummaryResponse(BaseModel):
    """The §79 session calculations, computed live (never persisted)."""

    set_count: int
    tonnage_kg: float
    hard_sets: int
    bodyweight_sets: int
    relative_intensity: float | None
    volume_by_group: dict[str, float]
    best_e1rm_by_exercise: dict[str, float]
    exercises: list[str]


class TrainingSessionResponse(BaseModel):
    id: uuid.UUID
    ts: datetime
    source: str
    raw_text: str | None
    summary: StrengthSummaryResponse


class TrainingWeekAggregate(BaseModel):
    """Tonnage / hard sets summed per ISO week (spec §79 weekly view)."""

    week: str  # e.g. "2026-W36"
    tonnage_kg: float
    hard_sets: int


class TrainingSessionListResponse(BaseModel):
    days: int
    sessions: list[TrainingSessionResponse]  # newest first
    weekly: list[TrainingWeekAggregate]  # oldest week first (trend order)


class ResponsePairRow(BaseModel):
    """One §80 coefficient with its evidence (spec §81: never a bare r)."""

    pair: tuple[str, str]
    n: int
    r: float
    p_value: float
    p_method: str
    band: str
    significant: bool = False  # set once α is known


class ResponseSkippedRow(BaseModel):
    pair: tuple[str, str]
    reason: str


class TrainingResponseResponse(BaseModel):
    """The §80 personal response — same shape as the correlations matrix."""

    days: int
    lag_days: int
    method: Method
    n_tests: int
    bonferroni_alpha: float
    pairs: list[ResponsePairRow] = Field(default_factory=list)
    skipped: list[ResponseSkippedRow] = Field(default_factory=list)
    note: str


# ── helpers ───────────────────────────────────────────────────────────────


def _summary_response(sets: list[TrainingSet]) -> StrengthSummaryResponse:
    summary = session_summary(
        [
            StrengthSet(
                exercise=row.exercise,
                weight_kg=row.weight_kg,
                reps=row.reps,
                rir=row.rir,
                rpe=row.rpe,
            )
            for row in sets
        ]
    )
    return StrengthSummaryResponse(
        set_count=summary.set_count,
        tonnage_kg=round(summary.tonnage_kg, 2),
        hard_sets=summary.hard_sets,
        bodyweight_sets=summary.bodyweight_sets,
        relative_intensity=(
            None
            if summary.relative_intensity is None
            else round(summary.relative_intensity, 4)
        ),
        volume_by_group={
            group: round(value, 2) for group, value in summary.volume_by_group.items()
        },
        best_e1rm_by_exercise={
            exercise: round(value, 2)
            for exercise, value in summary.best_e1rm_by_exercise.items()
        },
        exercises=summary.exercises,
    )


async def _session_with_sets(
    session: AsyncSession, session_id: uuid.UUID
) -> tuple[TrainingSession, list[TrainingSet]]:
    training_session = (
        await session.execute(
            select(TrainingSession).where(TrainingSession.id == session_id)
        )
    ).scalar_one_or_none()
    if training_session is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "no such training session")
    sets = list(
        (
            await session.execute(
                select(TrainingSet)
                .where(TrainingSet.session_id == session_id)
                .order_by(TrainingSet.exercise, TrainingSet.set_index)
            )
        )
        .scalars()
        .all()
    )
    return training_session, sets


def _iso_week(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _insufficient_reason(n: int) -> str:
    if n < MIN_OVERLAP_DAYS:
        return f"insufficient overlap (n={n}, need >= {MIN_OVERLAP_DAYS})"
    return ZERO_VARIANCE_REASON


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post("/sessions", response_model=TrainingSessionResponse)
async def create_training_session(
    payload: TrainingSessionCreate, user_id: AccountJwtDep, session: SessionDep
) -> TrainingSessionResponse:
    """Create a session + its sets (one transaction); the summary is
    computed live from what was stored. ``weight_kg`` omitted = bodyweight."""
    training_session = TrainingSession(
        user_id=user_id,
        ts=payload.ts if payload.ts is not None else datetime.now(UTC),
        source="api",
        raw_text=payload.raw_text,
    )
    session.add(training_session)
    await session.flush()
    for index, one_set in enumerate(payload.sets):
        session.add(
            TrainingSet(
                session_id=training_session.id,
                exercise=normalize_exercise_name(one_set.exercise),
                muscle_group=muscle_group_for(one_set.exercise),
                weight_kg=one_set.weight_kg,
                reps=one_set.reps,
                rir=one_set.rir,
                rpe=one_set.rpe,
                set_index=index,
            )
        )
    await session.commit()
    stored, sets = await _session_with_sets(session, training_session.id)
    return TrainingSessionResponse(
        id=stored.id,
        ts=stored.ts,
        source=stored.source,
        raw_text=stored.raw_text,
        summary=_summary_response(sets),
    )


@router.get("/sessions", response_model=TrainingSessionListResponse)
async def list_training_sessions(
    user_id: ReadUserDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = _DEFAULT_DAYS,
) -> TrainingSessionListResponse:
    """Sessions over the last ``days`` local days with live summaries and
    weekly (ISO-week) tonnage / hard-set aggregates; account JWT or
    data.read device token (spec §122), scoped to the authenticated user."""
    tz = ZoneInfo(get_settings().user_timezone)
    cutoff = datetime.now(UTC) - timedelta(days=days)
    sessions = list(
        (
            await session.execute(
                select(TrainingSession)
                .where(TrainingSession.user_id == user_id, TrainingSession.ts >= cutoff)
                .order_by(TrainingSession.ts.desc())
            )
        )
        .scalars()
        .all()
    )
    responses: list[TrainingSessionResponse] = []
    weekly: dict[str, list[float]] = {}
    for training_session in sessions:
        sets = list(
            (
                await session.execute(
                    select(TrainingSet)
                    .where(TrainingSet.session_id == training_session.id)
                    .order_by(TrainingSet.exercise, TrainingSet.set_index)
                )
            )
            .scalars()
            .all()
        )
        summary = _summary_response(sets)
        responses.append(
            TrainingSessionResponse(
                id=training_session.id,
                ts=training_session.ts,
                source=training_session.source,
                raw_text=training_session.raw_text,
                summary=summary,
            )
        )
        week = _iso_week(training_session.ts.astimezone(tz).date())
        totals = weekly.setdefault(week, [0.0, 0.0])
        totals[0] += summary.tonnage_kg
        totals[1] += summary.hard_sets
    return TrainingSessionListResponse(
        days=days,
        sessions=responses,
        weekly=[
            TrainingWeekAggregate(week=week, tonnage_kg=totals[0], hard_sets=int(totals[1]))
            for week, totals in sorted(weekly.items())
        ],
    )


@router.get("/response", response_model=TrainingResponseResponse)
async def read_training_response(
    user_id: ReadUserDep,
    session: SessionDep,
    days: Annotated[int, Query(ge=14, le=365)] = 90,
    method: Annotated[Method, Query(pattern="^(pearson|spearman)$")] = "spearman",
) -> TrainingResponseResponse:
    """The §80 personal response: muscular load (daily tonnage, daily hard
    sets) vs NEXT-day recovery (resting_hr, sleep-window RMSSD).

    Lag is fixed at +1 day (session today → recovery tomorrow, spec §80).
    Correlational by construction — the note rides on every response and
    no coefficient exists below 14 shared days (skipped, with the reason).
    """
    tz = ZoneInfo(get_settings().user_timezone)
    estimator = _ESTIMATORS[method]

    load_series = {
        metric: await training_load_series(session, metric, days, tz)
        for metric in TRAINING_LOAD_METRICS
    }
    recovery_series: dict[str, list[tuple[date, float]]] = {
        "resting_hr": await daily_metric_series(session, "resting_hr", days, tz),
        "rmssd": await rmssd_daily_series(session, days, tz),
    }

    computed: list[tuple[ResponsePairRow, float]] = []
    skipped: list[ResponseSkippedRow] = []
    for load_metric in TRAINING_LOAD_METRICS:
        for recovery_metric in RESPONSE_RECOVERY_METRICS:
            xs, ys = join_lagged(
                load_series[load_metric], recovery_series[recovery_metric],
                _RESPONSE_LAG_DAYS,
            )
            result = estimator(xs, ys)
            if result is None:
                skipped.append(
                    ResponseSkippedRow(
                        pair=(load_metric, recovery_metric),
                        reason=_insufficient_reason(len(xs)),
                    )
                )
                continue
            computed.append(
                (
                    ResponsePairRow(
                        pair=(load_metric, recovery_metric),
                        n=result.n,
                        r=round(result.r, _R_DISPLAY_DIGITS),
                        p_value=round(result.p_value, _P_DISPLAY_DIGITS),
                        p_method=result.p_method,
                        band=result.band,
                    ),
                    result.p_value,
                )
            )

    alpha = bonferroni_alpha(len(computed))
    rows = [row for row, _ in computed]
    for row, p_value in computed:
        row.significant = p_value < alpha
    rows.sort(key=lambda row: (-abs(row.r), row.pair))

    return TrainingResponseResponse(
        days=days,
        lag_days=_RESPONSE_LAG_DAYS,
        method=method,
        n_tests=len(rows),
        bonferroni_alpha=alpha,
        pairs=rows,
        skipped=skipped,
        note=CAUSAL_LANGUAGE_NOTE,
    )
