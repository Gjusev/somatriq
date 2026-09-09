"""Health Monitor PDF report behavior (Block 3; ADR 0009).

The sections are the deterministic core under test: every vital renders,
the disclaimer block is present, the frozen forbidden-terms vocabulary
never reaches the document, and the rendered bytes are a real PDF.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from somatriq_api.health_report import (
    FORBIDDEN_TERMS,
    render_health_report,
    report_sections,
)
from somatriq_api.main import app
from somatriq_contracts.health_monitor import (
    HEALTH_MONITOR_DISCLAIMER,
    HealthMonitorResponse,
    VitalSummary,
)
from somatriq_db.engine import get_engine
from somatriq_db.testing import requires_db
from sqlalchemy.ext.asyncio import AsyncSession

REPORT = "/api/v1/health-monitor/report.pdf"


@pytest.fixture(autouse=True)
async def fresh_connection_pool() -> AsyncIterator[None]:
    await get_engine().dispose(close=False)
    yield
    await get_engine().dispose(close=False)


@pytest.fixture()
async def api(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    await get_engine().dispose(close=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def _response() -> HealthMonitorResponse:
    return HealthMonitorResponse(
        days=30,
        timezone="UTC",
        vitals=[
            VitalSummary(
                vital="spo2_pct",
                label="SpO2",
                source="vendor daily",
                n_days=30,
                coverage=1.0,
                period_median=98.0,
                baseline_median=97.0,
                baseline_iqr=1.0,
                robust_z=2.0,
                status="elevated",
                note="above your baseline (z=+2.0)",
            )
        ],
    )


def test_sections_carry_vitals_and_disclaimer() -> None:
    sections = report_sections(_response(), generated_on=__import__("datetime").date(2026, 9, 9))
    flattened = "\n".join("\n".join(section) for section in sections)
    assert "SpO2" in flattened
    assert "coverage 100%" in flattened
    assert "vendor daily" in flattened
    assert HEALTH_MONITOR_DISCLAIMER in flattened
    narrative = flattened.lower().replace(HEALTH_MONITOR_DISCLAIMER.lower(), "")
    for term in FORBIDDEN_TERMS:
        assert term not in narrative  # the disclaimer is exempt by design


def test_rendered_bytes_are_a_pdf() -> None:
    payload = render_health_report(_response())
    assert payload[:5] == b"%PDF-"
    assert len(payload) > 500


@requires_db
async def test_report_endpoint_streams_pdf(api: httpx.AsyncClient) -> None:
    registered = await api.post(
        "/api/v1/auth/register",
        json={"username": "local", "password": "correct-horse-battery"},
    )
    assert registered.status_code == 200, registered.text
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    response = await api.get(REPORT, params={"days": 30}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert "somatriq-health-monitor-30d.pdf" in response.headers["content-disposition"]

    unauthenticated = await api.get(REPORT)
    assert unauthenticated.status_code == 401, unauthenticated.text

    bad_period = await api.get(REPORT, params={"days": 45}, headers=headers)
    assert bad_period.status_code == 422, bad_period.text
