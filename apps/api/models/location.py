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
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..database import Base
except ImportError:  # pragma: no cover - direct-import path for alembic
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped


LOCATION_SHOOT = "shoot"
LOCATION_HOTEL = "hotel"
LOCATION_HOSPITAL = "hospital"
LOCATION_OTHER = "other"
LOCATION_KINDS: tuple[str, ...] = (LOCATION_SHOOT, LOCATION_HOTEL, LOCATION_HOSPITAL, LOCATION_OTHER)


class Location(CompanyScoped, Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `lower(name)` with inner whitespace collapsed — the uniqueness key.
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

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
            "kind IN ('shoot', 'hotel', 'hospital', 'other')", name="ck_locations_kind"
        ),
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
        Index("ix_locations_company_kind", "company_id", "kind"),
        Index("ix_locations_contact_party", "contact_party_id"),
    )
