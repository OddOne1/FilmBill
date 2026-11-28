"""Locations: where a shooting day happens, where crew sleep, which hospital
is the emergency one (L1).

**One table, four kinds** — `shoot`, `hotel`, `hospital`, `other`. A hotel
is the same shape of record as a shoot location (an address, a phone, how to
get in); what differs is who may see it and where it may be used, and both
are the service's rules (`services/location_service.py`).

**A hotel is crew information.** It says where a person sleeps, so it is
visible only to a role that can see crew today (`parties.view`, D111); a
hotel the caller may not see is absent from every list and answers 404.

**Coordinates are typed or pasted**, never looked up: both or neither, in
range, six decimals. There is no geocoding (C2).

**A hospital can be marked verified** — a person checked this entry on this
date. Any change to its address, coordinates, phone or emergency phone
clears that; a change to its notes does not. "Verified" says somebody
checked the entry, not that it is the nearest hospital.

**Never deleted.** `is_active = False` archives a location; whatever already
uses it keeps showing it, marked archived. The name is unique among the
ACTIVE locations of a company, case-insensitively (`name_key`), so an
archived one never blocks its own name.
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..database import Base
except ImportError:  # pragma: no cover - direct-import path for alembic
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped


#: L1b: a location has a SET of uses (Mathias, 2026-11-28: "one place,
#: several uses"). A hotel or hospital is a shooting location only when the
#: shooting use is ticked on purpose.
USE_SHOOT = "shoot"
USE_HOTEL = "hotel"
USE_HOSPITAL = "hospital"
USE_RESTAURANT = "restaurant"
USE_OTHER = "other"
LOCATION_USES: tuple[str, ...] = (USE_SHOOT, USE_HOTEL, USE_HOSPITAL, USE_RESTAURANT, USE_OTHER)
# L1 names, kept for readability where one use is meant.
LOCATION_SHOOT, LOCATION_HOTEL, LOCATION_HOSPITAL, LOCATION_OTHER = USE_SHOOT, USE_HOTEL, USE_HOSPITAL, USE_OTHER


class Location(CompanyScoped, Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `lower(name)` with inner whitespace collapsed — the uniqueness key.
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: L1b. One or more of `LOCATION_USES` (was L1's single `kind`).
    #: `.with_variant(JSON, "sqlite")` for the same reason as `parties.tags`:
    #: one test file builds the schema on SQLite.
    uses: Mapped[list[str]] = mapped_column(ARRAY(String(16)).with_variant(JSON, "sqlite"), nullable=False)
    #: L1b. The map link it was made from, as pasted (Google or Apple).
    source_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: L1b. `node/123` etc. when added from the OpenStreetMap hospital
    #: search — one active location per company and OSM object.
    osm_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    street: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    street2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    zip: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: ISO 3166-1 alpha-2, the same as every other country in FilmBill (D96).
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)

    #: Both or neither (`ck_locations_coordinates_pair`), six decimals.
    latitude: Mapped[Optional[Any]] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Optional[Any]] = mapped_column(Numeric(9, 6), nullable=True)

    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    #: A hospital's emergency number (the field is offered for every kind).
    emergency_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    #: Parking, entrances, which door. **Not** gate codes or passwords — the
    #: form says so; the system cannot tell and does not try.
    access_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: An on-site contact from Contacts, optional, plus free text for one
    #: who is not a contact ("Frau Berger, Hausmeister, 0664 …").
    contact_party_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parties.id", ondelete="RESTRICT"), nullable=True
    )
    contact_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Hospitals only: who checked this entry, when, and a note.
    verified_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verification_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "cardinality(uses) >= 1 AND uses <@ "
            "ARRAY['shoot', 'hotel', 'hospital', 'restaurant', 'other']::varchar(16)[]",
            name="ck_locations_uses",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="ck_locations_coordinates_pair"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="ck_locations_latitude"
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_locations_longitude",
        ),
        Index(
            "uq_locations_company_name_active",
            "company_id",
            "name_key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "uq_locations_company_osm_active",
            "company_id",
            "osm_ref",
            unique=True,
            postgresql_where=text("is_active AND osm_ref IS NOT NULL"),
        ),
        Index("ix_locations_contact_party", "contact_party_id"),
    )


class ProjectLocation(CompanyScoped, Base):
    """L1b. A location this project uses — what its Locations tab lists. A
    location used on a strip or a day is added here by that use; anything
    else is added by hand. Removing it sets `is_active` false; adding it again
    reactivates the same row (`uq_project_locations_pair`)."""

    __tablename__ = "project_locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "location_id", name="uq_project_locations_pair"),
        Index("ix_project_locations_location", "location_id"),
    )


class HotelStay(CompanyScoped, Base):
    """L1b. One crew member of the production at one hotel, from check-in to
    check-out (the check-out date is the morning they leave). Several stays
    per person are allowed (two hotels on a long shoot). Removed with
    `is_active`. Crew information: visible only with `parties.view`."""

    __tablename__ = "hotel_stays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False
    )
    check_in: Mapped[date] = mapped_column(Date, nullable=False)
    check_out: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("check_in < check_out", name="ck_hotel_stays_dates"),
        Index("ix_hotel_stays_project", "project_id"),
        Index("ix_hotel_stays_location", "location_id"),
        Index("ix_hotel_stays_party", "party_id"),
    )


ATTACHMENT_FILE = "file"
ATTACHMENT_TEXT = "text"


class LocationAttachment(CompanyScoped, Base):
    """L1b. A booking confirmation for a location in a project: a stored file
    (PDF, an email saved as .eml, an image, a .txt) or pasted text. Removed
    with `is_active`; the stored object is kept. Crew information for a
    hotel: visible only with `parties.view`."""

    __tablename__ = "location_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(127), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('file', 'text')", name="ck_location_attachments_kind"),
        CheckConstraint(
            "(kind = 'file' AND s3_key IS NOT NULL) OR (kind = 'text' AND body_text IS NOT NULL)",
            name="ck_location_attachments_content",
        ),
        Index("ix_location_attachments_project_location", "project_id", "location_id"),
    )
