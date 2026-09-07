"""M11 experiment lifecycle (spec §83-86, §82, §204; ADR 0009): N-of-1
baseline-then-intervention workflows under /api/v1/experiments.

The lifecycle, stated plainly (spec §83-84):

* CREATE starts RUNNING by observing the status quo — the BASELINE window
  is the last ``baseline_days`` local days, backfilled immediately, because
  that data is already being collected. The intervention starts the day
  after the last baseline day.
* Intervention day rows materialize one per day as days pass:
  :func:`roll_forward` inserts every missing day row through today (never
  beyond the window), called by the reads and check-ins.
* CHECK-IN upserts one day's compliance (spec §85: adherence is never
  assumed; ``complied`` defaults true and the check-in is how you say no).
* COMPLETE freezes nothing derived: status flips to completed and the
  evaluation is computed LIVE on every read from that point (ADR 0012
  spirit — nothing derived is persisted). The response carries
  ``evaluation_version`` so a future evaluation can be distinguished.

The statistics live in :mod:`somatriq_analytics.experiments` (pure,
hand-tested); the outcome series comes from
:mod:`somatriq_analytics.correlation_data` (metric names validated against
its catalog — never a free-text outcome). Evaluation uses each phase's
COMPLIED days only; non-complied days are excluded AND counted.

Auth: every surface — writes and reads — requires the account JWT (spec
§122): experiment data answers the owner's web session, never the bare URL.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from somatriq_analytics.correlation_data import (
    UnknownMetricError,
    daily_metric_series,
    is_known_metric,
)
from somatriq_analytics.experiments import (
    CAVEAT,
    Direction,
    ExperimentResult,
    evaluate,
)
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session
from somatriq_db.models import Experiment, ExperimentDay
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError
from .security import ReadUserDep
from .settings import get_settings

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

EVALUATION_VERSION = "experiment_eval_v1"

_D_DISPLAY_DIGITS = 4
_DEFAULT_BASELINE_DAYS = 14
_DEFAULT_INTERVENTION_DAYS = 14

DirectionValue = Literal["increase", "decrease", "any"]
Phase = Literal["baseline", "intervention"]


# ── request / response models ─────────────────────────────────────────────


class ExperimentCreate(BaseModel):
    """The five required fields of an experiment (spec §84 example)."""

    name: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=1, max_length=2000)
    intervention: str = Field(min_length=1, max_length=2000)
    metric: str = Field(min_length=1, max_length=64)
    direction: DirectionValue
    baseline_days: int = Field(default=_DEFAULT_BASELINE_DAYS, ge=1, le=365)
    intervention_days: int = Field(default=_DEFAULT_INTERVENTION_DAYS, ge=1, le=365)


class CheckinRequest(BaseModel):
    """One day's compliance; day defaults to today (local)."""

    day: date | None = None
    complied: bool
    note: str | None = Field(default=None, max_length=2000)


class CheckinResponse(BaseModel):
    experiment_id: uuid.UUID
    day: date
    phase: Phase
    complied: bool


class PhaseProgress(BaseModel):
    elapsed: int  # days materialized through today
    total: int  # configured phase length


class PhaseCompliance(BaseModel):
    complied: int
    total: int  # materialized days (elapsed), not configured days


class ExperimentWindow(BaseModel):
    first_day: date
    last_day: date


class EvaluationResponse(BaseModel):
    """The computed-on-read result; wording per spec §82 (never "proves")."""

    evaluation_version: str
    verdict: str
    n_baseline: int
    n_intervention: int
    mean_baseline: float | None
    mean_intervention: float | None
    mean_difference: float | None
    cohens_d: float | None
    welch_t: float | None
    welch_df: int | None
    p_value: float | None
    p_method: str
    excluded_noncomplied: int
    caveat: str


class ExperimentResponse(BaseModel):
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
    window: ExperimentWindow
    current_phase: Phase | None  # None when not running / window elapsed
    progress: dict[str, PhaseProgress]
    compliance: dict[str, PhaseCompliance]
    evaluation: EvaluationResponse | None = None  # present once completed


# ── window math + roll_forward ────────────────────────────────────────────


def _window(experiment: Experiment, tz: ZoneInfo) -> tuple[date, date, date]:
    """(first_day, start_day, last_day).

    start_day is the LAST baseline day (the local day the experiment was
    created / started); the baseline window reaches baseline_days back
    from it, the intervention runs intervention_days forward from it.
    """
    start_day = experiment.started_at.astimezone(tz).date()
    first = start_day - timedelta(days=experiment.baseline_days - 1)
    last = start_day + timedelta(days=experiment.intervention_days)
    return first, start_day, last


def _phase_of(day: date, start_day: date) -> Phase:
    return "baseline" if day <= start_day else "intervention"


def _local_today(tz: ZoneInfo) -> date:
    return datetime.now(UTC).astimezone(tz).date()


async def roll_forward(
    session: AsyncSession, experiment: Experiment, today: date, tz: ZoneInfo
) -> None:
    """Materialize every missing day row through today (never beyond the
    window); called by reads and check-ins so the ledger grows one day at
    a time as days actually pass."""
    first, start_day, last = _window(experiment, tz)
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


async def _day_rows(
    session: AsyncSession, experiment_id: uuid.UUID
) -> list[ExperimentDay]:
    return list(
        (
            await session.execute(
                select(ExperimentDay)
                .where(ExperimentDay.experiment_id == experiment_id)
                .order_by(ExperimentDay.day)
            )
        )
        .scalars()
        .all()
    )


# ── evaluation (compute-on-read; nothing derived is persisted) ───────────


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _D_DISPLAY_DIGITS)


def _evaluation_response(result: ExperimentResult, excluded: int) -> EvaluationResponse:
    return EvaluationResponse(
        evaluation_version=EVALUATION_VERSION,
        verdict=result.verdict,
        n_baseline=result.n_baseline,
        n_intervention=result.n_intervention,
        mean_baseline=_round(result.mean_baseline),
        mean_intervention=_round(result.mean_intervention),
        mean_difference=_round(result.mean_difference),
        cohens_d=_round(result.cohens_d),
        welch_t=_round(result.welch_t),
        welch_df=result.welch_df,
        p_value=_round(result.p_value),
        p_method=result.p_method,
        excluded_noncomplied=excluded,
        caveat=CAVEAT,
    )


async def _evaluate_experiment(
    session: AsyncSession, experiment: Experiment, tz: ZoneInfo
) -> EvaluationResponse:
    """Outcome-metric values over each phase's COMPLIED days only.

    Non-complied days are excluded and counted; complied days without a
    metric value that day simply contribute nothing (absent-day-honest,
    spec §158) — the n's on the result say exactly what was used.
    """
    first, _, _ = _window(experiment, tz)
    days_back = max((_local_today(tz) - first).days + 1, 1)
    series = await daily_metric_series(session, experiment.metric, days_back, tz)
    values_by_day = dict(series)

    baseline_values: list[float] = []
    intervention_values: list[float] = []
    excluded = 0
    for row in await _day_rows(session, experiment.id):
        if not row.complied:
            excluded += 1
            continue
        value = values_by_day.get(row.day)
        if value is None:
            continue
        if row.phase == "baseline":
            baseline_values.append(value)
        else:
            intervention_values.append(value)

    direction = experiment.direction
    if direction not in ("increase", "decrease", "any"):
        # Unreachable: CHECK-constrained at the database.
        raise ApiError(
            status_code=500,
            code=ErrorCode.PERMANENT,
            message=f"invalid direction stored on experiment: {direction!r}",
        )
    result = evaluate(
        baseline_values, intervention_values, cast(Direction, direction)
    )
    return _evaluation_response(result, excluded)


# ── response assembly ─────────────────────────────────────────────────────


def _progress(
    experiment: Experiment, first: date, start_day: date, last: date, today: date
) -> dict[str, PhaseProgress]:
    baseline_elapsed = min(
        max((min(today, start_day) - first).days + 1, 0), experiment.baseline_days
    )
    intervention_first = start_day + timedelta(days=1)
    intervention_elapsed = min(
        max((today - intervention_first).days + 1, 0), experiment.intervention_days
    )
    return {
        "baseline": PhaseProgress(
            elapsed=baseline_elapsed, total=experiment.baseline_days
        ),
        "intervention": PhaseProgress(
            elapsed=intervention_elapsed, total=experiment.intervention_days
        ),
    }


def _compliance(rows: list[ExperimentDay]) -> dict[str, PhaseCompliance]:
    counts: dict[str, list[int]] = {
        "baseline": [0, 0],
        "intervention": [0, 0],
    }
    for row in rows:
        complied, total = counts[row.phase]
        counts[row.phase] = [complied + (1 if row.complied else 0), total + 1]
    return {
        phase: PhaseCompliance(complied=complied, total=total)
        for phase, (complied, total) in counts.items()
    }


def _current_phase(
    experiment: Experiment, start_day: date, last: date, today: date
) -> Phase | None:
    if experiment.status != "running":
        return None
    if today <= start_day:
        return "baseline"
    if today <= last:
        return "intervention"
    return None  # window elapsed, awaiting completion


def _build_response(
    experiment: Experiment,
    rows: list[ExperimentDay],
    tz: ZoneInfo,
    evaluation: EvaluationResponse | None,
) -> ExperimentResponse:
    first, start_day, last = _window(experiment, tz)
    today = _local_today(tz)
    return ExperimentResponse(
        id=experiment.id,
        name=experiment.name,
        hypothesis=experiment.hypothesis,
        intervention=experiment.intervention,
        metric=experiment.metric,
        direction=experiment.direction,
        baseline_days=experiment.baseline_days,
        intervention_days=experiment.intervention_days,
        status=experiment.status,
        started_at=experiment.started_at,
        created_at=experiment.created_at,
        completed_at=experiment.completed_at,
        window=ExperimentWindow(first_day=first, last_day=last),
        current_phase=_current_phase(experiment, start_day, last, today),
        progress=_progress(experiment, first, start_day, last, today),
        compliance=_compliance(rows),
        evaluation=evaluation,
    )


# ── endpoints ─────────────────────────────────────────────────────────────


async def _fetch_experiment(
    session: AsyncSession, experiment_id: uuid.UUID, user_id: uuid.UUID
) -> Experiment:
    statement = select(Experiment).where(
        Experiment.id == experiment_id, Experiment.user_id == user_id
    )
    experiment = (await session.execute(statement)).scalar_one_or_none()
    if experiment is None:
        raise ApiError(
            status_code=404,
            code=ErrorCode.NOT_FOUND,
            message="no such experiment",
        )
    return experiment


@router.post("", response_model=ExperimentResponse)
@router.post("/", response_model=ExperimentResponse)
async def create_experiment(
    payload: ExperimentCreate, user_id: AccountJwtDep, session: SessionDep
) -> ExperimentResponse:
    """Create a running experiment; the baseline window (the last
    baseline_days local days) is backfilled immediately — the experiment
    starts by OBSERVING the status quo, the intervention begins the next
    day."""
    if not is_known_metric(payload.metric):
        raise ApiError(422, ErrorCode.VALIDATION, str(UnknownMetricError(payload.metric)))
    tz = ZoneInfo(get_settings().user_timezone)
    experiment = Experiment(
        user_id=user_id,
        name=payload.name,
        hypothesis=payload.hypothesis,
        intervention=payload.intervention,
        metric=payload.metric,
        direction=payload.direction,
        baseline_days=payload.baseline_days,
        intervention_days=payload.intervention_days,
        started_at=datetime.now(UTC),
    )
    session.add(experiment)
    await session.flush()
    await roll_forward(session, experiment, _local_today(tz), tz)
    await session.refresh(experiment)
    rows = await _day_rows(session, experiment.id)
    return _build_response(experiment, rows, tz, None)


@router.get("", response_model=list[ExperimentResponse])
@router.get("/", response_model=list[ExperimentResponse])
async def list_experiments(
    user_id: ReadUserDep, session: SessionDep
) -> list[ExperimentResponse]:
    """Every experiment with live progress, compliance and (once completed)
    the evaluation — account JWT or data.read device token (spec §122),
    scoped to the authenticated user."""
    tz = ZoneInfo(get_settings().user_timezone)
    today = _local_today(tz)
    experiments = list(
        (
            await session.execute(
                select(Experiment)
                .where(Experiment.user_id == user_id)
                .order_by(Experiment.created_at, Experiment.id)
            )
        )
        .scalars()
        .all()
    )
    responses: list[ExperimentResponse] = []
    for experiment in experiments:
        await roll_forward(session, experiment, today, tz)
        rows = await _day_rows(session, experiment.id)
        evaluation = (
            await _evaluate_experiment(session, experiment, tz)
            if experiment.status == "completed"
            else None
        )
        responses.append(_build_response(experiment, rows, tz, evaluation))
    return responses


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def read_experiment(
    experiment_id: uuid.UUID, user_id: ReadUserDep, session: SessionDep
) -> ExperimentResponse:
    """One experiment: current phase, compliance, and (once completed) the
    evaluation computed live on this read; account JWT or data.read device
    token (spec §122), scoped to the authenticated user."""
    tz = ZoneInfo(get_settings().user_timezone)
    experiment = await _fetch_experiment(session, experiment_id, user_id)
    await roll_forward(session, experiment, _local_today(tz), tz)
    rows = await _day_rows(session, experiment.id)
    evaluation = (
        await _evaluate_experiment(session, experiment, tz)
        if experiment.status == "completed"
        else None
    )
    return _build_response(experiment, rows, tz, evaluation)


@router.post("/{experiment_id}/checkin", response_model=CheckinResponse)
async def checkin(
    experiment_id: uuid.UUID,
    payload: CheckinRequest,
    user_id: AccountJwtDep,
    session: SessionDep,
) -> CheckinResponse:
    """Upsert one day's compliance (default today local); 422 when the day
    falls outside the experiment window or the experiment is not running."""
    tz = ZoneInfo(get_settings().user_timezone)
    experiment = await _fetch_experiment(session, experiment_id, user_id)
    if experiment.status != "running":
        raise ApiError(
            422,
            ErrorCode.VALIDATION,
            "only a running experiment accepts check-ins",
        )
    first, start_day, last = _window(experiment, tz)
    today = _local_today(tz)
    day = payload.day if payload.day is not None else today
    if day < first or day > last:
        raise ApiError(
            422,
            ErrorCode.VALIDATION,
            "day outside the experiment window "
            f"({first.isoformat()} .. {last.isoformat()})",
        )
    # Reads and check-ins both advance the ledger (spec §85): the day rows
    # through today exist before the upsert lands on one of them.
    await roll_forward(session, experiment, today, tz)
    phase = _phase_of(day, start_day)
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
            "day": day,
            "phase": phase,
            "complied": payload.complied,
            "note": payload.note,
        },
    )
    await session.commit()
    return CheckinResponse(
        experiment_id=experiment.id, day=day, phase=phase, complied=payload.complied
    )


@router.post("/{experiment_id}/complete", response_model=ExperimentResponse)
async def complete_experiment(
    experiment_id: uuid.UUID, user_id: AccountJwtDep, session: SessionDep
) -> ExperimentResponse:
    """Flip to completed and return the evaluation. Nothing derived is
    stored — every later read recomputes it (see EVALUATION_VERSION)."""
    tz = ZoneInfo(get_settings().user_timezone)
    experiment = await _fetch_experiment(session, experiment_id, user_id)
    if experiment.status == "abandoned":
        raise ApiError(
            422, ErrorCode.VALIDATION, "an abandoned experiment cannot complete"
        )
    if experiment.status != "completed":
        experiment.status = "completed"
        experiment.completed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(experiment)
    await roll_forward(session, experiment, _local_today(tz), tz)
    rows = await _day_rows(session, experiment.id)
    evaluation = await _evaluate_experiment(session, experiment, tz)
    return _build_response(experiment, rows, tz, evaluation)
