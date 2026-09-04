"""Somatriq health MCP server — streamable HTTP under /mcp on port 8100 (ADR 0010).

Topology inside this process (spec §95-96, ADR 0007 single origin — Traefik
forwards ``/mcp`` here without stripping):

    /health       liveness, no dependencies (spec §146) — compose healthcheck
    /mcp/health   MCP-service liveness under the public origin
    /mcp          MCP streamable-http endpoint (official SDK 2.x — FastMCP
                  was renamed MCPServer there; spec §96 "FastMCP or the
                  current stable official equivalent")

The SDK app keeps its natural ``/mcp`` route and is mounted at the root as
the fallback route (Starlette sub-apps match against the full request path),
wrapped in the Bearer PAT gate. Health paths are declared before it, so they
never need a token (spec §146). Every /mcp request passes the gate (ADR 0010
enforcement path: credential → scopes → tools; default scope health.read,
spec §97). The full OAuth 2.1 resource-server flow of ADR 0010
(authorization/token endpoints, dynamic client registration) is a later
increment — the middleware already answers with WWW-Authenticate
protected-resource metadata so OAuth-capable clients can discover it when it
lands. All tools are read-only and deterministic (ADR 0009).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import tools
from .auth import PatAuthMiddleware
from .settings import get_settings

logger = logging.getLogger(__name__)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_INSTRUCTIONS = (
    "Somatriq personal health data tools. All tools are read-only and return "
    "deterministic computations with a data envelope: data, coverage, sources, "
    "quality, caveats, generated_at. Respect the caveats: insufficient data is "
    "reported explicitly and must never be treated as zero. Never invent "
    "numerical calculations; ask these tools instead."
)


def _register_tools(server: MCPServer) -> None:
    server.tool(
        name="get_heart_rate_summary",
        annotations=_READ_ONLY,
        structured_output=False,
    )(tools.get_heart_rate_summary)
    server.tool(
        name="get_daily_summary",
        annotations=_READ_ONLY,
        structured_output=False,
    )(tools.get_daily_summary)
    server.tool(
        name="get_sleep_sessions",
        annotations=_READ_ONLY,
        structured_output=False,
    )(tools.get_sleep_sessions)
    server.tool(
        name="get_baselines",
        annotations=_READ_ONLY,
        structured_output=False,
    )(tools.get_baselines)


def create_app() -> Starlette:
    """Assemble the service: health routes + PAT-gated MCP endpoint."""
    server = MCPServer(name="somatriq-health", instructions=_INSTRUCTIONS)
    _register_tools(server)

    # The SDK app serves the streamable-http endpoint at its natural /mcp
    # path. Stateless: every request is self-contained — no server-side
    # session resumption needed for read-only tools; host="0.0.0.0" keeps the
    # SDK's localhost DNS-rebinding guard off (we are always behind Traefik,
    # whose Host header is the public origin).
    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="0.0.0.0",
    )

    # The mounted sub-app's lifespan (which runs the streamable session
    # manager) does not execute automatically — Starlette only runs the outer
    # app's lifespan — so compose it explicitly.
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "somatriq_mcp"})

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/mcp/health", health, methods=["GET"]),
            # Fallback mount: the MCP endpoint and its PAT gate; exact health
            # routes above win by declaration order.
            Mount("/", app=PatAuthMiddleware(mcp_app)),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
