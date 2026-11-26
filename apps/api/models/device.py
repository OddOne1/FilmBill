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

**Nothing is deleted.** A device is archived (`is_active`), a unit entered by
mistake is retired with a note. Neither table has a DELETE route.

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
UNIT_IN_REPAIR = "in_repair"
UNIT_RETIRED = "retired"
UNIT_STATUSES = (UNIT_ACTIVE, UNIT_IN_REPAIR, UNIT_RETIRED)

#: Why a unit left. Optional: a unit entered by mistake is retired with a
#: note and no reason, rather than being made to claim it was sold.
RETIRED_REASONS = ("destroyed", "lost", "sold", "kept")


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
    #: device's units, retired ones included: a retired unit coming back is
    #: reactivated, not entered twice.
    serial_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=UNIT_ACTIVE
    )
    acquired_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    retired_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    retired_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
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
            "status IN ('active', 'in_repair', 'retired')",
            name="ck_device_units_status",
        ),
        CheckConstraint(
            "retired_reason IS NULL OR retired_reason IN "
            "('destroyed', 'lost', 'sold', 'kept')",
            name="ck_device_units_retired_reason",
        ),
        # A reason or a date only on a retired unit: an active unit that
        # says it was sold is a contradiction nobody could act on.
        CheckConstraint(
            "status = 'retired' OR (retired_reason IS NULL AND retired_on IS NULL)",
            name="ck_device_units_retired_fields",
        ),
        Index(
            "uq_device_units_device_serial",
            "device_id",
            "serial_key",
            unique=True,
        ),
        Index("ix_device_units_device", "device_id"),
    )
