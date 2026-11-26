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
"""

import uuid
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
from ..models.party import Party, PartyContactPoint
from ..schemas.crew import (
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
from ..services import audit, crew_service, party_service
from ..services.permissions import scoped

router = APIRouter(tags=["parties"])

#: A crew list is a few hundred people; a file this size is the wrong file.
MAX_IMPORT_BYTES = 1024 * 1024


def _unprocessable(message: str):
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def _members(db: Session, membership: Membership, rows: list[Party]) -> list[CrewMemberResponse]:
    contacts = crew_service.primary_contacts(db, membership.company_id, [row.id for row in rows])
    return [
        CrewMemberResponse(
            id=row.id,
            name=row.name,
            first_name=row.first_name,
            middle_name=row.middle_name,
            last_name=row.last_name,
            roles=list(row.tags or []),
            email=contacts[row.id].get("email"),
            phone=contacts[row.id].get("phone"),
            notes=row.notes,
            is_active=row.is_active,
            is_customer=row.is_customer,
            is_supplier=row.is_supplier,
            party_number=row.party_number,
            created_at=row.created_at,
        )
        for row in rows
    ]


def _crew_query(
    db: Session,
    membership: Membership,
    *,
    search: Optional[str],
    role: Optional[str],
    include_inactive: bool,
    sort: str,
):
    query = scoped(db.query(Party), membership).filter(Party.is_crew.is_(True))
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
    role: Optional[str] = Query(default=None, max_length=64, description="One role, case-insensitive."),
    include_inactive: bool = Query(default=False, description="Archived people too."),
    sort: CrewSort = Query(default="name"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """Everybody flagged crew. Archived people are hidden unless asked for."""
    query = _crew_query(
        db,
        membership,
        search=search,
        role=role,
        include_inactive=include_inactive,
        sort=sort,
    )
    total = query.count()
    rows = query.limit(limit).offset(offset).all()
    return CrewPage(members=_members(db, membership, rows), total=total)


@router.get("/crew/roles", response_model=CrewRoleSuggestions)
def crew_roles(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The roles already used, and the catalog's labor items, as suggestions."""
    used, catalog = crew_service.role_suggestions(db, membership.company_id)
    return CrewRoleSuggestions(used=used, catalog=catalog)


@router.get("/crew/export.csv")
def export_crew(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The list in the import's own columns:
    `first_name;middle_name;last_name;name;roles;email;phone;notes`."""
    rows = _crew_query(
        db, membership, search=None, role=None, include_inactive=include_inactive, sort="name"
    ).all()
    members = _members(db, membership, rows)
    payload = export_crew_csv(
        [
            CrewValues(
                name=member.name,
                first_name=member.first_name,
                middle_name=member.middle_name,
                last_name=member.last_name,
                roles=tuple(member.roles),
                email=member.email,
                phone=member.phone,
                notes=member.notes,
            )
            for member in members
        ]
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
    """
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
        )
    except crew_service.CrewError as error:
        _unprocessable(str(error))
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
                    "detail": f"There is already a party called {row.name}.",
                }
            )
    db.commit()
    db.refresh(party)
    return CrewCreateResponse(
        member=_members(db, membership, [party])[0],
        duplicates=[CrewDuplicateWarning(**entry) for entry in duplicates],
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


def _row(row, *, action: Optional[str] = None, party_id=None) -> CrewImportRowResponse:
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
    try:
        plan = crew_service.preview_import(db, membership.company_id, payload)
    except ImportFormatError as error:
        _unprocessable(str(error))
    return CrewImportPreviewResponse(
        rows=[_row(row) for row in plan.rows],
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
        )
    except ImportFormatError as error:
        db.rollback()
        _unprocessable(str(error))
    except crew_service.DigestMismatch as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    db.commit()
    rows = []
    for row in plan.rows:
        if row.line_number in created:
            rows.append(_row(row, action="created", party_id=created[row.line_number].id))
        elif row.action in (ACTION_CREATE, ACTION_DUPLICATE):
            rows.append(_row(row, action="skipped"))
        else:
            rows.append(_row(row))
    return CrewImportApplyResponse(
        rows=rows,
        created=len(created),
        skipped=sum(1 for row in rows if row.action == "skipped"),
        errors=len(plan.errors),
        digest=plan.digest,
    )
