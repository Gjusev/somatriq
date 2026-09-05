"""Every data read is behind the account JWT (spec §122) — one parametrized
401 contract over ALL read paths.

The routes under test are enumerated ONCE here (metrics heart_rate/daily/rr,
today, correlations pair/matrix, observations daily, sleep sessions,
training sessions/response, experiments list/detail). No token and a garbage
token both answer the flat AUTHENTICATION body; the valid-JWT path is
covered by each family's own tests and test_auth.py. The app carries only
the read routers with the REAL guard — no dependency override — so a route
that silently loses its guard fails here loudly.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from somatriq_api import correlations, experiments, metrics, observations, today, training
from somatriq_api.errors import ApiError
from somatriq_db.engine import get_session

# Every read path, with the minimal VALID query params for each (so query
# validation can never mask the missing-JWT 401 with a 422).
READ_PATHS: list[tuple[str, dict[str, str]]] = [
    ("/api/v1/metrics/heart_rate", {}),
    ("/api/v1/metrics/daily", {}),
    ("/api/v1/metrics/rr", {}),
    ("/api/v1/metrics/today", {}),
    ("/api/v1/correlations/pair", {"metric_a": "resting_hr", "metric_b": "avg_hrv"}),
    ("/api/v1/correlations/matrix", {}),
    ("/api/v1/observations/daily", {}),
    ("/api/v1/sleep/sessions", {}),
    ("/api/v1/training/sessions", {}),
    ("/api/v1/training/response", {}),
    ("/api/v1/experiments", {}),
]


@pytest.fixture()
def reads_client() -> Iterator[TestClient]:
    """App with every read router, the flat ApiError body, the REAL
    account-JWT guard — and a session stub that is harmless if dependency
    resolution ever enters it before the guard raises."""
    from somatriq_api.main import api_error_handler

    async def stub_session() -> AsyncIterator[None]:
        # Never reached: the guard raises before the handler runs.
        yield None

    app = FastAPI()
    app.exception_handler(ApiError)(api_error_handler)
    app.include_router(metrics.router)
    app.include_router(today.router)
    app.include_router(correlations.router)
    app.include_router(observations.observations_router)
    app.include_router(observations.sleep_router)
    app.include_router(observations.rr_router)
    app.include_router(training.router)
    app.include_router(experiments.router)
    app.dependency_overrides[get_session] = stub_session
    with TestClient(app) as client:
        yield client


def _assert_flat_401(response: Any, path: str) -> None:
    assert response.status_code == 401, f"{path}: expected 401, got {response.status_code}"
    body = response.json()
    assert "detail" not in body, f"{path}: body must be flat, got {body}"
    assert body["error_code"] == "AUTHENTICATION", f"{path}: {body}"
    assert "JWT" in body["message"], f"{path}: {body}"


@pytest.mark.parametrize(("path", "params"), READ_PATHS)
def test_read_without_jwt_is_flat_401(
    reads_client: TestClient, path: str, params: dict[str, str]
) -> None:
    response = reads_client.get(path, params=params)
    _assert_flat_401(response, path)


def test_experiment_detail_without_jwt_is_flat_401(reads_client: TestClient) -> None:
    path = f"/api/v1/experiments/{uuid.uuid4()}"
    response = reads_client.get(path)
    _assert_flat_401(response, path)


def test_read_with_garbage_jwt_is_flat_401(reads_client: TestClient) -> None:
    """A malformed Bearer token is indistinguishable from no token."""
    response = reads_client.get(
        "/api/v1/metrics/heart_rate", headers={"Authorization": "Bearer not-a-jwt"}
    )
    _assert_flat_401(response, "/api/v1/metrics/heart_rate")


def test_read_with_wrong_scheme_is_flat_401(reads_client: TestClient) -> None:
    """A non-Bearer Authorization header never authorizes a read."""
    response = reads_client.get(
        "/api/v1/metrics/today", headers={"Authorization": "Basic c29tZXRyaXE9"}
    )
    _assert_flat_401(response, "/api/v1/metrics/today")
