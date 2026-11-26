"""Gear: devices and the serial numbers of their units (B2b part 2).

`devices.view` reads (staff included: the crew needs to know what exists),
`devices.edit` writes. A member without the key gets a 404 from `require()`.
Another company's device or unit is a 404, never a 403 (rule 6).

**Purchase prices are cost information.** Every response that could carry
one carries it only when the caller also holds `budgets.view`, and says so
with `price_visible`. That covers the list, the detail, the export and the
import preview.

**No DELETE route.** Devices are archived; units are retired.
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
from ..models.catalog import Item
from ..models.device import UNIT_RETIRED, Device, DeviceUnit
from ..schemas.device import (
    DeviceCounts,
    DeviceCreate,
    DeviceDetail,
    DeviceImportApplyResponse,
    DeviceImportPreviewResponse,
    DeviceImportRowResponse,
    DevicePage,
    DeviceResponse,
    DeviceUnitChangeResponse,
    DeviceUnitReactivate,
    DeviceUnitResponse,
    DeviceUnitRetire,
    DeviceUnitsAdd,
    DeviceUnitsAddResponse,
    DeviceUnitUpdate,
    DeviceUpdate,
    SkippedSerial,
)
from ..services import audit, device_service
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


def _detail(db: Session, membership: Membership, device: Device) -> DeviceDetail:
    base = _responses(db, membership, [device])[0]
    units = (
        db.query(DeviceUnit)
        .filter(DeviceUnit.device_id == device.id)
        .order_by(
            (DeviceUnit.status == UNIT_RETIRED).asc(),
            func.lower(DeviceUnit.serial_number),
        )
        .all()
    )
    return DeviceDetail(
        **base.model_dump(),
        units=[DeviceUnitResponse.model_validate(unit) for unit in units],
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
    units that are not retired; the price column is empty for a role that
    may not see purchase prices."""
    rows = _devices_query(db, membership, search=None, category=None, include_inactive=include_inactive).all()
    visible = _can_see_price(membership)
    serials: dict[uuid.UUID, list[str]] = {row.id: [] for row in rows}
    if rows:
        for device_id, serial in (
            db.query(DeviceUnit.device_id, DeviceUnit.serial_number)
            .filter(
                DeviceUnit.device_id.in_(list(serials)),
                DeviceUnit.status != UNIT_RETIRED,
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
    edits, archives or retires anything that exists."""
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


def _writes_price(membership: Membership, fields: dict) -> None:
    if "purchase_price" in fields and fields["purchase_price"] is not None and not _can_see_price(membership):
        _refuse(
            ValueError(
                "You cannot set a purchase price: prices are only for people who "
                "can see budgets. Leave the price empty."
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
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog item not found") from None
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
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog item not found") from None
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
    """Record one serial number or a pasted list. Serials already on this
    device, or repeated in the list, are listed and skipped. The quantity
    owned does not change."""
    device = _device_or_404(db, membership, device_id)
    try:
        created, skipped = device_service.add_units(
            db,
            device,
            actor=audit.actor_from(request, membership=membership),
            serial_numbers=body.serial_numbers,
            acquired_on=body.acquired_on,
            notes=body.notes,
        )
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    for unit in created:
        db.refresh(unit)
    db.refresh(device)
    return DeviceUnitsAddResponse(
        device=_detail(db, membership, device),
        created=[DeviceUnitResponse.model_validate(unit) for unit in created],
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
    """Correct a serial number, a date or a note; send a unit to repair or
    bring it back from repair. Retiring is `/retire`."""
    device = _device_or_404(db, membership, device_id)
    unit = _unit_or_404(db, membership, device, device_unit_id)
    try:
        device_service.update_unit(
            db,
            unit,
            actor=audit.actor_from(request, membership=membership),
            changes=body.model_dump(exclude_unset=True),
        )
    except device_service.DeviceConflict as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    db.commit()
    db.refresh(unit)
    db.refresh(device)
    return DeviceUnitChangeResponse(
        device=_detail(db, membership, device),
        unit=DeviceUnitResponse.model_validate(unit),
        quantity_before=device.quantity_owned,
        quantity_after=device.quantity_owned,
    )


@router.post("/devices/{device_id}/units/{device_unit_id}/retire", response_model=DeviceUnitChangeResponse)
def retire_unit(
    device_id: uuid.UUID,
    device_unit_id: uuid.UUID,
    body: DeviceUnitRetire,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("devices.edit")),
):
    """Retire a unit — destroyed, lost, sold, kept, or entered by mistake
    (no reason, a note). `reduce_quantity` defaults to yes."""
    device = _device_or_404(db, membership, device_id)
    unit = _unit_or_404(db, membership, device, device_unit_id)
    try:
        before, after = device_service.retire_unit(
            db,
            device,
            unit,
            actor=audit.actor_from(request, membership=membership),
            reason=body.reason,
            retired_on=body.retired_on,
            notes=body.notes,
            reduce_quantity=body.reduce_quantity,
        )
    except device_service.DeviceError as error:
        _refuse(error, status.HTTP_409_CONFLICT)
    db.commit()
    db.refresh(unit)
    db.refresh(device)
    return DeviceUnitChangeResponse(
        device=_detail(db, membership, device),
        unit=DeviceUnitResponse.model_validate(unit),
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
    """Bring a retired unit back. `raise_quantity` defaults to yes."""
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
        unit=DeviceUnitResponse.model_validate(unit),
        quantity_before=before,
        quantity_after=after,
    )
