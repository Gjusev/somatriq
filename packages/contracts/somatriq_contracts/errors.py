"""Stable machine-readable error codes (spec §157).

API errors expose these verbatim; mobile maps RetryableIngestError /
PermanentIngestError semantics off them (ADR 0006).
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION = "VALIDATION"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    NOT_FOUND = "NOT_FOUND"
