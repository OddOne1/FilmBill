"""Projects and their production days.

Every handler takes its context from `require("projects.view")` or
`require("projects.edit")`, so there is no path to the company context that
skips the check, and another company's row is a 404 (rule 6) — including
another company's PARTY offered as a client, which is checked here rather
than left to the foreign key.

**No DELETE route, anywhere.** A project is retired by setting its status to
`archived`, a day by `POST .../remove`, which sets `is_active = False` and
writes a `project_day.removed` event. `test_openapi_surface.py` asserts the
absence on the OpenAPI document rather than on a handler's behaviour — the
same shape parties' and the catalog's use, and the only version of this check
that a new route cannot quietly escape.

**The number is allocated inside the create transaction** (rule 5), in
`project_service.create_project`. The handler's only job around it is to
commit once, at the end, so that a refused day range or a rejected client
leaves nothing behind.
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.company import Membership, require
from ..models.party import Party
from ..models.project import Budget, Project, ProjectDay
from ..schemas.project import (
    ProjectCreate,
    ProjectDayList,
    ProjectDayRangeRequest,
    ProjectDayRangeResponse,
    ProjectDayResponse,
    ProjectDayUpdate,
    ProjectDayWrite,
    ProjectPage,
    ProjectResponse,
    ProjectStatusRequest,
    ProjectUpdate,
)
from ..services import audit, project_service
from ..services.permissions import scoped

router = APIRouter(tags=["projects"])


# ─── Lookups and refusals ───────────────────────────────────────────────────


def _project_or_404(
    db: Session, membership: Membership, project_id: uuid.UUID
) -> Project:
    row = (
        scoped(db.query(Project), membership)
        .filter(Project.id == project_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return row


def _day_or_404(
    db: Session, membership: Membership, project: Project, day_id: uuid.UUID
) -> ProjectDay:
    row = (
        scoped(db.query(ProjectDay), membership)
        .filter(ProjectDay.id == day_id, ProjectDay.project_id == project.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Production day not found"
        )
    return row


def _refuse(error: Exception, code: int = status.HTTP_409_CONFLICT):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _check_client(
    db: Session, membership: Membership, client_party_id: Optional[uuid.UUID]
) -> None:
    """A client must be a party of THIS company, or there is no such client.

    404 and not 422: a 422 naming the field would confirm that the id exists
    somewhere, and "the id you sent belongs to another company" is exactly
    the sentence rule 6 exists to stop us writing. The foreign key would
    reject it too, but as a 500 with a constraint name in it.
    """
    if client_party_id is None:
        return
    found = (
        scoped(db.query(Party), membership)
        .filter(Party.id == client_party_id)
        .first()
    )
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client party not found"
        )


# ─── Response building ──────────────────────────────────────────────────────


def _day_counts(db: Session, project_ids: list[uuid.UUID]) -> dict:
    """`{project_id: {type: count}}` over active days, in one query.

    One query for the whole page rather than one per row: a 50-project list
    with a count per project is the shape that makes a list screen slow, and
    it is cheap to avoid.
    """
    if not project_ids:
        return {}
    rows = (
        db.query(ProjectDay.project_id, ProjectDay.type, func.count())
        .filter(
            ProjectDay.project_id.in_(project_ids),
            ProjectDay.is_active.is_(True),
        )
        .group_by(ProjectDay.project_id, ProjectDay.type)
        .all()
    )
    counts: dict = {}
    for project_id, day_type, total in rows:
        counts.setdefault(project_id, {})[day_type] = int(total)
    return counts


def _budget_counts(db: Session, project_ids: list[uuid.UUID]) -> dict:
    if not project_ids:
        return {}
    rows = (
        db.query(Budget.project_id, func.count())
        .filter(Budget.project_id.in_(project_ids), Budget.is_active.is_(True))
        .group_by(Budget.project_id)
        .all()
    )
    return {project_id: int(total) for project_id, total in rows}


def _client_names(db: Session, projects: list[Project]) -> dict:
    ids = [p.client_party_id for p in projects if p.client_party_id is not None]
    if not ids:
        return {}
    return {
        row.id: row.name
        for row in db.query(Party).filter(Party.id.in_(ids)).all()
    }


def _project_response(
    project: Project,
    *,
    client_name: Optional[str] = None,
    day_counts: Optional[dict] = None,
    budget_count: int = 0,
) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        project_number=project.project_number,
        name=project.name,
        client_party_id=project.client_party_id,
        client_name=client_name,
        description=project.description,
        status=project.status,
        start_date=project.start_date,
        end_date=project.end_date,
        notes=project.notes,
        day_counts=day_counts or {},
        budget_count=budget_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _one(db: Session, project: Project) -> ProjectResponse:
    names = _client_names(db, [project])
    return _project_response(
        project,
        client_name=names.get(project.client_party_id),
        day_counts=_day_counts(db, [project.id]).get(project.id, {}),
        budget_count=_budget_counts(db, [project.id]).get(project.id, 0),
    )


# ─── The list ───────────────────────────────────────────────────────────────


@router.get("/projects", response_model=ProjectPage)
def list_projects(
    search: Optional[str] = Query(default=None, max_length=255),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="planning | active | wrapped | archived",
    ),
    client_party_id: Optional[uuid.UUID] = Query(default=None),
    include_archived: bool = Query(
        default=False,
        description=(
            "Archived projects are left out unless asked for. They are never "
            "deleted, so the list would otherwise fill up with them."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.view")),
):
    """Search by name or project number; filter by status and by client."""
    query = scoped(db.query(Project), membership)
    if status_filter:
        query = query.filter(Project.status == status_filter)
    elif not include_archived:
        query = query.filter(Project.status != "archived")
    if client_party_id is not None:
        query = query.filter(Project.client_party_id == client_party_id)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        query = query.filter(
            func.lower(Project.name).like(needle)
            | func.lower(Project.project_number).like(needle)
        )

    total = query.count()
    rows = (
        query.order_by(Project.start_date.desc(), Project.project_number.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    ids = [row.id for row in rows]
    names = _client_names(db, rows)
    days = _day_counts(db, ids)
    budgets = _budget_counts(db, ids)
    return ProjectPage(
        projects=[
            _project_response(
                row,
                client_name=names.get(row.client_party_id),
                day_counts=days.get(row.id, {}),
                budget_count=budgets.get(row.id, 0),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED
)
def create_project(
    body: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Add a project. The number is allocated in this transaction (rule 5)."""
    _check_client(db, membership, body.client_party_id)
    try:
        project = project_service.create_project(
            db,
            membership,
            name=body.name,
            start_date=body.start_date,
            end_date=body.end_date,
            client_party_id=body.client_party_id,
            description=body.description,
            status=body.status,
            notes=body.notes,
            actor=audit.actor_from(request, membership=membership),
        )
    except project_service.ProjectError as error:
        _refuse(error)

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        project,
        project_service.PROJECT_CREATED,
        None,
        audit.snapshot(project, project_service.PROJECT_AUDITED),
        fields=project_service.PROJECT_AUDITED,
    )
    db.commit()
    db.refresh(project)
    return _one(db, project)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def read_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.view")),
):
    return _one(db, _project_or_404(db, membership, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    changes = body.model_dump(exclude_unset=True)
    if "client_party_id" in changes:
        _check_client(db, membership, changes["client_party_id"])

    before = audit.snapshot(project, project_service.PROJECT_AUDITED)
    for field_name, value in changes.items():
        setattr(project, field_name, value)

    start = project.start_date
    end = project.end_date
    if end is not None and start is not None and end < start:
        _refuse(
            project_service.ProjectError(
                f"the end date ({end}) would be before the start date "
                f"({start}). Move the start first, or leave the end empty."
            )
        )

    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        project,
        project_service.PROJECT_UPDATED,
        before,
        audit.snapshot(project, project_service.PROJECT_AUDITED),
        fields=project_service.PROJECT_AUDITED,
    )
    db.commit()
    db.refresh(project)
    return _one(db, project)


@router.post("/projects/{project_id}/status", response_model=ProjectResponse)
def change_status(
    project_id: uuid.UUID,
    body: ProjectStatusRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Move a project between planning, active, wrapped and archived.

    Its own endpoint and its own audit action, so that "when did this go
    active" and "when was it archived" are answerable from the log without
    diffing every `project.updated` event.
    """
    project = _project_or_404(db, membership, project_id)
    try:
        project_service.check_status(body.status)
    except project_service.ProjectError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)

    before = audit.snapshot(project, project_service.PROJECT_AUDITED)
    project.status = body.status
    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        project,
        project_service.PROJECT_STATUS_CHANGED,
        before,
        audit.snapshot(project, project_service.PROJECT_AUDITED),
        reason=body.reason,
        fields=project_service.PROJECT_AUDITED,
    )
    db.commit()
    db.refresh(project)
    return _one(db, project)


# ─── Days ───────────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/days", response_model=ProjectDayList)
def list_days(
    project_id: uuid.UUID,
    include_removed: bool = Query(
        default=False,
        description=(
            "Removed days are kept and audited, but a schedule shows the "
            "plan as it stands."
        ),
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.view")),
):
    project = _project_or_404(db, membership, project_id)
    query = scoped(db.query(ProjectDay), membership).filter(
        ProjectDay.project_id == project.id
    )
    if not include_removed:
        query = query.filter(ProjectDay.is_active.is_(True))
    rows = query.order_by(ProjectDay.date, ProjectDay.type).all()
    counts: dict[str, int] = {}
    for row in rows:
        if row.is_active:
            counts[row.type] = counts.get(row.type, 0) + 1
    return ProjectDayList(
        days=[ProjectDayResponse.model_validate(row) for row in rows],
        counts=counts,
    )


def _day_conflict(
    db: Session,
    membership: Membership,
    project: Project,
    *,
    day: date,
    day_type: str,
    exclude_id: Optional[uuid.UUID] = None,
) -> None:
    """One active day per (date, type), checked before the index does it.

    The partial unique index is the authority; this is so the answer is a
    409 with a sentence instead of an `IntegrityError` with a constraint
    name in it.
    """
    query = scoped(db.query(ProjectDay), membership).filter(
        ProjectDay.project_id == project.id,
        ProjectDay.date == day,
        ProjectDay.type == day_type,
        ProjectDay.is_active.is_(True),
    )
    if exclude_id is not None:
        query = query.filter(ProjectDay.id != exclude_id)
    if query.first() is not None:
        _refuse(
            project_service.ProjectError(
                f"{project.name} already has a {day_type} day on "
                f"{day.isoformat()}. Edit that one instead — a second would "
                f"be counted twice by anything that counts days, the "
                f"budget's shoot-day global included."
            )
        )


@router.post(
    "/projects/{project_id}/days",
    response_model=ProjectDayResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_day(
    project_id: uuid.UUID,
    body: ProjectDayWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    _day_conflict(db, membership, project, day=body.date, day_type=body.type)

    row = ProjectDay(
        company_id=membership.company_id,
        project_id=project.id,
        **body.model_dump(),
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        project_service.DAY_CREATED,
        None,
        audit.snapshot(row, project_service.PROJECT_DAY_AUDITED),
        fields=project_service.PROJECT_DAY_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return ProjectDayResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/days/range",
    response_model=ProjectDayRangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_day_range(
    project_id: uuid.UUID,
    body: ProjectDayRangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """"The 5th to the 12th, shoot days, no weekends" — one transaction.

    Days that already exist are reported rather than refused: adding a block
    over three days that are already in the plan is a normal act, and
    refusing the whole range because of them would make somebody enter the
    gaps by hand.

    **One audit event with counts**, not one per day. Fifteen rows in the log
    for one action is how a log stops being read.
    """
    project = _project_or_404(db, membership, project_id)
    try:
        created, existing, skipped = project_service.create_day_range(
            db,
            membership,
            project,
            start=body.from_,
            end=body.to,
            day_type=body.type,
            skip_weekends=body.skip_weekends,
        )
    except project_service.ProjectError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)

    parts = [f"{len(created)} {body.type} day(s) added"]
    if existing:
        parts.append(f"{len(existing)} already there")
    if skipped:
        parts.append(f"{len(skipped)} weekend day(s) skipped")
    detail = ", ".join(parts) + "."

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        project,
        project_service.DAY_CREATED,
        None,
        {
            "from": body.from_.isoformat(),
            "to": body.to.isoformat(),
            "type": body.type,
            "skip_weekends": body.skip_weekends,
            "created": len(created),
            "already_present": len(existing),
            "weekend_days_skipped": len(skipped),
        },
        reason=detail,
    )
    db.commit()
    return ProjectDayRangeResponse(
        created=[ProjectDayResponse.model_validate(row) for row in created],
        existing=existing,
        skipped=skipped,
        detail=detail,
    )


@router.patch(
    "/projects/{project_id}/days/{day_id}", response_model=ProjectDayResponse
)
def update_day(
    project_id: uuid.UUID,
    day_id: uuid.UUID,
    body: ProjectDayUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    project = _project_or_404(db, membership, project_id)
    row = _day_or_404(db, membership, project, day_id)
    changes = body.model_dump(exclude_unset=True)

    moved_date = changes.get("date", row.date)
    moved_type = changes.get("type", row.type)
    if moved_date != row.date or moved_type != row.type:
        _day_conflict(
            db,
            membership,
            project,
            day=moved_date,
            day_type=moved_type,
            exclude_id=row.id,
        )

    before = audit.snapshot(row, project_service.PROJECT_DAY_AUDITED)
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        project_service.DAY_UPDATED,
        before,
        audit.snapshot(row, project_service.PROJECT_DAY_AUDITED),
        fields=project_service.PROJECT_DAY_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return ProjectDayResponse.model_validate(row)


@router.post(
    "/projects/{project_id}/days/{day_id}/remove", response_model=ProjectDayResponse
)
def remove_day(
    project_id: uuid.UUID,
    day_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("projects.edit")),
):
    """Take a day out of the plan. The row stays; `is_active` goes false.

    Not a DELETE, and not only for the reason the other tables have one:
    anything that counts days — the budget's `days:<type>` globals above all
    — needs the row to stop counting without the record of who took it out
    disappearing with it.
    """
    project = _project_or_404(db, membership, project_id)
    row = _day_or_404(db, membership, project, day_id)
    if not row.is_active:
        return ProjectDayResponse.model_validate(row)

    before = audit.snapshot(row, project_service.PROJECT_DAY_AUDITED)
    row.is_active = False
    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        project_service.DAY_REMOVED,
        before,
        audit.snapshot(row, project_service.PROJECT_DAY_AUDITED),
        fields=project_service.PROJECT_DAY_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return ProjectDayResponse.model_validate(row)
