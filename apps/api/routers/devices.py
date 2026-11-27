"""Gear: devices and the serial numbers of their units (B2b part 2).

`devices.view` reads (staff included: the crew needs to know what exists),
`devices.edit` writes. A member without the key gets a 404 from `require()`.
Another company's device or unit is a 404, never a 403 (rule 6).

**Purchase prices are cost information.** Every response that could carry
one carries it only when the caller also holds `budgets.view`, and says so
with `price_visible`. That covers the list, the detail, the export and the
import preview.

**No DELETE route.** Devices are archived; units are removed (retired,
lost or sold) with a reason in words, and can come back.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..billing.resources.device_csv import (
    ACTION_CREATE,
    ACTION_DUPLICATE,
    DeviceValues,
    ImportFormatError,
    export_device_csv,
)
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.catalog import Item, RentalFactorGroup
from ..models.device import UNIT_REMOVED, Device, DeviceAlternative, DeviceAssociation, DeviceUnit
from ..models.party import Party
from ..schemas.device import (
    DeviceCounts,
    DeviceCreate,
    DeviceDetail,
    DeviceImportApplyResponse,
    DeviceImportPreviewResponse,
    DeviceImportRowResponse,
    DevicePage,
    DeviceRentalPrices,
    DeviceRentalWrite,
    AlternativeCreate,
    AlternativeMove,
    AlternativeUpdate,
    CatalogItemRef,
    DeviceAlternativeResponse,
    DeviceAlternatives,
    DependencyCreate,
    DependencyHint,
    DependencyHintRow,
    DependencyUpdate,
    DeviceDependencies,
    DeviceDependencyResponse,
    DeviceRef,
    DeviceResponse,
    DeviceUnitChangeResponse,
    DeviceUnitReactivate,
    DeviceUnitResponse,
    DeviceUnitRemove,
    DeviceUnitsAdd,
    DeviceUnitsAddResponse,
    DeviceUnitUpdate,
    DeviceUpdate,
    SkippedSerial,
)
from ..services import audit, device_links, device_service
from ..services.permissions import scoped

router = APIRouter(tags=["devices"])

MAX_IMPORT_BYTES = 1024 * 1024
PRICE_PERMISSION = "budgets.view"


def _can_see_price(membership: Membership) -> bool:
    return membership.can(PRICE_PERMISSION)


def _refuse(error: Exception, code: int):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _device_or_404(db: Session, membership: Membership, device_id: uuid.UUID) -> Device:
    row = scoped(db.query(Device), membership).filter(Device.id == device_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return row


def _unit_or_404(db: Session, membership: Membership, device: Device, unit_id: uuid.UUID) -> DeviceUnit:
    row = (
        scoped(db.query(DeviceUnit), membership)
        .filter(DeviceUnit.id == unit_id, DeviceUnit.device_id == device.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found")
    return row


def _responses(db: Session, membership: Membership, rows: list[Device]) -> list[DeviceResponse]:
    counts = device_service.counts_for(db, rows)
    visible = _can_see_price(membership)
    item_ids = {row.catalog_item_id for row in rows if row.catalog_item_id}
    items = (
        dict(db.query(Item.id, Item.name_de).filter(Item.id.in_(item_ids)).all()) if item_ids else {}
    )
    return [
        DeviceResponse(
            id=row.id,
            name=row.name,
            category=row.category,
            quantity_owned=row.quantity_owned,
            purchase_price=row.purchase_price if visible else None,
            price_visible=visible,
            purchase_date=row.purchase_date,
            catalog_item_id=row.catalog_item_id,
            catalog_item_name=items.get(row.catalog_item_id),
            notes=row.notes,
            is_active=row.is_active,
            counts=DeviceCounts(**counts[row.id].as_dict()),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def _units(db: Session, membership: Membership, units: list[DeviceUnit]) -> list[DeviceUnitResponse]:
    """Units, with what was paid and to whom only for `budgets.view`."""
    visible = _can_see_price(membership)
    seller_ids = {unit.seller_party_id for unit in units if unit.seller_party_id}
    sellers = (
        dict(db.query(Party.id, Party.name).filter(Party.id.in_(seller_ids)).all())
        if visible and seller_ids
        else {}
    )
    return [
        DeviceUnitResponse(
            id=unit.id,
            device_id=unit.device_id,
            serial_number=unit.serial_number,
            status=unit.status,
            acquired_on=unit.acquired_on,
            purchase_visible=visible,
            purchase_price=unit.purchase_price if visible else None,
            seller_party_id=unit.seller_party_id if visible else None,
            seller_party_name=sellers.get(unit.seller_party_id) if visible else None,
            seller_name=unit.seller_name if visible else None,
            purchase_reference=unit.purchase_reference if visible else None,
            removed_on=unit.removed_on,
            removed_reason=unit.removed_reason,
            notes=unit.notes,
            created_at=unit.created_at,
            updated_at=unit.updated_at,
        )
        for unit in units
    ]


#: Rates are written into the price list, so they need its edit key too.
RATES_EDIT_PERMISSION = "catalog.edit"


def _rental(db: Session, membership: Membership, device: Device) -> DeviceRentalPrices:
    """The Rental prices section: the linked catalog item's day rates."""
    from ..services.catalog_service import rate_of

    visible = _can_see_price(membership)
    can_devices = membership.can("devices.edit")
    can_catalog = membership.can(RATES_EDIT_PERMISSION)
    reason = None
    if not can_devices:
        reason = (
            "You can see the rental prices but not change them: that needs the "
            "gear edit permission. An owner or administrator can grant it under "
            "Settings → Company → Members."
        )
    elif not can_catalog:
        reason = (
            "Your role can change gear but not the price list. These prices are "
            "the catalog's, so changing them needs the catalog edit permission. "
            "An owner or administrator can grant it under Settings → Company → Members."
        )
    item = device_service.linked_item(db, device)
    if item is None:
        existing = device_service.existing_item_for(db, device)
        return DeviceRentalPrices(
            linked=False,
            rates_visible=visible,
            can_edit=can_devices and can_catalog,
            edit_blocked_reason=reason,
            existing_item=CatalogItemRef(id=existing.id, name=existing.name_de) if existing else None,
        )
    group_name = None
    if item.rental_factor_group_id is not None:
        group_name = (
            db.query(RentalFactorGroup.name)
            .filter(
                RentalFactorGroup.id == item.rental_factor_group_id,
                RentalFactorGroup.company_id == device.company_id,
            )
            .scalar()
        )
    return DeviceRentalPrices(
        linked=True,
        item_id=item.id,
        item_name=item.name_de,
        item_is_active=item.is_active,
        rates_visible=visible,
        price_per_day=rate_of(item, rate_type="bill", period_unit="day") if visible else None,
        cost_per_day=rate_of(item, rate_type="cost", period_unit="day") if visible else None,
        rental_factor_group_id=item.rental_factor_group_id,
        rental_factor_group_name=group_name,
        can_edit=can_devices and can_catalog,
        edit_blocked_reason=reason,
    )


def _detail(db: Session, membership: Membership, device: Device) -> DeviceDetail:
    base = _responses(db, membership, [device])[0]
    units = (
        db.query(DeviceUnit)
        .filter(DeviceUnit.device_id == device.id)
        .order_by(
            DeviceUnit.status.in_(UNIT_REMOVED).asc(),
            func.lower(DeviceUnit.serial_number),
        )
        .all()
    )
    return DeviceDetail(
        **base.model_dump(),
        units=_units(db, membership, units),
        rental=_rental(db, membership, device),
    )


def _devices_query(db, membership, *, search, category, include_inactive):
    query = scoped(db.query(Device), membership)
    if not include_inactive:
        query = query.filter(Device.is_active.is_(True))
    if category and category.strip():
        query = query.filter(func.lower(Device.category) == category.strip().lower())
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        serials = db.query(DeviceUnit.device_id).filter(
            DeviceUnit.company_id == membership.company_id,
            func.lower(DeviceUnit.serial_number).like(needle),
        )
        query = query.filter(
            or_(
                func.lower(Device.name).like(needle),
                func.lower(Device.category).like(needle),
                Device.id.in_(serials),
            )
        )
    return query.order_by(func.lower(Device.name), Device.id)


# ─── The list, the export, the import ───────────────────────────────────────
#
# Registered before `/devices/{device_id}` so `export.csv` and `import` are
# not read as device ids.


@router.get("/devices", response_model=DevicePage)
def list_devices(
    search: Optional[str] = Query(default=None, max_length=255, description="Name, category or serial number."),
    category: Optional[str] = Query(default=None, max_length=255),
    include_inactive: bool = Query(default=False, description="Archived devices too."),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    query = _devices_query(
        db, membership, search=search, category=category, include_inactive=include_inactive
    )
    total = query.count()
    rows = query.limit(limit).offset(offset).all()
    return DevicePage(
        devices=_responses(db, membership, rows),
        total=total,
        categories=device_service.category_suggestions(db, membership.company_id),
    )


@router.get("/devices/export.csv")
def export_devices(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    """`name;category;quantity;price;serials;notes`. The serials are the
    units still in hand (not lost, retired or sold); the price column is empty for a role that
    may not see purchase prices."""
    rows = _devices_query(db, membership, search=None, category=None, include_inactive=include_inactive).all()
    visible = _can_see_price(membership)
    serials: dict[uuid.UUID, list[str]] = {row.id: [] for row in rows}
    if rows:
        for device_id, serial in (
            db.query(DeviceUnit.device_id, DeviceUnit.serial_number)
            .filter(
                DeviceUnit.device_id.in_(list(serials)),
                DeviceUnit.status.notin_(UNIT_REMOVED),
            )
            .order_by(func.lower(DeviceUnit.serial_number))
            .all()
        ):
            serials[device_id].append(serial)
    payload = export_device_csv(
        [
            DeviceValues(
                name=row.name,
                category=row.category,
                quantity=row.quantity_owned,
                price=row.purchase_price if visible else None,
                serials=tuple(serials[row.id]),
                notes=row.notes,
            )
            for row in rows
        ]
    )
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gear.csv"'},
    )


async def _read_upload(file: UploadFile) -> bytes:
    payload = await file.read()
    if len(payload) > MAX_IMPORT_BYTES:
        _refuse(
            ValueError(
                f"That file is {len(payload) // 1024} KB and the limit is "
                f"{MAX_IMPORT_BYTES // 1024} KB. Check that it is the gear list."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if not payload.strip():
        _refuse(
            ValueError("That file is empty. Choose the CSV file with the gear list."),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return payload


def _import_row(row, *, visible: bool, action: Optional[str] = None, device_id=None) -> DeviceImportRowResponse:
    values = row.values
    return DeviceImportRowResponse(
        line_number=row.line_number,
        action=action or row.action,
        name=values.name if values else row.name,
        category=values.category if values else None,
        quantity=values.quantity if values else None,
        price=(values.price if values and visible else None),
        serials=list(values.serials) if values else [],
        notes=values.notes if values else None,
        message=row.message,
        can_include=row.can_include,
        duplicate_of_device_id=row.duplicate_of_device_id,
        device_id=device_id,
    )


@router.post("/devices/import/preview", response_model=DeviceImportPreviewResponse)
async def preview_device_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    payload = await _read_upload(file)
    try:
        plan = device_service.preview_import(db, membership.company_id, payload)
    except ImportFormatError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    visible = _can_see_price(membership)
    return DeviceImportPreviewResponse(
        rows=[_import_row(row, visible=visible) for row in plan.rows],
        created=len(plan.creates),
        duplicates=len(plan.duplicates),
        errors=len(plan.errors),
        digest=plan.digest,
        delimiter=plan.delimiter,
        ignored_columns=list(plan.ignored_columns),
    )


@router.post("/devices/import", response_model=DeviceImportApplyResponse)
async def apply_device_import(
    request: Request,
    file: UploadFile = File(...),
    digest: Optional[str] = Form(default=None),
    include_lines: Optional[str] = Form(
        default=None,
        description="Comma-separated line numbers of duplicates of ARCHIVED devices to create anyway.",
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Create the preview's new devices with their serial numbers. Never
    edits, archives or removes anything that exists."""
    payload = await _read_upload(file)
    try:
        ticked = [int(part) for part in (include_lines or "").split(",") if part.strip()]
    except ValueError:
        _refuse(
            ValueError("include_lines must be line numbers separated by commas, such as 3,7."),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        plan, created, units = device_service.apply_import(
            db,
            company_id=membership.company_id,
            payload=payload,
            actor=audit.actor_from(request, membership=membership),
            expected_digest=digest,
            include_lines=ticked,
        )
    except ImportFormatError as error:
        db.rollback()
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    except device_service.DigestMismatch as error:
        db.rollback()
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        db.rollback()
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    visible = _can_see_price(membership)
    rows = []
    for row in plan.rows:
        if row.line_number in created:
            rows.append(_import_row(row, visible=visible, action="created", device_id=created[row.line_number].id))
        elif row.action in (ACTION_CREATE, ACTION_DUPLICATE):
            rows.append(_import_row(row, visible=visible, action="skipped"))
        else:
            rows.append(_import_row(row, visible=visible))
    return DeviceImportApplyResponse(
        rows=rows,
        created=len(created),
        skipped=sum(1 for row in rows if row.action == "skipped"),
        errors=len(plan.errors),
        units_created=units,
        digest=plan.digest,
    )


# ─── One device ─────────────────────────────────────────────────────────────


#: What a unit was bought for and from whom: written, like it is read, only
#: with `budgets.view`.
PURCHASE_FIELDS = ("purchase_price", "seller_party_id", "seller_name", "purchase_reference")


def _writes_price(membership: Membership, fields: dict) -> None:
    if _can_see_price(membership):
        return
    if any(fields.get(name) not in (None, "") for name in PURCHASE_FIELDS):
        _refuse(
            ValueError(
                "You cannot enter purchase details: prices and sellers are only "
                "for people who can see budgets. Leave those fields empty."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.post("/devices", response_model=DeviceDetail, status_code=status.HTTP_201_CREATED)
def create_device(
    body: DeviceCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    fields = body.model_dump()
    _writes_price(membership, fields)
    try:
        device = device_service.create_device(
            db,
            company_id=membership.company_id,
            actor=audit.actor_from(request, membership=membership),
            **fields,
        )
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


@router.get("/devices/{device_id}", response_model=DeviceDetail)
def read_device(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    return _detail(db, membership, _device_or_404(db, membership, device_id))


@router.patch("/devices/{device_id}", response_model=DeviceDetail)
def update_device(
    device_id: uuid.UUID,
    body: DeviceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Change a device. `quantity_owned` may always be set by hand; it is
    recorded as its own event, `device.quantity_set`."""
    device = _device_or_404(db, membership, device_id)
    changes = body.model_dump(exclude_unset=True)
    _writes_price(membership, changes)
    try:
        device_service.update_device(
            db, device, actor=audit.actor_from(request, membership=membership), changes=changes
        )
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


@router.post("/devices/{device_id}/archive", response_model=DeviceDetail)
def archive_device(
    device_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    device = _device_or_404(db, membership, device_id)
    device_service.archive_device(db, device, actor=audit.actor_from(request, membership=membership))
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


@router.post("/devices/{device_id}/reactivate", response_model=DeviceDetail)
def reactivate_device(
    device_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    device = _device_or_404(db, membership, device_id)
    try:
        device_service.reactivate_device(db, device, actor=audit.actor_from(request, membership=membership))
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


# ─── Rental prices (B2b-2) ──────────────────────────────────────────────────


def _rates_editor(membership: Membership) -> None:
    """`devices.edit` came with the route; the price list's own key is the
    second check. Missing, it answers like any missing permission: 404."""
    if not membership.can(RATES_EDIT_PERMISSION):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/devices/{device_id}/rental/setup", response_model=DeviceDetail)
def set_up_rental(
    device_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Give the device its catalog item: the unlinked equipment item of
    exactly its name when there is one ("Use the existing ..."), otherwise a
    new one (kind equipment rental, unit day, the default equipment
    category). Never a second item of the same name."""
    _rates_editor(membership)
    device = _device_or_404(db, membership, device_id)
    try:
        device_service.set_up_rental(db, device, actor=audit.actor_from(request, membership=membership))
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


@router.put("/devices/{device_id}/rental", response_model=DeviceDetail)
def update_rental(
    device_id: uuid.UUID,
    body: DeviceRentalWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Price per day, cost per day and rental factor table — written to the
    linked catalog item through the catalog service and audited by the
    catalog's own events. Only the fields sent change."""
    _rates_editor(membership)
    device = _device_or_404(db, membership, device_id)
    changes = body.model_dump(exclude_unset=True)
    if not _can_see_price(membership) and changes.get("cost_per_day") not in (None, ""):
        _refuse(
            ValueError(
                "You cannot enter a cost per day: costs are only for people who "
                "can see budgets. Leave that field empty."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        device_service.update_rental(
            db,
            device,
            actor=audit.actor_from(request, membership=membership),
            currency=getattr(membership.company, "default_currency", None) or "EUR",
            changes=changes,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rental factor table not found") from None
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    db.refresh(device)
    return _detail(db, membership, device)


# ─── Units ──────────────────────────────────────────────────────────────────


@router.post(
    "/devices/{device_id}/units",
    response_model=DeviceUnitsAddResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_units(
    device_id: uuid.UUID,
    body: DeviceUnitsAdd,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Record the serial numbers of the Add new item(s) popup — one or
    many, in one request, with ONE audit event (`device_unit.created_bulk`,
    counts and the shared purchase details). Serials already on this
    device, or repeated in the list, are listed and skipped. The quantity
    owned does not change."""
    device = _device_or_404(db, membership, device_id)
    _writes_price(membership, body.model_dump())
    try:
        created, skipped = device_service.add_units(
            db,
            device,
            actor=audit.actor_from(request, membership=membership),
            serial_numbers=body.serial_numbers,
            acquired_on=body.acquired_on,
            notes=body.notes,
            purchase_price=body.purchase_price,
            seller_party_id=body.seller_party_id,
            seller_name=body.seller_name,
            purchase_reference=body.purchase_reference,
            audit_each=False,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller not found") from None
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    for unit in created:
        db.refresh(unit)
    db.refresh(device)
    return DeviceUnitsAddResponse(
        device=_detail(db, membership, device),
        created=_units(db, membership, created),
        skipped=[SkippedSerial(**entry) for entry in skipped],
    )


@router.patch("/devices/{device_id}/units/{device_unit_id}", response_model=DeviceUnitChangeResponse)
def update_unit(
    device_id: uuid.UUID,
    device_unit_id: uuid.UUID,
    body: DeviceUnitUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Correct a serial number, a date, a note or the purchase details;
    move a unit between active, needs check and in repair. Removing is
    `/remove`, coming back from it `/reactivate`."""
    device = _device_or_404(db, membership, device_id)
    unit = _unit_or_404(db, membership, device, device_unit_id)
    changes = body.model_dump(exclude_unset=True)
    _writes_price(membership, changes)
    try:
        device_service.update_unit(
            db,
            unit,
            actor=audit.actor_from(request, membership=membership),
            changes=changes,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller not found") from None
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    db.refresh(unit)
    db.refresh(device)
    return DeviceUnitChangeResponse(
        device=_detail(db, membership, device),
        unit=_units(db, membership, [unit])[0],
        quantity_before=device.quantity_owned,
        quantity_after=device.quantity_owned,
    )


@router.post("/devices/{device_id}/units/{device_unit_id}/remove", response_model=DeviceUnitChangeResponse)
def remove_unit(
    device_id: uuid.UUID,
    device_unit_id: uuid.UUID,
    body: DeviceUnitRemove,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Retired, lost or sold — with exactly what happened, in words (a unit
    entered by mistake is retired and says so). `reduce_quantity` defaults
    to yes."""
    device = _device_or_404(db, membership, device_id)
    unit = _unit_or_404(db, membership, device, device_unit_id)
    try:
        before, after = device_service.remove_unit(
            db,
            device,
            unit,
            actor=audit.actor_from(request, membership=membership),
            status=body.status,
            reason=body.reason,
            removed_on=body.removed_on,
            reduce_quantity=body.reduce_quantity,
        )
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    db.refresh(unit)
    db.refresh(device)
    return DeviceUnitChangeResponse(
        device=_detail(db, membership, device),
        unit=_units(db, membership, [unit])[0],
        quantity_before=before,
        quantity_after=after,
    )


@router.post(
    "/devices/{device_id}/units/{device_unit_id}/reactivate",
    response_model=DeviceUnitChangeResponse,
)
def reactivate_unit(
    device_id: uuid.UUID,
    device_unit_id: uuid.UUID,
    body: DeviceUnitReactivate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Bring a lost, retired or sold unit back. `raise_quantity` defaults to yes."""
    device = _device_or_404(db, membership, device_id)
    unit = _unit_or_404(db, membership, device, device_unit_id)
    try:
        before, after = device_service.reactivate_unit(
            db,
            device,
            unit,
            actor=audit.actor_from(request, membership=membership),
            raise_quantity=body.raise_quantity,
        )
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    db.refresh(unit)
    db.refresh(device)
    return DeviceUnitChangeResponse(
        device=_detail(db, membership, device),
        unit=_units(db, membership, [unit])[0],
        quantity_before=before,
        quantity_after=after,
    )


# ─── Dependencies (B2b-2 part 3) ────────────────────────────────────────────
#
# Data and screens only; booking with them is B2c. `devices.view` reads both
# lists and the setup hint (nothing here is a price), `devices.edit` writes.
# Removing is a POST that deactivates — there is no DELETE.


def device_refs(db: Session, ids: set) -> dict:
    """`{id: DeviceRef}` for the devices named, in one query."""
    if not ids:
        return {}
    rows = db.query(Device).filter(Device.id.in_(list(ids))).all()
    return {row.id: _ref(row) for row in rows}


def _ref(row: Device) -> DeviceRef:
    return DeviceRef(id=row.id, name=row.name, category=row.category, is_active=row.is_active)


def _dependency_rows(db: Session, links: list[DeviceAssociation]) -> list[DeviceDependencyResponse]:
    origins = {link.origin_association_id for link in links if link.origin_association_id}
    origin_device = (
        dict(
            db.query(DeviceAssociation.id, DeviceAssociation.device_id)
            .filter(DeviceAssociation.id.in_(list(origins)))
            .all()
        )
        if origins
        else {}
    )
    refs = device_refs(
        db,
        {link.device_id for link in links}
        | {link.associated_device_id for link in links}
        | set(origin_device.values()),
    )
    return [
        DeviceDependencyResponse(
            id=link.id,
            parent=refs[link.device_id],
            associated=refs[link.associated_device_id],
            quantity_per_parent=link.quantity_per_parent,
            booking_mode=link.booking_mode,
            pricing=link.pricing,
            show_on_documents=link.show_on_documents,
            note=link.note,
            origin_association_id=link.origin_association_id,
            origin_device_name=(
                refs[origin_device[link.origin_association_id]].name
                if link.origin_association_id in origin_device
                else None
            ),
            created_at=link.created_at,
            updated_at=link.updated_at,
        )
        for link in links
    ]


def _dependencies(db: Session, device: Device) -> DeviceDependencies:
    def active(column):
        return (
            db.query(DeviceAssociation)
            .filter(column == device.id, DeviceAssociation.is_active.is_(True))
            .order_by(DeviceAssociation.created_at, DeviceAssociation.id)
            .all()
        )

    return DeviceDependencies(
        needs=_dependency_rows(db, active(DeviceAssociation.device_id)),
        used_by=_dependency_rows(db, active(DeviceAssociation.associated_device_id)),
    )


def _dependency_or_404(db: Session, membership: Membership, device: Device, association_id: uuid.UUID) -> DeviceAssociation:
    row = (
        scoped(db.query(DeviceAssociation), membership)
        .filter(DeviceAssociation.id == association_id, DeviceAssociation.device_id == device.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dependency not found")
    return row


@router.get("/devices/{device_id}/dependencies", response_model=DeviceDependencies)
def list_dependencies(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    """What this device needs, and which devices need it."""
    return _dependencies(db, _device_or_404(db, membership, device_id))


@router.get("/devices/{device_id}/dependencies/hint", response_model=DependencyHint)
def dependency_hint(
    device_id: uuid.UUID,
    associated_device_id: uuid.UUID = Query(...),
    quantity_per_parent: int = Query(default=1, ge=1, le=10_000),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    """The setup hint: the chosen device's own dependencies, with the
    quantity per THIS device pre-multiplied. They are not booked through
    this device unless they are added here."""
    device = _device_or_404(db, membership, device_id)
    associated = _device_or_404(db, membership, associated_device_id)
    rows = device_links.hint(db, device, associated, quantity_per_parent)
    return DependencyHint(
        associated=_ref(associated),
        rows=[
            DependencyHintRow(**{**row, "device": _ref(row["device"])})
            for row in rows
        ],
    )


@router.post(
    "/devices/{device_id}/dependencies",
    response_model=DeviceDependencies,
    status_code=status.HTTP_201_CREATED,
)
def add_dependency(
    device_id: uuid.UUID,
    body: DependencyCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Add a dependency, and the ticked rows of the setup hint with it, in
    one transaction. A loop is refused with the chain named."""
    device = _device_or_404(db, membership, device_id)
    try:
        device_links.add_dependency(
            db,
            device,
            actor=audit.actor_from(request, membership=membership),
            associated_device_id=body.associated_device_id,
            quantity_per_parent=body.quantity_per_parent,
            booking_mode=body.booking_mode,
            pricing=body.pricing,
            show_on_documents=body.show_on_documents,
            note=body.note,
            from_hint=[choice.model_dump() for choice in body.from_hint],
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from None
    except device_service.DeviceConflict as error:
        db.rollback()
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        db.rollback()
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    return _dependencies(db, device)


@router.patch("/devices/{device_id}/dependencies/{association_id}", response_model=DeviceDependencies)
def update_dependency(
    device_id: uuid.UUID,
    association_id: uuid.UUID,
    body: DependencyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    device = _device_or_404(db, membership, device_id)
    link = _dependency_or_404(db, membership, device, association_id)
    try:
        device_links.update_dependency(
            db, link, actor=audit.actor_from(request, membership=membership), changes=body.model_dump(exclude_unset=True)
        )
    except device_service.DeviceError as error:
        db.rollback()
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    return _dependencies(db, device)


@router.post(
    "/devices/{device_id}/dependencies/{association_id}/remove", response_model=DeviceDependencies
)
def remove_dependency(
    device_id: uuid.UUID,
    association_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """The link goes inactive; it stays in the audit log. No DELETE."""
    device = _device_or_404(db, membership, device_id)
    link = _dependency_or_404(db, membership, device, association_id)
    device_links.remove_dependency(db, link, actor=audit.actor_from(request, membership=membership))
    db.commit()
    return _dependencies(db, device)


# ─── Alternatives (B2b-2 part 4) ────────────────────────────────────────────


def _alternative_rows(db: Session, rows: list[DeviceAlternative]) -> list[DeviceAlternativeResponse]:
    refs = device_refs(db, {row.device_id for row in rows} | {row.alternative_device_id for row in rows})
    return [
        DeviceAlternativeResponse(
            id=row.id,
            device=refs[row.device_id],
            alternative=refs[row.alternative_device_id],
            position=row.position,
            note=row.note,
            price_hint=row.price_hint,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def _alternatives(db: Session, device: Device, reverse_added: Optional[bool] = None) -> DeviceAlternatives:
    offered_by = (
        db.query(DeviceAlternative)
        .filter(DeviceAlternative.alternative_device_id == device.id, DeviceAlternative.is_active.is_(True))
        .order_by(DeviceAlternative.created_at, DeviceAlternative.id)
        .all()
    )
    return DeviceAlternatives(
        alternatives=_alternative_rows(db, device_links.alternatives_of(db, device.id)),
        offered_by=_alternative_rows(db, offered_by),
        reverse_added=reverse_added,
    )


def _alternative_or_404(db: Session, membership: Membership, device: Device, alternative_id: uuid.UUID) -> DeviceAlternative:
    row = (
        scoped(db.query(DeviceAlternative), membership)
        .filter(DeviceAlternative.id == alternative_id, DeviceAlternative.device_id == device.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alternative not found")
    return row


@router.get("/devices/{device_id}/alternatives", response_model=DeviceAlternatives)
def list_alternatives(
    device_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.view")),
):
    """This device's ordered alternatives, and the devices that offer it."""
    return _alternatives(db, _device_or_404(db, membership, device_id))


@router.post(
    "/devices/{device_id}/alternatives",
    response_model=DeviceAlternatives,
    status_code=status.HTTP_201_CREATED,
)
def add_alternative(
    device_id: uuid.UUID,
    body: AlternativeCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Append an alternative. One direction only, unless `also_reverse`."""
    device = _device_or_404(db, membership, device_id)
    try:
        _, reverse = device_links.add_alternative(
            db,
            device,
            actor=audit.actor_from(request, membership=membership),
            alternative_device_id=body.alternative_device_id,
            note=body.note,
            price_hint=body.price_hint,
            also_reverse=body.also_reverse,
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from None
    except device_service.DeviceConflict as error:
        db.rollback()
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        db.rollback()
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    return _alternatives(db, device, reverse_added=(reverse is not None) if body.also_reverse else None)


@router.patch("/devices/{device_id}/alternatives/{alternative_id}", response_model=DeviceAlternatives)
def update_alternative(
    device_id: uuid.UUID,
    alternative_id: uuid.UUID,
    body: AlternativeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    device = _device_or_404(db, membership, device_id)
    row = _alternative_or_404(db, membership, device, alternative_id)
    try:
        device_links.update_alternative(
            db, row, actor=audit.actor_from(request, membership=membership), changes=body.model_dump(exclude_unset=True)
        )
    except device_service.DeviceError as error:
        db.rollback()
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    return _alternatives(db, device)


@router.post("/devices/{device_id}/alternatives/{alternative_id}/move", response_model=DeviceAlternatives)
def move_alternative(
    device_id: uuid.UUID,
    alternative_id: uuid.UUID,
    body: AlternativeMove,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """One place up or down; audited as `device_alternative.reordered`."""
    device = _device_or_404(db, membership, device_id)
    row = _alternative_or_404(db, membership, device, alternative_id)
    if not row.is_active:
        _refuse(ValueError("This alternative was removed. Add it again instead."), status.HTTP_422_UNPROCESSABLE_ENTITY)
    device_links.move_alternative(db, device, row, actor=audit.actor_from(request, membership=membership), direction=body.direction)
    db.commit()
    return _alternatives(db, device)


@router.post("/devices/{device_id}/alternatives/{alternative_id}/remove", response_model=DeviceAlternatives)
def remove_alternative(
    device_id: uuid.UUID,
    alternative_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """The row goes inactive; no DELETE."""
    device = _device_or_404(db, membership, device_id)
    row = _alternative_or_404(db, membership, device, alternative_id)
    device_links.remove_alternative(db, row, actor=audit.actor_from(request, membership=membership))
    db.commit()
    return _alternatives(db, device)
