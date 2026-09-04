"""Metric read contracts (spec §71, §116, §174 — viewport-appropriate series)."""

from datetime import datetime

from pydantic import BaseModel, Field


class HeartRatePoint(BaseModel):
    ts: datetime
    bpm: float
    sample_count: int = Field(default=1, description=">1 when bucket aggregation is applied")


class MetricSeriesRequest(BaseModel):
    """Query params model for GET /api/v1/metrics/heart_rate."""

    from_ts: datetime | None = None
    to_ts: datetime | None = None
    last_hours: int = Field(default=24, ge=1, le=24 * 400)
    bucket: str = Field(default="none", pattern="^(none|1m|5m|1h)$")


class MetricSeriesResponse(BaseModel):
    metric: str = "heart_rate"
    unit: str = "bpm"
    points: list[HeartRatePoint]
    count: int
    coverage: float = Field(
        ge=0.0, le=1.0, description="Proportion of expected data present (spec §99 envelope)"
    )
    caveats: list[str] = Field(default_factory=list)
    generated_at: datetime
