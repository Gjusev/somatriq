"""Server-side connectors (spec §126; §206 one high-value connector at a time).

Importing this package fills the CONNECTORS registry: csv (implemented, the
M13 §129 import path) plus the documented NotImplemented cloud slots
(withings, garmin, oura, health_connect). Pure CSV parsing/preview lives in
csv_import; the HTTP surface (preview/commit, connectors listing) is the
API's imports router.
"""

from .base import (
    CONNECTORS,
    STATUS_AVAILABLE,
    STATUS_ERROR,
    STATUS_NOT_CONFIGURED,
    Connector,
    ConnectorStatus,
    NotImplementedConnector,
)
from .csv_import import (
    MAX_DISPLAY_WARNINGS,
    CsvImportError,
    CsvParseResult,
    CsvWarning,
    ImportDuplicates,
    ImportPreview,
    build_preview,
    parse_daily_csv,
)
from .registry_entries import (
    CsvConnector,
    GarminConnector,
    HealthConnectConnector,
    OuraConnector,
    WithingsConnector,
)

__all__ = [
    "CONNECTORS",
    "CsvConnector",
    "CsvImportError",
    "CsvParseResult",
    "CsvWarning",
    "Connector",
    "ConnectorStatus",
    "GarminConnector",
    "HealthConnectConnector",
    "ImportDuplicates",
    "ImportPreview",
    "MAX_DISPLAY_WARNINGS",
    "NotImplementedConnector",
    "OuraConnector",
    "STATUS_AVAILABLE",
    "STATUS_ERROR",
    "STATUS_NOT_CONFIGURED",
    "WithingsConnector",
    "build_preview",
    "parse_daily_csv",
]
