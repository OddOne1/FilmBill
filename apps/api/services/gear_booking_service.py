"""Gear on the calendar: bookings of devices on strips, and availability (B2c-2).

**Availability is `billing/planning/gear_availability.py` over the rows.**
This module only loads them: every active day of every booking of the
devices asked about, on an active strip, between two dates — in ANY status.
The pure module drops `cancelled`, never counts the booking being edited
against itself and takes the lowest day of a period; it is the one place
that rule lives, so a change to it is a change to a table of numbers
(`tests/fixtures/gear_availability/`).

No commit anywhere in this module.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..billing.planning import Holding, PeriodAvailability, period
from ..models.device import Device
from ..models.gear import GearBooking, GearBookingDay
from ..models.project import Project, ProjectBlock
from .calendar_service import say

#: A planner wider than this is a different question (the crew grid's limit).
MAX_RANGE_DAYS = 92


class GearBookingError(ValueError):
    """A sentence for the person; surfaces as a 422."""


class AlreadyOnStrip(GearBookingError):
    """Surfaces as a 409."""


def strip_name(block: ProjectBlock) -> str:
    return block.label or block.type.capitalize()


def dates_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def check_range(start: date, end: date) -> None:
    if start > end:
        raise GearBookingError(
            f"The range ends on {say(end)}, before it starts on {say(start)}. Swap the two dates."
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise GearBookingError(
            f"The range is {(end - start).days + 1} days long; the gear planner shows at "
            f"most {MAX_RANGE_DAYS}. Choose a shorter range."
        )


# ─── Who holds a device ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Holder:
    """What a booking is, for the sentence "held by …"."""

    booking_id: uuid.UUID
    device_id: uuid.UUID
    quantity: int
    status: str
    project_id: uuid.UUID
    project_name: str
    project_number: str
    block_id: uuid.UUID
    strip_name: str


@dataclass
class Loaded:
    """Every booking-day of some devices in a range: `device → date →
    holdings` for the pure module, and `booking id → Holder` to name them."""

    by_device: dict[uuid.UUID, dict[date, list[Holding]]]
    holders: dict[uuid.UUID, Holder]


def load(
    db: Session,
    company_id: uuid.UUID,
    device_ids: Iterable[uuid.UUID],
    start: date,
    end: date,
) -> Loaded:
    """One query for any number of devices. Cancelled bookings are loaded
    too and dropped by the pure module, which owns that rule; a booking on
    a removed strip, or an unticked day, holds nothing and is not loaded."""
    ids = list(set(device_ids))
    out = Loaded(by_device={did: {} for did in ids}, holders={})
    if not ids:
        return out
    rows = (
        db.query(GearBookingDay.date, GearBooking, Project, ProjectBlock)
        .join(GearBooking, GearBooking.id == GearBookingDay.booking_id)
        .join(Project, Project.id == GearBooking.project_id)
        .join(ProjectBlock, ProjectBlock.id == GearBooking.block_id)
        .filter(
            GearBookingDay.company_id == company_id,
            GearBooking.company_id == company_id,
            GearBooking.device_id.in_(ids),
            GearBookingDay.is_active.is_(True),
            ProjectBlock.is_active.is_(True),
            GearBookingDay.date >= start,
            GearBookingDay.date <= end,
        )
        .order_by(GearBookingDay.date, GearBooking.created_at, GearBooking.id)
        .all()
    )
    for when, booking, project, block in rows:
        out.by_device[booking.device_id].setdefault(when, []).append(
            Holding(booking_id=booking.id, quantity=booking.quantity, status=booking.status)
        )
        out.holders.setdefault(
            booking.id,
            Holder(
                booking_id=booking.id,
                device_id=booking.device_id,
                quantity=booking.quantity,
                status=booking.status,
                project_id=project.id,
                project_name=project.name,
                project_number=project.project_number,
                block_id=block.id,
                strip_name=strip_name(block),
            ),
        )
    return out


def availability(
    db: Session,
    company_id: uuid.UUID,
    devices: list[Device],
    dates: list[date],
    *,
    exclude: Optional[uuid.UUID] = None,
    loaded: Optional[Loaded] = None,
) -> dict[uuid.UUID, PeriodAvailability]:
    """`device id → its numbers over these dates`, the lowest day governing.
    `exclude` is the booking being edited."""
    if not devices:
        return {}
    if loaded is None:
        loaded = (
            load(db, company_id, [d.id for d in devices], min(dates), max(dates))
            if dates
            else Loaded(by_device={}, holders={})
        )
    return {
        device.id: period(
            device.quantity_owned,
            loaded.by_device.get(device.id, {}),
            dates,
            exclude=exclude,
        )
        for device in devices
    }


# ─── Reading bookings ───────────────────────────────────────────────────────


def active_dates(db: Session, booking_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[date]]:
    ids = list(booking_ids)
    out: dict[uuid.UUID, list[date]] = {bid: [] for bid in ids}
    if not ids:
        return out
    for row in (
        db.query(GearBookingDay)
        .filter(GearBookingDay.booking_id.in_(ids), GearBookingDay.is_active.is_(True))
        .order_by(GearBookingDay.date)
    ):
        out[row.booking_id].append(row.date)
    return out
