"""Gear: devices and their units (B2b part 2). No commit anywhere here.

**The quantity and the serial numbers are independent.** The only functions
that write `quantity_owned` are `set_quantity` (by hand), `remove_unit` with
`reduce_quantity`, and `reactivate_unit` with `raise_quantity` — and each of
those records the before and after. Adding a serial number does not touch it.

Every write records its audit event in the caller's transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..billing.resources.device_csv import (
    DeviceImportContext,
    DeviceImportPlan,
    clean_serial,
    name_key,
    plan_device_file,
    serial_key,
)
from ..models.catalog import Item, ItemCategory
from ..models.device import (
    UNIT_ACTIVE,
    UNIT_IN_HAND,
    UNIT_REMOVED,
    UNIT_STATUSES,
    Device,
    DeviceUnit,
)
from ..models.party import Party
from . import audit

DEVICE_AUDITED = (
    "name",
    "category",
    "purchase_price",
    "purchase_date",
    "catalog_item_id",
    "notes",
    "is_active",
)
UNIT_AUDITED = (
    "serial_number",
    "status",
    "acquired_on",
    "purchase_price",
    "seller_party_id",
    "seller_name",
    "purchase_reference",
    "removed_on",
    "removed_reason",
    "notes",
)

EQUIPMENT_KIND = "equipment_rental"


class DeviceError(ValueError):
    """A sentence for the person; the router picks 409 or 422."""


class DeviceConflict(DeviceError):
    """Refused because of another row (a name in use); a 409."""


class DigestMismatch(DeviceError):
    pass


# ─── Counts ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Counts:
    owned: int
    by_status: dict

    def of(self, status: str) -> int:
        return self.by_status.get(status, 0)

    @property
    def recorded(self) -> int:
        return sum(self.by_status.values())

    @property
    def in_hand(self) -> int:
        return sum(self.of(status) for status in UNIT_IN_HAND)

    @property
    def removed(self) -> int:
        return sum(self.of(status) for status in UNIT_REMOVED)

    @property
    def not_yet_recorded(self) -> int:
        return max(0, self.owned - self.in_hand)

    @property
    def beyond_quantity(self) -> int:
        return max(0, self.in_hand - self.owned)

    def as_dict(self) -> dict:
        return {
            "owned": self.owned,
            "recorded": self.recorded,
            **{status: self.of(status) for status in UNIT_STATUSES},
            "in_hand": self.in_hand,
            "removed": self.removed,
            "not_yet_recorded": self.not_yet_recorded,
            "beyond_quantity": self.beyond_quantity,
        }


def counts_for(db: Session, devices: Iterable[Device]) -> dict[uuid.UUID, Counts]:
    """The counts of every device given, in ONE query."""
    rows = list(devices)
    by_status: dict[uuid.UUID, dict[str, int]] = {row.id: {} for row in rows}
    if rows:
        for device_id, status, number in (
            db.query(DeviceUnit.device_id, DeviceUnit.status, func.count())
            .filter(DeviceUnit.device_id.in_([row.id for row in rows]))
            .group_by(DeviceUnit.device_id, DeviceUnit.status)
            .all()
        ):
            by_status[device_id][status] = number
    return {row.id: Counts(owned=row.quantity_owned, by_status=dict(by_status[row.id])) for row in rows}


# ─── Lookups and suggestions ────────────────────────────────────────────────


def category_suggestions(db: Session, company_id: uuid.UUID) -> list[str]:
    """Categories already on devices (most used first), then the catalog's
    active equipment categories that are not among them."""
    used = [
        row[0]
        for row in db.query(Device.category, func.count())
        .filter(Device.company_id == company_id, Device.category.isnot(None))
        .group_by(Device.category)
        .order_by(func.count().desc(), Device.category)
        .all()
    ]
    keys = {value.casefold() for value in used}
    catalog = [
        row[0]
        for row in db.query(ItemCategory.name)
        .filter(
            ItemCategory.company_id == company_id,
            ItemCategory.kind == EQUIPMENT_KIND,
            ItemCategory.is_active.is_(True),
        )
        .order_by(ItemCategory.name)
        .all()
    ]
    return used + [name for name in catalog if name.casefold() not in keys]


def check_seller(db: Session, company_id: uuid.UUID, party_id: Optional[uuid.UUID]) -> Optional[Party]:
    """The seller party: this company's, or "not found" (the caller's 404)."""
    if party_id is None:
        return None
    party = db.query(Party).filter(Party.id == party_id, Party.company_id == company_id).first()
    if party is None:
        raise LookupError("Seller not found")
    return party


def _clean_text(value: Optional[str]) -> Optional[str]:
    return " ".join((value or "").split()) or None


def _refuse_name_in_use(
    db: Session, company_id: uuid.UUID, name: str, exclude: Optional[uuid.UUID] = None
) -> None:
    query = db.query(Device).filter(
        Device.company_id == company_id,
        Device.is_active.is_(True),
        Device.name_key == name_key(name),
    )
    if exclude is not None:
        query = query.filter(Device.id != exclude)
    other = query.first()
    if other is not None:
        raise DeviceConflict(
            f"You already have a device called {other.name}. Give this one "
            f"another name, or add the units to {other.name}."
        )


# ─── Devices ────────────────────────────────────────────────────────────────


def _refuse_negative_price(price: Optional[Decimal]) -> None:
    if price is not None and price < 0:
        raise DeviceError("A purchase price cannot be negative. Enter the net amount you paid.")


def create_device(
    db: Session,
    *,
    company_id: uuid.UUID,
    actor: audit.Actor,
    name: str,
    category: Optional[str] = None,
    quantity_owned: int = 0,
    purchase_price: Optional[Decimal] = None,
    purchase_date: Optional[date] = None,
    notes: Optional[str] = None,
    source: Optional[str] = None,
) -> Device:
    clean = " ".join(name.split())
    if not clean:
        raise DeviceError("This device has no name. Type one in, such as Alexa 35.")
    _refuse_name_in_use(db, company_id, clean)
    _refuse_negative_price(purchase_price)
    device = Device(
        company_id=company_id,
        name=clean,
        name_key=name_key(clean),
        category=(" ".join((category or "").split()) or None),
        quantity_owned=quantity_owned,
        purchase_price=purchase_price,
        purchase_date=purchase_date,
        notes=(notes or "").strip() or None,
        is_active=True,
    )
    db.add(device)
    db.flush()
    after = audit.snapshot(device, DEVICE_AUDITED + ("quantity_owned",))
    if source:
        after["source"] = source
    audit.record(
        db,
        actor,
        device,
        "device.created",
        None,
        after,
        fields=DEVICE_AUDITED + ("quantity_owned", "source"),
    )
    return device


def update_device(db: Session, device: Device, *, actor: audit.Actor, changes: dict) -> None:
    """Everything but the quantity; `set_quantity` records that one."""
    quantity = changes.pop("quantity_owned", None)
    before = audit.snapshot(device, DEVICE_AUDITED)
    if "name" in changes and changes["name"] is not None:
        clean = " ".join(changes["name"].split())
        if not clean:
            raise DeviceError("A device needs a name.")
        if device.is_active:
            _refuse_name_in_use(db, device.company_id, clean, exclude=device.id)
        device.name = clean
        device.name_key = name_key(clean)
    if "category" in changes:
        device.category = " ".join((changes["category"] or "").split()) or None
    if "purchase_price" in changes:
        _refuse_negative_price(changes["purchase_price"])
    for field in ("purchase_price", "purchase_date"):
        if field in changes:
            setattr(device, field, changes[field])
    if "notes" in changes:
        device.notes = (changes["notes"] or "").strip() or None
    after = audit.snapshot(device, DEVICE_AUDITED)
    if audit.diff(before, after, fields=DEVICE_AUDITED):
        audit.record(db, actor, device, "device.updated", before, after, fields=DEVICE_AUDITED)
    if quantity is not None:
        set_quantity(db, device, actor=actor, quantity=quantity)


def set_quantity(db: Session, device: Device, *, actor: audit.Actor, quantity: int) -> None:
    """By hand, always allowed, whatever the serial numbers say."""
    if quantity < 0:
        raise DeviceError("The quantity owned cannot be below 0.")
    if quantity == device.quantity_owned:
        return
    before = device.quantity_owned
    device.quantity_owned = quantity
    audit.record(
        db,
        actor,
        device,
        "device.quantity_set",
        {"quantity_owned": before},
        {"quantity_owned": quantity},
        fields=("quantity_owned",),
    )


def archive_device(db: Session, device: Device, *, actor: audit.Actor) -> None:
    if not device.is_active:
        return
    device.is_active = False
    audit.record(
        db, actor, device, "device.archived", {"is_active": True}, {"is_active": False}, fields=("is_active",)
    )


def reactivate_device(db: Session, device: Device, *, actor: audit.Actor) -> None:
    if device.is_active:
        return
    _refuse_name_in_use(db, device.company_id, device.name, exclude=device.id)
    device.is_active = True
    audit.record(
        db, actor, device, "device.updated", {"is_active": False}, {"is_active": True}, fields=("is_active",)
    )


# ─── Rental prices: the linked catalog item's rates (B2b-2) ─────────────────
#
# A device has no prices of its own. Its price per day, cost per day and
# rental factor table ARE the rates of the equipment item it is linked to,
# read and written through `catalog_service`, so a budget line made from the
# item and the device page can never disagree. There is no picker of other
# items: a device either gets a new item of its own name ("Set up rental
# price") or, when an unlinked equipment item of exactly that name exists,
# that one ("Use the existing ...") — never a second item of the same name.


def linked_item(db: Session, device: Device) -> Optional[Item]:
    if device.catalog_item_id is None:
        return None
    return (
        db.query(Item)
        .filter(Item.id == device.catalog_item_id, Item.company_id == device.company_id)
        .first()
    )


def existing_item_for(db: Session, device: Device) -> Optional[Item]:
    """An active equipment item named exactly like the device (case and
    spacing aside) that no device of this company is linked to yet."""
    taken = db.query(Device.catalog_item_id).filter(
        Device.company_id == device.company_id,
        Device.catalog_item_id.isnot(None),
        Device.id != device.id,
    )
    wanted = name_key(device.name)
    candidates = (
        db.query(Item)
        .filter(
            Item.company_id == device.company_id,
            Item.kind == EQUIPMENT_KIND,
            Item.is_active.is_(True),
            Item.id.notin_(taken),
        )
        .order_by(Item.created_at, Item.id)
        .all()
    )
    return next((item for item in candidates if name_key(item.name_de) == wanted), None)


def set_up_rental(db: Session, device: Device, *, actor: audit.Actor) -> tuple[Item, bool]:
    """Link the device to its catalog item. `(item, created)`.

    The unlinked equipment item of the same name when there is one, else a
    new one (the catalog's `item.created`). Either way `device.catalog_linked`.
    """
    from . import catalog_service

    if device.catalog_item_id is not None:
        item = linked_item(db, device)
        raise DeviceConflict(
            f"{device.name} already has its rental price in the catalog"
            + (f" as {item.name_de}." if item else ".")
            + " Change the prices on this page."
        )
    item = existing_item_for(db, device)
    created = item is None
    if item is None:
        item = catalog_service.create_equipment_item(
            db,
            company_id=device.company_id,
            name=device.name,
            actor=actor,
            reason=f"Set up as the rental price of the device {device.name}",
        )
    device.catalog_item_id = item.id
    db.flush()
    audit.record(
        db,
        actor,
        device,
        "device.catalog_linked",
        {"catalog_item_id": None},
        {"catalog_item_id": item.id, "item_name": item.name_de, "item_created": created},
        fields=("catalog_item_id", "item_name", "item_created"),
    )
    return item, created


def update_rental(
    db: Session,
    device: Device,
    *,
    actor: audit.Actor,
    currency: str,
    changes: dict,
) -> Item:
    """Write the device's rates — that is, its catalog item's. `changes`
    holds only what was sent: `price_per_day`, `cost_per_day`,
    `rental_factor_group_id`; `None` removes."""
    from . import catalog_service

    item = linked_item(db, device)
    if item is None:
        raise DeviceError(
            f"{device.name} has no rental price yet. Click Set up rental price first."
        )
    for field in ("price_per_day", "cost_per_day"):
        value = changes.get(field)
        if value is not None and value < 0:
            raise DeviceError(
                "A rental price cannot be negative. Enter the net amount per day."
            )
    try:
        catalog_service.update_day_pricing(
            db,
            item=item,
            actor=actor,
            currency=currency,
            bill_day=changes.get("price_per_day", catalog_service.UNCHANGED),
            cost_day=changes.get("cost_per_day", catalog_service.UNCHANGED),
            rental_factor_group_id=changes.get("rental_factor_group_id", catalog_service.UNCHANGED),
            reason=f"Changed on the device page of {device.name}",
        )
    except catalog_service.CatalogError as error:
        raise DeviceError(str(error)) from None
    return item


# ─── Units ──────────────────────────────────────────────────────────────────


def add_units(
    db: Session,
    device: Device,
    *,
    actor: audit.Actor,
    serial_numbers: Iterable[str],
    acquired_on: Optional[date] = None,
    notes: Optional[str] = None,
    purchase_price: Optional[Decimal] = None,
    seller_party_id: Optional[uuid.UUID] = None,
    seller_name: Optional[str] = None,
    purchase_reference: Optional[str] = None,
) -> tuple[list[DeviceUnit], list[dict]]:
    """Record serial numbers. Repeats and serials already on this device
    are listed and skipped, never an error. `quantity_owned` is not read or
    written here — that is the whole point of the separation.

    The purchase details, when given, apply to every serial of the list:
    a pasted list is one purchase.
    """
    _refuse_negative_price(purchase_price)
    check_seller(db, device.company_id, seller_party_id)
    existing = {
        key: serial
        for key, serial in db.query(DeviceUnit.serial_key, DeviceUnit.serial_number).filter(
            DeviceUnit.device_id == device.id
        )
    }
    created: list[DeviceUnit] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for raw in serial_numbers:
        serial = clean_serial(raw)
        if not serial:
            continue
        if len(serial) > 128:
            raise DeviceError(
                f"The serial number {serial[:20]!r}… is longer than 128 characters."
            )
        key = serial_key(serial)
        if key in existing:
            skipped.append(
                {
                    "serial_number": serial,
                    "reason": "already_recorded",
                    "detail": f"{existing[key]} is already recorded on {device.name}.",
                }
            )
            continue
        if key in seen:
            skipped.append(
                {
                    "serial_number": serial,
                    "reason": "repeated",
                    "detail": f"{serial} is in the list twice; it was added once.",
                }
            )
            continue
        seen.add(key)
        unit = DeviceUnit(
            company_id=device.company_id,
            device_id=device.id,
            serial_number=serial,
            serial_key=key,
            status=UNIT_ACTIVE,
            acquired_on=acquired_on,
            purchase_price=purchase_price,
            seller_party_id=seller_party_id,
            seller_name=_clean_text(seller_name),
            purchase_reference=_clean_text(purchase_reference),
            notes=(notes or "").strip() or None,
        )
        db.add(unit)
        db.flush()
        audit.record(
            db,
            actor,
            unit,
            "device_unit.created",
            None,
            audit.snapshot(unit, UNIT_AUDITED),
            fields=UNIT_AUDITED,
        )
        created.append(unit)
    return created, skipped


def update_unit(db: Session, unit: DeviceUnit, *, actor: audit.Actor, changes: dict) -> None:
    """Corrections, purchase details, and moving between the statuses of a
    unit in hand. A removed unit comes back only through `reactivate_unit`,
    which asks about the quantity."""
    before = audit.snapshot(unit, UNIT_AUDITED)
    if "status" in changes and changes["status"] is not None:
        if unit.status in UNIT_REMOVED:
            raise DeviceError(
                f"Unit {unit.serial_number} is {unit.status.replace('_', ' ')}. Use "
                f"Back to active, which also asks whether to raise the quantity owned."
            )
        unit.status = changes["status"]
    if "serial_number" in changes and changes["serial_number"] is not None:
        serial = clean_serial(changes["serial_number"])
        if not serial:
            raise DeviceError("A unit needs a serial number.")
        key = serial_key(serial)
        clash = (
            db.query(DeviceUnit)
            .filter(
                DeviceUnit.device_id == unit.device_id,
                DeviceUnit.serial_key == key,
                DeviceUnit.id != unit.id,
            )
            .first()
        )
        if clash is not None:
            raise DeviceConflict(
                f"{clash.serial_number} is already recorded on this device."
            )
        unit.serial_number = serial
        unit.serial_key = key
    if "purchase_price" in changes:
        _refuse_negative_price(changes["purchase_price"])
        unit.purchase_price = changes["purchase_price"]
    if "seller_party_id" in changes:
        check_seller(db, unit.company_id, changes["seller_party_id"])
        unit.seller_party_id = changes["seller_party_id"]
    for field in ("seller_name", "purchase_reference"):
        if field in changes:
            setattr(unit, field, _clean_text(changes[field]))
    if "acquired_on" in changes:
        unit.acquired_on = changes["acquired_on"]
    if "notes" in changes:
        unit.notes = (changes["notes"] or "").strip() or None
    after = audit.snapshot(unit, UNIT_AUDITED)
    if audit.diff(before, after, fields=UNIT_AUDITED):
        audit.record(db, actor, unit, "device_unit.updated", before, after, fields=UNIT_AUDITED)


def remove_unit(
    db: Session,
    device: Device,
    unit: DeviceUnit,
    *,
    actor: audit.Actor,
    status: str,
    reason: str,
    removed_on: Optional[date],
    reduce_quantity: bool,
) -> tuple[int, int]:
    """Retired, lost or sold, with exactly what happened in words.
    `(quantity before, quantity after)`: the quantity moves only when the
    person ticked the box, and never below 0; the answer is in the event."""
    if status not in UNIT_REMOVED:
        raise DeviceError("A unit is removed as retired, lost or sold.")
    if unit.status in UNIT_REMOVED:
        raise DeviceError(
            f"Unit {unit.serial_number} is already {unit.status}. Bring it back "
            f"to active first if this was a mistake."
        )
    text = (reason or "").strip()
    if not text:
        raise DeviceError(
            "Say what happened to this unit — for example \"Fell off the crane "
            "on the 1000m shoot, insurance claim 2026-118\"."
        )
    before = audit.snapshot(unit, UNIT_AUDITED)
    unit.status = status
    unit.removed_reason = text
    unit.removed_on = removed_on or date.today()
    quantity_before = device.quantity_owned
    if reduce_quantity and device.quantity_owned > 0:
        device.quantity_owned -= 1
    after = audit.snapshot(unit, UNIT_AUDITED)
    before["quantity_owned"] = quantity_before
    after["quantity_owned"] = device.quantity_owned
    before["quantity_adjusted"] = None
    after["quantity_adjusted"] = bool(reduce_quantity and quantity_before != device.quantity_owned)
    audit.record(
        db,
        actor,
        unit,
        "device_unit.removed",
        before,
        after,
        fields=UNIT_AUDITED + ("quantity_owned", "quantity_adjusted"),
    )
    return quantity_before, device.quantity_owned


def reactivate_unit(
    db: Session,
    device: Device,
    unit: DeviceUnit,
    *,
    actor: audit.Actor,
    raise_quantity: bool,
) -> tuple[int, int]:
    """A removed unit back in hand (found, bought back, removed by mistake).
    The reason it was removed stays in the audit log."""
    if unit.status not in UNIT_REMOVED:
        raise DeviceError(f"Unit {unit.serial_number} was not removed.")
    before = audit.snapshot(unit, UNIT_AUDITED)
    unit.status = UNIT_ACTIVE
    unit.removed_on = None
    unit.removed_reason = None
    quantity_before = device.quantity_owned
    if raise_quantity:
        device.quantity_owned += 1
    after = audit.snapshot(unit, UNIT_AUDITED)
    before["quantity_owned"] = quantity_before
    after["quantity_owned"] = device.quantity_owned
    before["quantity_adjusted"] = None
    after["quantity_adjusted"] = bool(raise_quantity)
    audit.record(
        db,
        actor,
        unit,
        "device_unit.reactivated",
        before,
        after,
        fields=UNIT_AUDITED + ("quantity_owned", "quantity_adjusted"),
    )
    return quantity_before, device.quantity_owned


# ─── The import ─────────────────────────────────────────────────────────────


def import_context(db: Session, company_id: uuid.UUID) -> DeviceImportContext:
    by_key: dict[str, tuple[str, str, bool]] = {}
    for device_id, name, key, active in (
        db.query(Device.id, Device.name, Device.name_key, Device.is_active)
        .filter(Device.company_id == company_id)
        .order_by(Device.is_active.desc(), Device.created_at)
        .all()
    ):
        by_key.setdefault(key, (str(device_id), name, bool(active)))
    return DeviceImportContext(devices_by_key=by_key)


def preview_import(db: Session, company_id: uuid.UUID, payload: bytes) -> DeviceImportPlan:
    return plan_device_file(payload, import_context(db, company_id))


def apply_import(
    db: Session,
    *,
    company_id: uuid.UUID,
    payload: bytes,
    actor: audit.Actor,
    expected_digest: Optional[str],
    include_lines: Iterable[int] = (),
) -> tuple[DeviceImportPlan, dict[int, Device], int]:
    """`(plan, line → created device, units created)`. Only creates."""
    plan = preview_import(db, company_id, payload)
    if expected_digest and expected_digest != plan.digest:
        raise DigestMismatch(
            "The file no longer matches the preview you saw (a device may have "
            "been added or renamed in the meantime). Choose the file again to "
            "see a fresh preview."
        )
    created: dict[int, Device] = {}
    units = 0
    for row in plan.to_create(include_lines):
        values = row.values
        device = create_device(
            db,
            company_id=company_id,
            actor=actor,
            name=values.name,
            category=values.category,
            quantity_owned=values.quantity,
            purchase_price=values.price,
            notes=values.notes,
            source="devices_import",
        )
        made, _ = add_units(db, device, actor=actor, serial_numbers=values.serials)
        units += len(made)
        created[row.line_number] = device
    audit.record(
        db,
        actor,
        None,
        "devices.import_applied",
        None,
        {
            "devices_created": len(created),
            "units_created": units,
            "duplicates_skipped": sum(
                1 for row in plan.duplicates if row.line_number not in created
            ),
            "errors": len(plan.errors),
        },
        entity_type="devices",
        company_id=company_id,
    )
    return plan, created, units
