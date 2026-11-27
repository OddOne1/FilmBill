"""Links between devices: dependencies (B2b-2 part 3) and alternatives
(part 4). No commit anywhere here; every write is audited in the caller's
transaction.

**Dependencies** (SCOPE §4.4.3, code `associated_link`, table
`device_associations`): "Alexa 35 needs 2 × Battery V-Mount 150 Wh, always,
included". Two rules live here and nowhere else:

* **No loops.** A device cannot need itself, and a link that would close a
  cycle — the camera needs the battery, the battery needs the charger, the
  charger needs the camera — is refused. `path_between` walks the active
  links; the refusal names the chain.
* **The setup hint multiplies.** When the device being added has
  dependencies of its own, they are offered with the quantity per parent
  pre-multiplied: 2 batteries per camera × 1 charger per battery → 2
  chargers per camera (`premultiply`). Links added that way remember the
  link they copied (`origin_association_id`).

Booking with these links is B2c and follows ONE level: the charger is booked
with the camera only because the hint copied it onto the camera.
"""

import uuid
from collections import deque
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models.device import (
    ASSOCIATION_MODES,
    ASSOCIATION_PRICINGS,
    Device,
    DeviceAssociation,
)
from . import audit
from .device_service import DeviceConflict, DeviceError

ASSOCIATION_AUDITED = (
    "device_id",
    "associated_device_id",
    "quantity_per_parent",
    "booking_mode",
    "pricing",
    "show_on_documents",
    "note",
    "origin_association_id",
    "is_active",
)


def device_in_company(db: Session, company_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    """This company's device, or `LookupError` (the caller's 404)."""
    row = db.query(Device).filter(Device.id == device_id, Device.company_id == company_id).first()
    if row is None:
        raise LookupError("Device not found")
    return row


# ─── The graph ──────────────────────────────────────────────────────────────


def _needs(db: Session, company_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    graph: dict[uuid.UUID, list[uuid.UUID]] = {}
    for parent, child in db.query(
        DeviceAssociation.device_id, DeviceAssociation.associated_device_id
    ).filter(DeviceAssociation.company_id == company_id, DeviceAssociation.is_active.is_(True)):
        graph.setdefault(parent, []).append(child)
    return graph


def path_between(
    db: Session, company_id: uuid.UUID, start: uuid.UUID, goal: uuid.UUID
) -> Optional[list[uuid.UUID]]:
    """The chain of active links from `start` to `goal` (both included), or
    None. Breadth first, so the chain named in a refusal is the shortest."""
    graph = _needs(db, company_id)
    previous: dict[uuid.UUID, Optional[uuid.UUID]] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == goal:
            chain = [current]
            while previous[chain[-1]] is not None:
                chain.append(previous[chain[-1]])
            return list(reversed(chain))
        for nxt in graph.get(current, ()):
            if nxt not in previous:
                previous[nxt] = current
                queue.append(nxt)
    return None


def refuse_loop(db: Session, parent: Device, associated: Device) -> None:
    """A link `parent → associated` is refused when `associated` already
    needs `parent`, directly or through others — the new link would close a
    loop — or when the two are the same device."""
    if parent.id == associated.id:
        raise DeviceError(
            f"{parent.name} cannot depend on itself. Pick another device, such as "
            f"its battery or charger."
        )
    chain = path_between(db, parent.company_id, associated.id, parent.id)
    if chain is None:
        return
    names = dict(
        db.query(Device.id, Device.name).filter(Device.id.in_(chain)).all()
    )
    route = " → ".join(names[step] for step in chain)
    raise DeviceConflict(
        f"{associated.name} already depends on {parent.name} ({route}), so "
        f"{parent.name} cannot depend on {associated.name} as well: that would be "
        f"a loop. Remove one of the links first."
    )


def premultiply(quantity_per_parent: int, quantity_per_associated: int) -> int:
    """2 batteries per camera × 1 charger per battery = 2 chargers per camera."""
    return quantity_per_parent * quantity_per_associated


# ─── Dependencies ───────────────────────────────────────────────────────────


def active_dependency(db: Session, parent_id: uuid.UUID, associated_id: uuid.UUID) -> Optional[DeviceAssociation]:
    return (
        db.query(DeviceAssociation)
        .filter(
            DeviceAssociation.device_id == parent_id,
            DeviceAssociation.associated_device_id == associated_id,
            DeviceAssociation.is_active.is_(True),
        )
        .first()
    )


def own_dependencies(db: Session, device: Device) -> list[DeviceAssociation]:
    return (
        db.query(DeviceAssociation)
        .filter(DeviceAssociation.device_id == device.id, DeviceAssociation.is_active.is_(True))
        .order_by(DeviceAssociation.created_at, DeviceAssociation.id)
        .all()
    )


def hint(db: Session, parent: Device, associated: Device, quantity_per_parent: int) -> list[dict]:
    """What the setup hint offers when `associated` is added to `parent`:
    each of `associated`'s own dependencies, with the quantity per PARENT
    pre-multiplied, and whether it can be added at all."""
    rows = []
    for link in own_dependencies(db, associated):
        target = db.get(Device, link.associated_device_id)
        reason = None
        if target.id == parent.id or path_between(db, parent.company_id, target.id, parent.id):
            reason = f"{target.name} depends on {parent.name}, so adding it would make a loop."
        elif active_dependency(db, parent.id, target.id) is not None:
            reason = f"{parent.name} already depends on {target.name}."
        elif not target.is_active:
            reason = f"{target.name} is archived."
        rows.append(
            {
                "origin_association_id": link.id,
                "device": target,
                "quantity_per_associated": link.quantity_per_parent,
                "suggested_quantity": premultiply(quantity_per_parent, link.quantity_per_parent),
                "booking_mode": link.booking_mode,
                "pricing": link.pricing,
                "blocked_reason": reason,
            }
        )
    return rows


def _check_values(quantity: int, booking_mode: str, pricing: str) -> None:
    if quantity < 1:
        raise DeviceError("A dependency is needed at least once per device. Enter 1 or more.")
    if booking_mode not in ASSOCIATION_MODES:
        raise DeviceError("A dependency is booked either always or after asking.")
    if pricing not in ASSOCIATION_PRICINGS:
        raise DeviceError("A dependency is either included in the price or charged.")


def _create_link(
    db: Session,
    parent: Device,
    associated: Device,
    *,
    actor: audit.Actor,
    quantity_per_parent: int,
    booking_mode: str,
    pricing: str,
    show_on_documents: bool,
    note: Optional[str],
    origin_association_id: Optional[uuid.UUID],
) -> DeviceAssociation:
    if not associated.is_active:
        raise DeviceError(f"{associated.name} is archived. Reactivate it before adding it as a dependency.")
    refuse_loop(db, parent, associated)
    if active_dependency(db, parent.id, associated.id) is not None:
        raise DeviceConflict(
            f"{parent.name} already depends on {associated.name}. Change the quantity "
            f"in that row instead."
        )
    _check_values(quantity_per_parent, booking_mode, pricing)
    link = DeviceAssociation(
        company_id=parent.company_id,
        device_id=parent.id,
        associated_device_id=associated.id,
        quantity_per_parent=quantity_per_parent,
        booking_mode=booking_mode,
        pricing=pricing,
        show_on_documents=show_on_documents,
        note=(note or "").strip() or None,
        origin_association_id=origin_association_id,
        is_active=True,
    )
    db.add(link)
    db.flush()
    audit.record(
        db,
        actor,
        link,
        "device_association.created",
        None,
        audit.snapshot(link, ASSOCIATION_AUDITED),
        fields=ASSOCIATION_AUDITED,
    )
    return link


def add_dependency(
    db: Session,
    parent: Device,
    *,
    actor: audit.Actor,
    associated_device_id: uuid.UUID,
    quantity_per_parent: int = 1,
    booking_mode: str = "always",
    pricing: str = "included",
    show_on_documents: bool = False,
    note: Optional[str] = None,
    from_hint: Iterable[dict] = (),
) -> list[DeviceAssociation]:
    """The link, and the ticked rows of the setup hint, in one transaction.

    Each `from_hint` entry names the associated device's own link it copies
    (`origin_association_id`) and may carry an edited `quantity_per_parent`;
    without one the pre-multiplied quantity is used.
    """
    associated = device_in_company(db, parent.company_id, associated_device_id)
    created = [
        _create_link(
            db,
            parent,
            associated,
            actor=actor,
            quantity_per_parent=quantity_per_parent,
            booking_mode=booking_mode,
            pricing=pricing,
            show_on_documents=show_on_documents,
            note=note,
            origin_association_id=None,
        )
    ]
    for entry in from_hint:
        origin = (
            db.query(DeviceAssociation)
            .filter(
                DeviceAssociation.id == entry["origin_association_id"],
                DeviceAssociation.company_id == parent.company_id,
                DeviceAssociation.device_id == associated.id,
                DeviceAssociation.is_active.is_(True),
            )
            .first()
        )
        if origin is None:
            raise LookupError("Dependency not found")
        target = db.get(Device, origin.associated_device_id)
        quantity = entry.get("quantity_per_parent")
        created.append(
            _create_link(
                db,
                parent,
                target,
                actor=actor,
                quantity_per_parent=quantity
                if quantity is not None
                else premultiply(quantity_per_parent, origin.quantity_per_parent),
                booking_mode=origin.booking_mode,
                pricing=origin.pricing,
                show_on_documents=origin.show_on_documents,
                note=None,
                origin_association_id=origin.id,
            )
        )
    return created


def update_dependency(db: Session, link: DeviceAssociation, *, actor: audit.Actor, changes: dict) -> None:
    if not link.is_active:
        raise DeviceError("This dependency was removed. Add it again instead.")
    before = audit.snapshot(link, ASSOCIATION_AUDITED)
    for field in ("quantity_per_parent", "booking_mode", "pricing", "show_on_documents"):
        if field in changes and changes[field] is not None:
            setattr(link, field, changes[field])
    if "note" in changes:
        link.note = (changes["note"] or "").strip() or None
    _check_values(link.quantity_per_parent, link.booking_mode, link.pricing)
    after = audit.snapshot(link, ASSOCIATION_AUDITED)
    if audit.diff(before, after, fields=ASSOCIATION_AUDITED):
        audit.record(
            db, actor, link, "device_association.updated", before, after, fields=ASSOCIATION_AUDITED
        )


def remove_dependency(db: Session, link: DeviceAssociation, *, actor: audit.Actor) -> None:
    """Never a delete: the link goes inactive and stays explained."""
    if not link.is_active:
        return
    before = audit.snapshot(link, ASSOCIATION_AUDITED)
    link.is_active = False
    audit.record(
        db,
        actor,
        link,
        "device_association.removed",
        before,
        audit.snapshot(link, ASSOCIATION_AUDITED),
        fields=ASSOCIATION_AUDITED,
    )
