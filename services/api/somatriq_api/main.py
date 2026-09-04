"""API entrypoint.

Paths are served natively under /api/v1 (ADR 0007: single origin, path-based
routing — Traefik forwards /api without stripping).
"""

from fastapi import FastAPI

app = FastAPI(
    title="Somatriq API",
    version="0.0.0",
    # Rendered under https://host/api — openapi.json lives at /api/openapi.json
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)


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
