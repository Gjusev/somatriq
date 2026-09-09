"""Annotation CRUD (Block 3; grill P13; spec §181 surfaces).

The owner's longitudinal narrative: create, edit, delete — mutable by
design, no algorithm version, never invalidated by late data. Account JWT
only (annotations are authored words, not device state).
"""

from datetime import date as date_type
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from somatriq_contracts.errors import ErrorCode
from somatriq_contracts.explore import AnnotationCreate, AnnotationOut, AnnotationUpdate
from somatriq_db.engine import get_session
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from .accounts import AccountJwtDep
from .errors import ApiError

router = APIRouter(prefix="/api/v1/annotations", tags=["annotations"])

_LIST_SQL = """
    SELECT id, date_from, date_to, title, note, created_at, updated_at
    FROM health.annotations
    WHERE user_id = :user_id
      AND (CAST(:from_date AS date) IS NULL OR date_from >= :from_date)
      AND (CAST(:to_date AS date) IS NULL OR date_from <= :to_date)
    ORDER BY date_from, created_at
"""


def _out(row: Row[Any]) -> AnnotationOut:
    values = dict(row._mapping)  # noqa: SLF001 — Row mapping is the read API
    return AnnotationOut(
        id=str(values["id"]),
        date_from=values["date_from"],
        date_to=values["date_to"],
        title=values["title"],
        note=values["note"],
        created_at=values["created_at"].isoformat(),
        updated_at=values["updated_at"].isoformat(),
    )


def _range_guard(date_from: date_type | None, date_to: date_type | None) -> None:
    if date_from is not None and date_to is not None and date_to < date_from:
        raise ApiError(
            status_code=422,
            code=ErrorCode.VALIDATION,
            message="date_to must not be before date_from",
        )


@router.get("", response_model=list[AnnotationOut])
async def list_annotations(
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    from_date: Annotated[date_type | None, Query()] = None,
    to_date: Annotated[date_type | None, Query()] = None,
) -> list[AnnotationOut]:
    """The owner's annotations, ordered by anchor; optional window filter."""
    _range_guard(from_date, to_date)
    result = await session.execute(
        text(_LIST_SQL), {"user_id": user_id, "from_date": from_date, "to_date": to_date}
    )
    return [_out(row) for row in result.all()]


@router.post("", response_model=AnnotationOut, status_code=201)
async def create_annotation(
    payload: AnnotationCreate,
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AnnotationOut:
    _range_guard(payload.date_from, payload.date_to)
    row = (
        await session.execute(
            text(
                "INSERT INTO health.annotations "
                "(user_id, date_from, date_to, title, note) "
                "VALUES (:user_id, :date_from, :date_to, :title, :note) "
                "RETURNING id, date_from, date_to, title, note, created_at, updated_at"
            ),
            {
                "user_id": user_id,
                "date_from": payload.date_from,
                "date_to": payload.date_to,
                "title": payload.title,
                "note": payload.note,
            },
        )
    ).one()
    await session.commit()
    return _out(row)


async def _owned(session: AsyncSession, user_id: UUID, annotation_id: UUID) -> Row[Any]:
    row = (
        await session.execute(
            text(
                "SELECT id, date_from, date_to, title, note, created_at, updated_at "
                "FROM health.annotations WHERE id = :id AND user_id = :user_id"
            ),
            {"id": annotation_id, "user_id": user_id},
        )
    ).one_or_none()
    if row is None:
        raise ApiError(status_code=404, code=ErrorCode.NOT_FOUND, message="annotation not found")
    return row


@router.patch("/{annotation_id}", response_model=AnnotationOut)
async def update_annotation(
    annotation_id: UUID,
    payload: AnnotationUpdate,
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AnnotationOut:
    """Partial edit; an explicit null ``date_to`` CLEARS the range back to a
    single-day anchor (documented — absence of the field changes nothing)."""
    current = dict((await _owned(session, user_id, annotation_id))._mapping)  # noqa: SLF001
    provided = payload.model_dump(exclude_unset=True)

    date_from = provided.get("date_from", current["date_from"])
    date_to: date_type | None = provided["date_to"] if "date_to" in provided else current["date_to"]
    _range_guard(date_from, date_to)
    title = provided.get("title", current["title"])
    note = provided.get("note", current["note"])

    row = (
        await session.execute(
            text(
                "UPDATE health.annotations "
                "SET date_from = :date_from, date_to = :date_to, title = :title, "
                "    note = :note, updated_at = now() "
                "WHERE id = :id AND user_id = :user_id "
                "RETURNING id, date_from, date_to, title, note, created_at, updated_at"
            ),
            {
                "id": annotation_id,
                "user_id": user_id,
                "date_from": date_from,
                "date_to": date_to,
                "title": title,
                "note": note,
            },
        )
    ).one()
    await session.commit()
    return _out(row)


@router.delete("/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: UUID,
    user_id: AccountJwtDep,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _owned(session, user_id, annotation_id)
    await session.execute(
        text("DELETE FROM health.annotations WHERE id = :id AND user_id = :user_id"),
        {"id": annotation_id, "user_id": user_id},
    )
    await session.commit()
