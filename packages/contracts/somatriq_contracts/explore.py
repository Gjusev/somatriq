"""Explore wire contracts (Block 3; grill P12/P13; ADR 0014).

Day-grain only — ONE honest server contract: gaps stay gaps (absent days
simply do not appear), week/month/year aggregation happens client-side
from <= ~1100 points, and the span is capped at a frozen 3 years.
Annotations are the owner's narrative (P13): system facts stay computed
overlays and are never annotation rows.
"""

from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXPLORE_MAX_SPAN_DAYS = 1096  # frozen: 3 years (plan §3.1, grill P12)

ExploreSourceKind = str  # "computed" | "vendor_daily" | "journal"


class ExplorePoint(BaseModel):
    date: date_type
    value: float
    """Present only for computed metrics (daily_features coverage)."""
    coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class ExploreMetricSeries(BaseModel):
    metric: str
    source_kind: ExploreSourceKind
    dominant_device: str | None = None
    days: list[ExplorePoint] = Field(default_factory=list)


class ExploreSeriesResponse(BaseModel):
    from_date: date_type
    to_date: date_type
    timezone: str
    metrics: list[ExploreMetricSeries] = Field(default_factory=list)


class ExploreDeviceBoundary(BaseModel):
    id: str
    name: str
    model: str
    active_from: date_type
    active_to: date_type | None = None


class ExploreAlgorithmVersion(BaseModel):
    name: str
    description: str
    first_seen: date_type | None = None


class ExploreJournalKind(BaseModel):
    kind: str
    days: int


class ExploreTrainingDay(BaseModel):
    date: date_type
    sessions: int


class ExploreTimezoneChange(BaseModel):
    date: date_type
    timezone: str


class ExploreContextResponse(BaseModel):
    from_date: date_type
    to_date: date_type
    timezone: str
    devices: list[ExploreDeviceBoundary] = Field(default_factory=list)
    algorithms: list[ExploreAlgorithmVersion] = Field(default_factory=list)
    journal_kinds: list[ExploreJournalKind] = Field(default_factory=list)
    training_days: list[ExploreTrainingDay] = Field(default_factory=list)
    timezone_changes: list[ExploreTimezoneChange] = Field(default_factory=list)


class AnnotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date_type
    date_to: date_type | None = None
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class AnnotationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date_type | None = None
    date_to: date_type | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        return value


class AnnotationOut(BaseModel):
    id: str
    date_from: date_type
    date_to: date_type | None = None
    title: str
    note: str | None = None
    created_at: str
    updated_at: str
