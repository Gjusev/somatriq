"""Spec §126 Connector interface.

Every external data source is a Connector with six async operations
(authenticate / discover / backfill / incremental_sync / normalize /
health_check) normalizing to canonical observations — Health Connect
semantics never leak into the core tables directly (§127, ADR 0012 vendor
scores are Observations). ``CONNECTORS`` is the registry the API reads;
entries are contributed by :mod:`somatriq_connectors.registry_entries`
(imported for its side effect by the package ``__init__``).

§206 policy: one high-value connector at a time, never five half-working
integrations. M13 ships CSV (§129); the authenticated cloud sources stay as
documented :class:`NotImplementedConnector` slots whose ``health_check``
answers ``not_configured`` — the registry stays honest about what exists.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from somatriq_contracts.observations import DailyObservationItem

# Registry-facing health states (§126 health_check "status" values).
STATUS_AVAILABLE = "available"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class ConnectorStatus:
    """One registry entry's health, as served by GET /api/v1/connectors."""

    name: str
    status: str  # available | not_configured | error
    reason: str | None = None


class Connector(Protocol):
    """The §126 connector interface — every method is async.

    Normalization target is the frozen ``DailyObservationItem`` contract;
    raw source payloads stay preserved by the ingest layer (ADR 0013), the
    connector only maps source semantics onto canonical observations.
    """

    name: str

    async def authenticate(self, credentials: Mapping[str, str]) -> None:
        """§126 authenticate: verify/refresh source credentials.

        Raises on invalid or expired credentials; a connector that needs no
        credentials (e.g. local file imports) accepts and returns None.
        """

    async def discover(self) -> list[str]:
        """§126 discover: which metric streams this source can provide.

        Returns catalog metric names (VENDOR_DAILY_METRICS keys); used to
        show the user what a source would contribute before connecting it.
        """

    async def backfill(self, start: date, end: date) -> list[DailyObservationItem]:
        """§126 backfill: pull the historical window [start, end] inclusive.

        The inclusive-local-day window maps onto wake-dates; the caller owns
        idempotency via the ingest batch machinery (ADR 0006).
        """

    async def incremental_sync(self) -> list[DailyObservationItem]:
        """§126 incremental_sync: everything since the connector's cursor.

        Sync retries must never create duplicates — the connector returns
        canonical items and the ingest natural keys dedup (ADR 0006).
        """

    async def normalize(self, raw: object) -> list[DailyObservationItem]:
        """§126 normalize: one source payload → canonical observations.

        Pure mapping, no storage side effects; source payload metadata is
        preserved upstream (§127), semantics are never silently rewritten
        (ADR 0012).
        """

    async def health_check(self) -> dict[str, str]:
        """§126 health_check: ``{"status": ..., "reason": ...}``.

        available | not_configured | error — reported verbatim by the
        registry endpoint; a half-working connector must say so here.
        """


CONNECTORS: dict[str, type[Connector]] = {}
"""Registry name → connector class (§126 planned list; §206 one-at-a-time)."""


class NotImplementedConnector(Connector):
    """Base for a documented future cloud slot (§206).

    The data methods raise :class:`NotImplementedError` and ``health_check``
    answers ``not_configured`` with the reason a user needs to understand
    why. Subclasses set ``name`` and document the vendor in their docstring.
    """

    name = "not-implemented"

    async def authenticate(self, credentials: Mapping[str, str]) -> None:
        raise NotImplementedError(
            f"{self.name}: connector is a documented NotImplemented slot (spec §206)"
        )

    async def discover(self) -> list[str]:
        raise NotImplementedError(
            f"{self.name}: connector is a documented NotImplemented slot (spec §206)"
        )

    async def backfill(self, start: date, end: date) -> list[DailyObservationItem]:
        raise NotImplementedError(
            f"{self.name}: connector is a documented NotImplemented slot (spec §206)"
        )

    async def incremental_sync(self) -> list[DailyObservationItem]:
        raise NotImplementedError(
            f"{self.name}: connector is a documented NotImplemented slot (spec §206)"
        )

    async def normalize(self, raw: object) -> list[DailyObservationItem]:
        raise NotImplementedError(
            f"{self.name}: connector is a documented NotImplemented slot (spec §206)"
        )

    async def health_check(self) -> dict[str, str]:
        return {
            "status": STATUS_NOT_CONFIGURED,
            "reason": "credentials not provided; see ADR 0008/spec §206",
        }
