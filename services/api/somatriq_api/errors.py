"""Flat wire error bodies (spec §157).

Business errors travel as the frozen IngestErrorDetail shape —
``{"error_code": "...", "message": "...", "details": []}`` — with no
FastAPI ``{"detail": {...}}`` wrapper. Raise :class:`ApiError` anywhere
(router or dependency); the handler registered in main.py renders it.

Pydantic validation 422s keep FastAPI's default request-validation shape;
clients map those by status code (accepted deviation, see contracts
README).
"""

from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestErrorDetail


class ApiError(Exception):
    """Business error with an HTTP status and a contract-shaped body."""

    def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
        self.status_code = status_code
        self.body = IngestErrorDetail(error_code=code.value, message=message)
        super().__init__(message)
