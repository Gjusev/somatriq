"""Registry entries (spec §126 planned list; §206 one-at-a-time policy).

``csv`` is the M13 high-value import path (§129 — a Withings scale export,
a NOOP CSV, a hand-kept log) and is fully implemented. The authenticated
cloud sources (Withings/Garmin/Oura cloud APIs, Health Connect on Android)
stay as documented :class:`NotImplementedConnector` slots: the registry
answers ``not_configured`` instead of shipping half-working integrations.
"""

from collections.abc import Mapping
from datetime import date

from somatriq_contracts.observations import VENDOR_DAILY_METRICS, DailyObservationItem

from .base import (
    CONNECTORS,
    STATUS_AVAILABLE,
    Connector,
    NotImplementedConnector,
)
from .csv_import import parse_daily_csv


class CsvConnector(Connector):
    """§129 CSV import — a stateless file connector (the M13 slice).

    A file import has no credentials, no cursor and no vendor API: the
    preview + commit flow (/api/v1/imports) drives it end to end. ``backfill``
    and ``incremental_sync`` are honestly NotImplemented rather than
    pretending files have windows or cursors; re-importing the same file is
    idempotent by content hash at the API layer.
    """

    name = "csv"

    async def authenticate(self, credentials: Mapping[str, str]) -> None:
        """No credentials exist for file imports — accepted and ignored."""

    async def discover(self) -> list[str]:
        """The full frozen daily-metric catalog (any column may appear)."""
        return sorted(VENDOR_DAILY_METRICS)

    async def backfill(self, start: date, end: date) -> list[DailyObservationItem]:
        raise NotImplementedError(
            "csv import is a one-shot preview + commit flow (/api/v1/imports), "
            "not a windowed backfill"
        )

    async def incremental_sync(self) -> list[DailyObservationItem]:
        raise NotImplementedError(
            "csv files have no cursor; re-import is idempotent by content hash "
            "(/api/v1/imports)"
        )

    async def normalize(self, raw: object) -> list[DailyObservationItem]:
        """CSV text → canonical observation candidates (pure parse)."""
        return parse_daily_csv(str(raw)).rows

    async def health_check(self) -> dict[str, str]:
        return {"status": STATUS_AVAILABLE}


class WithingsConnector(NotImplementedConnector):
    """Withings cloud API (scale data arrives via CSV export today)."""

    name = "withings"


class GarminConnector(NotImplementedConnector):
    """Garmin Connect cloud API."""

    name = "garmin"


class OuraConnector(NotImplementedConnector):
    """Oura cloud API."""

    name = "oura"


class HealthConnectConnector(NotImplementedConnector):
    """Android Health Connect on-device hub (§127 — a connector, not core)."""

    name = "health_connect"


CONNECTORS.update(
    csv=CsvConnector,
    withings=WithingsConnector,
    garmin=GarminConnector,
    oura=OuraConnector,
    health_connect=HealthConnectConnector,
)
