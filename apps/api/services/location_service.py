"""Locations (L1): the rules, the writes and their audit events.

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
    LOCATION_HOSPITAL,
    LOCATION_HOTEL,
    LOCATION_KINDS,
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
    "kind",
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

KIND_LABELS = {"shoot": "shoot location", "hotel": "hotel", "hospital": "hospital", "other": "location"}


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
        query = query.filter(Location.kind != LOCATION_HOTEL)
    return query


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
    if "kind" in changes and changes["kind"] is not None:
        if changes["kind"] not in LOCATION_KINDS:  # pragma: no cover - the schema refuses first
            raise LocationError("This kind of location does not exist. Pick shoot, hotel, hospital or other.")
        if changes["kind"] == LOCATION_HOTEL and not can_see_hotels(membership):
            raise LookupError("hotel")
        row.kind = changes["kind"]
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
    row = Location(company_id=membership.company_id, kind=changes.get("kind") or "shoot", name="", name_key="")
    if row.kind == LOCATION_HOTEL and not can_see_hotels(membership):
        raise LookupError("hotel")
    _apply(db, membership, row, changes)
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
    if row.kind != LOCATION_HOSPITAL and row.verified_on is not None:
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
    if row.kind != LOCATION_HOSPITAL:
        raise LocationError(
            f"“{row.name}” is a {KIND_LABELS[row.kind]}, not a hospital. Only a hospital can be marked verified."
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


def in_use_counts(db: Session, company_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """How many things use each location. Nothing attaches to a location
    until part 2 of L1 adds the strip, day and crew links."""
    return {}


def usage(db: Session, membership, row: Location) -> list[Use]:
    return []
