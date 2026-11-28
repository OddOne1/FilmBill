"""Locations (L1, L1b): the rules, the writes and their audit events.

**L1b.** A location has a set of USES (shoot, hotel, hospital, restaurant,
other). It belongs to projects through `project_locations` (what a
project's Locations tab lists; a location used on a strip or day is added
there by that use). Hotels hold STAYS (a crew member of the production,
check-in to check-out) and every location in a project can hold booking
CONFIRMATIONS (a file or pasted text). A location whose only use is hotel is
crew information exactly as an L1 hotel was; one that is also a shooting
location is visible as one, without its hotel use, stays or confirmations.

**Hotels are crew information.** `can_see_hotels` is `locations.view` AND
`HOTEL_PERMISSION` (`parties.view`, the permission that shows crew today,
D111). `visible_query` is the ONE way a location is looked up for a caller:
a hotel the caller may not see is not in it, so it is absent from lists and
a lookup by id is a 404 — the same answer as a location of another company.

**Verification.** `VERIFICATION_FIELDS` are the fields whose change clears a
hospital's verification: the address, the coordinates, the phone and the
emergency phone. Notes, access notes, the website and the contact do not.
The write that clears it records `location.updated` with the cleared
fields AND `location.verification_cleared`, and says so in its answer.

Every write is audited in the caller's transaction (`audit.record` flushes,
never commits); the router commits.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..billing.planning import map_links
from ..core.countries import normalise_country
from ..models.location import (
    LOCATION_USES,
    USE_HOSPITAL,
    USE_HOTEL,
    USE_SHOOT,
    Location,
)
from ..models.party import Party
from . import audit
from .permissions import scoped

#: The permission that lets a role see crew today (D111). A hotel says where
#: a crew member sleeps, so seeing one needs this as well as `locations.view`.
HOTEL_PERMISSION = "parties.view"

#: A change to any of these clears a hospital's verification.
VERIFICATION_FIELDS: tuple[str, ...] = (
    "street",
    "street2",
    "zip",
    "city",
    "country",
    "latitude",
    "longitude",
    "phone",
    "emergency_phone",
)

#: Every field an audit entry may record, in a fixed order.
AUDIT_FIELDS: tuple[str, ...] = (
    "name",
    "uses",
    "source_link",
    "street",
    "street2",
    "zip",
    "city",
    "country",
    "latitude",
    "longitude",
    "phone",
    "email",
    "website",
    "emergency_phone",
    "access_notes",
    "notes",
    "contact_party_id",
    "contact_text",
    "verified_on",
    "verified_by",
    "verification_note",
    "is_active",
)

TEXT_FIELDS: tuple[str, ...] = (
    "source_link",
    "street",
    "street2",
    "zip",
    "city",
    "phone",
    "email",
    "website",
    "emergency_phone",
    "access_notes",
    "notes",
    "contact_text",
)

VERIFICATION_CLEARED_MESSAGE = (
    "This hospital is no longer marked verified, because its address or phone changed. "
    "Check the entry and mark it verified again."
)

USE_LABELS = {
    "shoot": "Shooting location",
    "hotel": "Hotel",
    "hospital": "Hospital",
    "restaurant": "Restaurant",
    "other": "Other",
}


class LocationError(ValueError):
    """A refusal with a sentence a form can show (422)."""


class LocationNameTaken(LocationError):
    """Another active location already has this name (409)."""


def name_key(name: str) -> str:
    return " ".join(name.split()).lower()


# ─── Who sees what ──────────────────────────────────────────────────────────


def can_see_hotels(membership) -> bool:
    return membership.can("locations.view") and membership.can(HOTEL_PERMISSION)


def can_see_contacts(membership) -> bool:
    return membership.can("parties.view")


def visible_query(db: Session, membership):
    """Locations of the caller's company the caller may see. Hotels only
    with `HOTEL_PERMISSION`."""
    query = scoped(db.query(Location), membership)
    if not can_see_hotels(membership):
        # A place used ONLY as a hotel is crew information; one that is also
        # a shooting location (or anything else) is visible as that.
        query = query.filter(~Location.uses.contained_by([USE_HOTEL]))
    return query


def visible_uses(membership, row: Location) -> list[str]:
    """The uses a caller is shown: without `hotel` for one who may not see
    hotels. In `LOCATION_USES` order."""
    shown = [u for u in LOCATION_USES if u in (row.uses or [])]
    if not can_see_hotels(membership):
        shown = [u for u in shown if u != USE_HOTEL]
    return shown


def is_hotel(row: Location) -> bool:
    return USE_HOTEL in (row.uses or [])


def visible(db: Session, membership, location_id: uuid.UUID) -> Optional[Location]:
    return visible_query(db, membership).filter(Location.id == location_id).first()


# ─── Cleaning a request ─────────────────────────────────────────────────────

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^[0-9+()/.\-\s]+$")


def _text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _check_phone(field: str, value: Optional[str]) -> None:
    if value is None:
        return
    label = "emergency phone" if field == "emergency_phone" else "phone"
    if not _PHONE.match(value) or not any(ch.isdigit() for ch in value):
        raise LocationError(
            f"The {label} number “{value}” has characters a phone number cannot have. "
            "Use digits, spaces and + - / ( ) only, for example +43 1 40400."
        )


def _check_email(value: Optional[str]) -> None:
    if value is not None and not _EMAIL.match(value):
        raise LocationError(
            f"The email “{value}” is not an email address. Write it like office@example.com."
        )


def _check_website(value: Optional[str]) -> None:
    if value is not None and any(ch.isspace() for ch in value):
        raise LocationError(
            f"The website “{value}” has a space in it. Write it like www.example.com."
        )


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise LocationError(
            f"The coordinate “{value}” is not a number. Paste them like {map_links.EXAMPLE}."
        ) from None


def _coordinates(changes: dict) -> Optional[tuple[Optional[Decimal], Optional[Decimal]]]:
    """The (latitude, longitude) a request asks for, or None when it says
    nothing about them. Both or neither; in range; six decimals."""
    has_text = "coordinates" in changes
    has_pair = "latitude" in changes or "longitude" in changes
    if has_text and has_pair:
        raise LocationError(
            "These coordinates were sent twice. Send either the pasted pair or the two numbers, not both."
        )
    if has_text:
        text = changes["coordinates"]
        if text is None or not str(text).strip():
            return (None, None)
        parsed = map_links.parse_coordinates(text)
        if not parsed.ok:
            raise LocationError(parsed.refusal)
        return (parsed.latitude, parsed.longitude)
    if has_pair:
        latitude = _decimal(changes.get("latitude"))
        longitude = _decimal(changes.get("longitude"))
        if (latitude is None) != (longitude is None):
            raise LocationError(
                "These coordinates have only one number. Give both the latitude and the longitude, "
                "or neither."
            )
        if latitude is None:
            return (None, None)
        parsed = map_links.parse_coordinates(f"{latitude}; {longitude}")
        if not parsed.ok:
            raise LocationError(parsed.refusal)
        return (parsed.latitude, parsed.longitude)
    return None


def _contact(db: Session, membership, party_id: Optional[uuid.UUID]) -> Optional[uuid.UUID]:
    """An on-site contact must be one of the company's own contacts that the
    caller may see. Anything else is not found."""
    if party_id is None:
        return None
    if not can_see_contacts(membership):
        raise LookupError("contact")
    party = scoped(db.query(Party), membership).filter(Party.id == party_id).first()
    if party is None:
        raise LookupError("contact")
    return party.id


def _ensure_name_free(db: Session, company_id: uuid.UUID, key: str, *, except_id: Optional[uuid.UUID]) -> None:
    query = db.query(Location).filter(
        Location.company_id == company_id,
        Location.name_key == key,
        Location.is_active.is_(True),
    )
    if except_id is not None:
        query = query.filter(Location.id != except_id)
    other = query.first()
    if other is not None:
        raise LocationNameTaken(
            f"A location called “{other.name}” already exists. Give this one another name, "
            "or open the existing one."
        )


def _apply(db: Session, membership, row: Location, changes: dict) -> None:
    if "name" in changes:
        name = _text(changes["name"])
        if not name:
            raise LocationError("This location has no name. Give it a name, for example “Studio Wien”.")
        row.name = " ".join(name.split())
        row.name_key = name_key(name)
    if "uses" in changes and changes["uses"] is not None:
        wanted = [u for u in LOCATION_USES if u in set(changes["uses"])]
        if not can_see_hotels(membership):
            if USE_HOTEL in wanted:
                raise LookupError("hotel")
            # The caller cannot see the hotel use, so cannot have meant to
            # remove it: an existing one stays.
            if USE_HOTEL in (row.uses or []):
                wanted = [u for u in LOCATION_USES if u in set(wanted) | {USE_HOTEL}]
        if not wanted:
            raise LocationError(
                "This location has no use ticked. Tick at least one: Shooting location, Hotel, Hospital, "
                "Restaurant or Other."
            )
        row.uses = wanted
    for field in TEXT_FIELDS:
        if field in changes:
            setattr(row, field, _text(changes[field]))
    _check_phone("phone", row.phone)
    _check_phone("emergency_phone", row.emergency_phone)
    _check_email(row.email)
    _check_website(row.website)
    if "country" in changes:
        try:
            row.country = normalise_country(changes["country"])
        except ValueError as error:
            raise LocationError(str(error)) from None
    pair = _coordinates(changes)
    if pair is not None:
        row.latitude, row.longitude = pair
    if "contact_party_id" in changes:
        row.contact_party_id = _contact(db, membership, changes["contact_party_id"])


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        if a is None or b is None:
            return a is None and b is None
        return Decimal(a) == Decimal(b)
    return a == b


# ─── Writes ─────────────────────────────────────────────────────────────────


@dataclass
class Change:
    location: Location
    verification_cleared: bool = False

    @property
    def message(self) -> Optional[str]:
        return VERIFICATION_CLEARED_MESSAGE if self.verification_cleared else None


def create(db: Session, membership, changes: dict, *, actor: audit.Actor) -> Change:
    row = Location(company_id=membership.company_id, uses=[], name="", name_key="")
    _apply(db, membership, row, {"uses": [USE_SHOOT], **changes})
    _ensure_name_free(db, membership.company_id, row.name_key, except_id=None)
    db.add(row)
    db.flush()
    audit.record(
        db, actor, row, "location.created", None, audit.snapshot(row, AUDIT_FIELDS), fields=AUDIT_FIELDS
    )
    return Change(location=row)


def update(db: Session, membership, row: Location, changes: dict, *, actor: audit.Actor) -> Change:
    before = audit.snapshot(row, AUDIT_FIELDS)
    _apply(db, membership, row, changes)
    if row.name_key != name_key(before["name"]) and row.is_active:
        _ensure_name_free(db, row.company_id, row.name_key, except_id=row.id)
    if USE_HOSPITAL not in (row.uses or []) and row.verified_on is not None:
        # A hospital that became something else is no longer a verified
        # hospital; the verification belongs to the kind.
        changed_kind = True
    else:
        changed_kind = False
    cleared = False
    was_verified = before["verified_on"] is not None
    if was_verified and (
        changed_kind or any(not _same(before[f], getattr(row, f)) for f in VERIFICATION_FIELDS)
    ):
        row.verified_on = None
        row.verified_by = None
        row.verification_note = None
        cleared = True
    after = audit.snapshot(row, AUDIT_FIELDS)
    if audit.diff(before, after, fields=AUDIT_FIELDS):
        db.flush()
        audit.record(db, actor, row, "location.updated", before, after, fields=AUDIT_FIELDS)
    if cleared:
        audit.record(
            db,
            actor,
            row,
            "location.verification_cleared",
            {"verified_on": before["verified_on"], "verified_by": before["verified_by"]},
            {"verified_on": None, "verified_by": None},
            reason="The address, coordinates or phone changed.",
        )
    return Change(location=row, verification_cleared=cleared)


def verify(db: Session, row: Location, *, note: Optional[str], user_id: uuid.UUID, actor: audit.Actor, today: Optional[date] = None) -> Change:
    if USE_HOSPITAL not in (row.uses or []):
        raise LocationError(
            f"“{row.name}” is not marked as a hospital. Only a hospital can be marked verified; tick "
            "“Hospital” on it first if it is one."
        )
    if not row.is_active:
        raise LocationError(f"“{row.name}” is archived. Restore it before marking it verified.")
    before = audit.snapshot(row, ("verified_on", "verified_by", "verification_note"))
    row.verified_on = today or date.today()
    row.verified_by = user_id
    row.verification_note = _text(note)
    db.flush()
    audit.record(
        db,
        actor,
        row,
        "location.verified",
        before,
        audit.snapshot(row, ("verified_on", "verified_by", "verification_note")),
    )
    return Change(location=row)


def archive(db: Session, row: Location, *, actor: audit.Actor) -> Change:
    if not row.is_active:
        return Change(location=row)
    row.is_active = False
    db.flush()
    audit.record(db, actor, row, "location.archived", {"is_active": True}, {"is_active": False})
    return Change(location=row)


def restore(db: Session, row: Location, *, actor: audit.Actor) -> Change:
    if row.is_active:
        return Change(location=row)
    _ensure_name_free(db, row.company_id, row.name_key, except_id=row.id)
    row.is_active = True
    db.flush()
    audit.record(db, actor, row, "location.restored", {"is_active": False}, {"is_active": True})
    return Change(location=row)


# ─── Where a location is used ───────────────────────────────────────────────


@dataclass
class Use:
    kind: str
    project_id: uuid.UUID
    project_name: str
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    date: Optional[date] = None
    party_name: Optional[str] = None


def _strip_name(block) -> str:
    from .crew_assignment_service import strip_name

    return strip_name(block)


def in_use_counts(db: Session, company_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many active projects, strips, days and hotel stays use each
    location — four grouped queries."""
    from sqlalchemy import func

    from ..models.location import HotelStay, ProjectLocation
    from ..models.project import ProjectBlock, ProjectDay

    if not ids:
        return {}
    counts: dict[uuid.UUID, int] = {}
    queries = (
        db.query(ProjectLocation.location_id, func.count())
        .filter(ProjectLocation.company_id == company_id, ProjectLocation.location_id.in_(ids), ProjectLocation.is_active.is_(True))
        .group_by(ProjectLocation.location_id),
        db.query(ProjectBlock.location_id, func.count())
        .filter(ProjectBlock.company_id == company_id, ProjectBlock.location_id.in_(ids), ProjectBlock.is_active.is_(True))
        .group_by(ProjectBlock.location_id),
        db.query(ProjectDay.location_id, func.count())
        .filter(ProjectDay.company_id == company_id, ProjectDay.location_id.in_(ids), ProjectDay.is_active.is_(True))
        .group_by(ProjectDay.location_id),
        db.query(HotelStay.location_id, func.count())
        .filter(HotelStay.company_id == company_id, HotelStay.location_id.in_(ids), HotelStay.is_active.is_(True))
        .group_by(HotelStay.location_id),
    )
    for query in queries:
        for location_id, count in query.all():
            counts[location_id] = counts.get(location_id, 0) + count
    return counts


def usage(db: Session, membership, row: Location) -> list[Use]:
    """Every project, strip, day and hotel stay that uses `row`. Stays (a
    person's name) only for a caller who may see hotels."""
    from ..models.location import HotelStay, ProjectLocation
    from ..models.party import Party
    from ..models.project import Project, ProjectBlock, ProjectDay

    company_id = membership.company_id
    uses: list[Use] = []
    for project in (
        db.query(Project)
        .join(ProjectLocation, ProjectLocation.project_id == Project.id)
        .filter(ProjectLocation.company_id == company_id, ProjectLocation.location_id == row.id, ProjectLocation.is_active.is_(True))
        .order_by(Project.name, Project.id)
    ):
        uses.append(Use("project", project.id, project.name, project.project_number))
    for block, project in (
        db.query(ProjectBlock, Project)
        .join(Project, Project.id == ProjectBlock.project_id)
        .filter(ProjectBlock.company_id == company_id, ProjectBlock.location_id == row.id, ProjectBlock.is_active.is_(True))
        .order_by(ProjectBlock.start_date, ProjectBlock.id)
    ):
        uses.append(Use("strip", project.id, project.name, project.project_number, block.id, _strip_name(block)))
    for day, block, project in (
        db.query(ProjectDay, ProjectBlock, Project)
        .join(Project, Project.id == ProjectDay.project_id)
        .outerjoin(ProjectBlock, ProjectBlock.id == ProjectDay.block_id)
        .filter(ProjectDay.company_id == company_id, ProjectDay.location_id == row.id, ProjectDay.is_active.is_(True))
        .order_by(ProjectDay.date, ProjectDay.id)
    ):
        uses.append(
            Use("day", project.id, project.name, project.project_number,
                block.id if block else None, _strip_name(block) if block else None, day.date)
        )
    if can_see_hotels(membership):
        for stay, project, name in (
            db.query(HotelStay, Project, Party.name)
            .join(Project, Project.id == HotelStay.project_id)
            .join(Party, Party.id == HotelStay.party_id)
            .filter(HotelStay.company_id == company_id, HotelStay.location_id == row.id, HotelStay.is_active.is_(True))
            .order_by(HotelStay.check_in, Party.name, HotelStay.id)
        ):
            uses.append(Use("hotel_stay", project.id, project.name, project.project_number, date=stay.check_in, party_name=name))
    return uses


# ─── Attaching a location ───────────────────────────────────────────────────


def _attachable(db: Session, membership, location_id: uuid.UUID, *, current: Optional[uuid.UUID]) -> Location:
    row = visible(db, membership, location_id)
    if row is None:
        raise LookupError("location")
    if not row.is_active and row.id != current:
        raise LocationError(
            f"“{row.name}” is archived. Restore it under the project's Locations, or pick another location."
        )
    return row


def place_for_day(db: Session, membership, location_id: Optional[uuid.UUID], *, current: Optional[uuid.UUID]) -> Optional[Location]:
    """The location a strip or a day may be set to: one marked as a
    shooting location, active (or the one it already has). None clears it."""
    if location_id is None:
        return None
    row = _attachable(db, membership, location_id, current=current)
    if USE_SHOOT not in (row.uses or []):
        raise LocationError(
            f"“{row.name}” is not marked as a shooting location. Tick “Shooting location” on it first, "
            "or pick another location."
        )
    return row


def hotel(db: Session, membership, location_id: uuid.UUID, *, current: Optional[uuid.UUID] = None) -> Location:
    """A location that may take hotel stays: marked as a hotel, active (or
    the one already set). Not visible: not found."""
    if not can_see_hotels(membership):
        raise LookupError("hotel")
    row = _attachable(db, membership, location_id, current=current)
    if not is_hotel(row):
        raise LocationError(
            f"“{row.name}” is not marked as a hotel. Tick “Hotel” on it first, or pick a hotel."
        )
    return row


def by_id(db: Session, company_id: uuid.UUID, ids) -> dict[uuid.UUID, Location]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return {
        row.id: row
        for row in db.query(Location).filter(Location.company_id == company_id, Location.id.in_(ids))
    }


# ─── A project's locations (L1b) ────────────────────────────────────────────


def _pair(db: Session, project_id: uuid.UUID, location_id: uuid.UUID):
    from ..models.location import ProjectLocation

    return (
        db.query(ProjectLocation)
        .filter(ProjectLocation.project_id == project_id, ProjectLocation.location_id == location_id)
        .first()
    )


def add_to_project(db: Session, project, location: Location, *, actor: audit.Actor, reason: Optional[str] = None) -> bool:
    """Adds `location` to the project's list; True when it was not on it."""
    from ..models.location import ProjectLocation

    row = _pair(db, project.id, location.id)
    if row is not None and row.is_active:
        return False
    if row is None:
        row = ProjectLocation(company_id=project.company_id, project_id=project.id, location_id=location.id)
        db.add(row)
    else:
        row.is_active = True
    db.flush()
    audit.record(
        db, actor, row, "project_location.added", None,
        {"project_id": str(project.id), "location_id": str(location.id), "location": location.name},
        reason=reason,
    )
    return True


def ensure_in_project(db: Session, project, location: Optional[Location], *, actor: audit.Actor) -> None:
    """A location used on a strip or a day is one of the project's."""
    if location is not None:
        add_to_project(db, project, location, actor=actor, reason="Used on the calendar.")


def remove_from_project(db: Session, project, location: Location, *, actor: audit.Actor) -> None:
    """Off the project's list. Refused while a strip, a day or a stay of the
    project still uses it — the sentence says which."""
    from ..models.location import HotelStay
    from ..models.project import ProjectBlock, ProjectDay

    strips = (
        db.query(ProjectBlock)
        .filter(ProjectBlock.project_id == project.id, ProjectBlock.location_id == location.id, ProjectBlock.is_active.is_(True))
        .count()
    )
    days = (
        db.query(ProjectDay)
        .filter(ProjectDay.project_id == project.id, ProjectDay.location_id == location.id, ProjectDay.is_active.is_(True))
        .count()
    )
    stays = (
        db.query(HotelStay)
        .filter(HotelStay.project_id == project.id, HotelStay.location_id == location.id, HotelStay.is_active.is_(True))
        .count()
    )
    if strips or days or stays:
        parts = []
        if strips:
            parts.append(f"{strips} {'strip' if strips == 1 else 'strips'}")
        if days:
            parts.append(f"{days} {'day' if days == 1 else 'days'}")
        if stays:
            parts.append(f"{stays} hotel {'guest' if stays == 1 else 'guests'}")
        raise LocationError(
            f"“{location.name}” is still used by {', '.join(parts)} of this project. Change those first, "
            "then remove it from the project."
        )
    row = _pair(db, project.id, location.id)
    if row is None or not row.is_active:
        return
    row.is_active = False
    db.flush()
    audit.record(
        db, actor, row, "project_location.removed",
        {"project_id": str(project.id), "location_id": str(location.id), "location": location.name}, None,
    )


def project_location_ids(db: Session, project_id: uuid.UUID) -> list[uuid.UUID]:
    from ..models.location import ProjectLocation

    return [
        r.location_id
        for r in db.query(ProjectLocation.location_id)
        .filter(ProjectLocation.project_id == project_id, ProjectLocation.is_active.is_(True))
    ]


# ─── Hotel stays (L1b) ──────────────────────────────────────────────────────

STAY_FIELDS: tuple[str, ...] = ("location_id", "party_id", "check_in", "check_out", "notes", "is_active")


def crew_dates(db: Session, project_id: uuid.UUID, party_id: uuid.UUID) -> list[date]:
    """The active days of the person's open assignments in the project."""
    from ..models.crew import OCCUPYING_STATUSES, CrewAssignment, CrewAssignmentDay

    return sorted(
        {
            d
            for (d,) in db.query(CrewAssignmentDay.date)
            .join(CrewAssignment, CrewAssignment.id == CrewAssignmentDay.assignment_id)
            .filter(
                CrewAssignment.project_id == project_id,
                CrewAssignment.party_id == party_id,
                CrewAssignment.status.in_(OCCUPYING_STATUSES),
                CrewAssignmentDay.is_active.is_(True),
            )
        }
    )


def _is_crew_of(db: Session, project_id: uuid.UUID, party_id: uuid.UUID) -> bool:
    from ..models.crew import OCCUPYING_STATUSES, CrewAssignment

    return (
        db.query(CrewAssignment)
        .filter(
            CrewAssignment.project_id == project_id,
            CrewAssignment.party_id == party_id,
            CrewAssignment.status.in_(OCCUPYING_STATUSES),
        )
        .first()
        is not None
    )


def _check_dates(check_in: date, check_out: date) -> None:
    if check_in >= check_out:
        raise LocationError(
            "This stay checks out on or before its check-in. Set the check-out to a later day than the check-in."
        )


def add_stay(db: Session, project, location: Location, party, *, check_in: Optional[date], check_out: Optional[date],
             notes: Optional[str], actor: audit.Actor):
    """A crew member of this production at this hotel. Without dates: from
    their first assigned day to the day after their last."""
    from datetime import timedelta

    from ..models.location import HotelStay

    if not _is_crew_of(db, project.id, party.id):
        raise LocationError(
            f"{party.name} is not on the crew of this project. Add them to a strip first, then give them a hotel."
        )
    if check_in is None or check_out is None:
        days = crew_dates(db, project.id, party.id)
        if not days:
            raise LocationError(
                f"{party.name} has no days on this project yet, so the stay has no dates. Enter the check-in and check-out."
            )
        check_in = check_in or days[0]
        check_out = check_out or days[-1] + timedelta(days=1)
    _check_dates(check_in, check_out)
    stay = HotelStay(
        company_id=project.company_id, project_id=project.id, location_id=location.id, party_id=party.id,
        check_in=check_in, check_out=check_out, notes=_text(notes),
    )
    db.add(stay)
    db.flush()
    audit.record(db, actor, stay, "hotel_stay.added", None, audit.snapshot(stay, STAY_FIELDS), fields=STAY_FIELDS)
    return stay


def update_stay(db: Session, stay, changes: dict, *, actor: audit.Actor):
    before = audit.snapshot(stay, STAY_FIELDS)
    if "check_in" in changes and changes["check_in"] is not None:
        stay.check_in = changes["check_in"]
    if "check_out" in changes and changes["check_out"] is not None:
        stay.check_out = changes["check_out"]
    if "notes" in changes:
        stay.notes = _text(changes["notes"])
    _check_dates(stay.check_in, stay.check_out)
    db.flush()
    audit.record(db, actor, stay, "hotel_stay.updated", before, audit.snapshot(stay, STAY_FIELDS), fields=STAY_FIELDS)
    return stay


def remove_stay(db: Session, stay, *, actor: audit.Actor):
    if not stay.is_active:
        return stay
    stay.is_active = False
    db.flush()
    audit.record(db, actor, stay, "hotel_stay.removed", {"is_active": True}, {"is_active": False})
    return stay


# ─── Booking confirmations (L1b) ────────────────────────────────────────────

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
#: What a confirmation may be, by its first bytes where that can tell, else
#: by the name: PDF, an email (.eml), an image, plain text.
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_BY_EXTENSION = {".eml": "message/rfc822", ".txt": "text/plain", ".msg": "application/vnd.ms-outlook", ".webp": "image/webp"}


def attachment_type(file_name: str, payload: bytes) -> str:
    import os

    for magic, content_type in _SIGNATURES:
        if payload.startswith(magic):
            return content_type
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    ext = os.path.splitext(file_name.lower())[1]
    if ext in (".eml", ".txt"):
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            try:
                payload.decode("latin-1")
            except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes anything
                pass
        return _BY_EXTENSION[ext]
    if ext == ".msg":
        return _BY_EXTENSION[ext]
    raise LocationError(
        f"“{file_name}” is not a PDF, an email (.eml or .msg), a picture or a text file. Save the confirmation "
        "as one of those, or paste its text instead."
    )


def add_file_attachment(db: Session, project, location: Location, *, title: Optional[str], file_name: str,
                        payload: bytes, user_id, actor: audit.Actor):
    from . import s3_service
    from ..models.location import ATTACHMENT_FILE, LocationAttachment

    if not payload:
        raise LocationError(f"“{file_name}” is empty. Pick the file again.")
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise LocationError(f"“{file_name}” is larger than 15 MB. Save a smaller copy, or paste the text instead.")
    content_type = attachment_type(file_name, payload)
    row = LocationAttachment(
        company_id=project.company_id, project_id=project.id, location_id=location.id, kind=ATTACHMENT_FILE,
        title=_text(title) or file_name, file_name=file_name[:255], content_type=content_type,
        size_bytes=len(payload), created_by=user_id,
    )
    row.id = uuid.uuid4()
    row.s3_key = f"companies/{project.company_id}/projects/{project.id}/locations/{location.id}/{row.id}"
    s3_service.put_object(row.s3_key, payload, content_type=content_type)
    db.add(row)
    db.flush()
    audit.record(db, actor, row, "location_attachment.added", None,
                 {"title": row.title, "kind": row.kind, "file_name": row.file_name, "location": location.name})
    return row


def add_text_attachment(db: Session, project, location: Location, *, title: Optional[str], text: str, user_id,
                        actor: audit.Actor):
    from ..models.location import ATTACHMENT_TEXT, LocationAttachment

    body = (text or "").strip()
    if not body:
        raise LocationError("The confirmation text is empty. Paste the email or message into the box.")
    row = LocationAttachment(
        company_id=project.company_id, project_id=project.id, location_id=location.id, kind=ATTACHMENT_TEXT,
        title=_text(title) or "Booking confirmation", body_text=body, created_by=user_id,
    )
    db.add(row)
    db.flush()
    audit.record(db, actor, row, "location_attachment.added", None,
                 {"title": row.title, "kind": row.kind, "location": location.name})
    return row


def remove_attachment(db: Session, row, *, actor: audit.Actor):
    if row.is_active:
        row.is_active = False
        db.flush()
        audit.record(db, actor, row, "location_attachment.removed", {"title": row.title}, None)
    return row


# ─── Hospitals near the shooting locations (L1b) ────────────────────────────


@dataclass
class HospitalOffer:
    hospital: object  # osm_hospitals.OsmHospital
    distance_km: float
    nearest_to: str
    existing_location_id: Optional[uuid.UUID]


@dataclass
class HospitalGroup:
    shooting_locations: list[str]
    offers: list[HospitalOffer]


def hospital_suggestions(db: Session, membership, project, *, client=None, per_group: int = 3):
    """Per group of shooting locations within 30 km of each other (pure
    `billing/planning/geo.clusters`), the nearest hospitals OpenStreetMap
    knows — ones with a confirmed emergency department first. Returns
    (groups, names of shooting locations without coordinates)."""
    from ..billing.planning import Point, clusters, distance_km
    from ..config import settings
    from . import osm_hospitals

    ids = project_location_ids(db, project.id)
    places = [
        p for p in db.query(Location).filter(Location.company_id == membership.company_id, Location.id.in_(ids))
        .order_by(Location.name_key, Location.id)
        if USE_SHOOT in (p.uses or []) and p.is_active
    ]
    missing = [p.name for p in places if p.latitude is None]
    located = [p for p in places if p.latitude is not None]
    by_id_ = {p.id: p for p in located}
    groups = clusters([Point(p.id, p.latitude, p.longitude) for p in located])
    known = {
        row.osm_ref: row.id
        for row in db.query(Location).filter(
            Location.company_id == membership.company_id, Location.osm_ref.isnot(None), Location.is_active.is_(True)
        )
    }
    out: list[HospitalGroup] = []
    radius = settings.hospital_search_radius_km * 1000
    for group in groups:
        members = [by_id_[i] for i in group]
        best: dict[str, HospitalOffer] = {}
        for member in members:
            for found in osm_hospitals.near(member.latitude, member.longitude, radius, client=client):
                km = distance_km(member.latitude, member.longitude, found.latitude, found.longitude)
                current = best.get(found.osm_ref)
                if current is None or km < current.distance_km:
                    best[found.osm_ref] = HospitalOffer(found, round(km, 1), member.name, known.get(found.osm_ref))
        ranked = sorted(best.values(), key=lambda o: (o.hospital.emergency is not True, o.distance_km, o.hospital.name))
        out.append(HospitalGroup([m.name for m in members], ranked[:per_group]))
    return out, missing


def add_hospital_from_osm(db: Session, membership, project, offer: dict, *, actor: audit.Actor) -> Location:
    """Creates (or finds, by its OpenStreetMap id) the hospital and adds it
    to the project. The data is what the suggestion showed."""
    existing = (
        db.query(Location)
        .filter(Location.company_id == membership.company_id, Location.osm_ref == offer["osm_ref"], Location.is_active.is_(True))
        .first()
    )
    if existing is None:
        changes = {
            "name": offer["name"],
            "uses": [USE_HOSPITAL],
            "latitude": offer["latitude"],
            "longitude": offer["longitude"],
            "street": offer.get("street"),
            "zip": offer.get("zip"),
            "city": offer.get("city"),
            "website": offer.get("website"),
        }
        if offer.get("phone"):
            changes["emergency_phone"] = None
            changes["phone"] = offer["phone"] if _PHONE.match(offer["phone"]) else None
        if offer.get("country") and len(offer["country"]) == 2:
            changes["country"] = offer["country"]
        name = " ".join(offer["name"].split())
        taken = db.query(Location).filter(
            Location.company_id == membership.company_id, Location.name_key == name_key(name), Location.is_active.is_(True)
        ).first()
        if taken is not None:
            changes["name"] = f"{name} ({offer.get('city') or 'OpenStreetMap'})"
        existing = create(db, membership, changes, actor=actor).location
        existing.osm_ref = offer["osm_ref"]
        db.flush()
    elif USE_HOSPITAL not in existing.uses:
        existing.uses = [u for u in LOCATION_USES if u in set(existing.uses) | {USE_HOSPITAL}]
        db.flush()
    add_to_project(db, project, existing, actor=actor, reason="Suggested by the OpenStreetMap hospital search.")
    return existing
