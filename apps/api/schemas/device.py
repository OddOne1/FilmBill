"""Gear: devices and the serial numbers of their units (B2b part 2).

`purchase_price` is a `Money` string — and is `null` with `price_visible:
false` for a caller without `budgets.view`. The flag is the positive marker
a screen (and a test) can check, rather than inferring from an absent value
whether the price is unknown or withheld.
"""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..core.money import Money

UnitStatus = Literal["active", "in_repair", "retired"]
RetiredReason = Literal["destroyed", "lost", "sold", "kept"]


class DeviceCounts(BaseModel):
    """The two numbers and how they relate. Never an error, never corrected.

    `recorded` is every serial number on file, retired ones included;
    `not_yet_recorded` is how many of the owned quantity have no usable
    (non-retired) serial yet; `beyond_quantity` is how many usable serials
    there are above the quantity owned — allowed, and said in words.
    """

    owned: int
    recorded: int
    active: int
    in_repair: int
    retired: int
    not_yet_recorded: int
    beyond_quantity: int


class DeviceUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    serial_number: str
    status: UnitStatus
    acquired_on: Optional[date] = None
    retired_on: Optional[date] = None
    retired_reason: Optional[RetiredReason] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DeviceResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: Optional[str] = None
    quantity_owned: int
    #: Net, company currency. `null` unless `price_visible`.
    purchase_price: Optional[Money] = None
    #: Whether this caller may see purchase prices (`budgets.view`).
    price_visible: bool
    purchase_date: Optional[date] = None
    catalog_item_id: Optional[uuid.UUID] = None
    catalog_item_name: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    counts: DeviceCounts
    created_at: datetime
    updated_at: datetime


class DeviceDetail(DeviceResponse):
    units: list[DeviceUnitResponse] = []


class DevicePage(BaseModel):
    devices: list[DeviceResponse]
    total: int
    #: Categories already used by devices, then the names of the catalog's
    #: active equipment categories, as suggestions.
    categories: list[str] = []


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, max_length=255)
    quantity_owned: int = Field(default=0, ge=0, le=100_000)
    purchase_price: Optional[Money] = None
    purchase_date: Optional[date] = None
    catalog_item_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class DeviceUpdate(BaseModel):
    """Only what changes. `quantity_owned` may always be set by hand."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, max_length=255)
    quantity_owned: Optional[int] = Field(default=None, ge=0, le=100_000)
    purchase_price: Optional[Money] = None
    purchase_date: Optional[date] = None
    catalog_item_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class DeviceUnitsAdd(BaseModel):
    """One serial number, or a pasted list. Never changes the quantity."""

    model_config = ConfigDict(extra="forbid")

    serial_numbers: list[str] = Field(min_length=1, max_length=1000)
    acquired_on: Optional[date] = None
    notes: Optional[str] = None


class SkippedSerial(BaseModel):
    serial_number: str
    #: `already_recorded` (on this device) or `repeated` (twice in the list).
    reason: str
    detail: str


class DeviceUnitsAddResponse(BaseModel):
    device: DeviceDetail
    created: list[DeviceUnitResponse]
    skipped: list[SkippedSerial] = []


class DeviceUnitUpdate(BaseModel):
    """Corrections, and the in-repair round trip. Retiring is its own call."""

    model_config = ConfigDict(extra="forbid")

    serial_number: Optional[str] = Field(default=None, min_length=1, max_length=128)
    acquired_on: Optional[date] = None
    notes: Optional[str] = None
    status: Optional[Literal["active", "in_repair"]] = None


class DeviceUnitRetire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[RetiredReason] = None
    retired_on: Optional[date] = None
    notes: Optional[str] = None
    #: "Also reduce the quantity owned by 1?" — ticked by default: a retired
    #: unit cannot be booked.
    reduce_quantity: bool = True


class DeviceUnitReactivate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: "Also raise the quantity owned by 1?" — ticked by default.
    raise_quantity: bool = True


class DeviceUnitChangeResponse(BaseModel):
    device: DeviceDetail
    unit: DeviceUnitResponse
    #: What the quantity was and is, when this call changed it.
    quantity_before: int
    quantity_after: int


class DeviceImportRowResponse(BaseModel):
    line_number: int
    #: `create`, `duplicate`, `error`; after an apply also `created`, `skipped`.
    action: str
    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = None
    #: Shown only with `budgets.view`, like every purchase price.
    price: Optional[Money] = None
    serials: list[str] = []
    notes: Optional[str] = None
    message: Optional[str] = None
    can_include: bool = False
    duplicate_of_device_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None


class DeviceImportPreviewResponse(BaseModel):
    rows: list[DeviceImportRowResponse]
    created: int
    duplicates: int
    errors: int
    digest: str
    delimiter: str
    ignored_columns: list[str] = []


class DeviceImportApplyResponse(BaseModel):
    rows: list[DeviceImportRowResponse]
    created: int
    skipped: int
    errors: int
    units_created: int
    digest: str
