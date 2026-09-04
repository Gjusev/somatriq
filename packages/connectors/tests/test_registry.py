"""Connector registry behavior (spec §126 planned list; §206 one-at-a-time).

The registry answers honestly: csv is implemented and available; the
authenticated cloud slots are documented NotImplemented entries whose
health_check reports not_configured with the reason that explains why.
"""

from datetime import date

import pytest
from somatriq_connectors import (
    CONNECTORS,
    STATUS_AVAILABLE,
    STATUS_NOT_CONFIGURED,
    CsvConnector,
    HealthConnectConnector,
    NotImplementedConnector,
)
from somatriq_contracts.observations import VENDOR_DAILY_METRICS, DailyObservationItem


def test_registry_contains_the_planned_m13_entries() -> None:
    assert sorted(CONNECTORS) == ["csv", "garmin", "health_connect", "oura", "withings"]


def test_every_entry_is_instantiable_and_named() -> None:
    for name, connector_cls in CONNECTORS.items():
        instance = connector_cls()
        assert instance.name == name


async def test_csv_health_check_is_available() -> None:
    health = await CONNECTORS["csv"]().health_check()
    assert health == {"status": STATUS_AVAILABLE}


async def test_cloud_slots_answer_not_configured_with_reason() -> None:
    for name in ("withings", "garmin", "oura", "health_connect"):
        health = await CONNECTORS[name]().health_check()
        assert health["status"] == STATUS_NOT_CONFIGURED
        assert "credentials not provided" in health["reason"]
        assert "§206" in health["reason"]


async def test_cloud_slot_data_methods_raise_not_implemented() -> None:
    connector = HealthConnectConnector()
    with pytest.raises(NotImplementedError, match="health_connect"):
        await connector.authenticate({"token": "x"})
    with pytest.raises(NotImplementedError, match="health_connect"):
        await connector.discover()
    with pytest.raises(NotImplementedError, match="health_connect"):
        await connector.backfill(date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(NotImplementedError, match="health_connect"):
        await connector.incremental_sync()
    with pytest.raises(NotImplementedError, match="health_connect"):
        await connector.normalize({"anything": 1})


async def test_csv_connector_normalizes_text_to_canonical_items() -> None:
    rows = await CsvConnector().normalize("date,weight_kg\n2026-01-05,80.0\n")
    assert rows == [DailyObservationItem(day=date(2026, 1, 5), metric="weight_kg", value=80.0)]


async def test_csv_connector_discovers_the_full_catalog() -> None:
    assert await CsvConnector().discover() == sorted(VENDOR_DAILY_METRICS)


async def test_csv_connector_has_no_credentials_and_no_cursor() -> None:
    connector = CsvConnector()
    await connector.authenticate({})  # files need no credentials — accepted
    with pytest.raises(NotImplementedError, match="/api/v1/imports"):
        await connector.backfill(date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(NotImplementedError, match="idempotent"):
        await connector.incremental_sync()


def test_not_implemented_base_is_itself_a_named_slot() -> None:
    assert NotImplementedConnector.name == "not-implemented"
