"""Health paths stay exactly as the M0 stub served them (compose contract).

/health answers the in-container Docker healthcheck (direct port 8100);
/mcp/health answers under the public single-origin /mcp prefix (ADR 0007).
Neither requires a token — liveness is unauthenticated by design (spec §146).
"""

import httpx2
import pytest
from somatriq_mcp.main import create_app
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "somatriq_mcp"}


def test_mcp_health_200(client: TestClient) -> None:
    response = client.get("/mcp/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "somatriq_mcp"}


def test_unknown_path_404(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404


def test_mcp_unauthenticated_over_sync_client(client: TestClient) -> None:
    """The PAT gate also applies through the plain sync test client."""
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION"


def test_asgi_transport_health() -> None:
    """Same assertion over the raw ASGI transport used by protocol tests."""
    import asyncio

    async def _check() -> int:
        app: Starlette = create_app()
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://t") as http:
            response = await http.get("/mcp/health")
            return response.status_code

    assert asyncio.run(_check()) == 200
