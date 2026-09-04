"""Flat wire error bodies for the MCP surface (spec §157 — same shape as the api).

Two failure planes share one contract:

- HTTP-plane auth failures (401/403 from the Bearer PAT gate) render the flat
  ``{"error_code", "message", "details"}`` body the api uses, never a bare
  {"detail": …} wrapper.
- Tool-plane failures raise :class:`McpError` and surface as a normal tool
  *result* carrying the same flat body, so AI clients read a structured,
  machine-readable error instead of an opaque protocol error (ADR 0010
  contract: responses stay caveat-aware and interpretable).
"""

import functools
from collections.abc import Callable, Coroutine
from typing import Any

from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestErrorDetail

ToolFunc = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


class McpError(Exception):
    """Business error inside a tool; rendered as a flat body tool result."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.body = IngestErrorDetail(error_code=code.value, message=message)
        super().__init__(message)


def validation_error(message: str) -> McpError:
    return McpError(ErrorCode.VALIDATION, message)


def error_body(code: ErrorCode, message: str) -> dict[str, Any]:
    """Flat error dict — the exact wire shape of api error responses."""
    return IngestErrorDetail(error_code=code.value, message=message).model_dump()


def tool_error(func: ToolFunc) -> ToolFunc:
    """Wrap a tool so business errors return the flat body instead of raising.

    Unexpected exceptions are also flattened (RETRYABLE — transient infra
    dominates; deterministic math is covered by tests) so no tool call ever
    answers with an unstructured 500 (spec §158: no silent failure).
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await func(*args, **kwargs)
        except McpError as exc:
            return exc.body.model_dump()
        except Exception:  # noqa: BLE001 - tools must never leak a raw 500
            return error_body(ErrorCode.RETRYABLE, "internal error while executing tool")

    return wrapper
