"""Explore data assembly (Block 3; grill P12; ADR 0013/0017).

Window-based day-grain series over the same three families the
correlation catalog reads (computed / vendor daily / journal counts) —
absent-day-honest: a day without the value simply does not appear, gaps
stay gaps, nothing is zero-filled (spec §158). The context read gathers
the longitudinal overlays: device boundaries, algorithm versions with
first-seen dates, journal kinds, training days and timezone changes.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from somatriq_contracts.daily import FEATURE_SET_VERSION
from somatriq_contracts.explore import (
    ExploreAlgorithmVersion,
    ExploreDeviceBoundary,
    ExploreJournalKind,
    ExploreMetricSeries,
    ExplorePoint,
    ExploreTimezoneChange,
    ExploreTrainingDay,
)
from somatriq_contracts.observations import VENDOR_DAILY_METRICS
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .correlation_data import (
    COMPUTED_DAILY_METRICS,
    JOURNAL_COUNT_METRICS,
    UnknownMetricError,
)

_COMPUTED_SQL = """
    SELECT date, {column} AS value, coverage_ratio
    FROM derived.daily_features
    WHERE feature_set_version = :fsv AND date >= :first AND date <= :last
      AND {column} IS NOT NULL
    ORDER BY date
"""

_VENDOR_SQL = """
    SELECT DISTINCT ON (day) day, value, device_id
    FROM health.daily_observations
    WHERE metric = :metric AND day >= :first AND day <= :last
    ORDER BY day, received_at DESC, device_id
"""

_DEVICE_NAME_SQL = "SELECT id, name FROM identity.devices"

_JOURNAL_KIND_SQL = """
    SELECT kind, count(DISTINCT (ts AT TIME ZONE :tz)::date)::int AS days
    FROM health.journal_events
    WHERE ts >= :first_start AND ts < :last_end
    GROUP BY kind ORDER BY kind
"""

_TRAINING_SQL = """
    SELECT (ts AT TIME ZONE :tz)::date AS day, count(*)::int AS sessions
    FROM health.training_sessions
    WHERE ts >= :first_start AND ts < :last_end
    GROUP BY 1 ORDER BY 1
"""

_TIMEZONE_SQL = """
    SELECT DISTINCT ON (date) date, timezone
    FROM derived.daily_features
    WHERE date >= :first AND date <= :last
    ORDER BY date, feature_set_version DESC
"""

_ALGORITHMS_SQL = "SELECT name, description FROM system.algorithms ORDER BY name"

_FIRST_SEEN_SQL = """
    SELECT algorithm_version, min(date) AS first_seen
    FROM (
        SELECT algorithm_version, date FROM derived.daily_features
        WHERE algorithm_version IS NOT NULL AND date >= :first AND date <= :last
        UNION ALL
        SELECT algorithm_version, date FROM derived.daily_derived
        WHERE date >= :first AND date <= :last
    ) versions
    GROUP BY algorithm_version
"""


def _day_bounds(first: date, last: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[first local midnight, day after last local midnight) in UTC."""
    start = datetime.combine(first, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def _source_kind(metric: str) -> str:
    if metric in COMPUTED_DAILY_METRICS:
        return "computed"
    if metric in JOURNAL_COUNT_METRICS:
        return "journal"
    return "vendor_daily"


async def _series(
    session: AsyncSession, metric: str, first: date, last: date, tz: ZoneInfo
) -> ExploreMetricSeries:
    source_kind = _source_kind(metric)
    if (
        metric not in COMPUTED_DAILY_METRICS
        and metric not in JOURNAL_COUNT_METRICS
        and metric not in VENDOR_DAILY_METRICS
    ):
        raise UnknownMetricError(metric)
    if source_kind == "computed":
        column = COMPUTED_DAILY_METRICS[metric]
        rows = (
            await session.execute(
                text(_COMPUTED_SQL.format(column=column)),  # noqa: S608 — fixed whitelist
                {"fsv": FEATURE_SET_VERSION, "first": first, "last": last},
            )
        ).all()
        points = [
            ExplorePoint(date=day, value=float(value), coverage=float(coverage))
            for day, value, coverage in rows
        ]
        return ExploreMetricSeries(
            metric=metric, source_kind=source_kind, dominant_device=None, days=points
        )

    if source_kind == "vendor_daily":
        rows = (
            await session.execute(
                text(_VENDOR_SQL), {"metric": metric, "first": first, "last": last}
            )
        ).all()
        device_names: dict[uuid.UUID, str] = {
            device_id: name
            for device_id, name in (await session.execute(text(_DEVICE_NAME_SQL))).all()
        }
        counts: dict[str, int] = {}
        for _, _, device_id in rows:
            name = device_names.get(device_id, "unknown")
            counts[name] = counts.get(name, 0) + 1
        dominant = max(counts, key=counts.get) if counts else None  # type: ignore[arg-type]
        points = [
            ExplorePoint(date=day, value=float(value), coverage=None) for day, value, _ in rows
        ]
        return ExploreMetricSeries(
            metric=metric, source_kind=source_kind, dominant_device=dominant, days=points
        )

    # journal counts: local-day bucketing in SQL (ADR 0017).
    kind = JOURNAL_COUNT_METRICS[metric]
    start, end = _day_bounds(first, last, tz)
    rows = (
        await session.execute(
            text(
                "SELECT (ts AT TIME ZONE :tz)::date AS day, count(*)::float AS value "
                "FROM health.journal_events "
                "WHERE kind = :kind AND ts >= :first_start AND ts < :last_end "
                "GROUP BY 1 ORDER BY 1"
            ),
            {"tz": tz.key, "kind": kind, "first_start": start, "last_end": end},
        )
    ).all()
    points = [ExplorePoint(date=day, value=float(value), coverage=None) for day, value in rows]
    return ExploreMetricSeries(
        metric=metric, source_kind=source_kind, dominant_device=None, days=points
    )


async def explore_series(
    session: AsyncSession,
    *,
    metrics: list[str],
    first: date,
    last: date,
    tz: ZoneInfo,
) -> list[ExploreMetricSeries]:
    """Day-grain series per requested metric, gaps honest, order stable."""
    return [await _series(session, metric, first, last, tz) for metric in metrics]


async def explore_context(
    session: AsyncSession, *, first: date, last: date, tz: ZoneInfo
) -> dict[str, Any]:
    """The longitudinal overlays for the window (plan §3.1)."""
    start, end = _day_bounds(first, last, tz)

    devices = [
        ExploreDeviceBoundary(
            id=str(device_id),
            name=str(name),
            model=str(model),
            active_from=active_from,
            active_to=active_to,
        )
        for device_id, name, model, active_from, active_to in (
            await session.execute(
                text(
                    "SELECT id, name, model, active_from::date, active_to::date "
                    "FROM identity.devices "
                    "WHERE active_from::date <= :last "
                    "AND (active_to IS NULL OR active_to::date >= :first) "
                    "ORDER BY active_from"
                ),
                {"first": first, "last": last},
            )
        ).all()
    ]

    first_seen: dict[str, date] = {
        str(version): seen
        for version, seen in (
            await session.execute(text(_FIRST_SEEN_SQL), {"first": first, "last": last})
        ).all()
    }
    algorithms = [
        ExploreAlgorithmVersion(
            name=str(name),
            description=str(description),
            first_seen=first_seen.get(str(name)),
        )
        for name, description in (await session.execute(text(_ALGORITHMS_SQL))).all()
    ]

    journal_kinds = [
        ExploreJournalKind(kind=str(kind), days=int(days))
        for kind, days in (
            await session.execute(
                text(_JOURNAL_KIND_SQL),
                {"tz": tz.key, "first_start": start, "last_end": end},
            )
        ).all()
    ]

    training_days = [
        ExploreTrainingDay(date=day, sessions=int(sessions))
        for day, sessions in (
            await session.execute(
                text(_TRAINING_SQL),
                {"tz": tz.key, "first_start": start, "last_end": end},
            )
        ).all()
    ]

    # Timezone change points: the first row of each run of equal timezones.
    runs = (await session.execute(text(_TIMEZONE_SQL), {"first": first, "last": last})).all()
    changes: list[ExploreTimezoneChange] = []
    previous: str | None = None
    for day, zone in runs:
        if zone != previous:
            changes.append(ExploreTimezoneChange(date=day, timezone=str(zone)))
            previous = str(zone)

    return {
        "devices": devices,
        "algorithms": algorithms,
        "journal_kinds": journal_kinds,
        "training_days": training_days,
        "timezone_changes": changes,
    }
