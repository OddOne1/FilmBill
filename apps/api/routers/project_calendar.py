"""The project calendar and its strips (B2a part 2).

`GET /projects/{id}/calendar` is what the calendar screen draws: the strips
touching a range, each with its days, the days outside any strip drawn as
one-day strips of their own, and the project's per-type counts — distinct
dates, the number the budget's `days:<type>` globals use. The screen never
counts.

Every write is `projects.edit`; reading is `projects.view` (staff included,
as for the Days tab). Another company's project or strip is a 404 (rule 6).
**No DELETE route**: a strip leaves the plan through `POST .../remove`, and
`test_openapi_surface` keeps it that way.

Per-day details (location, call, wrap, notes) stay on the existing day
endpoints in `routers/projects.py`.
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import Membership, require
from ..models.project import Project, ProjectBlock, ProjectDay
from ..schemas.project import (
    BlockChangeResponse,
    BlockCreate,
    BlockDaysRequest,
    BlockDuplicate,
    BlockMove,
    BlockResize,
    BlockUpdate,
    CalendarDayResponse,
    CalendarStripResponse,
    ProjectCalendarResponse,
)
from ..services import audit, calendar_service
from ..services.permissions import scoped
from ..services.project_service import ProjectError

router = APIRouter(tags=["projects"])


# ─── Lookups ────────────────────────────────────────────────────────────────


def _project_or_404(db: Session, membership: Membership, project_id: uuid.UUID) -> Project:
    row = scoped(db.query(Project), membership).filter(Project.id == project_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return row


def _block_or_404(
    db: Session, membership: Membership, project: Project, block_id: uuid.UUID
) -> ProjectBlock:
    row = (
        scoped(db.query(ProjectBlock), membership)
        .filter(
            ProjectBlock.id == block_id,
            ProjectBlock.project_id == project.id,
            ProjectBlock.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strip not found")
    return row


def _unprocessable(error: Exception):
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
    ) from None


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Response building ──────────────────────────────────────────────────────


def _day(day: ProjectDay, block: Optional[ProjectBlock]) -> CalendarDayResponse:
    return CalendarDayResponse(
        id=day.id,
        block_id=day.block_id,
        date=day.date,
        type=day.type,
        location=day.location,
        call_time=day.call_time,
        wrap_time=day.wrap_time,
        notes=day.notes,
        effective_location=day.location or (block.location if block else None),
        effective_call_time=day.call_time or (block.default_call_time if block else None),
        effective_wrap_time=day.wrap_time or (block.default_wrap_time if block else None),
        is_override=block is not None and day.type != block.type,
        has_own_data=calendar_service.has_own_data(day),
        updated_at=day.updated_at,
    )


def _strip(block: ProjectBlock, days: list[ProjectDay]) -> CalendarStripResponse:
    return CalendarStripResponse(
        id=block.id,
        kind="strip",
        type=block.type,
        label=block.label,
        start_date=block.start_date,
        end_date=block.end_date,
        location=block.location,
        default_call_time=block.default_call_time,
        default_wrap_time=block.default_wrap_time,
        notes=block.notes,
        position=block.position,
        is_active=block.is_active,
        days=[_day(day, block) for day in days],
        counts=calendar_service.block_counts(days),
    )


def _loose(day: ProjectDay) -> CalendarStripResponse:
    return CalendarStripResponse(
        id=day.id,
        kind="day",
        type=day.type,
        start_date=day.date,
        end_date=day.date,
        location=day.location,
        days=[_day(day, None)],
        counts={day.type: 1},
    )


def _change(
    db: Session,
    project: Project,
    block: ProjectBlock,
    change: calendar_service.Change,
) -> BlockChangeResponse:
    db.refresh(block)
    days = calendar_service.block_days(db, block) if block.is_active else []
    return BlockChangeResponse(
        applied=change.applied,
        needs_confirm=change.needs_confirm,
        detail=change.detail,
        strip=_strip(block, days),
        days_with_data=[_day(day, block) for day in change.at_risk],
        added=change.added,
        removed=change.removed,
        changed=change.changed,
        counts=calendar_service.project_counts(db, project),
    )


# ─── Reading ────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/calendar", response_model=ProjectCalendarResponse)
def read_calendar(
    project_id: uuid.UUID,
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.view")),
):
    project = _project_or_404(db, membership, project_id)
    try:
        view = calendar_service.calendar(db, project, range_start=from_, range_end=to)
    except ProjectError as error:
        _unprocessable(error)
    strips = [_strip(block, days) for block, days in view.blocks]
    strips += [_loose(day) for day in view.loose]
    strips.sort(key=lambda strip: (strip.start_date, strip.kind != "strip"))
    return ProjectCalendarResponse(
        project_id=project.id,
        project_start=project.start_date,
        project_end=project.end_date,
        range_start=from_,
        range_end=to,
        strips=strips,
        counts=view.counts,
    )


# ─── Writing ────────────────────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/blocks",
    response_model=BlockChangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_block(
    project_id: uuid.UUID,
    body: BlockCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    try:
        block, change = calendar_service.create_block(
            db,
            membership,
            project,
            actor=_actor(request, membership),
            day_type=body.type,
            start=body.start_date,
            end=body.end_date,
            label=body.label,
            location=body.location,
            default_call_time=body.default_call_time,
            default_wrap_time=body.default_wrap_time,
            notes=body.notes,
            weekends=body.weekends,
        )
    except ProjectError as error:
        _unprocessable(error)
    db.commit()
    return _change(db, project, block, change)


@router.patch(
    "/projects/{project_id}/blocks/{block_id}", response_model=BlockChangeResponse
)
def update_block(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    try:
        change = calendar_service.update_block(
            db, block, body.model_dump(exclude_unset=True), actor=_actor(request, membership)
        )
    except ProjectError as error:
        _unprocessable(error)
    db.commit()
    return _change(db, project, block, change)


@router.post(
    "/projects/{project_id}/blocks/{block_id}/move", response_model=BlockChangeResponse
)
def move_block(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockMove,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    change = calendar_service.move_block(
        db, block, body.start_date, actor=_actor(request, membership)
    )
    db.commit()
    return _change(db, project, block, change)


@router.post(
    "/projects/{project_id}/blocks/{block_id}/resize", response_model=BlockChangeResponse
)
def resize_block(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockResize,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Growing adds days of the main type; shrinking takes days out.

    When a day that would be taken out carries notes, a location or a time,
    the answer is `applied: false, needs_confirm: true` with those days
    listed, and NOTHING changes until the same request comes back with
    `confirm: true`.
    """
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    try:
        change = calendar_service.resize_block(
            db,
            block,
            new_start=body.start_date,
            new_end=body.end_date,
            confirm=body.confirm,
            actor=_actor(request, membership),
        )
    except ProjectError as error:
        _unprocessable(error)
    if change.applied:
        db.commit()
    else:
        db.rollback()
    return _change(db, project, block, change)


@router.put(
    "/projects/{project_id}/blocks/{block_id}/days", response_model=BlockChangeResponse
)
def set_block_days(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockDaysRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Mark days as travel, off, any type — or `null` for the main type."""
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    try:
        change = calendar_service.set_days(
            db,
            block,
            [(entry.date, entry.type) for entry in body.days],
            actor=_actor(request, membership),
        )
    except ProjectError as error:
        _unprocessable(error)
    db.commit()
    return _change(db, project, block, change)


@router.post(
    "/projects/{project_id}/blocks/{block_id}/duplicate",
    response_model=BlockChangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_block(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockDuplicate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    copy, change = calendar_service.duplicate_block(
        db, membership, project, block, body.start_date, actor=_actor(request, membership)
    )
    db.commit()
    return _change(db, project, copy, change)


@router.post(
    "/projects/{project_id}/blocks/{block_id}/remove", response_model=BlockChangeResponse
)
def remove_block(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """The strip and its days out of the plan; the response counts them."""
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    change = calendar_service.remove_block(db, block, actor=_actor(request, membership))
    db.commit()
    return _change(db, project, block, change)
