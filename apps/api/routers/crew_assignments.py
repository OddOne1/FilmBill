"""Crew on the calendar (B2c-1 part 2): assignments to strips, unavailability
and the availability grid.

**Permissions.** Reading an assignment needs `projects.view` AND
`parties.view` — so staff, who hold the first and not the second, see no
crew names on the calendar (D111, open). Changing one needs `projects.edit`
(and `parties.view`). Unavailability is part of the person: `parties.view`
reads it, `parties.edit` changes it. Any missing permission is a 404, like
everywhere. Every `day_rate` and rate is null without `budgets.view`, and
writing one, or filtering by one, is a 404 without it.

**Clashes warn and never block.** Every response that can produce one lists
them with a sentence; nothing here is refused because of one.

**No DELETE route.** Removing somebody from a strip sets `cancelled`; an
unavailability period is retired with `.../remove`.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import NOT_FOUND_DETAIL, Membership, require
from ..models.catalog import Item
from ..models.crew import (
    ASSIGNMENT_BOOKED,
    ASSIGNMENT_CANCELLED,
    ASSIGNMENT_DECLINED,
    ASSIGNMENT_SCHEDULED,
    OCCUPYING_STATUSES,
    CrewAssignment,
    CrewUnavailability,
)
from ..models.party import Party
from ..models.project import Project, ProjectBlock, ProjectDay
from ..models.user import User
from ..schemas.crew_assignments import (
    AvailabilityDay,
    AvailabilityEntry,
    AvailabilityPerson,
    AvailabilityResponse,
    CrewAssignmentDayResponse,
    CrewAssignmentResponse,
    CrewAssignmentsCreate,
    CrewAssignmentsCreated,
    CrewAssignmentUpdate,
    CrewBulkSkipped,
    CrewBulkStatusChange,
    CrewBulkStatusResult,
    CrewClashResponse,
    CrewStatusChange,
    CrewStripCounts,
    ProjectCrewResponse,
    ProjectCrewStrip,
    UnavailabilityCreate,
    UnavailabilityList,
    UnavailabilityResponse,
    UnavailabilityUpdate,
)
from ..services import audit, crew_assignment_service as service, crew_roles_service, party_service
from ..services.permissions import scoped

router = APIRouter()


# ─── Permissions ────────────────────────────────────────────────────────────


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def can_view(membership: Membership = Depends(require("projects.view"))) -> Membership:
    """`projects.view` and `parties.view`: staff see no crew names (D111)."""
    if not membership.can("parties.view"):
        _not_found()
    return membership


def can_change(membership: Membership = Depends(require("projects.edit"))) -> Membership:
    if not membership.can("parties.view"):
        _not_found()
    return membership


def rates_visible(membership: Membership) -> bool:
    return membership.can(crew_roles_service.RATE_PERMISSION)


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


def _assignment(db: Session, membership: Membership, project: Project, assignment_id: uuid.UUID) -> CrewAssignment:
    row = (
        scoped(db.query(CrewAssignment), membership)
        .filter(CrewAssignment.id == assignment_id, CrewAssignment.project_id == project.id)
        .first()
    )
    if row is None:
        _not_found()
    return row


def _labor_item(db: Session, membership: Membership, item_id: Optional[uuid.UUID]) -> Optional[Item]:
    if item_id is None:
        return None
    item = crew_roles_service.labor_item(db, membership.company_id, item_id)
    if item is None:
        _not_found()
    return item


def _party(db: Session, membership: Membership, party_id: uuid.UUID) -> Party:
    row = scoped(db.query(Party), membership).filter(Party.id == party_id).first()
    if row is None:
        _not_found()
    return row


# ─── Response building ──────────────────────────────────────────────────────


def _clash(clash: service.Clash) -> CrewClashResponse:
    return CrewClashResponse(
        kind=clash.kind,
        party_id=clash.party_id,
        party_name=clash.party_name,
        dates=sorted(clash.dates),
        message=clash.message,
        assignment_id=clash.assignment_id,
        project_id=clash.project_id,
        project_name=clash.project_name,
        project_number=clash.project_number,
        block_id=clash.block_id,
        strip_name=clash.strip_name,
        status=clash.status,
        unavailability_id=clash.unavailability_id,
        reason=clash.reason,
        start_date=clash.start_date,
        end_date=clash.end_date,
    )


def responses(db: Session, membership: Membership, rows: list[CrewAssignment]) -> list[CrewAssignmentResponse]:
    """Assignments with names, days (own, else the day's, else the strip's
    call and wrap) and clashes, in a fixed number of queries."""
    if not rows:
        return []
    company_id = membership.company_id
    visible = rates_visible(membership)
    names = dict(db.query(Party.id, Party.name).filter(Party.id.in_({r.party_id for r in rows})).all())
    role_ids = {r.role_item_id for r in rows if r.role_item_id}
    role_names = dict(db.query(Item.id, Item.name_de).filter(Item.id.in_(role_ids)).all()) if role_ids else {}
    users = {r.booked_by for r in rows if r.booked_by}
    user_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(users)).all()} if users else {}
    days = service.active_days(db, [r.id for r in rows])
    blocks = {b.id: b for b in db.query(ProjectBlock).filter(ProjectBlock.id.in_({r.block_id for r in rows}))}
    project_days = {
        (d.block_id, d.date): d
        for d in db.query(ProjectDay).filter(
            ProjectDay.company_id == company_id,
            ProjectDay.block_id.in_(set(blocks)),
            ProjectDay.is_active.is_(True),
        )
    }
    clashes = service.clashes_for(
        db,
        company_id,
        [
            (r.id, r.party_id, names.get(r.party_id, ""), [d.date for d in days[r.id]])
            for r in rows
            if r.status in OCCUPYING_STATUSES
        ],
    )
    out = []
    for row in rows:
        block = blocks.get(row.block_id)
        day_rows = []
        for day in days[row.id]:
            pday = project_days.get((row.block_id, day.date))
            day_rows.append(
                CrewAssignmentDayResponse(
                    date=day.date,
                    call_time=day.call_time,
                    wrap_time=day.wrap_time,
                    effective_call_time=day.call_time
                    or (pday.call_time if pday else None)
                    or (block.default_call_time if block else None),
                    effective_wrap_time=day.wrap_time
                    or (pday.wrap_time if pday else None)
                    or (block.default_wrap_time if block else None),
                    day_type=pday.type if pday else None,
                )
            )
        out.append(
            CrewAssignmentResponse(
                id=row.id,
                project_id=row.project_id,
                block_id=row.block_id,
                party_id=row.party_id,
                party_name=names.get(row.party_id, ""),
                role_item_id=row.role_item_id,
                role_name=role_names.get(row.role_item_id) if row.role_item_id else None,
                status=row.status,
                rates_visible=visible,
                day_rate=row.day_rate if visible else None,
                status_note=row.status_note,
                booked_on=row.booked_on,
                booked_by_name=user_names.get(row.booked_by) if row.booked_by else None,
                days=day_rows,
                clashes=[_clash(c) for c in clashes.get(row.id, [])],
                created_at=row.created_at,
            )
        )
    return out


def _one(db: Session, membership: Membership, row: CrewAssignment) -> CrewAssignmentResponse:
    db.refresh(row)
    return responses(db, membership, [row])[0]


def counts(assignments: list[CrewAssignmentResponse]) -> CrewStripCounts:
    return CrewStripCounts(
        scheduled=sum(1 for a in assignments if a.status == ASSIGNMENT_SCHEDULED),
        booked=sum(1 for a in assignments if a.status == ASSIGNMENT_BOOKED),
        declined=sum(1 for a in assignments if a.status == ASSIGNMENT_DECLINED),
        with_clashes=sum(1 for a in assignments if a.clashes),
    )


# ─── Reading ────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/crew", response_model=ProjectCrewResponse, tags=["projects"])
def project_crew(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Per strip: who is scheduled, booked or declined, with counts and
    clashes. Removed (`cancelled`) entries are not listed."""
    project = _project(db, membership, project_id)
    blocks = (
        scoped(db.query(ProjectBlock), membership)
        .filter(ProjectBlock.project_id == project.id, ProjectBlock.is_active.is_(True))
        .order_by(ProjectBlock.start_date, ProjectBlock.created_at)
        .all()
    )
    rows = (
        scoped(db.query(CrewAssignment), membership)
        .filter(
            CrewAssignment.project_id == project.id,
            CrewAssignment.status != ASSIGNMENT_CANCELLED,
        )
        .order_by(CrewAssignment.created_at, CrewAssignment.id)
        .all()
    )
    built = responses(db, membership, rows)
    by_block: dict[uuid.UUID, list[CrewAssignmentResponse]] = {}
    for item in built:
        by_block.setdefault(item.block_id, []).append(item)
    return ProjectCrewResponse(
        project_id=project.id,
        rates_visible=rates_visible(membership),
        strips=[
            ProjectCrewStrip(
                block_id=block.id,
                strip_name=service.strip_name(block),
                type=block.type,
                start_date=block.start_date,
                end_date=block.end_date,
                counts=counts(by_block.get(block.id, [])),
                assignments=by_block.get(block.id, []),
            )
            for block in blocks
        ],
    )


# ─── Writing ────────────────────────────────────────────────────────────────


@router.post(
    "/projects/{project_id}/blocks/{block_id}/crew",
    response_model=CrewAssignmentsCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def add_crew(
    project_id: uuid.UUID,
    block_id: uuid.UUID,
    body: CrewAssignmentsCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Several people on a strip at once, `scheduled`, each with an optional
    role and days (default: every day of the strip that is not `off`).
    All or none. Clashes are listed, never refused."""
    project = _project(db, membership, project_id)
    block = _block(db, membership, project, block_id)
    people = []
    for entry in body.people:
        people.append(
            service.NewAssignment(
                party=_party(db, membership, entry.party_id),
                role=_labor_item(db, membership, entry.role_item_id),
                days=[service.DayWish(date=d) for d in entry.dates] if entry.dates is not None else None,
            )
        )
    try:
        made = service.create_assignments(
            db,
            project=project,
            block=block,
            people=people,
            actor=audit.actor_from(request, membership=membership),
        )
    except service.AssignmentError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    built = responses(db, membership, made)
    return CrewAssignmentsCreated(
        assignments=built, clashes=[clash for item in built for clash in item.clashes]
    )


@router.patch(
    "/projects/{project_id}/crew/{assignment_id}",
    response_model=CrewAssignmentResponse,
    tags=["projects"],
)
def update_crew(
    project_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: CrewAssignmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Role, day rate (needs `budgets.view`) and days with call and wrap. A
    new role without a rate copies the person's effective rate in it."""
    project = _project(db, membership, project_id)
    row = _assignment(db, membership, project, assignment_id)
    block = db.get(ProjectBlock, row.block_id)
    changes = body.model_dump(exclude_unset=True)
    if "day_rate" in changes and not rates_visible(membership):
        _not_found()
    role = _labor_item(db, membership, changes.get("role_item_id")) if "role_item_id" in changes else None
    if "days" in changes:
        changes["days"] = [
            service.DayWish(date=d["date"], call_time=d.get("call_time"), wrap_time=d.get("wrap_time"))
            for d in changes["days"] or []
        ]
    try:
        if "day_rate" in changes:
            changes["day_rate"] = crew_roles_service.clean_rate(changes["day_rate"])
        elif "role_item_id" in changes:
            party = db.get(Party, row.party_id)
            changes["day_rate"] = service._rate_for(db, party, role)
        service.update_assignment(
            db,
            row=row,
            block=block,
            changes=changes,
            role=role,
            actor=audit.actor_from(request, membership=membership),
        )
    except (service.AssignmentError, crew_roles_service.CrewRoleError) as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _one(db, membership, row)


@router.post(
    "/projects/{project_id}/crew/{assignment_id}/status",
    response_model=CrewAssignmentResponse,
    tags=["projects"],
)
def set_crew_status(
    project_id: uuid.UUID,
    assignment_id: uuid.UUID,
    body: CrewStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """scheduled → booked (by hand, the note says how) or declined; booked
    → cancelled, or back to scheduled with a note."""
    project = _project(db, membership, project_id)
    row = _assignment(db, membership, project, assignment_id)
    name = db.get(Party, row.party_id).name
    try:
        service.set_status(
            db,
            row=row,
            name=name,
            wanted=body.status,
            note=body.note,
            actor=audit.actor_from(request, membership=membership),
        )
    except service.AssignmentError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _one(db, membership, row)


@router.post(
    "/projects/{project_id}/crew/status",
    response_model=CrewBulkStatusResult,
    tags=["projects"],
)
def set_crew_status_bulk(
    project_id: uuid.UUID,
    body: CrewBulkStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """"Mark selected as booked" (or another status) for several people.
    The ones that cannot take the change are listed and left alone."""
    project = _project(db, membership, project_id)
    rows = (
        scoped(db.query(CrewAssignment, Party.name), membership)
        .join(Party, Party.id == CrewAssignment.party_id)
        .filter(
            CrewAssignment.project_id == project.id,
            CrewAssignment.id.in_(set(body.assignment_ids)),
        )
        .all()
    )
    if len(rows) != len(set(body.assignment_ids)):
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
    return CrewBulkStatusResult(
        changed=len(result.changed),
        skipped=[
            CrewBulkSkipped(assignment_id=row.id, party_name=names[row.id], reason=reason)
            for row, reason in result.skipped
        ],
        assignments=responses(db, membership, [row for row, _ in rows]),
    )


@router.post(
    "/projects/{project_id}/crew/{assignment_id}/remove",
    response_model=CrewAssignmentResponse,
    tags=["projects"],
)
def remove_crew(
    project_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Take the person off the strip: `cancelled`, never deleted."""
    project = _project(db, membership, project_id)
    row = _assignment(db, membership, project, assignment_id)
    service.remove_assignment(db, row=row, actor=audit.actor_from(request, membership=membership))
    db.commit()
    return _one(db, membership, row)


# ─── Availability ───────────────────────────────────────────────────────────


@router.get("/crew/availability", response_model=AvailabilityResponse, tags=["parties"])
def availability(
    from_: date = Query(alias="from"),
    to: date = Query(),
    role_item_id: Optional[uuid.UUID] = Query(default=None, description="Only holders of this labor item."),
    max_rate: Optional[Decimal] = Query(default=None, ge=0, description="Effective day rate at most. Needs budgets.view."),
    project_id: Optional[uuid.UUID] = Query(default=None, description="Only people on this project."),
    search: Optional[str] = Query(default=None, max_length=255),
    available_only: bool = Query(default=False, description="Only people free on every day of the range."),
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """For each crew person, per day: free, scheduled, booked or unavailable,
    with what it is. At most 92 days."""
    if max_rate is not None and not rates_visible(membership):
        _not_found()
    try:
        service.check_range(from_, to)
    except service.AssignmentError as error:
        _unprocessable(error)
    company_id = membership.company_id
    query = scoped(db.query(Party), membership).filter(Party.is_crew.is_(True), Party.is_active.is_(True))
    if role_item_id is not None:
        query = query.filter(crew_roles_service.has_role(company_id, Party.id, role_item_id))
    if max_rate is not None:
        query = query.filter(
            crew_roles_service.in_rate_range(company_id, Party.id, item_id=role_item_id, low=None, high=max_rate)
        )
    if project_id is not None:
        on_project = (
            db.query(CrewAssignment.party_id)
            .filter(
                CrewAssignment.company_id == company_id,
                CrewAssignment.project_id == project_id,
                CrewAssignment.status != ASSIGNMENT_CANCELLED,
            )
        )
        query = query.filter(Party.id.in_(on_project))
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(Party.name.ilike(needle), Party.first_name.ilike(needle), Party.last_name.ilike(needle))
        )
    people = query.order_by(*party_service.sort_key_columns()).limit(500).all()
    ids = [p.id for p in people]
    cells = service.grid(db, company_id, ids, from_, to)
    roles = crew_roles_service.roles_of(db, company_id, ids)
    visible = rates_visible(membership)
    dates = service._dates(from_, to)
    out = []
    for person in people:
        days = []
        for day in dates:
            entries = cells[person.id].get(day, [])
            days.append(
                AvailabilityDay(
                    date=day,
                    state=service.day_state(entries),
                    clash=len(entries) > 1,
                    entries=[
                        AvailabilityEntry(
                            kind=e.kind,
                            status=e.status,
                            assignment_id=e.assignment_id,
                            project_id=e.project_id,
                            project_name=e.project_name,
                            block_id=e.block_id,
                            strip_name=e.strip_name,
                            unavailability_id=e.unavailability_id,
                            reason=e.reason,
                        )
                        for e in entries
                    ],
                )
            )
        free = sum(1 for d in days if d.state == "free")
        if available_only and free != len(days):
            continue
        views = roles[person.id]
        considered = [v for v in views if role_item_id is None or v.row.item_id == role_item_id]
        rates = [v.rate.effective for v in considered if v.rate.effective is not None]
        out.append(
            AvailabilityPerson(
                party_id=person.id,
                name=person.name,
                roles=[v.name for v in views],
                rate=min(rates) if visible and rates else None,
                days=days,
                free_days=free,
                is_team_member=person.user_id is not None,
            )
        )
    return AvailabilityResponse(range_start=from_, range_end=to, rates_visible=visible, people=out)


# ─── Unavailability ─────────────────────────────────────────────────────────


def _period(row: CrewUnavailability) -> UnavailabilityResponse:
    return UnavailabilityResponse(
        id=row.id,
        party_id=row.party_id,
        start_date=row.start_date,
        end_date=row.end_date,
        reason=row.reason,
        note=row.note,
        created_at=row.created_at,
    )


def _periods(db: Session, membership: Membership, party: Party) -> UnavailabilityList:
    rows = (
        scoped(db.query(CrewUnavailability), membership)
        .filter(CrewUnavailability.party_id == party.id, CrewUnavailability.is_active.is_(True))
        .order_by(CrewUnavailability.start_date)
        .all()
    )
    return UnavailabilityList(party_id=party.id, periods=[_period(r) for r in rows])


def _period_or_404(db: Session, membership: Membership, party: Party, period_id: uuid.UUID) -> CrewUnavailability:
    row = (
        scoped(db.query(CrewUnavailability), membership)
        .filter(
            CrewUnavailability.id == period_id,
            CrewUnavailability.party_id == party.id,
            CrewUnavailability.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        _not_found()
    return row


@router.get("/parties/{party_id}/unavailability", response_model=UnavailabilityList, tags=["parties"])
def list_unavailability(
    party_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The person's own "not available" periods."""
    return _periods(db, membership, _party(db, membership, party_id))


@router.post(
    "/parties/{party_id}/unavailability",
    response_model=UnavailabilityList,
    status_code=status.HTTP_201_CREATED,
    tags=["parties"],
)
def add_unavailability(
    party_id: uuid.UUID,
    body: UnavailabilityCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """A vacation, another job elsewhere. Warns on the calendar, never blocks."""
    party = _party(db, membership, party_id)
    try:
        service.add_unavailability(
            db,
            party=party,
            start=body.start_date,
            end=body.end_date,
            reason=body.reason,
            note=body.note,
            actor=audit.actor_from(request, membership=membership),
        )
    except service.AssignmentError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _periods(db, membership, party)


@router.patch(
    "/parties/{party_id}/unavailability/{unavailability_id}",
    response_model=UnavailabilityList,
    tags=["parties"],
)
def update_unavailability(
    party_id: uuid.UUID,
    unavailability_id: uuid.UUID,
    body: UnavailabilityUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    party = _party(db, membership, party_id)
    row = _period_or_404(db, membership, party, unavailability_id)
    try:
        service.update_unavailability(
            db, row=row, changes=body.model_dump(exclude_unset=True),
            actor=audit.actor_from(request, membership=membership),
        )
    except service.AssignmentError as error:
        db.rollback()
        _unprocessable(error)
    db.commit()
    return _periods(db, membership, party)


@router.post(
    "/parties/{party_id}/unavailability/{unavailability_id}/remove",
    response_model=UnavailabilityList,
    tags=["parties"],
)
def remove_unavailability(
    party_id: uuid.UUID,
    unavailability_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Retire the period; the row is kept."""
    party = _party(db, membership, party_id)
    row = _period_or_404(db, membership, party, unavailability_id)
    service.remove_unavailability(db, row=row, actor=audit.actor_from(request, membership=membership))
    db.commit()
    return _periods(db, membership, party)
