"""Health endpoint behavior (spec §146, §8: API behavior is TDD territory)."""

import pytest
from fastapi.testclient import TestClient
from somatriq_api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_health_is_dependency_free(client: TestClient) -> None:
    """Liveness answers 200 with service identity even when nothing else is up."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "somatriq_api"


def test_ready_reports_missing_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without DATABASE_URL the service must not claim readiness."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
