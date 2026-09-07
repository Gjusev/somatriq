"""M13 external import surface (spec §129) + connector registry read (§126).

POST /api/v1/imports/preview — parse a base64 CSV and answer the exact §129
preview (date range, metrics, record count, duplicates, validation
warnings) before anything is committed, plus a one-shot ``preview_token``.
The token is an HMAC-SHA256 signature over ``sha256(content)`` + expiry
(~15 min) keyed by SECRET_KEY: there is NO server-side staging state — the
commit must present the same content bytes, verified against the same
signature.

POST /api/v1/imports/commit — verifies the token against sha256(content),
builds a DailyObservationBatchRequest (decoder_version ``csv-import/1``)
and drives the EXISTING /api/v1/ingest/daily-observations service function
directly (same tables, same ADR 0006 batch replay machinery, same
per-record natural-key dedup — never a second write path). The batch UUID
is derived deterministically from the content hash (uuid5), so importing
the same file twice replays the original batch: every row counts as a
duplicate and nothing is written twice.

GET /api/v1/connectors — the §126 registry with honest health statuses:
csv is available; the authenticated cloud slots answer not_configured
(spec §206 — one high-value connector at a time, never five half-working
integrations).

Imports are owner operations: every route requires the account JWT.
"""

import base64
import binascii
import hashlib
import hmac
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from somatriq_connectors import CONNECTORS, CsvImportError, build_preview, parse_daily_csv
from somatriq_connectors.csv_import import MAX_DISPLAY_WARNINGS
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.ingest import IngestAck
from somatriq_contracts.observations import DailyObservationBatchRequest
from somatriq_db.engine import get_session
from somatriq_db.models import DailyObservation, Device
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from .accounts import AccountJwtDep, jwt_secret
from .errors import ApiError
from .observations import submit_daily_observations

imports_router = APIRouter(prefix="/api/v1/imports", tags=["imports"])
connectors_router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: Marks every CSV-imported row (health.daily_observations.decoder_version).
DECODER_VERSION = "csv-import/1"
#: The preview token must be used within this window (§129 preview→commit).
PREVIEW_TTL_SECONDS = 900
#: Mirrors the DailyObservationBatchRequest items cap; rejected cleanly here
#: instead of as a pydantic ValidationError deeper in the ingest path.
MAX_BATCH_ITEMS = 5_000
#: HMAC domain-separation context (never reuse the JWT signing input).
PREVIEW_TOKEN_CONTEXT = "somatriq-import-preview/1"

IMPORTS_B64_MAX = 8_000_000  # base64 chars ≈ 6 MB of CSV — far past the row cap


# ── request / response models ─────────────────────────────────────────────


class ImportPreviewRequest(BaseModel):
    filename: str | None = Field(default=None, max_length=255)
    content_b64: str = Field(min_length=1, max_length=IMPORTS_B64_MAX)


class ImportWarningOut(BaseModel):
    line: int
    reason: str


class ImportMetricCount(BaseModel):
    metric: str
    count: int


class ImportDateRange(BaseModel):
    first_day: date
    last_day: date


class ImportDuplicatesOut(BaseModel):
    in_file: int
    already_present: int


class ImportPreviewResponse(BaseModel):
    """The §129 preview fields, verbatim, plus the one-shot commit token."""

    filename: str | None
    decoder_version: str
    date_range: ImportDateRange | None
    metrics: list[ImportMetricCount]
    record_count: int
    duplicates: ImportDuplicatesOut
    validation_warnings: list[ImportWarningOut]
    validation_warning_count: int
    skipped_columns: list[str]
    preview_token: str
    expires_at: datetime


class ImportCommitRequest(BaseModel):
    content_b64: str = Field(min_length=1, max_length=IMPORTS_B64_MAX)
    preview_token: str = Field(min_length=1, max_length=255)


class ImportCommitResponse(BaseModel):
    ack: IngestAck
    validation_warnings: list[ImportWarningOut]


class ConnectorHealthOut(BaseModel):
    name: str
    status: str
    reason: str | None = None


class ConnectorsResponse(BaseModel):
    connectors: list[ConnectorHealthOut]


# ── preview token (one-shot HMAC over content hash + expiry) ──────────────


def _content_sha256(text: str) -> str:
    """sha256 over the decoded CSV text — the identity commit must re-present."""
    return hashlib.sha256(text.encode()).hexdigest()


def sign_preview_token(content_sha: str, expires_at: datetime) -> str:
    """``"<exp_epoch>.<hmac_hex>"`` over context|content hash|exp epoch."""
    exp = int(expires_at.timestamp())
    digest = hmac.new(
        jwt_secret().encode(),
        f"{PREVIEW_TOKEN_CONTEXT}|{content_sha}|{exp}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp}.{digest}"


def verify_preview_token(token: str, content_sha: str) -> None:
    """Reject (422) malformed, mismatched or expired tokens; return quietly
    only when the token is THIS content's valid signature."""
    parts = token.split(".")
    if len(parts) != 2 or not parts[0].isdigit():
        raise ApiError(
            status_code=422, code=ErrorCode.VALIDATION, message="malformed preview token"
        )
    exp = int(parts[0])
    digest = hmac.new(
        jwt_secret().encode(),
        f"{PREVIEW_TOKEN_CONTEXT}|{content_sha}|{exp}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(parts[1], digest):
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=(
                "preview token does not match this content — preview again and commit the same file"
            ),
        )
    if datetime.fromtimestamp(exp, tz=UTC) <= datetime.now(UTC):
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="preview token expired — preview the file again",
        )


# ── helpers ───────────────────────────────────────────────────────────────


def _decode_content(content_b64: str) -> str:
    """base64 → UTF-8 text (BOM tolerated); 422 on anything else."""
    try:
        return base64.b64decode(content_b64, validate=True).decode("utf-8-sig")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="content_b64 is not valid base64-encoded UTF-8 text",
        ) from None


async def _existing_pairs(
    session: AsyncSession, user_id: uuid.UUID, days: set[date]
) -> set[tuple[date, str]]:
    """(day, metric) pairs already stored for this user across all devices."""
    if not days:
        return set()
    result = await session.execute(
        select(DailyObservation.day, DailyObservation.metric).where(
            DailyObservation.user_id == user_id,
            DailyObservation.day.in_(sorted(days)),
        )
    )
    return {(row[0], row[1]) for row in result.all()}


async def _import_device_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """The user's earliest device — CSV rows are attributed to it (the
    decoder_version column distinguishes the import path)."""
    device_id = (
        await session.execute(
            select(Device.id).where(Device.user_id == user_id).order_by(Device.active_from).limit(1)
        )
    ).scalar_one_or_none()
    if device_id is None:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="no device to attribute an import to — pair a device first",
        )
    return device_id


def _principal_request(user_id: uuid.UUID, device_id: uuid.UUID) -> Request:
    """A synthetic Request carrying the (user, device) principal so the
    reused ingest service function resolves identity exactly as it does for
    the collector wire (resolve_identity reads request.state)."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/imports/commit",
        "headers": [],
        "query_string": b"",
        "state": {},
    }
    request = Request(scope)
    request.state.user_id = user_id
    request.state.device_id = device_id
    return request


# ── endpoints ─────────────────────────────────────────────────────────────


@imports_router.post("/preview", response_model=ImportPreviewResponse)
async def preview_import(
    payload: ImportPreviewRequest, user_id: AccountJwtDep, session: SessionDep
) -> ImportPreviewResponse:
    """Parse a CSV and answer the §129 preview; nothing is written."""
    text = _decode_content(payload.content_b64)
    try:
        parse_result = parse_daily_csv(text)
    except CsvImportError as exc:
        raise ApiError(
            status_code=422, code=ErrorCode.VALIDATION, message=f"CSV import rejected: {exc}"
        ) from None

    existing = await _existing_pairs(session, user_id, {row.day for row in parse_result.rows})
    preview = build_preview(parse_result, frozenset(existing))

    expires_at = datetime.now(UTC) + timedelta(seconds=PREVIEW_TTL_SECONDS)
    return ImportPreviewResponse(
        filename=payload.filename,
        decoder_version=DECODER_VERSION,
        date_range=None
        if preview.date_range is None
        else ImportDateRange(first_day=preview.date_range[0], last_day=preview.date_range[1]),
        metrics=[ImportMetricCount(metric=m, count=c) for m, c in preview.metrics],
        record_count=preview.record_count,
        duplicates=ImportDuplicatesOut(
            in_file=preview.duplicates.in_file,
            already_present=preview.duplicates.already_present,
        ),
        validation_warnings=[
            ImportWarningOut(line=w.line, reason=w.reason) for w in preview.validation_warnings
        ],
        validation_warning_count=preview.validation_warning_count,
        skipped_columns=preview.skipped_columns,
        preview_token=sign_preview_token(_content_sha256(text), expires_at),
        expires_at=expires_at,
    )


@imports_router.post("/commit", response_model=ImportCommitResponse)
async def commit_import(
    payload: ImportCommitRequest, user_id: AccountJwtDep, session: SessionDep
) -> ImportCommitResponse:
    """Commit the previewed content through the existing ingest path.

    Idempotent by construction: the batch UUID is uuid5-derived from
    sha256(content), so re-committing the same file replays the original
    batch (all duplicates, no double rows, ADR 0006).
    """
    text = _decode_content(payload.content_b64)
    content_sha = _content_sha256(text)
    verify_preview_token(payload.preview_token, content_sha)

    try:
        parse_result = parse_daily_csv(text)
    except CsvImportError as exc:
        raise ApiError(
            status_code=422, code=ErrorCode.VALIDATION, message=f"CSV import rejected: {exc}"
        ) from None
    if not parse_result.rows:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="CSV contains no importable rows (see the preview warnings)",
        )
    if len(parse_result.rows) > MAX_BATCH_ITEMS:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message=(
                f"CSV yields {len(parse_result.rows)} rows; the batch cap is "
                f"{MAX_BATCH_ITEMS} — split the file"
            ),
        )

    device_id = await _import_device_id(session, user_id)
    batch_request = DailyObservationBatchRequest(
        # Deterministic: same file → same UUID → replay, never double rows.
        batch_id=uuid.uuid5(uuid.NAMESPACE_URL, f"somatriq:{DECODER_VERSION}:{content_sha}"),
        decoder_version=DECODER_VERSION,
        items=parse_result.rows,
    )
    ack = await submit_daily_observations(
        batch_request, session, _principal_request(user_id, device_id)
    )
    return ImportCommitResponse(
        ack=ack,
        validation_warnings=[
            ImportWarningOut(line=w.line, reason=w.reason)
            for w in parse_result.warnings[:MAX_DISPLAY_WARNINGS]
        ],
    )


@connectors_router.get("", response_model=ConnectorsResponse)
async def list_connectors(_: AccountJwtDep) -> ConnectorsResponse:
    """The §126 registry with each connector's health_check verdict."""
    connectors = []
    for connector_name, connector_cls in sorted(CONNECTORS.items()):
        health = await connector_cls().health_check()
        connectors.append(
            ConnectorHealthOut(
                name=connector_name,
                status=health["status"],
                reason=health.get("reason"),
            )
        )
    return ConnectorsResponse(connectors=connectors)
