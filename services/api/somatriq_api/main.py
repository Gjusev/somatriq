"""API entrypoint.

Paths are served natively under /api/v1 (ADR 0007: single origin, path-based
routing — Traefik forwards /api without stripping).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from somatriq_api.errors import ApiError

app = FastAPI(
    title="Somatriq API",
    version="0.0.0",
    # Rendered under https://host/api — openapi.json lives at /api/openapi.json
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    """Business errors render flat (spec §157): no {"detail": …} wrapper."""
    return JSONResponse(status_code=exc.status_code, content=exc.body.model_dump())


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness — no dependencies checked (spec §146).

    Two paths: /health for the in-container Docker healthcheck (direct port),
    /api/health for the public single-origin route (Traefik forwards /api
    without stripping — ADR 0007).
    """
    return {"status": "ok", "service": "somatriq_api"}


@app.get("/ready")
@app.get("/api/ready")
async def ready() -> dict[str, str]:
    """Readiness — checks database reachability. Returns 503 when not ready."""
    from somatriq_api.db import database_ready

    ok, detail = await database_ready()
    if not ok:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=detail)
    return {"status": "ready", "database": detail}


# M1 vertical slice (spec §194): idempotent ingest + metric read.
# M2 (ADR 0003/0015): local account auth, device pairing, device management.
# M6 (spec §41-42): vendor daily observations, sleep sessions, RR intervals;
# M6 today slice (spec §76): recovery + today under the metrics prefix.
# M8 (spec §89-94, ADR 0009): AI coach — deterministic tools + privacy chokepoint.
from somatriq_api import (  # noqa: E402
    admin,
    auth,
    coach,
    devices,
    ingest,
    metrics,
    observations,
    pairing,
    today,
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(pairing.router)
app.include_router(devices.router)
app.include_router(ingest.router)
app.include_router(observations.ingest_router)
app.include_router(observations.observations_router)
app.include_router(observations.sleep_router)
app.include_router(observations.rr_router)
app.include_router(metrics.router)
app.include_router(today.router)
app.include_router(coach.router)
