"""Locations (L1, L1b): list, create, edit, archive, restore, mark a
hospital verified, where a location is used, reading a pasted pair or a
pasted Google / Apple Maps link. A project's own list, its hotel guests and
booking confirmations are in `routers/project_locations.py`.

**Permissions.** Reading needs `locations.view` (everybody who can see
projects, staff included); a hotel also needs `parties.view` — without it a
hotel is absent from the list and its id answers 404, exactly like another
company's location. Writing needs `locations.edit` (owner, admin,
producer). Every missing permission is a 404 (rule 6).

**Links are the server's.** `map_link` is built here from the coordinates,
else the address (`billing/planning/map_links.py`); the web app only
renders it. Nothing is fetched from anywhere.

**No DELETE route.** A location is archived (`.../archive`) and comes back
with `.../restore`; whatever used it keeps showing it, marked archived.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..billing.planning import map_links
from ..database import get_db
from ..middleware.company import NOT_FOUND_DETAIL, Membership, require
from ..models.location import Location
from ..models.party import Party
from ..models.user import User
from ..schemas.location import (
    CoordinatesParse,
    CoordinatesParsed,
    LocationChange,
    LocationCreate,
    MapLinkResolve,
    MapLinkResolved,
    PlaceUse,
    LocationList,
    LocationRef,
    LocationResponse,
    LocationUpdate,
    LocationUsage,
    LocationUse,
    LocationVerify,
)
from ..services import audit, location_service as service, map_link_resolver

router = APIRouter(tags=["locations"])


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def _refuse(error: service.LocationError):
    code = status.HTTP_409_CONFLICT if isinstance(error, service.LocationNameTaken) else status.HTTP_422_UNPROCESSABLE_ENTITY
    raise HTTPException(status_code=code, detail=str(error)) from None


def _location(db: Session, membership: Membership, location_id: uuid.UUID) -> Location:
    row = service.visible(db, membership, location_id)
    if row is None:
        _not_found()
    return row


def ref(row: Optional[Location], membership: Optional[Membership] = None) -> Optional[LocationRef]:
    """A location as a strip, a day or a crew row carries it, with the
    server's map link and map view. Without `membership` every use is
    shown (callers that already decided the caller may see this row)."""
    if row is None:
        return None
    return LocationRef(
        id=row.id,
        name=row.name,
        uses=service.visible_uses(membership, row) if membership is not None else list(row.uses),
        embed_link=map_links.embed_link(row.latitude, row.longitude),
        city=row.city,
        latitude=row.latitude,
        longitude=row.longitude,
        is_active=row.is_active,
        map_link=map_links.map_link(row),
    )


def responses(db: Session, membership: Membership, rows: list[Location]) -> list[LocationResponse]:
    if not rows:
        return []
    see_contacts = service.can_see_contacts(membership)
    party_ids = {r.contact_party_id for r in rows if r.contact_party_id}
    party_names = (
        dict(db.query(Party.id, Party.name).filter(Party.company_id == membership.company_id, Party.id.in_(party_ids)).all())
        if party_ids and see_contacts
        else {}
    )
    user_ids = {r.verified_by for r in rows if r.verified_by}
    user_names = {u.id: u.name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    counts = service.in_use_counts(db, membership.company_id, [r.id for r in rows])
    out = []
    for row in rows:
        out.append(
            LocationResponse(
                id=row.id,
                name=row.name,
                uses=service.visible_uses(membership, row),
                source_link=row.source_link,
                from_openstreetmap=row.osm_ref is not None,
                street=row.street,
                street2=row.street2,
                zip=row.zip,
                city=row.city,
                country=row.country,
                latitude=row.latitude,
                longitude=row.longitude,
                coordinates_display=map_links.coordinates_text(row.latitude, row.longitude),
                phone=row.phone,
                email=row.email,
                website=row.website,
                emergency_phone=row.emergency_phone,
                access_notes=row.access_notes,
                notes=row.notes,
                contact_party_id=row.contact_party_id if see_contacts else None,
                contact_party_name=party_names.get(row.contact_party_id) if row.contact_party_id else None,
                contact_text=row.contact_text,
                verified=row.verified_on is not None,
                verified_on=row.verified_on,
                verified_by_name=user_names.get(row.verified_by) if row.verified_by else None,
                verification_note=row.verification_note,
                is_active=row.is_active,
                map_link=map_links.map_link(row),
                embed_link=map_links.embed_link(row.latitude, row.longitude),
                in_use=counts.get(row.id, 0),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return out


def _change(db: Session, membership: Membership, change: service.Change) -> LocationChange:
    db.refresh(change.location)
    return LocationChange(
        location=responses(db, membership, [change.location])[0],
        verification_cleared=change.verification_cleared,
        message=change.message,
    )


# ─── Reading ────────────────────────────────────────────────────────────────


@router.get("/locations", response_model=LocationList)
def list_locations(
    q: Optional[str] = Query(default=None, max_length=200),
    use: Optional[PlaceUse] = Query(default=None),
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.view")),
):
    """Search finds the name, the city, the street and the zip."""
    query = service.visible_query(db, membership)
    if not include_archived:
        query = query.filter(Location.is_active.is_(True))
    if use is not None:
        query = query.filter(Location.uses.contains([use]))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Location.name.ilike(like),
                Location.city.ilike(like),
                Location.street.ilike(like),
                Location.zip.ilike(like),
            )
        )
    rows = query.order_by(Location.is_active.desc(), Location.name_key, Location.id).all()
    return LocationList(locations=responses(db, membership, rows), hotels_visible=service.can_see_hotels(membership))


@router.post("/locations/coordinates", response_model=CoordinatesParsed)
def parse_coordinates(
    body: CoordinatesParse,
    membership: Membership = Depends(require("locations.view")),
):
    """Read a pasted pair the way a save would, without saving anything:
    what the Coordinates field shows back while somebody types."""
    parsed = map_links.parse_coordinates(body.text)
    return CoordinatesParsed(
        ok=parsed.ok,
        latitude=parsed.latitude,
        longitude=parsed.longitude,
        display=map_links.coordinates_text(parsed.latitude, parsed.longitude),
        refusal=parsed.refusal,
    )


@router.post("/locations/resolve-link", response_model=MapLinkResolved)
def resolve_link(
    body: MapLinkResolve,
    membership: Membership = Depends(require("locations.view")),
):
    """L1b. A pasted Google or Apple Maps link: the position, a name and an
    address text where the link carries them, and the map view. A full link
    is read without any request; a short share link is opened once
    (`services/map_link_resolver.py`)."""
    try:
        info = map_link_resolver.resolve(body.url)
    except map_link_resolver.ResolveError as error:
        return MapLinkResolved(ok=False, refusal=str(error))
    if info.refusal:
        return MapLinkResolved(ok=False, provider=info.provider, refusal=info.refusal)
    return MapLinkResolved(
        ok=True,
        latitude=info.latitude,
        longitude=info.longitude,
        name=info.name,
        address=info.address,
        provider=info.provider,
        display=map_links.coordinates_text(info.latitude, info.longitude),
        embed_link=map_links.embed_link(info.latitude, info.longitude),
    )


@router.get("/locations/{location_id}", response_model=LocationResponse)
def read_location(
    location_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.view")),
):
    return responses(db, membership, [_location(db, membership, location_id)])[0]


@router.get("/locations/{location_id}/usage", response_model=LocationUsage)
def location_usage(
    location_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.view")),
):
    """Every strip, day, strip hotel list and crew row that uses it, with
    the project — what archiving it warns about."""
    row = _location(db, membership, location_id)
    uses = service.usage(db, membership, row)
    return LocationUsage(
        location_id=row.id,
        count=len(uses),
        uses=[LocationUse(**use.__dict__) for use in uses],
    )


# ─── Writing ────────────────────────────────────────────────────────────────


def _run(db: Session, call):
    try:
        change = call()
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    except LookupError:
        db.rollback()
        _not_found()
    db.commit()
    return change


@router.post("/locations", response_model=LocationChange, status_code=status.HTTP_201_CREATED)
def create_location(
    body: LocationCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.edit")),
):
    actor = audit.actor_from(request, membership=membership)
    project = None
    if body.project_id is not None:
        project = _project_for_edit(db, membership, body.project_id)
    changes = body.model_dump(exclude_unset=True, exclude={"project_id"}) | {"name": body.name}

    def make():
        change = service.create(db, membership, changes, actor=actor)
        if project is not None:
            service.add_to_project(db, project, change.location, actor=actor)
        return change

    change = _run(db, make)
    return _change(db, membership, change)


def _project_for_edit(db: Session, membership: Membership, project_id: uuid.UUID):
    """L1b. Creating a location straight into a project changes the
    project: `projects.edit`, and the project must be the caller's."""
    from ..models.project import Project
    from ..services.permissions import scoped

    if not membership.can("projects.edit"):
        _not_found()
    project = scoped(db.query(Project), membership).filter(Project.id == project_id).first()
    if project is None:
        _not_found()
    return project


@router.patch("/locations/{location_id}", response_model=LocationChange)
def update_location(
    location_id: uuid.UUID,
    body: LocationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.edit")),
):
    """A change to the address, coordinates, phone or emergency phone of a
    verified hospital clears its verification, and the answer says so."""
    row = _location(db, membership, location_id)
    actor = audit.actor_from(request, membership=membership)
    change = _run(db, lambda: service.update(db, membership, row, body.model_dump(exclude_unset=True), actor=actor))
    return _change(db, membership, change)


@router.post("/locations/{location_id}/verify", response_model=LocationChange)
def verify_location(
    location_id: uuid.UUID,
    body: LocationVerify,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.edit")),
):
    """Hospitals only: the caller, today, and an optional note."""
    row = _location(db, membership, location_id)
    actor = audit.actor_from(request, membership=membership)
    change = _run(db, lambda: service.verify(db, row, note=body.note, user_id=membership.user.id, actor=actor))
    return _change(db, membership, change)


@router.post("/locations/{location_id}/archive", response_model=LocationChange)
def archive_location(
    location_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.edit")),
):
    row = _location(db, membership, location_id)
    actor = audit.actor_from(request, membership=membership)
    change = _run(db, lambda: service.archive(db, row, actor=actor))
    return _change(db, membership, change)


@router.post("/locations/{location_id}/restore", response_model=LocationChange)
def restore_location(
    location_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("locations.edit")),
):
    row = _location(db, membership, location_id)
    actor = audit.actor_from(request, membership=membership)
    change = _run(db, lambda: service.restore(db, row, actor=actor))
    return _change(db, membership, change)
