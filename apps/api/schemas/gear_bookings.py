"""Gear on the calendar (B2c-2): bookings, availability, warnings.

No prices anywhere: a booking carries a quantity, days and a status. Weight
is a string of kilograms with three decimals, like every decimal on the
wire. Overbooking is a warning — every response that can produce one lists
it, and nothing is refused for one.
"""

import datetime as dt
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

GearStatus = Literal["scheduled", "booked", "cancelled"]


class GearHolder(BaseModel):
    """A booking that holds the device: which project, which strip."""

    booking_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    project_number: str
    block_id: uuid.UUID
    strip_name: str
    status: GearStatus
    quantity: int
    #: The dates it holds the device, within what was asked about.
    dates: list[dt.date] = []


class GearDay(BaseModel):
    date: dt.date
    owned: int
    scheduled: int
    booked: int
    #: owned − scheduled − booked; negative when overbooked.
    available: int
    overbooked: bool
    shortage: int
    holders: list[GearHolder] = []


class DeviceAvailability(BaseModel):
    """A device over a range: the lowest day governs (SCOPE §4.4.7)."""

    device_id: uuid.UUID
    name: str
    category: Optional[str] = None
    thumbnail_url: Optional[str] = None
    owned: int
    min_available: int
    worst_day: Optional[dt.date] = None
    #: The worst day's numbers.
    scheduled: int
    booked: int
    overbooked: bool
    shortage: int
    days: list[GearDay] = []


class GearAvailabilityResponse(BaseModel):
    range_start: dt.date
    range_end: dt.date
    devices: list[DeviceAvailability]


class GearAlternativeOffer(BaseModel):
    """An alternative of an overbooked device, with its availability on the
    same days."""

    alternative_id: uuid.UUID
    device_id: uuid.UUID
    name: str
    thumbnail_url: Optional[str] = None
    position: int
    note: Optional[str] = None
    price_hint: Optional[str] = None
    owned: int
    min_available: int
    worst_day: Optional[dt.date] = None
    #: Enough free on every day for this booking's quantity.
    enough: bool


class GearWarning(BaseModel):
    kind: Literal["overbooked"] = "overbooked"
    booking_id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    owned: int
    #: How many are wanted here.
    quantity: int
    shortage: int
    worst_day: dt.date
    #: The days on which fewer are free than wanted here.
    dates: list[dt.date]
    message: str
    held_by: list[GearHolder] = []
    alternatives: list[GearAlternativeOffer] = []


class GearBookingResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    project_number: str
    block_id: uuid.UUID
    strip_name: str
    device_id: uuid.UUID
    device_name: str
    category: Optional[str] = None
    thumbnail_url: Optional[str] = None
    quantity: int
    status: GearStatus
    status_note: Optional[str] = None
    booked_on: Optional[dt.datetime] = None
    booked_by_name: Optional[str] = None
    added_via_booking_id: Optional[uuid.UUID] = None
    #: The parent's device, for "added with Camera A".
    added_via_device_name: Optional[str] = None
    association_id: Optional[uuid.UUID] = None
    quantity_per_parent: Optional[int] = None
    follows_parent: bool = False
    overbooking_note: Optional[str] = None
    removal_note: Optional[str] = None
    #: The device's weight per piece, kg, three decimals; null when unknown.
    weight_kg: Optional[str] = None
    dates: list[dt.date] = []
    warning: Optional[GearWarning] = None
    created_at: dt.datetime


class GearSuggestion(BaseModel):
    """An `ask` dependency: offered with its pre-calculated quantity, added
    only when chosen."""

    parent_device_id: uuid.UUID
    parent_device_name: str
    association_id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    thumbnail_url: Optional[str] = None
    quantity: int
    quantity_per_parent: int
    note: Optional[str] = None


class GearChosenDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    association_id: uuid.UUID
    #: Omitted: the pre-calculated quantity (parent × per parent).
    quantity: Optional[int] = Field(default=None, ge=1)


class GearNewBooking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=100000)
    #: Omitted: every day of the strip that is not `off`.
    dates: Optional[list[dt.date]] = None
    #: The `ask` dependencies ticked in the popup.
    chosen_dependencies: list[GearChosenDependency] = []
    overbooking_note: Optional[str] = Field(default=None, max_length=2000)


class GearBookingsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bookings: list[GearNewBooking] = Field(min_length=1, max_length=100)


class GearPlannedBooking(BaseModel):
    """What a create would write: the device, or one of its dependencies."""

    device_id: uuid.UUID
    device_name: str
    thumbnail_url: Optional[str] = None
    quantity: int
    via: Literal["direct", "always", "ask"]
    parent_device_id: Optional[uuid.UUID] = None
    association_id: Optional[uuid.UUID] = None
    dates: list[dt.date] = []
    #: The device's numbers on these days BEFORE this request.
    owned: int
    min_available: int
    #: What would be left after it (may be negative).
    available_after: int


class GearBookingsPreview(BaseModel):
    bookings: list[GearPlannedBooking]
    suggestions: list[GearSuggestion] = []


class GearBookingsCreated(BaseModel):
    bookings: list[GearBookingResponse]
    #: Every overbooking among the new rows, for one warning list.
    warnings: list[GearWarning] = []
    #: The `ask` dependencies that were not chosen.
    suggestions: list[GearSuggestion] = []


class GearBookingUpdate(BaseModel):
    """Only the fields sent change. `dates` replaces the ticked days;
    `device_id` swaps the device (days and status kept)."""

    model_config = ConfigDict(extra="forbid")

    quantity: Optional[int] = Field(default=None, ge=1, le=100000)
    dates: Optional[list[dt.date]] = None
    device_id: Optional[uuid.UUID] = None
    overbooking_note: Optional[str] = Field(default=None, max_length=2000)


class GearBookingUpdated(BaseModel):
    booking: GearBookingResponse
    warnings: list[GearWarning] = []
    #: Dependents whose quantity followed the new parent quantity.
    recalculated: list[GearBookingResponse] = []
    #: A dependent edited by hand no longer follows its parent.
    stopped_following: bool = False
    message: Optional[str] = None


class GearStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["scheduled", "booked"]
    #: How it was confirmed ("by phone"); required to reopen a booking.
    note: Optional[str] = Field(default=None, max_length=2000)


class GearBulkStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: Literal["scheduled", "booked"]
    note: Optional[str] = Field(default=None, max_length=2000)


class GearBulkSkipped(BaseModel):
    booking_id: uuid.UUID
    device_name: str
    reason: str


class GearBulkStatusResult(BaseModel):
    changed: int
    skipped: list[GearBulkSkipped] = []
    bookings: list[GearBookingResponse] = []


class GearRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=2000)
    #: What happens to the dependents of a parent: required when it has any.
    dependents: Optional[Literal["cancel", "keep"]] = None


class GearRemoveResult(BaseModel):
    removed: bool
    booking: GearBookingResponse
    #: The parent's open dependents: the question (`removed` false), or
    #: the ones the answer cancelled or kept.
    dependents: list[GearBookingResponse] = []
    dependents_choice: Optional[Literal["cancel", "keep"]] = None
    question: Optional[str] = None


class GearStripCounts(BaseModel):
    """Pieces, not bookings: 2 cameras and 4 batteries are 6."""

    scheduled: int = 0
    booked: int = 0
    #: How many bookings on the strip are overbooked.
    overbooked: int = 0


class ProjectGearStrip(BaseModel):
    block_id: uuid.UUID
    strip_name: str
    type: str
    start_date: dt.date
    end_date: dt.date
    counts: GearStripCounts
    #: Σ quantity × weight, kg, three decimals.
    total_weight_kg: str
    #: Pieces whose device has no weight.
    items_without_weight: int
    bookings: list[GearBookingResponse] = []


class ProjectGearResponse(BaseModel):
    project_id: uuid.UUID
    counts: GearStripCounts
    total_weight_kg: str
    items_without_weight: int
    strips: list[ProjectGearStrip]


class DeviceBookingsResponse(BaseModel):
    device_id: uuid.UUID
    bookings: list[GearBookingResponse]
