"""Gear: devices and the serial numbers of their units (B2b part 2).

**Two numbers, independent by design.** A `Device` is one product type —
"Alexa 35" — with the QUANTITY the company owns and a purchase price. Its
`DeviceUnit` rows are the serial numbers of individual units. The two may
legitimately disagree, and nothing reconciles them:

  * three owned and two serial numbers known — the third was bought on a
    production, by a partner, or is delivered late, and is entered in advance
    so a future production cannot overbook it;
  * more serial numbers than usable devices — a unit destroyed on a
    production, or kept on the shelf as a museum piece.

**The quantity is what planning will use** (B2c); serial numbers are
records. So adding a serial never changes `quantity_owned`, and retiring one
only reduces it when the person says so (the retire dialog asks, ticked by
default).

**Nothing is deleted.** A device is archived (`is_active`); a unit is
removed — retired, lost or sold — with a free-text reason that says exactly
what happened, and a unit entered by mistake is retired with that said.
Neither table has a DELETE route.

**Two prices.** The device's `purchase_price` is the usual price; a unit's
own `purchase_price` records what that one cost when it differs (a later
purchase, a discount). Each unit can also name its seller and the seller's
invoice number, for a repair, a return or an insurance claim.

**Case-insensitive uniqueness through stored keys.** `name_key` and
`serial_key` are the normalised forms (`services/device_service.name_key` /
`serial_key`) and carry plain unique indexes, because this schema has no
expression indexes and `alembic check` — the job that compares models with
migrations — cannot compare one. "ab-123" and "AB-123" are one serial.
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

try:
    from ..core.money import AmountColumn
except ImportError:  # pragma: no cover
    from core.money import AmountColumn


UNIT_ACTIVE = "active"
UNIT_NEEDS_CHECK = "needs_check"
UNIT_IN_REPAIR = "in_repair"
UNIT_LOST = "lost"
UNIT_RETIRED = "retired"
UNIT_SOLD = "sold"
UNIT_STATUSES = (
    UNIT_ACTIVE,
    UNIT_NEEDS_CHECK,
    UNIT_IN_REPAIR,
    UNIT_LOST,
    UNIT_RETIRED,
    UNIT_SOLD,
)
#: Still the company's, possibly not bookable today: a unit that needs a
#: check or is in repair comes back by itself.
UNIT_IN_HAND = (UNIT_ACTIVE, UNIT_NEEDS_CHECK, UNIT_IN_REPAIR)
#: Gone — retired, lost or sold. Removing a unit always says exactly what
#: happened (`removed_reason`, free text, required), Mathias 2026-11-26.
UNIT_REMOVED = (UNIT_LOST, UNIT_RETIRED, UNIT_SOLD)


class Device(CompanyScoped, Base):
    """One product type the company owns, and how many of it."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `lower(name)` with inner whitespace collapsed. One active device of a
    #: name per company.
    name_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Free text with suggestions ("Camera", "Light", "Sound"), not a pointer
    #: into the catalog's categories: those label PRICE-LIST items and are
    #: edited under the catalog permission, and a gear import must not have to
    #: create price-list categories to file a lamp (SCOPE, B2b).
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: What planning will book against. Never changed by a serial number.
    quantity_owned: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: Net, in the company's currency. Served only to a role holding
    #: `budgets.view` — a purchase price is cost information.
    purchase_price: Mapped[Optional[Any]] = mapped_column(AmountColumn(), nullable=True)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: The catalog's equipment item this device is rented out as, so a budget
    #: line and a device can later be matched. Optional.
    catalog_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("quantity_owned >= 0", name="ck_devices_quantity_owned"),
        Index(
            "uq_devices_company_name_active",
            "company_id",
            "name_key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_devices_company_active", "company_id", "is_active"),
        Index("ix_devices_catalog_item", "catalog_item_id"),
    )


class DeviceUnit(CompanyScoped, Base):
    """One physical unit of a device, known by its serial number."""

    __tablename__ = "device_units"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False)
    #: `serial_number` stripped and case-folded. Unique among ALL of a
    #: device's units, removed ones included: a unit coming back is
    #: reactivated, not entered twice.
    serial_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=UNIT_ACTIVE
    )
    #: The purchase date of this unit.
    acquired_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: What THIS unit cost, net — when it differs from the device's price
    #: (a later purchase at another price, a discount). Empty means the
    #: device's price applies. Served only with `budgets.view`, like it.
    purchase_price: Mapped[Optional[Any]] = mapped_column(AmountColumn(), nullable=True)
    #: Who sold it, for a repair, a return or an insurance claim: a party
    #: on file, or a name typed in when the seller is not one.
    seller_party_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    seller_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: The seller's invoice or order number.
    purchase_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: When and why it was removed (retired, lost or sold). Both are set
    #: exactly when the status is one of those three — a CHECK says so.
    removed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    removed_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'needs_check', 'in_repair', 'lost', 'retired', 'sold')",
            name="ck_device_units_status",
        ),
        # A removed unit says when and exactly why; a unit in hand says
        # neither — an active unit "sold on 3 May" is a contradiction
        # nobody could act on.
        CheckConstraint(
            "(status IN ('lost', 'retired', 'sold') AND removed_on IS NOT NULL "
            "AND removed_reason IS NOT NULL AND length(trim(removed_reason)) > 0) "
            "OR (status NOT IN ('lost', 'retired', 'sold') AND removed_on IS NULL "
            "AND removed_reason IS NULL)",
            name="ck_device_units_removed_fields",
        ),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_device_units_purchase_price",
        ),
        Index(
            "uq_device_units_device_serial",
            "device_id",
            "serial_key",
            unique=True,
        ),
        Index("ix_device_units_device", "device_id"),
        Index("ix_device_units_seller", "seller_party_id"),
    )


ASSOCIATION_ALWAYS = "always"
ASSOCIATION_ASK = "ask"
ASSOCIATION_MODES = (ASSOCIATION_ALWAYS, ASSOCIATION_ASK)
PRICING_INCLUDED = "included"
PRICING_CHARGED = "charged"
ASSOCIATION_PRICINGS = (PRICING_INCLUDED, PRICING_CHARGED)


class DeviceAssociation(CompanyScoped, Base):
    """A dependency: `device` needs `associated_device`, so many per parent
    (SCOPE §4.4.3 "associated items"; the screen says Dependencies).

    B2b-2 builds the DATA and the screens; what booking does with a link is
    B2c, and B2c follows ONE level only — an associated device's own
    dependencies are never pulled in through it. A link that would close a
    loop (a device needing itself, directly or through others) is refused
    by `services/device_links.py`, which walks the graph; the self-link is
    also a CHECK here.

    `origin_association_id` is set when the link was added through the setup
    hint ("Battery has its own dependencies: Charger ..."): it points at the
    battery's link to the charger, so a later "dependencies changed, review?"
    notice (open, not built) can find every parent that copied it.

    Removing a link sets `is_active` false; there is no DELETE. The unique
    index is partial, so the same pair can be linked again afterwards.
    """

    __tablename__ = "device_associations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    associated_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_per_parent: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    booking_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=ASSOCIATION_ALWAYS
    )
    pricing: Mapped[str] = mapped_column(String(8), nullable=False, server_default=PRICING_INCLUDED)
    show_on_documents: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin_association_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_associations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("quantity_per_parent >= 1", name="ck_device_associations_quantity"),
        CheckConstraint(
            "booking_mode IN ('always', 'ask')", name="ck_device_associations_booking_mode"
        ),
        CheckConstraint("pricing IN ('included', 'charged')", name="ck_device_associations_pricing"),
        CheckConstraint(
            "device_id <> associated_device_id", name="ck_device_associations_not_self"
        ),
        Index(
            "uq_device_associations_pair_active",
            "device_id",
            "associated_device_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_device_associations_device", "device_id"),
        Index("ix_device_associations_associated", "associated_device_id"),
        Index("ix_device_associations_origin", "origin_association_id"),
    )


class DeviceAlternative(CompanyScoped, Base):
    """`alternative_device` can stand in for `device` — offered when the
    device is overbooked or somebody wants something similar (SCOPE §4.4.5).

    **One-directional**: the Alexa 35 listing the Venice 2 does not make the
    Venice list the Alexa; the screen offers "also add the reverse". An
    ordered list per device (`position`), each with an optional note
    ("needs a different mount") and a free-text price hint ("about +150 per
    day"). Used by B2c's overbooking popup; never booked automatically.

    Removing sets `is_active` false; there is no DELETE.
    """

    __tablename__ = "device_alternatives"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    alternative_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_hint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "device_id <> alternative_device_id", name="ck_device_alternatives_not_self"
        ),
        Index(
            "uq_device_alternatives_pair_active",
            "device_id",
            "alternative_device_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_device_alternatives_device", "device_id"),
        Index("ix_device_alternatives_alternative", "alternative_device_id"),
    )
