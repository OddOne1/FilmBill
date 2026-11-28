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
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..billing.planning import effective_location
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.location import Location
from ..models.project import Project, ProjectBlock, ProjectDay
from ..schemas.project import (
    BlockChangeResponse,
    BlockCreate,
    BlockDaysLocation,
    BlockDaysRequest,
    BlockDuplicate,
    BlockMove,
    BlockResize,
    BlockUpdate,
    CalendarDayResponse,
    CalendarStripResponse,
    ProjectCalendarResponse,
    StripHotelAdd,
)
from ..services import audit, calendar_service, location_service
from ..services.permissions import scoped
from ..services import project_service
from ..services.project_service import ProjectError
from .locations import ref as location_ref

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


def _place_or_refuse(db: Session, membership: Membership, location_id, *, current):
    """L1. A location a strip or a day may use: `locations.view` to pick
    one at all, then shoot or other, active. Not visible: 404."""
    if location_id is None:
        return None
    if not membership.can("locations.view"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    try:
        return location_service.place_for_day(db, membership, location_id, current=current)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found") from None
    except location_service.LocationError as error:
        _unprocessable(error)


def _hotel_or_refuse(db: Session, membership: Membership, location_id) -> Location:
    if not membership.can("locations.view"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    try:
        return location_service.hotel(db, membership, location_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found") from None
    except location_service.LocationError as error:
        _unprocessable(error)


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Response building ──────────────────────────────────────────────────────


@dataclass
class Book:
    """The location records the strips of one response point at, loaded
    once (L1). A hotel reaches a response only for a caller who may see
    hotels — the same rule as the locations list."""

    locations: dict[uuid.UUID, Location] = field(default_factory=dict)
    hotels: dict[uuid.UUID, list[Location]] = field(default_factory=dict)
    hotels_visible: bool = False

    def get(self, location_id: Optional[uuid.UUID]) -> Optional[Location]:
        row = self.locations.get(location_id) if location_id else None
        if row is not None and row.kind == "hotel" and not self.hotels_visible:
            return None
        return row


def book_for(db: Session, membership: Membership, blocks: list[ProjectBlock], days: list[ProjectDay]) -> Book:
    visible = location_service.can_see_hotels(membership)
    ids = [b.location_id for b in blocks] + [d.location_id for d in days]
    return Book(
        locations=location_service.by_id(db, membership.company_id, ids),
        hotels=location_service.strip_hotels(db, membership.company_id, [b.id for b in blocks]) if visible else {},
        hotels_visible=visible,
    )


def _day(day: ProjectDay, block: Optional[ProjectBlock], book: Optional[Book] = None) -> CalendarDayResponse:
    book = book or Book()
    own = book.get(day.location_id)
    effective = effective_location(own, book.get(block.location_id) if block else None)
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
        location_id=own.id if own else None,
        location_ref=location_ref(own),
        effective_location_ref=location_ref(effective.location),
        effective_location_source=effective.source,
        effective_call_time=day.call_time or (block.default_call_time if block else None),
        effective_wrap_time=day.wrap_time or (block.default_wrap_time if block else None),
        is_override=block is not None and day.type != block.type,
        has_own_data=calendar_service.has_own_data(day),
        updated_at=day.updated_at,
    )


def _strip(block: ProjectBlock, days: list[ProjectDay], book: Optional[Book] = None) -> CalendarStripResponse:
    book = book or Book()
    place = book.get(block.location_id)
    return CalendarStripResponse(
        id=block.id,
        kind="strip",
        type=block.type,
        label=block.label,
        start_date=block.start_date,
        end_date=block.end_date,
        location=block.location,
        location_id=place.id if place else None,
        location_ref=location_ref(place),
        hotels=[location_ref(h) for h in book.hotels.get(block.id, [])],
        hotels_visible=book.hotels_visible,
        default_call_time=block.default_call_time,
        default_wrap_time=block.default_wrap_time,
        notes=block.notes,
        position=block.position,
        is_active=block.is_active,
        days=[_day(day, block, book) for day in days],
        counts=calendar_service.block_counts(days),
    )


def _loose(day: ProjectDay, book: Optional[Book] = None) -> CalendarStripResponse:
    return CalendarStripResponse(
        id=day.id,
        kind="day",
        type=day.type,
        start_date=day.date,
        end_date=day.date,
        location=day.location,
        hotels_visible=(book or Book()).hotels_visible,
        days=[_day(day, None, book)],
        counts={day.type: 1},
    )


def _change(
    db: Session,
    project: Project,
    block: ProjectBlock,
    change: calendar_service.Change,
    membership: Optional[Membership] = None,
) -> BlockChangeResponse:
    db.refresh(block)
    days = calendar_service.block_days(db, block) if block.is_active else []
    book = book_for(db, membership, [block], days + list(change.at_risk)) if membership is not None else Book()
    return BlockChangeResponse(
        applied=change.applied,
        needs_confirm=change.needs_confirm,
        detail=change.detail,
        strip=_strip(block, days, book),
        days_with_data=[_day(day, block, book) for day in change.at_risk],
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
    book = book_for(
        db,
        membership,
        [block for block, _ in view.blocks],
        [day for _, days in view.blocks for day in days] + list(view.loose),
    )
    strips = [_strip(block, days, book) for block, days in view.blocks]
    strips += [_loose(day, book) for day in view.loose]
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
    return _change(db, project, block, change, membership)


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
    changes = body.model_dump(exclude_unset=True)
    if "location_id" in changes:
        _place_or_refuse(db, membership, changes["location_id"], current=block.location_id)
    try:
        change = calendar_service.update_block(db, block, changes, actor=_actor(request, membership))
    except ProjectError as error:
        _unprocessable(error)
    db.commit()
    return _change(db, project, block, change, membership)


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
    return _change(db, project, block, change, membership)


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
    return _change(db, project, block, change, membership)


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
    return _change(db, project, block, change, membership)


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
    return _change(db, project, copy, change, membership)


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
    return _change(db, project, block, change, membership)


# ─── Locations on a strip (L1 part 2) ───────────────────────────────────────


@router.post(
    "/projects/{project_id}/blocks/{block_id}/days/location", response_model=BlockChangeResponse
)
def set_days_location(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: BlockDaysLocation,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """"Set for selected days": these days of the strip get this location
    of their own (shoot or other); null gives them back the strip's. Each
    changed day is audited as `project_day.updated`."""
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    days = {day.date: day for day in calendar_service.block_days(db, block)}
    wanted = sorted(set(body.dates))
    missing = [d for d in wanted if d not in days]
    if missing:
        _unprocessable(
            ProjectError(
                f"{', '.join(calendar_service.say(d) for d in missing)} "
                f"{'is not a day' if len(missing) == 1 else 'are not days'} of this strip. "
                "Pick days inside the strip."
            )
        )
    current = {days[d].location_id for d in wanted}
    place = _place_or_refuse(
        db, membership, body.location_id, current=body.location_id if body.location_id in current else None
    )
    target = place.id if place else None
    actor = _actor(request, membership)
    changed = 0
    for d in wanted:
        day = days[d]
        if day.location_id == target:
            continue
        before = audit.snapshot(day, project_service.PROJECT_DAY_AUDITED)
        day.location_id = target
        db.flush()
        audit.record(
            db,
            actor,
            day,
            project_service.DAY_UPDATED,
            before,
            audit.snapshot(day, project_service.PROJECT_DAY_AUDITED),
            fields=project_service.PROJECT_DAY_AUDITED,
        )
        changed += 1
    db.commit()
    detail = (
        f"{changed} {'day' if changed == 1 else 'days'} "
        + (f"set to {place.name}." if place else "back to the strip's location.")
    )
    return _change(db, project, block, calendar_service.Change(detail=detail, changed=changed), membership)


@router.post("/projects/{project_id}/blocks/{block_id}/hotels", response_model=BlockChangeResponse)
def add_strip_hotel(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: StripHotelAdd,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """A hotel in use on this strip — what a person's hotel is picked
    from. Needs to see hotels (`parties.view`, D111)."""
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    hotel = _hotel_or_refuse(db, membership, body.location_id)
    added = location_service.add_strip_hotel(db, membership, block, hotel, actor=_actor(request, membership))
    db.commit()
    detail = f"{hotel.name} added to the strip's hotels." if added else f"{hotel.name} is already one of the strip's hotels."
    return _change(db, project, block, calendar_service.Change(detail=detail), membership)


@router.post(
    "/projects/{project_id}/blocks/{block_id}/hotels/{location_id}/remove",
    response_model=BlockChangeResponse,
)
def remove_strip_hotel(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    location_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Takes the hotel off the strip's list. People already sleeping there
    keep it on their row."""
    project = _project_or_404(db, membership, project_id)
    block = _block_or_404(db, membership, project, block_id)
    if not location_service.can_see_hotels(membership):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    hotel = location_service.visible(db, membership, location_id)
    if hotel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    removed = location_service.remove_strip_hotel(db, block, hotel, actor=_actor(request, membership))
    db.commit()
    detail = f"{hotel.name} removed from the strip's hotels." if removed else f"{hotel.name} was not one of the strip's hotels."
    return _change(db, project, block, calendar_service.Change(detail=detail), membership)
