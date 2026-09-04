"""Bearer PAT gate for the MCP endpoint (ADR 0010 enforcement path).

Pure-ASGI middleware wrapped around the MCP streamable-http app (mounted at
``/mcp``) — every tool call passes it, health paths do not. Failures render
the api's flat ``{"error_code", "message", "details"}`` body (spec §157)
with a ``WWW-Authenticate`` header so OAuth-2.1-capable clients can discover
the protected resource once the OAuth increment lands; PATs and OAuth tokens
will then share this exact path (ADR 0010: same enforcement path).

Token handling lives in :mod:`somatriq_mcp.pats` (hash lookup, revoked /
expired checks, ``last_used_at`` bump, scope ``health.read`` required by
every tool, spec §97).
"""

import json

from somatriq_contracts.errors import ErrorCode
from somatriq_db.engine import get_session_factory
from starlette.types import ASGIApp, Receive, Scope, Send

from . import pats
from .errors import error_body

_AUTHENTICATE_WWW = 'Bearer resource_metadata="/.well-known/oauth-protected-resource"'

# The gate owns exactly the MCP endpoint paths; anything else falls through
# to the SDK app (404) untouched — health paths live outside this middleware.
_PROTECTED_PATHS = frozenset({"/mcp", "/mcp/"})


async def _flat_response(send: Send, status_code: int, code: ErrorCode, message: str) -> None:
    body = json.dumps(error_body(code, message)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", _AUTHENTICATE_WWW.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PatAuthMiddleware:
    """Require a live, ``health.read``-scoped PAT on every request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") not in _PROTECTED_PATHS:
            await self.app(scope, receive, send)
            return

        token = self._bearer_token(scope)
        if token is None or not token.startswith(pats.PAT_TOKEN_PREFIX):
            await _flat_response(
                send, 401, ErrorCode.AUTHENTICATION, "personal access token required"
            )
            return

        factory = get_session_factory()
        try:
            async with factory() as session:
                await pats.verify_pat(session, token)
        except pats.PatInvalid:
            await _flat_response(
                send, 401, ErrorCode.AUTHENTICATION, "invalid or expired personal access token"
            )
            return
        except pats.PatRevoked:
            await _flat_response(send, 403, ErrorCode.AUTHORIZATION, "token revoked")
            return
        except pats.PatScopeMissing as exc:
            await _flat_response(
                send,
                403,
                ErrorCode.AUTHORIZATION,
                f"token lacks required scope {exc.required_scope}",
            )
            return
        except Exception:  # noqa: BLE001 - auth store down is 503, never a 500
            await _flat_response(
                send, 503, ErrorCode.RETRYABLE, "authentication store unavailable"
            )
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _bearer_token(scope: Scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key == b"authorization" and value.lower().startswith(b"bearer "):
                return str(value[7:].decode("latin-1").strip())
        return None


__all__ = ["PatAuthMiddleware"]
