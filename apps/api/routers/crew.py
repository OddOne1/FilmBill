"""The crew list: people in the parties list with the `is_crew` flag (B2b).

Under the `parties` tag and the `parties.*` permissions because a crew member
IS a party: `parties.view` reads the list, `parties.edit` adds to it. A
member without the permission gets a 404 from `require()`, as everywhere.

**Neither direction leaks.** This list filters on `is_crew` and nothing else,
and the customer and supplier pickers (`GET /parties?role=customer|supplier`)
filter on their own flags and nothing else — so a crew-only person is never
offered as a customer, and a customer who is not crew never appears here.

**No DELETE.** Archiving a crew member is archiving the party, on its own
page. An import only ever creates.

**R1: roles and rates.** A person's roles are catalog labor items
(`/parties/{id}/crew-roles`); every rate in any response here is there only
for a member holding `budgets.view`, and the rate filter and sort are refused
(404, like every missing permission) without it.
"""

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from ..billing.resources.crew_csv import (
    ACTION_CREATE,
    ACTION_DUPLICATE,
    CrewValues,
    ImportFormatError,
    export_crew_csv,
)
from ..database import get_db
from ..middleware.company import Membership, require
from ..middleware.company import NOT_FOUND_DETAIL
from ..models.party import Party, PartyContactPoint
from ..schemas.crew import (
    CrewKind,
    CrewLaborItem,
    CrewRoleCreate,
    CrewRoleFromTag,
    CrewRoleResponse,
    CrewRoleUpdate,
    CrewTagSuggestion,
    PartyCrewRolesResponse,
    CrewCreate,
    CrewCreateResponse,
    CrewDuplicateWarning,
    CrewImportApplyResponse,
    CrewImportPreviewResponse,
    CrewImportRowResponse,
    CrewMemberResponse,
    CrewPage,
    CrewRoleSuggestions,
    CrewSort,
)
from ..services import audit, crew_roles_service, crew_service, party_service
from ..services.permissions import scoped

router = APIRouter(tags=["parties"])

#: A crew list is a few hundred people; a file this size is the wrong file.
MAX_IMPORT_BYTES = 1024 * 1024


def _unprocessable(message: str):
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def rates_visible(membership: Membership) -> bool:
    return membership.can(crew_roles_service.RATE_PERMISSION)


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def role_response(view: crew_roles_service.RoleView, visible: bool) -> CrewRoleResponse:
    """One role; the numbers only when `visible`."""
    rate = view.rate
    return CrewRoleResponse(
        id=view.row.id,
        item_id=view.row.item_id,
        name=view.name,
        notes=view.row.notes,
        own_day_rate=rate.own if visible else None,
        typical_day_rate=rate.typical if visible else None,
        effective_day_rate=rate.effective if visible else None,
        rate_source=rate.source if visible else None,
        difference_to_typical=rate.difference if visible else None,
        confirmed=view.row.confirmed,
        set_by=view.row.set_by,
    )


def _members(
    db: Session,
    membership: Membership,
    rows: list[Party],
    *,
    role_item_id: Optional[uuid.UUID] = None,
) -> list[CrewMemberResponse]:
    contacts = crew_service.primary_contacts(db, membership.company_id, [row.id for row in rows])
    roles = crew_roles_service.roles_of(db, membership.company_id, [row.id for row in rows])
    visible = rates_visible(membership)
    out = []
    for row in rows:
        views = roles[row.id]
        considered = [v for v in views if role_item_id is None or v.row.item_id == role_item_id]
        rates = [v.rate.effective for v in considered if v.rate.effective is not None]
        source = considered[0].rate.source if role_item_id is not None and considered else None
        out.append(_member(row, contacts, views, visible, rates, source))
    return out


def _member(row, contacts, views, visible, rates, source) -> CrewMemberResponse:
    return CrewMemberResponse(
            id=row.id,
            name=row.name,
            first_name=row.first_name,
            middle_name=row.middle_name,
            last_name=row.last_name,
            roles=list(row.tags or []),
            crew_roles=[role_response(v, visible) for v in views],
            rates_visible=visible,
            rate_low=min(rates) if visible and rates else None,
            rate_high=max(rates) if visible and rates else None,
            rate_source=source if visible else None,
            email=contacts[row.id].get("email"),
            phone=contacts[row.id].get("phone"),
            notes=row.notes,
            is_active=row.is_active,
            is_customer=row.is_customer,
            is_supplier=row.is_supplier,
            party_number=row.party_number,
            is_team_member=row.user_id is not None,
            created_at=row.created_at,
    )


def _crew_query(
    db: Session,
    membership: Membership,
    *,
    search: Optional[str],
    role: Optional[str],
    include_inactive: bool,
    sort: str,
    role_item_id: Optional[uuid.UUID] = None,
    min_rate: Optional[Decimal] = None,
    max_rate: Optional[Decimal] = None,
    kind: Optional[str] = None,
):
    query = scoped(db.query(Party), membership).filter(Party.is_crew.is_(True))
    if kind == "team":
        query = query.filter(Party.user_id.isnot(None))
    elif kind == "freelance":
        query = query.filter(Party.user_id.is_(None))
    company_id = membership.company_id
    if role_item_id is not None:
        query = query.filter(crew_roles_service.has_role(company_id, Party.id, role_item_id))
    if min_rate is not None or max_rate is not None:
        # The EFFECTIVE rate: own if set, else typical (SQL in the service).
        query = query.filter(
            crew_roles_service.in_rate_range(
                company_id, Party.id, item_id=role_item_id, low=min_rate, high=max_rate
            )
        )
    if not include_inactive:
        query = query.filter(Party.is_active.is_(True))
    if role and role.strip():
        # Case-insensitive, so "dop" finds the "DoP" somebody typed.
        query = query.filter(
            text(
                "EXISTS (SELECT 1 FROM unnest(parties.tags) AS crew_tag(value) "
                "WHERE lower(crew_tag.value) = :crew_role)"
            ).bindparams(crew_role=role.strip().lower())
        )
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        emails = (
            db.query(PartyContactPoint.party_id)
            .filter(
                PartyContactPoint.company_id == membership.company_id,
                PartyContactPoint.kind == "email",
                func.lower(PartyContactPoint.value).like(needle),
            )
        )
        query = query.filter(
            or_(
                func.lower(Party.name).like(needle),
                func.lower(Party.first_name).like(needle),
                func.lower(Party.middle_name).like(needle),
                func.lower(Party.last_name).like(needle),
                func.lower(func.array_to_string(Party.tags, "|")).like(needle),
                Party.id.in_(emails),
            )
        )
    descending = sort.startswith("-")
    key = sort.lstrip("-")
    if key == "created":
        order = [Party.created_at.desc() if descending else Party.created_at]
    elif key == "role":
        first_role = func.lower(func.coalesce(Party.tags[1], ""))
        order = [first_role.desc() if descending else first_role]
    elif key == "rate":
        lowest = crew_roles_service.lowest_rate_expr(company_id, Party.id, role_item_id)
        order = [lowest.desc().nulls_last() if descending else lowest.asc().nulls_last()]
    else:
        order = []
    # P1b-4: by last name, and by `name` for somebody without one — the same
    # key as every party list (`party_service.sort_key_columns`).
    for column in party_service.sort_key_columns()[:3]:
        order.append(column.desc() if descending and key == "name" else column)
    order.append(Party.id)
    return query.order_by(*order)


@router.get("/crew", response_model=CrewPage)
def list_crew(
    search: Optional[str] = Query(default=None, max_length=255, description="Name, role or email."),
    role: Optional[str] = Query(default=None, max_length=64, description="One tag, case-insensitive."),
    role_item_id: Optional[uuid.UUID] = Query(default=None, description="One role: a catalog labor item."),
    min_rate: Optional[Decimal] = Query(default=None, ge=0, description="Effective day rate at least. Needs budgets.view."),
    max_rate: Optional[Decimal] = Query(default=None, ge=0, description="Effective day rate at most. Needs budgets.view."),
    include_inactive: bool = Query(default=False, description="Archived people too."),
    kind: Optional[CrewKind] = Query(default=None, description="R2. `team`: linked to a login; `freelance`: not."),
    sort: CrewSort = Query(default="name"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """Everybody flagged crew. Archived people are hidden unless asked for.

    The rate range and the rate sort use the EFFECTIVE rate (own else
    typical) of the role filtered by, or of any role; both are a 404 for a
    member who may not see rates, like any missing permission."""
    uses_rates = min_rate is not None or max_rate is not None or sort.lstrip("-") == "rate"
    if uses_rates and not rates_visible(membership):
        _not_found()
    query = _crew_query(
        db,
        membership,
        search=search,
        role=role,
        include_inactive=include_inactive,
        sort=sort,
        role_item_id=role_item_id,
        min_rate=min_rate,
        max_rate=max_rate,
        kind=kind,
    )
    total = query.count()
    rows = query.limit(limit).offset(offset).all()
    return CrewPage(
        members=_members(db, membership, rows, role_item_id=role_item_id), total=total
    )


@router.get("/crew/roles", response_model=CrewRoleSuggestions)
def crew_roles(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The roles already used, and the catalog's labor items, as suggestions."""
    used, catalog = crew_service.role_suggestions(db, membership.company_id)
    by_name = crew_roles_service.labor_items_by_name(db, membership.company_id)
    items = sorted({item_id: name for item_id, name in by_name.values()}.items(), key=lambda e: e[1].casefold())
    return CrewRoleSuggestions(
        used=used,
        catalog=catalog,
        labor_items=[CrewLaborItem(item_id=item_id, name=name) for item_id, name in items],
    )


@router.get("/crew/export.csv")
def export_crew(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The list in the import's own columns:
    `first_name;middle_name;last_name;name;roles;email;phone;notes;day_rate`.

    `roles` lists the catalog roles first, then the tags. `day_rate` is the
    person's OWN rate in their first role, so a re-import gives it back; the
    column is not written at all for a member who may not see rates."""
    rows = _crew_query(
        db, membership, search=None, role=None, include_inactive=include_inactive, sort="name"
    ).all()
    members = _members(db, membership, rows)
    visible = rates_visible(membership)
    payload = export_crew_csv(
        [
            CrewValues(
                name=member.name,
                first_name=member.first_name,
                middle_name=member.middle_name,
                last_name=member.last_name,
                roles=tuple([r.name for r in member.crew_roles] + list(member.roles)),
                email=member.email,
                phone=member.phone,
                notes=member.notes,
                day_rate=(member.crew_roles[0].own_day_rate if visible and member.crew_roles else None),
            )
            for member in members
        ],
        with_rates=visible,
    )
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="crew.csv"'},
    )


@router.post("/crew", response_model=CrewCreateResponse, status_code=status.HTTP_201_CREATED)
def quick_add(
    body: CrewCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """A person with a name and, if known, roles, phone, email and notes.

    No address, no tax data, no number. Somebody who looks like a party
    already on file is created anyway and the answer says who it looked
    like — a warning, never a refusal, like every party create.

    R1: a role that is a catalog labor item becomes a role; any other stays
    a tag and is listed in `unknown_roles`. `day_rate` (needs
    `budgets.view`) is the own rate in the first matched role.
    """
    if body.day_rate is not None and not rates_visible(membership):
        _not_found()
    try:
        values = crew_service.clean_values(
            name=body.name,
            first_name=body.first_name,
            middle_name=body.middle_name,
            last_name=body.last_name,
            roles=body.roles,
            email=body.email,
            phone=body.phone,
            notes=body.notes,
            day_rate=body.day_rate,
        )
    except crew_service.CrewError as error:
        _unprocessable(str(error))
    values = crew_service.with_role_matches(db, membership.company_id, values)
    party = crew_service.create_member(
        db,
        company_id=membership.company_id,
        values=values,
        actor=audit.actor_from(request, membership=membership),
        source=crew_service.SOURCE_QUICK_ADD,
    )
    duplicates = party_service.find_duplicates(
        db,
        company_id=membership.company_id,
        email=values.email,
        exclude_id=party.id,
    )
    same_name = (
        scoped(db.query(Party), membership)
        .filter(
            func.lower(func.regexp_replace(func.trim(Party.name), r"\s+", " ", "g"))
            == " ".join(values.name.split()).lower(),
            Party.id != party.id,
        )
        .all()
    )
    seen = {entry["party_id"] for entry in duplicates}
    for row in same_name:
        if row.id not in seen:
            duplicates.append(
                {
                    "party_id": row.id,
                    "name": row.name,
                    "reason": "name",
                    "detail": f"There is already a contact called {row.name}.",
                }
            )
    db.commit()
    db.refresh(party)
    return CrewCreateResponse(
        member=_members(db, membership, [party])[0],
        duplicates=[CrewDuplicateWarning(**entry) for entry in duplicates],
        unknown_roles=list(values.unknown_roles),
        notes=list(values.notes_for_preview()),
    )


async def _read_upload(file: UploadFile) -> bytes:
    payload = await file.read()
    if len(payload) > MAX_IMPORT_BYTES:
        _unprocessable(
            f"That file is {len(payload) // 1024} KB and the limit is "
            f"{MAX_IMPORT_BYTES // 1024} KB. Check that it is the crew list."
        )
    if not payload.strip():
        _unprocessable("That file is empty. Choose the CSV file with the crew list.")
    return payload


def _row(row, *, action: Optional[str] = None, party_id=None, visible: bool = False) -> CrewImportRowResponse:
    values = row.values
    return CrewImportRowResponse(
        line_number=row.line_number,
        action=action or row.action,
        name=values.name if values else row.name,
        first_name=values.first_name if values else None,
        middle_name=values.middle_name if values else None,
        last_name=values.last_name if values else None,
        roles=list(values.roles) if values else [],
        email=values.email if values else None,
        phone=values.phone if values else None,
        notes=values.notes if values else None,
        matched_roles=[name for _, name in values.matched_roles] if values else [],
        unknown_roles=list(values.unknown_roles) if values else [],
        day_rate=values.day_rate if values and visible else None,
        role_notes=list(values.notes_for_preview()) if values else [],
        message=row.message,
        duplicate_of_party_id=row.duplicate_of_party_id,
        duplicate_of_name=row.duplicate_of_name,
        duplicate_of_line=row.duplicate_of_line,
        duplicate_reason=row.duplicate_reason,
        party_id=party_id,
    )


@router.post("/crew/import/preview", response_model=CrewImportPreviewResponse)
async def preview_crew_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """What the file would create, which lines look like people already on
    file, and which lines cannot be read. Writes nothing."""
    payload = await _read_upload(file)
    visible = rates_visible(membership)
    try:
        plan = crew_service.preview_import(db, membership.company_id, payload, read_rates=visible)
    except ImportFormatError as error:
        _unprocessable(str(error))
    return CrewImportPreviewResponse(
        rows=[_row(row, visible=visible) for row in plan.rows],
        created=len(plan.creates),
        duplicates=len(plan.duplicates),
        errors=len(plan.errors),
        digest=plan.digest,
        delimiter=plan.delimiter,
        ignored_columns=list(plan.ignored_columns),
    )


@router.post("/crew/import", response_model=CrewImportApplyResponse)
async def apply_crew_import(
    request: Request,
    file: UploadFile = File(...),
    digest: Optional[str] = Form(
        default=None,
        description="The preview's digest. When the file no longer plans the same thing, nothing is written.",
    ),
    include_lines: Optional[str] = Form(
        default=None,
        description="Comma-separated line numbers of the duplicate lines to create anyway.",
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Create the preview's new people, plus the duplicates that were ticked.

    Never updates, archives or flags an existing party. Error lines are
    listed and skipped; they never stop the rest of the file.
    """
    payload = await _read_upload(file)
    try:
        ticked = [int(part) for part in (include_lines or "").split(",") if part.strip()]
    except ValueError:
        _unprocessable("include_lines must be line numbers separated by commas, such as 3,7.")
    try:
        plan, created = crew_service.apply_import(
            db,
            company_id=membership.company_id,
            payload=payload,
            actor=audit.actor_from(request, membership=membership),
            expected_digest=digest,
            include_lines=ticked,
            read_rates=rates_visible(membership),
        )
    except ImportFormatError as error:
        db.rollback()
        _unprocessable(str(error))
    except crew_service.DigestMismatch as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    db.commit()
    rows = []
    visible = rates_visible(membership)
    for row in plan.rows:
        if row.line_number in created:
            rows.append(_row(row, action="created", party_id=created[row.line_number].id, visible=visible))
        elif row.action in (ACTION_CREATE, ACTION_DUPLICATE):
            rows.append(_row(row, action="skipped", visible=visible))
        else:
            rows.append(_row(row, visible=visible))
    return CrewImportApplyResponse(
        rows=rows,
        created=len(created),
        skipped=sum(1 for row in rows if row.action == "skipped"),
        errors=len(plan.errors),
        digest=plan.digest,
    )


# ─── Roles and rates on one person (R1) ─────────────────────────────────────


def _party_or_404(db: Session, membership: Membership, party_id: uuid.UUID) -> Party:
    party = scoped(db.query(Party), membership).filter(Party.id == party_id).first()
    if party is None:
        _not_found()
    return party


def _roles_response(db: Session, membership: Membership, party: Party) -> PartyCrewRolesResponse:
    visible = rates_visible(membership)
    views = crew_roles_service.roles_of(db, membership.company_id, [party.id])[party.id]
    return PartyCrewRolesResponse(
        party_id=party.id,
        is_crew=party.is_crew,
        rates_visible=visible,
        roles=[role_response(view, visible) for view in views],
        tag_suggestions=[
            CrewTagSuggestion(tag=tag, item_id=item_id, item_name=name)
            for tag, item_id, name in crew_roles_service.tag_suggestions(db, party)
        ],
    )


def _role_error(error: crew_roles_service.CrewRoleError):
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(error, crew_roles_service.RoleExists)
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    raise HTTPException(status_code=code, detail=str(error)) from None


@router.get("/parties/{party_id}/crew-roles", response_model=PartyCrewRolesResponse)
def read_crew_roles(
    party_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The person's roles with own, typical and effective rates (rates only
    for `budgets.view`), and the tags that could become a role."""
    return _roles_response(db, membership, _party_or_404(db, membership, party_id))


@router.post(
    "/parties/{party_id}/crew-roles",
    response_model=PartyCrewRolesResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_crew_role(
    party_id: uuid.UUID,
    body: CrewRoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """A role from the catalog's labor items, with an optional own rate."""
    party = _party_or_404(db, membership, party_id)
    if body.own_day_rate is not None and not rates_visible(membership):
        _not_found()
    item = crew_roles_service.labor_item(db, membership.company_id, body.item_id)
    if item is None:
        _not_found()
    try:
        crew_roles_service.add_role(
            db,
            party=party,
            item=item,
            own_day_rate=crew_roles_service.clean_rate(body.own_day_rate),
            notes=body.notes,
            actor=audit.actor_from(request, membership=membership),
        )
    except crew_roles_service.CrewRoleError as error:
        db.rollback()
        _role_error(error)
    db.commit()
    return _roles_response(db, membership, party)


@router.patch("/parties/{party_id}/crew-roles/{role_id}", response_model=PartyCrewRolesResponse)
def update_crew_role(
    party_id: uuid.UUID,
    role_id: uuid.UUID,
    body: CrewRoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Own rate (null clears it: the typical rate applies again) and notes."""
    party = _party_or_404(db, membership, party_id)
    found = crew_roles_service.active_role(db, membership.company_id, party.id, role_id)
    if found is None:
        _not_found()
    changes = body.model_dump(exclude_unset=True)
    if "own_day_rate" in changes and not rates_visible(membership):
        _not_found()
    try:
        if "own_day_rate" in changes:
            changes["own_day_rate"] = crew_roles_service.clean_rate(changes["own_day_rate"])
        crew_roles_service.update_role(
            db,
            row=found[0],
            name=found[1],
            changes=changes,
            actor=audit.actor_from(request, membership=membership),
        )
    except crew_roles_service.CrewRoleError as error:
        db.rollback()
        _role_error(error)
    db.commit()
    return _roles_response(db, membership, party)


@router.post("/parties/{party_id}/crew-roles/{role_id}/remove", response_model=PartyCrewRolesResponse)
def remove_crew_role(
    party_id: uuid.UUID,
    role_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Retire the role. The row is kept; there is no DELETE."""
    party = _party_or_404(db, membership, party_id)
    found = crew_roles_service.active_role(db, membership.company_id, party.id, role_id)
    if found is None:
        _not_found()
    crew_roles_service.remove_role(
        db, row=found[0], name=found[1], actor=audit.actor_from(request, membership=membership)
    )
    db.commit()
    return _roles_response(db, membership, party)


@router.post("/parties/{party_id}/crew-roles/from-tag", response_model=PartyCrewRolesResponse)
def crew_role_from_tag(
    party_id: uuid.UUID,
    body: CrewRoleFromTag,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """"Use 'Gaffer' as a role": the tag becomes that labor item's role and
    leaves the tags. Only ever on request — tags are never converted by
    themselves."""
    party = _party_or_404(db, membership, party_id)
    try:
        crew_roles_service.role_from_tag(
            db, party=party, tag=body.tag, actor=audit.actor_from(request, membership=membership)
        )
    except crew_roles_service.CrewRoleError as error:
        db.rollback()
        _role_error(error)
    db.commit()
    db.refresh(party)
    return _roles_response(db, membership, party)
