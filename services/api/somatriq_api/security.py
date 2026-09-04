"""Ingest authentication guard (M1 stopgap per ADR 0015).

Real device credentials with minimal scopes land with M2 pairing; until then
ingest is guarded by a shared token from the environment. In production the
token is mandatory — a misconfigured deployment must reject writes, not
accept them silently.
"""

from typing import Annotated

from fastapi import Header, HTTPException, status

from somatriq_api.settings import get_settings


async def require_ingest_token(
    x_somatriq_token: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    if settings.ingest_token:
        if x_somatriq_token != settings.ingest_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error_code": "AUTHENTICATION",
                    "message": "invalid or missing ingest token",
                },
            )
    elif settings.somatriq_env == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "AUTHENTICATION",
                "message": (
                    "INGEST_TOKEN not configured; refusing unauthenticated writes in production"
                ),
            },
        )
