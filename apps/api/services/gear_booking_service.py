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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..billing.planning import Holding, PeriodAvailability, period
from ..models.device import (
    ASSOCIATION_ALWAYS,
    ASSOCIATION_ASK,
    Device,
    DeviceAlternative,
    DeviceAssociation,
)
from ..models.gear import (
    GEAR_BOOKED,
    GEAR_CANCELLED,
    GEAR_SCHEDULED,
    HOLDING_STATUSES,
    GearBooking,
    GearBookingDay,
)
from ..models.project import Project, ProjectBlock
from . import audit
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


# ─── Overbooking warnings ───────────────────────────────────────────────────
#
# A booking is overbooked when, on one of its days, the device has fewer
# free than the booking's quantity — counting every OTHER booking (the
# booking never counts against itself). The save is never refused for it.


@dataclass
class AlternativeView:
    """An alternative device, with its own availability on the same days."""

    row: DeviceAlternative
    device: Device
    #: Its own numbers on the booking's days.
    numbers: PeriodAvailability


@dataclass
class HeldBy:
    holder: Holder
    dates: list[date]


@dataclass
class Overbooking:
    booking: GearBooking
    device: Device
    #: The device's numbers on the booking's days WITHOUT this booking.
    numbers: PeriodAvailability
    shortage: int
    worst_day: date
    short_dates: list[date]
    held_by: list[HeldBy]
    alternatives: list[AlternativeView]

    @property
    def message(self) -> str:
        """B1.2: names the device, says what is wrong and what to do."""
        others = ", ".join(
            f"{h.holder.project_name} (strip “{h.holder.strip_name}”, {h.holder.status} {h.holder.quantity})"
            for h in self.held_by
        )
        held = f" {others} {'holds' if len(self.held_by) == 1 else 'hold'} it there." if others else ""
        return (
            f"{self.device.name} is overbooked by {self.shortage} on {say_dates(self.short_dates)}: "
            f"{self.device.quantity_owned} owned, {self.booking.quantity} wanted here.{held} "
            "Swap to an alternative, or book anyway with a note."
        )


def say_dates(dates: Iterable[date]) -> str:
    from .crew_assignment_service import say_dates as _say

    return _say(dates)


def alternatives_of(db: Session, company_id: uuid.UUID, device_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[tuple[DeviceAlternative, Device]]]:
    """Active alternatives of each device, in their order, active devices only."""
    ids = list(set(device_ids))
    out: dict[uuid.UUID, list[tuple[DeviceAlternative, Device]]] = {did: [] for did in ids}
    if not ids:
        return out
    rows = (
        db.query(DeviceAlternative, Device)
        .join(Device, Device.id == DeviceAlternative.alternative_device_id)
        .filter(
            DeviceAlternative.company_id == company_id,
            DeviceAlternative.device_id.in_(ids),
            DeviceAlternative.is_active.is_(True),
            Device.is_active.is_(True),
        )
        .order_by(DeviceAlternative.position, DeviceAlternative.created_at)
        .all()
    )
    for alt, device in rows:
        out[alt.device_id].append((alt, device))
    return out


def overbookings(
    db: Session, company_id: uuid.UUID, rows: list[GearBooking]
) -> dict[uuid.UUID, Optional[Overbooking]]:
    """For each booking that holds its device: the overbooking, or None.
    A fixed number of queries for any number of bookings."""
    out: dict[uuid.UUID, Optional[Overbooking]] = {row.id: None for row in rows}
    holding = [row for row in rows if row.status in HOLDING_STATUSES]
    if not holding:
        return out
    days = active_dates(db, [row.id for row in holding])
    all_dates = [d for row in holding for d in days[row.id]]
    if not all_dates:
        return out
    devices = {d.id: d for d in db.query(Device).filter(Device.id.in_({r.device_id for r in holding}))}
    alts = alternatives_of(db, company_id, devices)
    alt_devices = {device.id: device for pairs in alts.values() for _, device in pairs}
    loaded = load(db, company_id, set(devices) | set(alt_devices), min(all_dates), max(all_dates))
    for row in holding:
        dates = days[row.id]
        if not dates:
            continue
        device = devices[row.device_id]
        numbers = period(device.quantity_owned, loaded.by_device.get(device.id, {}), dates, exclude=row.id)
        shortage = max(0, row.quantity - numbers.min_available)
        if shortage == 0:
            continue
        short = [d for d in numbers.days if d.available < row.quantity]
        held: dict[uuid.UUID, HeldBy] = {}
        for entry in short:
            for holding_row in entry.holdings:
                info = loaded.holders[holding_row.booking_id]
                held.setdefault(info.booking_id, HeldBy(holder=info, dates=[])).dates.append(entry.date)
        out[row.id] = Overbooking(
            booking=row,
            device=device,
            numbers=numbers,
            shortage=shortage,
            worst_day=numbers.worst_day,
            short_dates=[d.date for d in short],
            held_by=list(held.values()),
            alternatives=[
                AlternativeView(
                    row=alt,
                    device=alt_device,
                    numbers=period(
                        alt_device.quantity_owned, loaded.by_device.get(alt_device.id, {}), dates
                    ),
                )
                for alt, alt_device in alts.get(device.id, [])
            ],
        )
    return out


# ─── Writing: days ──────────────────────────────────────────────────────────


def default_dates(db: Session, block: ProjectBlock) -> list[date]:
    """Every day of the strip that is not `off` — the crew rule, shared."""
    from .crew_assignment_service import default_dates as _default

    return _default(db, block)


def check_dates(block: ProjectBlock, dates: list[date], device_name: str) -> list[date]:
    seen: set[date] = set()
    for when in dates:
        if when < block.start_date or when > block.end_date:
            raise GearBookingError(
                f"{say(when)} is outside the strip “{strip_name(block)}” "
                f"({say(block.start_date)} – {say(block.end_date)}). Choose days "
                "inside the strip, or change the strip first."
            )
        if when in seen:
            raise GearBookingError(
                f"{say(when)} is listed twice for {device_name}. List each day once."
            )
        seen.add(when)
    return sorted(seen)


def _write_days(db: Session, row: GearBooking, dates: list[date]) -> None:
    """Make the active days exactly `dates`: untick the rest, reactivate or
    add the wished ones."""
    existing = (
        db.query(GearBookingDay)
        .filter(GearBookingDay.booking_id == row.id)
        .order_by(GearBookingDay.is_active.desc(), GearBookingDay.created_at)
        .all()
    )
    by_date: dict[date, GearBookingDay] = {}
    for day in existing:
        by_date.setdefault(day.date, day)
    wanted = set(dates)
    for day in existing:
        if day.is_active and day.date not in wanted:
            day.is_active = False
    db.flush()
    for when in sorted(wanted):
        day = by_date.get(when)
        if day is None:
            db.add(GearBookingDay(company_id=row.company_id, booking_id=row.id, date=when, is_active=True))
        else:
            day.is_active = True
    db.flush()


# ─── Writing: the dependency step and creating ──────────────────────────────

BOOKING_FIELDS = (
    "project_id",
    "block_id",
    "device_id",
    "quantity",
    "status",
    "status_note",
    "booked_on",
    "booked_by",
    "added_via_booking_id",
    "association_id",
    "quantity_per_parent",
    "follows_parent",
    "overbooking_note",
    "removal_note",
    "days",
)


def _snapshot(db: Session, row: GearBooking, *, with_days: bool = True) -> dict:
    out = {name: getattr(row, name) for name in BOOKING_FIELDS if name != "days"}
    if with_days:
        out["days"] = [d.isoformat() for d in active_dates(db, [row.id])[row.id]]
    return out


@dataclass
class Chosen:
    """An `ask` dependency the person ticked, with an optional own quantity."""

    association_id: uuid.UUID
    quantity: Optional[int] = None


@dataclass
class NewBooking:
    device: Device
    quantity: int
    dates: Optional[list[date]] = None
    chosen: list[Chosen] = field(default_factory=list)
    overbooking_note: Optional[str] = None


@dataclass
class Planned:
    """One row the create step will write."""

    device: Device
    quantity: int
    dates: list[date]
    via: str  # "direct" | "always" | "ask"
    parent: Optional[int] = None  # index into Plan.entries
    association: Optional[DeviceAssociation] = None
    follows_parent: bool = False
    overbooking_note: Optional[str] = None


@dataclass
class Suggestion:
    """An `ask` dependency that was not chosen: offered, never added."""

    parent: int
    association: DeviceAssociation
    device: Device
    quantity: int


@dataclass
class Plan:
    entries: list[Planned]
    suggestions: list[Suggestion]


def dependencies_of(
    db: Session, company_id: uuid.UUID, device_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[DeviceAssociation, Device]]]:
    """Each device's OWN active dependency links to active devices. One
    level: nothing here looks at a dependency's own links."""
    ids = list(set(device_ids))
    out: dict[uuid.UUID, list[tuple[DeviceAssociation, Device]]] = {did: [] for did in ids}
    if not ids:
        return out
    rows = (
        db.query(DeviceAssociation, Device)
        .join(Device, Device.id == DeviceAssociation.associated_device_id)
        .filter(
            DeviceAssociation.company_id == company_id,
            DeviceAssociation.device_id.in_(ids),
            DeviceAssociation.is_active.is_(True),
            Device.is_active.is_(True),
        )
        .order_by(DeviceAssociation.created_at, DeviceAssociation.id)
        .all()
    )
    for link, device in rows:
        out[link.device_id].append((link, device))
    return out


def plan_bookings(db: Session, *, block: ProjectBlock, wanted: list[NewBooking]) -> Plan:
    """What booking these devices on the strip adds: each device, its
    `always` dependencies (quantity × per parent, same days), and the `ask`
    ones that were chosen; the `ask` ones not chosen become suggestions.
    One level only. Nothing is written."""
    company_id = block.company_id
    ids = [entry.device.id for entry in wanted]
    if len(set(ids)) != len(ids):
        raise GearBookingError("The same device is listed twice. Add each device once, with its quantity.")
    for entry in wanted:
        if not entry.device.is_active:
            raise GearBookingError(
                f"{entry.device.name} is archived. Reactivate it on the Gear page to book it."
            )
        if entry.quantity < 1:
            raise GearBookingError(f"Book at least 1 of {entry.device.name}.")
    taken = (
        db.query(GearBooking, Device.name)
        .join(Device, Device.id == GearBooking.device_id)
        .filter(
            GearBooking.company_id == company_id,
            GearBooking.block_id == block.id,
            GearBooking.device_id.in_(ids),
            GearBooking.added_via_booking_id.is_(None),
            GearBooking.status != GEAR_CANCELLED,
        )
        .all()
    )
    if taken:
        names = ", ".join(name for _, name in taken)
        raise AlreadyOnStrip(
            f"{names} {'is' if len(taken) == 1 else 'are'} already on the strip "
            f"“{strip_name(block)}”. Change the quantity there instead."
        )
    links = dependencies_of(db, company_id, ids)
    defaults: Optional[list[date]] = None
    plan = Plan(entries=[], suggestions=[])
    for entry in wanted:
        if entry.dates is None:
            if defaults is None:
                defaults = default_dates(db, block)
            dates = list(defaults)
        else:
            dates = check_dates(block, entry.dates, entry.device.name)
        parent_index = len(plan.entries)
        plan.entries.append(
            Planned(
                device=entry.device,
                quantity=entry.quantity,
                dates=dates,
                via="direct",
                overbooking_note=(entry.overbooking_note or "").strip() or None,
            )
        )
        chosen = {c.association_id: c for c in entry.chosen}
        own = {link.id: (link, device) for link, device in links.get(entry.device.id, [])}
        for association_id in chosen:
            link = own.get(association_id, (None, None))[0]
            if link is None or link.booking_mode != ASSOCIATION_ASK:
                raise GearBookingError(
                    f"A chosen dependency is not one {entry.device.name} asks for. "
                    "Reload the list and choose again."
                )
        for link, device in links.get(entry.device.id, []):
            derived = entry.quantity * link.quantity_per_parent
            if link.booking_mode == ASSOCIATION_ALWAYS:
                plan.entries.append(
                    Planned(device=device, quantity=derived, dates=dates, via="always",
                            parent=parent_index, association=link, follows_parent=True)
                )
            elif link.id in chosen:
                own_quantity = chosen[link.id].quantity
                if own_quantity is not None and own_quantity < 1:
                    raise GearBookingError(f"Book at least 1 of {device.name}, or untick it.")
                quantity = own_quantity if own_quantity is not None else derived
                plan.entries.append(
                    Planned(device=device, quantity=quantity, dates=dates, via="ask",
                            parent=parent_index, association=link, follows_parent=quantity == derived)
                )
            else:
                plan.suggestions.append(
                    Suggestion(parent=parent_index, association=link, device=device, quantity=derived)
                )
    return plan


def create_bookings(
    db: Session,
    *,
    project: Project,
    block: ProjectBlock,
    wanted: list[NewBooking],
    actor: audit.Actor,
) -> tuple[list[GearBooking], Plan]:
    """Several devices on one strip at once, with their dependencies, all or
    none, `scheduled`. One `gear_booking.created` per device added directly;
    the dependencies of each are ONE event listing the rows. No commit."""
    plan = plan_bookings(db, block=block, wanted=wanted)
    made: list[GearBooking] = []
    for entry in plan.entries:
        parent = made[entry.parent] if entry.parent is not None else None
        row = GearBooking(
            company_id=project.company_id,
            project_id=project.id,
            block_id=block.id,
            device_id=entry.device.id,
            quantity=entry.quantity,
            status=parent.status if parent is not None else GEAR_SCHEDULED,
            added_via_booking_id=parent.id if parent is not None else None,
            association_id=entry.association.id if entry.association is not None else None,
            quantity_per_parent=entry.association.quantity_per_parent if entry.association is not None else None,
            follows_parent=entry.follows_parent,
            overbooking_note=entry.overbooking_note,
        )
        db.add(row)
        db.flush()
        _write_days(db, row, entry.dates)
        if parent is None:
            audit.record(db, actor, row, "gear_booking.created", None, _snapshot(db, row), fields=BOOKING_FIELDS)
        made.append(row)
    for index, entry in enumerate(plan.entries):
        if entry.parent is not None:
            continue
        dependents = [
            (made[i], plan.entries[i]) for i in range(len(plan.entries)) if plan.entries[i].parent == index
        ]
        if not dependents:
            continue
        audit.record(
            db,
            actor,
            None,
            "gear_booking.created",
            None,
            {
                "added_via_booking_id": made[index].id,
                "count": len(dependents),
                "bookings": [
                    {
                        "booking_id": str(row.id),
                        "device_id": str(row.device_id),
                        "quantity": row.quantity,
                        "via": planned.via,
                        "days": [d.isoformat() for d in planned.dates],
                    }
                    for row, planned in dependents
                ],
            },
            fields=("added_via_booking_id", "count", "bookings"),
            entity_type="gear_bookings",
            entity_id=made[index].id,
            company_id=project.company_id,
        )
    return made, plan


# ─── Writing: changes ───────────────────────────────────────────────────────


@dataclass
class UpdateResult:
    #: Dependents whose quantity followed a parent's new quantity.
    recalculated: list[GearBooking] = field(default_factory=list)
    #: True when a dependent's quantity or days were set by hand just now.
    stopped_following: bool = False


def open_dependents(db: Session, row: GearBooking) -> list[GearBooking]:
    return (
        db.query(GearBooking)
        .filter(
            GearBooking.company_id == row.company_id,
            GearBooking.added_via_booking_id == row.id,
            GearBooking.status != GEAR_CANCELLED,
        )
        .order_by(GearBooking.created_at, GearBooking.id)
        .all()
    )


def _check_route_free(db: Session, row: GearBooking, device: Device) -> None:
    query = db.query(GearBooking.id).filter(
        GearBooking.company_id == row.company_id,
        GearBooking.block_id == row.block_id,
        GearBooking.device_id == device.id,
        GearBooking.status != GEAR_CANCELLED,
        GearBooking.id != row.id,
    )
    if row.added_via_booking_id is None:
        query = query.filter(GearBooking.added_via_booking_id.is_(None))
    else:
        query = query.filter(GearBooking.added_via_booking_id == row.added_via_booking_id)
    if query.first() is not None:
        raise AlreadyOnStrip(
            f"{device.name} is already on this strip. Change the quantity there instead of swapping."
        )


def update_booking(
    db: Session,
    *,
    row: GearBooking,
    block: ProjectBlock,
    changes: dict,
    device: Optional[Device],
    actor: audit.Actor,
) -> UpdateResult:
    """Quantity, days, the device (a swap) and the overbooking note.

    A parent's new quantity recalculates the dependents that still follow
    it (`quantity × per parent`, one `gear_booking.updated` event listing
    them); a dependent's own quantity or days set by hand ends
    `follows_parent`. A swap keeps days and status and is audited with the
    device before and after."""
    if row.status == GEAR_CANCELLED:
        raise GearBookingError(
            "This booking was removed. Add the device to the strip again to plan with it."
        )
    result = UpdateResult()
    before = _snapshot(db, row, with_days=False)
    if "device_id" in changes and device is not None and device.id != row.device_id:
        if not device.is_active:
            raise GearBookingError(f"{device.name} is archived. Reactivate it on the Gear page to book it.")
        _check_route_free(db, row, device)
        row.device_id = device.id
    if "quantity" in changes and changes["quantity"] is not None and changes["quantity"] != row.quantity:
        if changes["quantity"] < 1:
            raise GearBookingError("Book at least 1, or remove the booking.")
        row.quantity = changes["quantity"]
        if row.added_via_booking_id is not None and row.follows_parent:
            row.follows_parent = False
            result.stopped_following = True
    if "overbooking_note" in changes:
        row.overbooking_note = (changes["overbooking_note"] or "").strip() or None
    db.flush()
    after = _snapshot(db, row, with_days=False)
    if before != after:
        audit.record(db, actor, row, "gear_booking.updated", before, after, fields=BOOKING_FIELDS)
    if "dates" in changes and changes["dates"] is not None:
        name = db.get(Device, row.device_id).name
        dates = check_dates(block, changes["dates"], name)
        days_before = [d.isoformat() for d in active_dates(db, [row.id])[row.id]]
        _write_days(db, row, dates)
        days_after = [d.isoformat() for d in active_dates(db, [row.id])[row.id]]
        if days_before != days_after:
            audit.record(
                db, actor, row, "gear_booking.days_set", {"days": days_before}, {"days": days_after}, fields=("days",)
            )
            if row.added_via_booking_id is not None and row.follows_parent:
                row.follows_parent = False
                result.stopped_following = True
                db.flush()
    if before["quantity"] != row.quantity and row.added_via_booking_id is None:
        changed = []
        for dependent in open_dependents(db, row):
            if not dependent.follows_parent or dependent.quantity_per_parent is None:
                continue
            wanted = row.quantity * dependent.quantity_per_parent
            if wanted != dependent.quantity:
                changed.append({"booking_id": str(dependent.id), "before": dependent.quantity, "after": wanted})
                dependent.quantity = wanted
                result.recalculated.append(dependent)
        db.flush()
        if changed:
            audit.record(
                db,
                actor,
                None,
                "gear_booking.updated",
                None,
                {"added_via_booking_id": row.id, "count": len(changed), "recalculated": changed},
                fields=("added_via_booking_id", "count", "recalculated"),
                entity_type="gear_bookings",
                entity_id=row.id,
                company_id=row.company_id,
            )
    return result


# ─── Writing: status ────────────────────────────────────────────────────────

#: Remove (→ cancelled) is its own action.
TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {(GEAR_SCHEDULED, GEAR_BOOKED), (GEAR_BOOKED, GEAR_SCHEDULED)}
)


def transition_error(name: str, current: str, wanted: str) -> Optional[str]:
    if (current, wanted) in TRANSITIONS:
        return None
    if current == wanted:
        return f"{name} is already {current} on this strip."
    if current == GEAR_CANCELLED:
        return f"{name} was removed from this strip. Add it again to plan with it."
    if wanted == GEAR_CANCELLED:
        return f"To cancel {name}, remove the booking (you can add a note why)."
    return f"{name} is {current} on this strip, so it cannot be changed to {wanted}."


def _apply_status(row: GearBooking, wanted: str, note: Optional[str], user_id) -> None:
    row.status = wanted
    row.status_note = note
    if wanted == GEAR_BOOKED:
        row.booked_on = datetime.now(timezone.utc)
        row.booked_by = user_id
    else:
        row.booked_on = None
        row.booked_by = None


def set_status(db: Session, *, row: GearBooking, name: str, wanted: str, note: Optional[str], actor: audit.Actor) -> None:
    note = (note or "").strip() or None
    problem = transition_error(name, row.status, wanted)
    if problem:
        raise GearBookingError(problem)
    if row.status == GEAR_BOOKED and wanted == GEAR_SCHEDULED and not note:
        raise GearBookingError(f"Reopening the booking of {name} needs a note saying why. Add one, then reopen.")
    before = _snapshot(db, row, with_days=False)
    _apply_status(row, wanted, note, actor.user_id)
    db.flush()
    audit.record(
        db, actor, row, "gear_booking.status_changed", before, _snapshot(db, row, with_days=False), fields=BOOKING_FIELDS
    )


@dataclass
class BulkResult:
    changed: list[GearBooking]
    skipped: list[tuple[GearBooking, str]]


def set_status_bulk(
    db: Session,
    *,
    rows: list[tuple[GearBooking, str]],
    wanted: str,
    note: Optional[str],
    actor: audit.Actor,
    project: Project,
) -> BulkResult:
    """"Mark selected as booked": the allowed ones change, the others are
    listed with the reason. ONE audit event with the counts."""
    note = (note or "").strip() or None
    result = BulkResult(changed=[], skipped=[])
    for row, name in rows:
        problem = transition_error(name, row.status, wanted)
        if problem is None and row.status == GEAR_BOOKED and wanted == GEAR_SCHEDULED and not note:
            problem = f"Reopening the booking of {name} needs a note saying why."
        if problem:
            result.skipped.append((row, problem))
            continue
        _apply_status(row, wanted, note, actor.user_id)
        result.changed.append(row)
    db.flush()
    audit.record(
        db,
        actor,
        None,
        "gear_booking.status_changed",
        None,
        {
            "status": wanted,
            "status_note": note,
            "changed": len(result.changed),
            "skipped": len(result.skipped),
            "booking_ids": [row.id for row in result.changed],
            "project_id": project.id,
        },
        fields=("status", "status_note", "changed", "skipped", "booking_ids", "project_id"),
        entity_type="projects",
        entity_id=project.id,
        company_id=project.company_id,
    )
    return result


# ─── Writing: removing ──────────────────────────────────────────────────────

DEPENDENTS_CANCEL = "cancel"
DEPENDENTS_KEEP = "keep"


@dataclass
class RemoveResult:
    removed: bool
    #: The parent's open dependents: the question, or what the answer did.
    dependents: list[GearBooking]
    choice: Optional[str] = None


def remove_booking(
    db: Session,
    *,
    row: GearBooking,
    note: Optional[str],
    dependents_choice: Optional[str],
    actor: audit.Actor,
) -> RemoveResult:
    """Remove = `cancelled` with an optional note; the row and its days are
    kept. **A parent with open dependents is not removed until the person
    says what happens to them** — `cancel` them too, or `keep` them as
    bookings of their own (they stop following). Without the answer nothing
    changes and the dependents are returned as the question."""
    note = (note or "").strip() or None
    if row.status == GEAR_CANCELLED:
        return RemoveResult(removed=True, dependents=[])
    dependents = open_dependents(db, row)
    if dependents and dependents_choice not in (DEPENDENTS_CANCEL, DEPENDENTS_KEEP):
        return RemoveResult(removed=False, dependents=dependents)
    before = _snapshot(db, row, with_days=False)
    row.status = GEAR_CANCELLED
    row.removal_note = note
    db.flush()
    after = _snapshot(db, row, with_days=False)
    extra_fields: tuple[str, ...] = ()
    if dependents:
        after = {**after, "dependents": dependents_choice, "dependent_ids": [d.id for d in dependents]}
        extra_fields = ("dependents", "dependent_ids")
    audit.record(db, actor, row, "gear_booking.removed", before, after, fields=BOOKING_FIELDS + extra_fields)
    if dependents and dependents_choice == DEPENDENTS_CANCEL:
        for dependent in dependents:
            dependent.status = GEAR_CANCELLED
            dependent.removal_note = note or "Removed with the device it was added with."
        db.flush()
        audit.record(
            db, actor, None, "gear_booking.removed", None,
            {"added_via_booking_id": row.id, "count": len(dependents), "booking_ids": [d.id for d in dependents]},
            fields=("added_via_booking_id", "count", "booking_ids"),
            entity_type="gear_bookings", entity_id=row.id, company_id=row.company_id,
        )
    elif dependents:
        for dependent in dependents:
            dependent.follows_parent = False
        db.flush()
        audit.record(
            db, actor, None, "gear_booking.updated", None,
            {"added_via_booking_id": row.id, "count": len(dependents), "kept": [d.id for d in dependents],
             "follows_parent": False},
            fields=("added_via_booking_id", "count", "kept", "follows_parent"),
            entity_type="gear_bookings", entity_id=row.id, company_id=row.company_id,
        )
    return RemoveResult(removed=True, dependents=dependents, choice=dependents_choice if dependents else None)
