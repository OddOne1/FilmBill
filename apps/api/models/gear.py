"""Gear on the calendar: bookings of devices on strips, with days (B2c-2).

**The crew pattern, for devices.** A `GearBooking` puts a QUANTITY of one
device on one strip of one project and carries its own days
(`GearBookingDay`), so two cameras can be on days 3 to 5 of a shoot only.
The status words are crew's: `scheduled` (tentative, like an option) and
`booked` (confirmed), marked by hand; removing a booking sets `cancelled`
with a note. Nothing is ever deleted.

**Availability is computed, never stored.** How many of a device are free
on a day is `billing/planning/gear_availability.py` over these rows;
overbooking warns and never blocks (decided). `overbooking_note` records
why somebody booked anyway ("renting in").

**Dependencies, one level** (SCOPE §4.4.3, D126). Booking a parent adds its
`always` dependencies as bookings of their own, `added_via_booking_id`
pointing at the parent, `association_id` at the link and
`quantity_per_parent` copying the link's number at that moment — so a link
changed later never changes a booking by itself. `follows_parent` is true
while the dependent's quantity and days are still derived from the parent;
editing either by hand ends it.

**One active booking per strip, device and route**: two partial unique
indexes, because a NULL `added_via_booking_id` is distinct from every other
NULL to PostgreSQL — the direct booking and each parent's dependent are the
three routes a battery can take onto one strip.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


GEAR_SCHEDULED = "scheduled"
GEAR_BOOKED = "booked"
GEAR_CANCELLED = "cancelled"
GEAR_STATUSES: tuple[str, ...] = (GEAR_SCHEDULED, GEAR_BOOKED, GEAR_CANCELLED)
#: The statuses that hold a device on its days.
HOLDING_STATUSES: tuple[str, ...] = (GEAR_SCHEDULED, GEAR_BOOKED)


class GearBooking(CompanyScoped, Base):
    __tablename__ = "gear_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_blocks.id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=GEAR_SCHEDULED)
    #: How a booking was confirmed ("by phone"), why it was reopened.
    status_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    booked_on: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    booked_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: The parent booking, when this row was added as its dependency.
    added_via_booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gear_bookings.id", ondelete="RESTRICT"), nullable=True
    )
    #: The dependency link it came through, and that link's quantity per
    #: parent when it was booked.
    association_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device_associations.id", ondelete="RESTRICT"), nullable=True
    )
    quantity_per_parent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    follows_parent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: Why it was booked although too few are free ("renting in").
    overbooking_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Why it was removed (cancelled).
    removal_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled', 'booked', 'cancelled')", name="ck_gear_bookings_status"
        ),
        CheckConstraint("quantity >= 1", name="ck_gear_bookings_quantity"),
        CheckConstraint(
            "quantity_per_parent IS NULL OR quantity_per_parent >= 1",
            name="ck_gear_bookings_quantity_per_parent",
        ),
        CheckConstraint(
            "added_via_booking_id IS NULL OR added_via_booking_id <> id",
            name="ck_gear_bookings_not_own_parent",
        ),
        Index(
            "uq_gear_bookings_direct_open",
            "block_id",
            "device_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled' AND added_via_booking_id IS NULL"),
        ),
        Index(
            "uq_gear_bookings_dependent_open",
            "block_id",
            "device_id",
            "added_via_booking_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled' AND added_via_booking_id IS NOT NULL"),
        ),
        Index("ix_gear_bookings_project", "project_id"),
        Index("ix_gear_bookings_block", "block_id"),
        Index("ix_gear_bookings_device", "device_id"),
        Index("ix_gear_bookings_parent", "added_via_booking_id"),
        Index("ix_gear_bookings_association", "association_id"),
    )


class GearBookingDay(CompanyScoped, Base):
    """One day of a gear booking, inside its strip's range. Unticking sets
    `is_active` false; ticking again reactivates the same row."""

    __tablename__ = "gear_booking_days"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gear_bookings.id", ondelete="RESTRICT"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_gear_booking_days_active",
            "booking_id",
            "date",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_gear_booking_days_booking", "booking_id"),
        Index("ix_gear_booking_days_company_date", "company_id", "date"),
    )
