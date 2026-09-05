"""M10 correlation reads (spec §81-82, §203; ADR 0009): GET /pair and
GET /matrix under /api/v1/correlations.

The coefficient math is somatriq_analytics.correlations (deterministic,
stdlib-only, hand-tested); the series loading is
somatriq_analytics.correlation_data (one name → one dated daily series
across derived features, vendor observations and journal counts). This
module is the wire surface only.

Honesty rules held here (spec §81-82, §88):

* every response embeds CAUSAL_LANGUAGE_NOTE verbatim — no causal claims
  from observational co-movement, ever;
* insufficient overlap (< 14 shared days) is a VALID 200 null result with
  the reason, never an error and never an invented r;
* /matrix reports n_tests and a Bonferroni-adjusted threshold, and lists
  the pairs it skipped with why — multiple comparisons are not hidden;
* Spearman is the default method: rank correlation is robust to the
  outliers and non-normal daily values wearable data produces (spec §88 —
  simple and honest beats elaborate). Pearson stays one parameter away.

Reads are behind the account JWT like the other metric reads (spec §122):
coefficients over the owner's health data answer the owner's web session,
never the bare URL.
"""

from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from somatriq_analytics.correlation_data import (
    MATRIX_METRICS,
    UnknownMetricError,
    is_known_metric,
    lagged_pair,
    matrix_series,
)
from somatriq_analytics.correlations import (
    CAUSAL_LANGUAGE_NOTE,
    FAMILY_ALPHA,
    MIN_OVERLAP_DAYS,
    ZERO_VARIANCE_REASON,
    CorrelationResult,
    bonferroni_alpha,
    join_lagged,
    pearson,
    spearman,
)
from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError
from .settings import get_settings

router = APIRouter(prefix="/api/v1/correlations", tags=["correlations"])

Method = Literal["pearson", "spearman"]

_ESTIMATORS = {"pearson": pearson, "spearman": spearman}

_R_DISPLAY_DIGITS = 4
_P_DISPLAY_DIGITS = 4


class PairCorrelationResponse(BaseModel):
    """A computed pair — a coefficient with its evidence (spec §81)."""

    metric_a: str
    metric_b: str
    lag_days: int
    method: Method
    n: int
    r: float
    p_value: float
    p_method: str
    band: str
    significant: bool
    alpha: float
    note: str


class PairInsufficientResponse(BaseModel):
    """Insufficient data is a valid answer, stated with its reason."""

    metric_a: str
    metric_b: str
    lag_days: int
    method: Method
    result: None = None
    reason: str
    note: str


def _require_known(metric: str) -> None:
    if not is_known_metric(metric):
        raise ApiError(422, ErrorCode.VALIDATION, str(UnknownMetricError(metric)))


def _round_r(value: float) -> float:
    return round(value, _R_DISPLAY_DIGITS)


def _insufficient_reason(n: int) -> str:
    if n < MIN_OVERLAP_DAYS:
        return f"insufficient overlap (n={n}, need >= {MIN_OVERLAP_DAYS})"
    return ZERO_VARIANCE_REASON  # n was enough; one series simply never moved


@router.get(
    "/pair",
    response_model=PairCorrelationResponse | PairInsufficientResponse,
)
async def read_pair(
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    metric_a: Annotated[str, Query(min_length=1, max_length=64)],
    metric_b: Annotated[str, Query(min_length=1, max_length=64)],
    days: Annotated[int, Query(ge=14, le=365)] = 60,
    lag: Annotated[int, Query(ge=-7, le=7)] = 0,
    method: Annotated[Method, Query(pattern="^(pearson|spearman)$")] = "spearman",
) -> PairCorrelationResponse | PairInsufficientResponse:
    """One metric pair over the last ``days`` local days (default spearman).

    ``lag=1`` asks "a today vs b tomorrow" (spec §80). Insufficient overlap
    returns 200 with ``result: null`` and the reason — an honest empty
    answer, not an error.
    """
    _require_known(metric_a)
    _require_known(metric_b)
    tz = ZoneInfo(get_settings().user_timezone)
    estimator = _ESTIMATORS[method]

    n, xs, ys = await lagged_pair(session, metric_a, metric_b, days, tz, lag)
    result: CorrelationResult | None = estimator(xs, ys)
    if result is None:
        return PairInsufficientResponse(
            metric_a=metric_a,
            metric_b=metric_b,
            lag_days=lag,
            method=method,
            reason=_insufficient_reason(n),
            note=CAUSAL_LANGUAGE_NOTE,
        )
    return PairCorrelationResponse(
        metric_a=metric_a,
        metric_b=metric_b,
        lag_days=lag,
        method=method,
        n=result.n,
        r=_round_r(result.r),
        p_value=round(result.p_value, _P_DISPLAY_DIGITS),
        p_method=result.p_method,
        band=result.band,
        significant=result.p_value < FAMILY_ALPHA,  # single test: nominal α
        alpha=FAMILY_ALPHA,
        note=CAUSAL_LANGUAGE_NOTE,
    )


class MatrixPairRow(BaseModel):
    pair: tuple[str, str]
    n: int
    r: float
    band: str
    # Placeholder until α is known; always set before the response leaves.
    significant: bool = False


class MatrixSkippedRow(BaseModel):
    pair: tuple[str, str]
    reason: str


class MatrixResponse(BaseModel):
    days: int
    method: Method
    n_tests: int
    bonferroni_alpha: float
    pairs: list[MatrixPairRow] = Field(default_factory=list)
    skipped: list[MatrixSkippedRow] = Field(default_factory=list)
    note: str


@router.get("/matrix", response_model=MatrixResponse)
async def read_matrix(
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=14, le=365)] = 90,
    method: Annotated[Method, Query(pattern="^(pearson|spearman)$")] = "spearman",
) -> MatrixResponse:
    """Every catalog pair over the last ``days`` local days, sorted by |r|.

    The catalog is curated (MATRIX_METRICS: our computed heart metrics, a
    curated vendor set, caffeine_count) because an unbounded pairwise scan
    multiplies noise. ``n_tests`` counts the pairs actually computed and the
    significance threshold is Bonferroni-adjusted for them; pairs without
    enough shared days are skipped and listed with their reasons.
    """
    tz = ZoneInfo(get_settings().user_timezone)
    estimator = _ESTIMATORS[method]

    series = await matrix_series(session, MATRIX_METRICS, days, tz)

    computed: list[tuple[MatrixPairRow, float]] = []
    skipped: list[MatrixSkippedRow] = []
    for i, metric_a in enumerate(MATRIX_METRICS):
        for metric_b in MATRIX_METRICS[i + 1 :]:
            xs, ys = join_lagged(series[metric_a], series[metric_b], 0)
            result = estimator(xs, ys)
            if result is None:
                skipped.append(
                    MatrixSkippedRow(
                        pair=(metric_a, metric_b),
                        reason=_insufficient_reason(len(xs)),
                    )
                )
                continue
            computed.append(
                (
                    MatrixPairRow(
                        pair=(metric_a, metric_b),
                        n=result.n,
                        r=_round_r(result.r),
                        band=result.band,
                    ),
                    result.p_value,
                )
            )

    # Multiple-comparisons honesty (spec §88): α is Bonferroni-adjusted for
    # the number of coefficients actually computed.
    alpha = bonferroni_alpha(len(computed))
    rows = [row for row, _ in computed]
    for row, p_value in computed:
        row.significant = p_value < alpha
    # |r| descending; pair labels as the deterministic tie-break.
    rows.sort(key=lambda row: (-abs(row.r), row.pair))

    return MatrixResponse(
        days=days,
        method=method,
        n_tests=len(rows),
        bonferroni_alpha=alpha,
        pairs=rows,
        skipped=skipped,
        note=CAUSAL_LANGUAGE_NOTE,
    )
