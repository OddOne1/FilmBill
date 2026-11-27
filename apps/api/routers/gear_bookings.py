"""Gear on the calendar (B2c-2 part 2): bookings of devices on strips,
availability and the overbooking warning.

**Permissions.** Reading needs `projects.view` AND `devices.view` — staff
hold both, so staff see gear bookings (gear is visible to staff; nothing
here costs anything). Changing needs `projects.edit` (and `devices.view`).
Any missing permission is a 404, like everywhere.

**Overbooking warns and never blocks** (decided). Every create and update
response carries a `warnings` list — the shortage, the worst day, who holds
the device, and the device's alternatives with their own availability on
the same days. The save is stored either way.

**Dependencies, one level** (SCOPE §4.4.3). `always` ones are added with the
parent, `ask` ones come back as suggestions until chosen; a dependency's
own dependencies are never followed.

**No DELETE route.** Removing a booking sets `cancelled`; removing a parent
with open dependents first asks what happens to them.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..billing.planning import PeriodAvailability
from ..database import get_db
from ..middleware.company import NOT_FOUND_DETAIL, Membership, require
from ..models.device import Device
from ..models.gear import GEAR_BOOKED, GEAR_CANCELLED, GEAR_SCHEDULED, GearBooking
from ..models.project import Project, ProjectBlock
from ..models.user import User
from ..schemas.gear_bookings import (
    DeviceAvailability,
    DeviceBookingsResponse,
    GearAlternativeOffer,
    GearAvailabilityResponse,
    GearBookingResponse,
    GearBookingsCreate,
    GearBookingsCreated,
    GearBookingsPreview,
    GearBookingUpdate,
    GearBookingUpdated,
    GearBulkSkipped,
    GearBulkStatusChange,
    GearBulkStatusResult,
    GearDay,
    GearHolder,
    GearPlannedBooking,
    GearRemove,
    GearRemoveResult,
    GearStatusChange,
    GearStripCounts,
    GearSuggestion,
    GearWarning,
    ProjectGearResponse,
    ProjectGearStrip,
)
from ..services import audit, gear_booking_service as service
from ..services.permissions import scoped
from .devices import thumbnail_url

router = APIRouter()

WEIGHT_STEP = Decimal("0.001")


# ─── Permissions ────────────────────────────────────────────────────────────


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def can_view(membership: Membership = Depends(require("projects.view"))) -> Membership:
    if not membership.can("devices.view"):
        _not_found()
    return membership


def can_change(membership: Membership = Depends(require("projects.edit"))) -> Membership:
    if not membership.can("devices.view"):
        _not_found()
    return membership


def _unprocessable(error: Exception):
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(error, service.AlreadyOnStrip)
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    raise HTTPException(status_code=code, detail=str(error)) from None


# ─── Lookups ────────────────────────────────────────────────────────────────


def _project(db: Session, membership: Membership, project_id: uuid.UUID) -> Project:
    row = scoped(db.query(Project), membership).filter(Project.id == project_id).first()
    if row is None:
        _not_found()
    return row


def _block(db: Session, membership: Membership, project: Project, block_id: uuid.UUID) -> ProjectBlock:
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
        _not_found()
    return row


def _booking(db: Session, membership: Membership, project: Project, booking_id: uuid.UUID) -> GearBooking:
    row = (
        scoped(db.query(GearBooking), membership)
        .filter(GearBooking.id == booking_id, GearBooking.project_id == project.id)
        .first()
    )
    if row is None:
        _not_found()
    return row


def _device(db: Session, membership: Membership, device_id: uuid.UUID) -> Device:
    row = scoped(db.query(Device), membership).filter(Device.id == device_id).first()
    if row is None:
        _not_found()
    return row


# ─── Response building ──────────────────────────────────────────────────────


def weight_text(value: Optional[Decimal]) -> Optional[str]:
    return str(Decimal(value).quantize(WEIGHT_STEP)) if value is not None else None


def _holder(holder: service.Holder, dates: list[date]) -> GearHolder:
    return GearHolder(
        booking_id=holder.booking_id,
        project_id=holder.project_id,
        project_name=holder.project_name,
        project_number=holder.project_number,
        block_id=holder.block_id,
        strip_name=holder.strip_name,
        status=holder.status,
        quantity=holder.quantity,
        dates=sorted(dates),
    )


def _warning(over: service.Overbooking) -> GearWarning:
    return GearWarning(
        booking_id=over.booking.id,
        device_id=over.device.id,
        device_name=over.device.name,
        owned=over.device.quantity_owned,
        quantity=over.booking.quantity,
        shortage=over.shortage,
        worst_day=over.worst_day,
        dates=over.short_dates,
        message=over.message,
        held_by=[_holder(h.holder, h.dates) for h in over.held_by],
        alternatives=[
            GearAlternativeOffer(
                alternative_id=alt.row.id,
                device_id=alt.device.id,
                name=alt.device.name,
                thumbnail_url=thumbnail_url(alt.device),
                position=alt.row.position,
                note=alt.row.note,
                price_hint=alt.row.price_hint,
                owned=alt.device.quantity_owned,
                min_available=alt.numbers.min_available,
                worst_day=alt.numbers.worst_day,
                enough=alt.numbers.min_available >= over.booking.quantity,
            )
            for alt in over.alternatives
        ],
    )


def responses(db: Session, membership: Membership, rows: list[GearBooking]) -> list[GearBookingResponse]:
    """Bookings with names, days and warnings, in a fixed number of queries."""
    if not rows:
        return []
    company_id = membership.company_id
    devices = {d.id: d for d in db.query(Device).filter(Device.id.in_({r.device_id for r in rows}))}
    projects = {p.id: p for p in db.query(Project).filter(Project.id.in_({r.project_id for r in rows}))}
    blocks = {b.id: b for b in db.query(ProjectBlock).filter(ProjectBlock.id.in_({r.block_id for r in rows}))}
    parent_ids = {r.added_via_booking_id for r in rows if r.added_via_booking_id}
    parent_names = (
        dict(
            db.query(GearBooking.id, Device.name)
            .join(Device, Device.id == GearBooking.device_id)
            .filter(GearBooking.id.in_(parent_ids))
            .all()
        )
        if parent_ids
        else {}
    )
    users = {r.booked_by for r in rows if r.booked_by}
    user_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(users)).all()} if users else {}
    dates = service.active_dates(db, [r.id for r in rows])
    warnings = service.overbookings(db, company_id, rows)
    out = []
    for row in rows:
        device = devices[row.device_id]
        project = projects[row.project_id]
        block = blocks[row.block_id]
        over = warnings.get(row.id)
        out.append(
            GearBookingResponse(
                id=row.id,
                project_id=row.project_id,
                project_name=project.name,
                project_number=project.project_number,
                block_id=row.block_id,
                strip_name=service.strip_name(block),
                device_id=row.device_id,
                device_name=device.name,
                category=device.category,
                thumbnail_url=thumbnail_url(device),
                quantity=row.quantity,
                status=row.status,
                status_note=row.status_note,
                booked_on=row.booked_on,
                booked_by_name=user_names.get(row.booked_by) if row.booked_by else None,
                added_via_booking_id=row.added_via_booking_id,
                added_via_device_name=parent_names.get(row.added_via_booking_id) if row.added_via_booking_id else None,
                association_id=row.association_id,
                quantity_per_parent=row.quantity_per_parent,
                follows_parent=row.follows_parent,
                overbooking_note=row.overbooking_note,
                removal_note=row.removal_note,
                weight_kg=weight_text(device.weight_kg),
                dates=dates[row.id],
                warning=_warning(over) if over else None,
                created_at=row.created_at,
            )
        )
    return out


def in_reading_order(rows: list[GearBooking], names: dict[uuid.UUID, str]) -> list[GearBooking]:
    """Each device added directly, in the order it was added, followed by
    the dependencies added with it (by name). Rows made in one request share
    a `created_at`, so the time alone does not order them."""
    ids = {row.id for row in rows}
    children: dict[uuid.UUID, list[GearBooking]] = {}
    tops = []
    for row in rows:
        if row.added_via_booking_id in ids:
            children.setdefault(row.added_via_booking_id, []).append(row)
        else:
            tops.append(row)
    tops.sort(key=lambda r: (r.created_at, r.added_via_booking_id is not None, names.get(r.device_id, "").lower(), r.id))
    out = []
    for row in tops:
        out.append(row)
        out.extend(sorted(children.get(row.id, []), key=lambda r: (names.get(r.device_id, "").lower(), r.id)))
    return out


def _one(db: Session, membership: Membership, row: GearBooking) -> GearBookingResponse:
    db.refresh(row)
    return responses(db, membership, [row])[0]


def _device_numbers(
    device: Device, numbers: PeriodAvailability, holders: dict[uuid.UUID, service.Holder]
) -> DeviceAvailability:
    days = []
    for entry in numbers.days:
        grouped: dict[uuid.UUID, int] = {}
        for holding in entry.holdings:
            grouped[holding.booking_id] = holding.quantity
        days.append(
            GearDay(
                date=entry.date,
                owned=entry.owned,
                scheduled=entry.scheduled,
                booked=entry.booked,
                available=entry.available,
                overbooked=entry.overbooked,
                shortage=entry.shortage,
                holders=[_holder(holders[bid], [entry.date]) for bid in grouped],
            )
        )
    return DeviceAvailability(
        device_id=device.id,
        name=device.name,
        category=device.category,
        thumbnail_url=thumbnail_url(device),
        owned=device.quantity_owned,
        min_available=numbers.min_available,
        worst_day=numbers.worst_day,
        scheduled=numbers.scheduled,
        booked=numbers.booked,
        overbooked=numbers.overbooked,
        shortage=numbers.shortage,
        days=days,
    )


def _counts(bookings: list[GearBookingResponse]) -> GearStripCounts:
    return GearStripCounts(
        scheduled=sum(b.quantity for b in bookings if b.status == GEAR_SCHEDULED),
        booked=sum(b.quantity for b in bookings if b.status == GEAR_BOOKED),
        overbooked=sum(1 for b in bookings if b.warning is not None),
    )


def _weight(bookings: list[GearBookingResponse]) -> tuple[str, int]:
    """Σ quantity × weight per piece, and the pieces with no weight."""
    total = Decimal("0")
    missing = 0
    for booking in bookings:
        if booking.weight_kg is None:
            missing += booking.quantity
        else:
            total += Decimal(booking.weight_kg) * booking.quantity
    return str(total.quantize(WEIGHT_STEP)), missing


def _suggestion(plan: service.Plan, suggestion: service.Suggestion) -> GearSuggestion:
    parent = plan.entries[suggestion.parent].device
    return GearSuggestion(
        parent_device_id=parent.id,
        parent_device_name=parent.name,
        association_id=suggestion.association.id,
        device_id=suggestion.device.id,
        device_name=suggestion.device.name,
        thumbnail_url=thumbnail_url(suggestion.device),
        quantity=suggestion.quantity,
        quantity_per_parent=suggestion.association.quantity_per_parent,
        note=suggestion.association.note,
    )


def _wanted(db: Session, membership: Membership, body: GearBookingsCreate) -> list[service.NewBooking]:
    return [
        service.NewBooking(
            device=_device(db, membership, entry.device_id),
            quantity=entry.quantity,
            dates=list(entry.dates) if entry.dates is not None else None,
            chosen=[service.Chosen(c.association_id, c.quantity) for c in entry.chosen_dependencies],
            overbooking_note=entry.overbooking_note,
        )
        for entry in body.bookings
    ]


# ─── Availability ───────────────────────────────────────────────────────────


def _range(from_: date, to: date) -> list[date]:
    try:
        service.check_range(from_, to)
    except service.GearBookingError as error:
        _unprocessable(error)
    return service.dates_between(from_, to)


@router.get("/devices/{device_id}/availability", response_model=DeviceAvailability, tags=["devices"])
def device_availability(
    device_id: uuid.UUID,
    from_: date = Query(alias="from"),
    to: date = Query(),
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Per day: owned, scheduled, booked, available (may be negative) and
    who holds it; over the range the lowest day governs. At most 92 days."""
    device = _device(db, membership, device_id)
    dates = _range(from_, to)
    loaded = service.load(db, membership.company_id, [device.id], from_, to)
    numbers = service.availability(db, membership.company_id, [device], dates, loaded=loaded)[device.id]
    return _device_numbers(device, numbers, loaded.holders)


@router.get("/gear/availability", response_model=GearAvailabilityResponse, tags=["devices"])
def gear_availability(
    from_: date = Query(alias="from"),
    to: date = Query(),
    category: Optional[str] = Query(default=None, max_length=255),
    q: Optional[str] = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Many devices at once, for the picker and the planner. Active devices
    only, at most 500, by name."""
    dates = _range(from_, to)
    query = scoped(db.query(Device), membership).filter(Device.is_active.is_(True))
    if category and category.strip():
        query = query.filter(func.lower(Device.category) == category.strip().lower())
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.filter(or_(func.lower(Device.name).like(needle), func.lower(Device.category).like(needle)))
    devices = query.order_by(func.lower(Device.name), Device.id).limit(500).all()
    loaded = service.load(db, membership.company_id, [d.id for d in devices], from_, to)
    numbers = service.availability(db, membership.company_id, devices, dates, loaded=loaded)
    return GearAvailabilityResponse(
        range_start=from_,
        range_end=to,
        devices=[_device_numbers(d, numbers[d.id], loaded.holders) for d in devices],
    )


# ─── Reading bookings ───────────────────────────────────────────────────────


@router.get("/projects/{project_id}/gear", response_model=ProjectGearResponse, tags=["projects"])
def project_gear(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Per strip: the bookings with their warnings, counts in pieces and the
    total weight. Removed (`cancelled`) bookings are not listed. No prices."""
    project = _project(db, membership, project_id)
    blocks = (
        scoped(db.query(ProjectBlock), membership)
        .filter(ProjectBlock.project_id == project.id, ProjectBlock.is_active.is_(True))
        .order_by(ProjectBlock.start_date, ProjectBlock.created_at)
        .all()
    )
    rows = (
        scoped(db.query(GearBooking), membership)
        .filter(GearBooking.project_id == project.id, GearBooking.status != GEAR_CANCELLED)
        .all()
    )
    names = dict(db.query(Device.id, Device.name).filter(Device.id.in_({r.device_id for r in rows})).all()) if rows else {}
    built = responses(db, membership, in_reading_order(rows, names))
    by_block: dict[uuid.UUID, list[GearBookingResponse]] = {}
    for item in built:
        by_block.setdefault(item.block_id, []).append(item)
    strips = []
    shown: list[GearBookingResponse] = []
    for block in blocks:
        bookings = by_block.get(block.id, [])
        shown.extend(bookings)
        total, missing = _weight(bookings)
        strips.append(
            ProjectGearStrip(
                block_id=block.id,
                strip_name=service.strip_name(block),
                type=block.type,
                start_date=block.start_date,
                end_date=block.end_date,
                counts=_counts(bookings),
                total_weight_kg=total,
                items_without_weight=missing,
                bookings=bookings,
            )
        )
    total, missing = _weight(shown)
    return ProjectGearResponse(
        project_id=project.id,
        counts=_counts(shown),
        total_weight_kg=total,
        items_without_weight=missing,
        strips=strips,
    )


@router.get("/devices/{device_id}/bookings", response_model=DeviceBookingsResponse, tags=["devices"])
def device_bookings(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """The device's open bookings in date order (first day), by project and
    strip, with status."""
    device = _device(db, membership, device_id)
    rows = (
        scoped(db.query(GearBooking), membership)
        .join(ProjectBlock, ProjectBlock.id == GearBooking.block_id)
        .filter(
            GearBooking.device_id == device.id,
            GearBooking.status != GEAR_CANCELLED,
            ProjectBlock.is_active.is_(True),
        )
        .all()
    )
    built = responses(db, membership, rows)
    built.sort(key=lambda b: (b.dates[0] if b.dates else date.max, b.project_name.lower(), b.strip_name.lower()))
    return DeviceBookingsResponse(device_id=device.id, bookings=built)


# ─── Writing ────────────────────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/blocks/{block_id}/gear/preview",
    response_model=GearBookingsPreview,
    tags=["projects"],
)
def preview_gear(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: GearBookingsCreate,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """What booking these devices would add — the devices, their `always`
    dependencies with quantities, the chosen `ask` ones — with the numbers
    before and after, and the `ask` dependencies still to choose. Nothing
    is stored."""
    project = _project(db, membership, project_id)
    block = _block(db, membership, project, block_id)
    try:
        plan = service.plan_bookings(db, block=block, wanted=_wanted(db, membership, body))
    except service.GearBookingError as error:
        _unprocessable(error)
    devices = list({e.device.id: e.device for e in plan.entries}.values())
    all_dates = sorted({d for e in plan.entries for d in e.dates})
    loaded = (
        service.load(db, membership.company_id, [d.id for d in devices], all_dates[0], all_dates[-1])
        if all_dates
        else service.Loaded(by_device={}, holders={})
    )
    planned = []
    wanted_per_device: dict[uuid.UUID, int] = {}
    for entry in plan.entries:
        wanted_per_device[entry.device.id] = wanted_per_device.get(entry.device.id, 0) + entry.quantity
    for entry in plan.entries:
        numbers = service.availability(db, membership.company_id, [entry.device], entry.dates, loaded=loaded)[entry.device.id]
        parent = plan.entries[entry.parent].device if entry.parent is not None else None
        planned.append(
            GearPlannedBooking(
                device_id=entry.device.id,
                device_name=entry.device.name,
                thumbnail_url=thumbnail_url(entry.device),
                quantity=entry.quantity,
                via=entry.via,
                parent_device_id=parent.id if parent else None,
                association_id=entry.association.id if entry.association else None,
                dates=entry.dates,
                owned=entry.device.quantity_owned,
                min_available=numbers.min_available,
                available_after=numbers.min_available - wanted_per_device[entry.device.id],
            )
        )
    return GearBookingsPreview(bookings=planned, suggestions=[_suggestion(plan, s) for s in plan.suggestions])


@router.post(
    "/projects/{project_id}/blocks/{block_id}/gear",
    response_model=GearBookingsCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def add_gear(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: GearBookingsCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Several devices on a strip at once, `scheduled`, each with a quantity
    and days (default: every day of the strip that is not `off`), their
    `always` dependencies and the chosen `ask` ones. All or none.
    Overbooking is listed in `warnings`, never refused."""
    project = _project(db, membership, project_id)
    block = _block(db, membership, project, block_id)
    wanted = _wanted(db, membership, body)
    try:
        made, plan = service.create_bookings(
            db, project=project, block=block, wanted=wanted, actor=audit.actor_from(request, membership=membership)
        )
    except service.GearBookingError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    built = responses(db, membership, made)
    return GearBookingsCreated(
        bookings=built,
        warnings=[b.warning for b in built if b.warning is not None],
        suggestions=[_suggestion(plan, s) for s in plan.suggestions],
    )


@router.patch("/projects/{project_id}/gear/{booking_id}", response_model=GearBookingUpdated, tags=["projects"])
def update_gear(
    project_id: uuid.UUID,
    booking_id: uuid.UUID,
    body: GearBookingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Quantity, days, the device (a swap, e.g. to an alternative) and the
    overbooking note. A parent's new quantity recalculates the dependents
    that still follow it; a dependent changed by hand stops following."""
    project = _project(db, membership, project_id)
    row = _booking(db, membership, project, booking_id)
    block = db.get(ProjectBlock, row.block_id)
    changes = body.model_dump(exclude_unset=True)
    device = _device(db, membership, changes["device_id"]) if changes.get("device_id") else None
    try:
        result = service.update_booking(
            db, row=row, block=block, changes=changes, device=device,
            actor=audit.actor_from(request, membership=membership),
        )
    except service.GearBookingError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    built = _one(db, membership, row)
    recalculated = responses(db, membership, result.recalculated)
    message = None
    if result.stopped_following:
        message = (
            f"{built.device_name} no longer follows {built.added_via_device_name or 'its parent'}: "
            "its quantity and days stay as you set them."
        )
    return GearBookingUpdated(
        booking=built,
        warnings=[b.warning for b in [built, *recalculated] if b.warning is not None],
        recalculated=recalculated,
        stopped_following=result.stopped_following,
        message=message,
    )


@router.post("/projects/{project_id}/gear/{booking_id}/status", response_model=GearBookingResponse, tags=["projects"])
def set_gear_status(
    project_id: uuid.UUID,
    booking_id: uuid.UUID,
    body: GearStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """scheduled → booked (by hand, the note says how), or back to scheduled
    with a note. Cancelling is Remove."""
    project = _project(db, membership, project_id)
    row = _booking(db, membership, project, booking_id)
    name = db.get(Device, row.device_id).name
    try:
        service.set_status(
            db, row=row, name=name, wanted=body.status, note=body.note,
            actor=audit.actor_from(request, membership=membership),
        )
    except service.GearBookingError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _one(db, membership, row)


@router.post("/projects/{project_id}/gear/status", response_model=GearBulkStatusResult, tags=["projects"])
def set_gear_status_bulk(
    project_id: uuid.UUID,
    body: GearBulkStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """"Mark selected as booked" for several bookings. The ones that cannot
    take the change are listed and left alone."""
    project = _project(db, membership, project_id)
    rows = (
        scoped(db.query(GearBooking, Device.name), membership)
        .join(Device, Device.id == GearBooking.device_id)
        .filter(GearBooking.project_id == project.id, GearBooking.id.in_(set(body.booking_ids)))
        .all()
    )
    if len(rows) != len(set(body.booking_ids)):
        _not_found()
    result = service.set_status_bulk(
        db,
        rows=[(row, name) for row, name in rows],
        wanted=body.status,
        note=body.note,
        actor=audit.actor_from(request, membership=membership),
        project=project,
    )
    db.commit()
    names = {row.id: name for row, name in rows}
    return GearBulkStatusResult(
        changed=len(result.changed),
        skipped=[
            GearBulkSkipped(booking_id=row.id, device_name=names[row.id], reason=reason)
            for row, reason in result.skipped
        ],
        bookings=responses(db, membership, [row for row, _ in rows]),
    )


@router.post("/projects/{project_id}/gear/{booking_id}/remove", response_model=GearRemoveResult, tags=["projects"])
def remove_gear(
    project_id: uuid.UUID,
    booking_id: uuid.UUID,
    request: Request,
    body: Optional[GearRemove] = None,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Take the device off the strip: `cancelled` with an optional note,
    never deleted. A parent with open dependents is removed only once
    `dependents` says `cancel` (them too) or `keep` (as bookings of their
    own); without it nothing changes and the answer lists them."""
    body = body or GearRemove()
    project = _project(db, membership, project_id)
    row = _booking(db, membership, project, booking_id)
    result = service.remove_booking(
        db, row=row, note=body.note, dependents_choice=body.dependents,
        actor=audit.actor_from(request, membership=membership),
    )
    db.commit()
    built = _one(db, membership, row)
    dependents = responses(db, membership, result.dependents)
    question = None
    if not result.removed:
        names = ", ".join(f"{d.quantity} × {d.device_name}" for d in dependents)
        question = (
            f"{built.device_name} was booked with {names}. Cancel them too, or keep them "
            "on the strip as bookings of their own?"
        )
    return GearRemoveResult(
        removed=result.removed,
        booking=built,
        dependents=dependents,
        dependents_choice=result.choice,
        question=question,
    )
