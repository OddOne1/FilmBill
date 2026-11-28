"""A project's locations (L1b): its list, hotel guests, booking
confirmations and the hospitals near its shooting locations.

**Permissions.** Reading needs `projects.view` and `locations.view` (staff
included). Changing the list, guests or confirmations needs `projects.edit`
(and `locations.view`). Hotel guests and the confirmations of a location
used as a hotel are crew information: they need `parties.view` as well
(`location_service.can_see_hotels`) — without it they are absent and their
routes answer 404. Adding a suggested hospital creates a location:
`locations.edit` too. Every missing permission is a 404 (rule 6).

**No DELETE route.** Off the list, a guest, a confirmation: `.../remove`.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..billing.planning import PlaceData, map_links
from ..database import get_db
from ..middleware.company import NOT_FOUND_DETAIL, Membership, require
from ..models.location import HotelStay, Location, LocationAttachment
from ..models.party import Party
from ..models.project import Project, ProjectBlock, ProjectDay
from ..models.user import User
from ..schemas.location import (
    AttachmentDownload,
    AttachmentResponse,
    AttachmentText,
    HospitalAdd,
    HospitalGroupResponse,
    HospitalOfferResponse,
    HospitalSuggestions,
    HotelStayCreate,
    HotelStayResponse,
    HotelStayUpdate,
    ProjectLocationAdd,
    ProjectLocationEntry,
    ProjectLocations,
)
from ..services import audit, location_service as service, osm_hospitals, s3_service
from ..services.crew_assignment_service import strip_name
from ..services.permissions import scoped
from .locations import responses as location_responses

router = APIRouter(tags=["projects"])


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


def can_view(membership: Membership = Depends(require("projects.view"))) -> Membership:
    if not membership.can("locations.view"):
        _not_found()
    return membership


def can_change(membership: Membership = Depends(require("projects.edit"))) -> Membership:
    if not membership.can("locations.view"):
        _not_found()
    return membership


def _project(db: Session, membership: Membership, project_id: uuid.UUID) -> Project:
    row = scoped(db.query(Project), membership).filter(Project.id == project_id).first()
    if row is None:
        _not_found()
    return row


def _location(db: Session, membership: Membership, location_id: uuid.UUID) -> Location:
    row = service.visible(db, membership, location_id)
    if row is None:
        _not_found()
    return row


def _crew_location(db: Session, membership: Membership, location_id: uuid.UUID) -> Location:
    """A location whose guests or confirmations the caller may touch: any
    visible one, but one used as a hotel only for who may see hotels."""
    row = _location(db, membership, location_id)
    if service.is_hotel(row) and not service.can_see_hotels(membership):
        _not_found()
    return row


def _refuse(error: Exception):
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from None


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Building the answer ────────────────────────────────────────────────────


def _stay(stay: HotelStay, names: dict) -> HotelStayResponse:
    return HotelStayResponse(
        id=stay.id,
        location_id=stay.location_id,
        party_id=stay.party_id,
        party_name=names.get(stay.party_id, ""),
        check_in=stay.check_in,
        check_out=stay.check_out,
        nights=(stay.check_out - stay.check_in).days,
        notes=stay.notes,
    )


def _attachment(row: LocationAttachment, users: dict) -> AttachmentResponse:
    return AttachmentResponse(
        id=row.id,
        location_id=row.location_id,
        kind=row.kind,
        title=row.title,
        file_name=row.file_name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        body_text=row.body_text,
        created_by_name=users.get(row.created_by) if row.created_by else None,
        created_at=row.created_at,
    )


def project_locations(db: Session, membership: Membership, project: Project) -> ProjectLocations:
    ids = service.project_location_ids(db, project.id)
    rows = (
        service.visible_query(db, membership).filter(Location.id.in_(ids)).order_by(Location.name_key, Location.id).all()
        if ids
        else []
    )
    hotels = service.can_see_hotels(membership)
    visible_ids = [r.id for r in rows]
    strips: dict = {}
    for block in db.query(ProjectBlock).filter(
        ProjectBlock.project_id == project.id, ProjectBlock.is_active.is_(True), ProjectBlock.location_id.in_(visible_ids)
    ).order_by(ProjectBlock.start_date, ProjectBlock.id):
        strips.setdefault(block.location_id, []).append(strip_name(block))
    days: dict = {}
    for day in db.query(ProjectDay).filter(
        ProjectDay.project_id == project.id, ProjectDay.is_active.is_(True), ProjectDay.location_id.in_(visible_ids)
    ).order_by(ProjectDay.date, ProjectDay.id):
        days.setdefault(day.location_id, []).append(day.date)
    stays: dict = {}
    names: dict = {}
    if hotels:
        found = (
            db.query(HotelStay)
            .filter(HotelStay.project_id == project.id, HotelStay.is_active.is_(True), HotelStay.location_id.in_(visible_ids))
            .order_by(HotelStay.check_in, HotelStay.id)
            .all()
        )
        names = dict(db.query(Party.id, Party.name).filter(Party.id.in_({s.party_id for s in found})).all()) if found else {}
        for stay in found:
            stays.setdefault(stay.location_id, []).append(stay)
    attachments: dict = {}
    found_attachments = (
        db.query(LocationAttachment)
        .filter(
            LocationAttachment.project_id == project.id,
            LocationAttachment.is_active.is_(True),
            LocationAttachment.location_id.in_(visible_ids),
        )
        .order_by(LocationAttachment.created_at, LocationAttachment.id)
        .all()
        if visible_ids
        else []
    )
    users_ids = {a.created_by for a in found_attachments if a.created_by}
    users = {u.id: u.name for u in db.query(User).filter(User.id.in_(users_ids))} if users_ids else {}
    by_id = {r.id: r for r in rows}
    for row in found_attachments:
        if service.is_hotel(by_id[row.location_id]) and not hotels:
            continue
        attachments.setdefault(row.location_id, []).append(row)
    built = location_responses(db, membership, rows)
    return ProjectLocations(
        project_id=project.id,
        hotels_visible=hotels,
        locations=[
            ProjectLocationEntry(
                location=response,
                strip_names=strips.get(response.id, []),
                day_dates=days.get(response.id, []),
                stays=[_stay(s, names) for s in stays.get(response.id, [])],
                attachments=[_attachment(a, users) for a in attachments.get(response.id, [])],
            )
            for response in built
        ],
    )


# ─── The list ───────────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/locations", response_model=ProjectLocations)
def list_project_locations(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Every location the project uses: added by hand, or by a strip or a
    day. Hotel guests and a hotel's confirmations only for who may see
    crew."""
    return project_locations(db, membership, _project(db, membership, project_id))


@router.post("/projects/{project_id}/locations", response_model=ProjectLocations)
def add_project_location(
    project_id: uuid.UUID,
    body: ProjectLocationAdd,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Add a location the company already has (from any project)."""
    project = _project(db, membership, project_id)
    row = _location(db, membership, body.location_id)
    if not row.is_active:
        _refuse(service.LocationError(f"“{row.name}” is archived. Restore it first, then add it to the project."))
    service.add_to_project(db, project, row, actor=_actor(request, membership))
    db.commit()
    return project_locations(db, membership, project)


@router.post("/projects/{project_id}/locations/{location_id}/remove", response_model=ProjectLocations)
def remove_project_location(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """Off the project's list (the location itself stays for other
    projects). Refused while a strip, a day or a guest still uses it."""
    project = _project(db, membership, project_id)
    row = _location(db, membership, location_id)
    try:
        service.remove_from_project(db, project, row, actor=_actor(request, membership))
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)


# ─── Hotel guests ───────────────────────────────────────────────────────────


def _hotels_only(membership: Membership) -> None:
    if not service.can_see_hotels(membership):
        _not_found()


def _stay_row(db: Session, membership: Membership, project: Project, stay_id: uuid.UUID) -> HotelStay:
    row = (
        scoped(db.query(HotelStay), membership)
        .filter(HotelStay.id == stay_id, HotelStay.project_id == project.id)
        .first()
    )
    if row is None:
        _not_found()
    return row


@router.post("/projects/{project_id}/locations/{location_id}/stays", response_model=ProjectLocations)
def add_stay(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    body: HotelStayCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """A crew member of this production at this hotel, with their own
    dates (default: first assigned day to the day after the last)."""
    _hotels_only(membership)
    project = _project(db, membership, project_id)
    actor = _actor(request, membership)
    try:
        hotel = service.hotel(db, membership, location_id)
    except LookupError:
        _not_found()
    except service.LocationError as error:
        _refuse(error)
    party = scoped(db.query(Party), membership).filter(Party.id == body.party_id).first()
    if party is None:
        _not_found()
    try:
        service.add_to_project(db, project, hotel, actor=actor)
        service.add_stay(
            db, project, hotel, party, check_in=body.check_in, check_out=body.check_out, notes=body.notes, actor=actor
        )
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)


@router.patch("/projects/{project_id}/stays/{stay_id}", response_model=ProjectLocations)
def update_stay(
    project_id: uuid.UUID,
    stay_id: uuid.UUID,
    body: HotelStayUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    _hotels_only(membership)
    project = _project(db, membership, project_id)
    stay = _stay_row(db, membership, project, stay_id)
    try:
        service.update_stay(db, stay, body.model_dump(exclude_unset=True), actor=_actor(request, membership))
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)


@router.post("/projects/{project_id}/stays/{stay_id}/remove", response_model=ProjectLocations)
def remove_stay(
    project_id: uuid.UUID,
    stay_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    _hotels_only(membership)
    project = _project(db, membership, project_id)
    stay = _stay_row(db, membership, project, stay_id)
    service.remove_stay(db, stay, actor=_actor(request, membership))
    db.commit()
    return project_locations(db, membership, project)


# ─── Booking confirmations ──────────────────────────────────────────────────


def _attachment_row(db: Session, membership: Membership, project: Project, attachment_id: uuid.UUID) -> LocationAttachment:
    row = (
        scoped(db.query(LocationAttachment), membership)
        .filter(
            LocationAttachment.id == attachment_id,
            LocationAttachment.project_id == project.id,
            LocationAttachment.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        _not_found()
    _crew_location(db, membership, row.location_id)
    return row


@router.post("/projects/{project_id}/locations/{location_id}/attachments", response_model=ProjectLocations)
async def add_file_attachment(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """A confirmation as a file: PDF, an email (.eml / .msg), a picture or
    a .txt, at most 15 MB, kept in the company's storage."""
    project = _project(db, membership, project_id)
    location = _crew_location(db, membership, location_id)
    payload = await file.read(service.MAX_ATTACHMENT_BYTES + 1)
    actor = _actor(request, membership)
    try:
        service.add_to_project(db, project, location, actor=actor)
        service.add_file_attachment(
            db, project, location, title=title, file_name=file.filename or "confirmation",
            payload=payload, user_id=membership.user.id, actor=actor,
        )
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)


@router.post("/projects/{project_id}/locations/{location_id}/attachments/text", response_model=ProjectLocations)
def add_text_attachment(
    project_id: uuid.UUID,
    location_id: uuid.UUID,
    body: AttachmentText,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """A confirmation as pasted text — an email or a message."""
    project = _project(db, membership, project_id)
    location = _crew_location(db, membership, location_id)
    actor = _actor(request, membership)
    try:
        service.add_to_project(db, project, location, actor=actor)
        service.add_text_attachment(db, project, location, title=body.title, text=body.text, user_id=membership.user.id, actor=actor)
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)


@router.get("/projects/{project_id}/attachments/{attachment_id}/download", response_model=AttachmentDownload)
def download_attachment(
    project_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """A short-lived link to the stored file (the public presign client,
    rule 16). A text confirmation has no file: its text is in the list."""
    project = _project(db, membership, project_id)
    row = _attachment_row(db, membership, project, attachment_id)
    if row.s3_key is None:
        _not_found()
    return AttachmentDownload(
        url=s3_service.generate_presigned_get_url(row.s3_key, expires_in=600, download_filename=row.file_name)
    )


@router.post("/projects/{project_id}/attachments/{attachment_id}/remove", response_model=ProjectLocations)
def remove_attachment(
    project_id: uuid.UUID,
    attachment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    project = _project(db, membership, project_id)
    row = _attachment_row(db, membership, project, attachment_id)
    service.remove_attachment(db, row, actor=_actor(request, membership))
    db.commit()
    return project_locations(db, membership, project)


# ─── Hospitals near the shooting locations ──────────────────────────────────


@router.get("/projects/{project_id}/hospital-suggestions", response_model=HospitalSuggestions)
def hospital_suggestions(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_view),
):
    """Per group of the project's shooting locations (within 30 km of each
    other), the nearest hospitals OpenStreetMap knows — confirmed emergency
    departments first. Nothing is added; each one is added by a click."""
    project = _project(db, membership, project_id)
    in_project = set(service.project_location_ids(db, project.id))
    try:
        groups, missing = service.hospital_suggestions(db, membership, project)
    except osm_hospitals.HospitalSearchError as error:
        return HospitalSuggestions(groups=[], error=str(error))
    return HospitalSuggestions(
        groups=[
            HospitalGroupResponse(
                shooting_locations=group.shooting_locations,
                offers=[
                    HospitalOfferResponse(
                        osm_ref=o.hospital.osm_ref,
                        name=o.hospital.name,
                        latitude=o.hospital.latitude,
                        longitude=o.hospital.longitude,
                        emergency=o.hospital.emergency,
                        distance_km=o.distance_km,
                        nearest_to=o.nearest_to,
                        phone=o.hospital.phone,
                        street=o.hospital.street,
                        zip=o.hospital.zip,
                        city=o.hospital.city,
                        country=o.hospital.country,
                        website=o.hospital.website,
                        map_link=map_links.map_link(PlaceData(latitude=o.hospital.latitude, longitude=o.hospital.longitude)),
                        existing_location_id=o.existing_location_id,
                        in_project=o.existing_location_id in in_project if o.existing_location_id else False,
                    )
                    for o in group.offers
                ],
            )
            for group in groups
        ],
        missing_coordinates=missing,
    )


@router.post("/projects/{project_id}/hospital-suggestions/add", response_model=ProjectLocations)
def add_suggested_hospital(
    project_id: uuid.UUID,
    body: HospitalAdd,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(can_change),
):
    """One suggestion, as shown, becomes a hospital location of the company
    (or the one already made from the same OpenStreetMap entry) on this
    project's list."""
    if not membership.can("locations.edit"):
        _not_found()
    project = _project(db, membership, project_id)
    try:
        service.add_hospital_from_osm(db, membership, project, body.model_dump(), actor=_actor(request, membership))
    except service.LocationError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return project_locations(db, membership, project)
