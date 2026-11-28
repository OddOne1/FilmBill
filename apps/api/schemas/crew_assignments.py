"""Crew on the calendar (B2c-1): assignments, days, clashes, availability.

`day_rate` and every rate here is cost data: null, with `rates_visible:
false`, for a member without `budgets.view`. Clashes are warnings — every
response that can produce one lists them, and nothing is refused for one.
"""

import datetime as dt
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .location import LocationRef

from ..core.money import Money

AssignmentStatus = Literal["scheduled", "booked", "declined", "cancelled"]
DayState = Literal["free", "scheduled", "booked", "unavailable"]


class CrewClashResponse(BaseModel):
    """One thing a person's days run into. `message` is the sentence to show."""

    kind: Literal["assignment", "unavailable"]
    party_id: uuid.UUID
    party_name: str
    dates: list[dt.date]
    message: str
    assignment_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    project_number: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    status: Optional[AssignmentStatus] = None
    unavailability_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None


class CrewAssignmentDayResponse(BaseModel):
    date: dt.date
    #: This person's own call and wrap on the day, if set.
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None
    #: Own, else the day's, else the strip's default.
    effective_call_time: Optional[dt.time] = None
    effective_wrap_time: Optional[dt.time] = None
    #: The project day's type on that date (`shoot`, `travel` …), null for a
    #: date in the strip's range with no day row.
    day_type: Optional[str] = None


class CrewAssignmentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    block_id: uuid.UUID
    party_id: uuid.UUID
    party_name: str
    role_item_id: Optional[uuid.UUID] = None
    role_name: Optional[str] = None
    status: AssignmentStatus
    rates_visible: bool = False
    day_rate: Optional[Money] = None
    status_note: Optional[str] = None
    booked_on: Optional[dt.datetime] = None
    booked_by_name: Optional[str] = None
    days: list[CrewAssignmentDayResponse] = []
    clashes: list[CrewClashResponse] = []
    #: L1. Where this person sleeps — only for a caller who may see hotels
    #: (`hotel_visible`); null and false otherwise.
    hotel_visible: bool = False
    hotel: Optional[LocationRef] = None
    #: L1. Directions from the hotel to the effective location of the
    #: person's first day — a link built by the server; nothing is fetched.
    route_link: Optional[str] = None
    created_at: dt.datetime


class CrewHotelSet(BaseModel):
    """L1. A person's hotel on this strip; null clears it."""

    model_config = ConfigDict(extra="forbid")

    location_id: Optional[uuid.UUID] = None


class CrewStripCounts(BaseModel):
    scheduled: int = 0
    booked: int = 0
    declined: int = 0
    #: How many of the people on the strip have at least one clash.
    with_clashes: int = 0


class CrewNewPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_id: uuid.UUID
    #: A catalog labor item. The day rate is copied from the person's
    #: effective rate in it.
    role_item_id: Optional[uuid.UUID] = None
    #: Omitted: every day of the strip that is not `off`.
    dates: Optional[list[dt.date]] = None


class CrewAssignmentsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people: list[CrewNewPerson] = Field(min_length=1, max_length=100)


class CrewAssignmentsCreated(BaseModel):
    assignments: list[CrewAssignmentResponse]
    #: Every clash of every new assignment, for one warning list.
    clashes: list[CrewClashResponse] = []


class CrewDayWish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None


class CrewAssignmentUpdate(BaseModel):
    """Only the fields sent change. `days` replaces the ticked days (with
    their own call and wrap); a day left out is unticked."""

    model_config = ConfigDict(extra="forbid")

    role_item_id: Optional[uuid.UUID] = None
    day_rate: Optional[Money] = None
    days: Optional[list[CrewDayWish]] = None


class CrewStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["scheduled", "booked", "declined", "cancelled"]
    #: How a booking was confirmed ("by phone"), or why. Required to reopen
    #: a booking.
    note: Optional[str] = Field(default=None, max_length=2000)


class CrewBulkStatusChange(CrewStatusChange):
    assignment_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class CrewBulkSkipped(BaseModel):
    assignment_id: uuid.UUID
    party_name: str
    reason: str


class CrewBulkStatusResult(BaseModel):
    changed: int
    skipped: list[CrewBulkSkipped] = []
    assignments: list[CrewAssignmentResponse] = []


class ProjectCrewStrip(BaseModel):
    block_id: uuid.UUID
    strip_name: str
    type: str
    start_date: dt.date
    end_date: dt.date
    counts: CrewStripCounts
    assignments: list[CrewAssignmentResponse] = []


class ProjectCrewResponse(BaseModel):
    project_id: uuid.UUID
    rates_visible: bool
    strips: list[ProjectCrewStrip] = []


class AvailabilityEntry(BaseModel):
    kind: Literal["assignment", "unavailable"]
    status: Optional[AssignmentStatus] = None
    assignment_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    block_id: Optional[uuid.UUID] = None
    strip_name: Optional[str] = None
    unavailability_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None


class AvailabilityDay(BaseModel):
    date: dt.date
    state: DayState
    #: More than one thing on the day: a clash.
    clash: bool = False
    entries: list[AvailabilityEntry] = []


class AvailabilityPerson(BaseModel):
    party_id: uuid.UUID
    name: str
    roles: list[str] = []
    #: The effective rate in the role filtered by, else the lowest; null
    #: without `budgets.view`.
    rate: Optional[Money] = None
    days: list[AvailabilityDay] = []
    #: Counts over the range: how many days are free.
    free_days: int = 0
    #: R2. Linked to a login: the picker's "Team" chip.
    is_team_member: bool = False


class AvailabilityResponse(BaseModel):
    range_start: dt.date
    range_end: dt.date
    rates_visible: bool
    people: list[AvailabilityPerson] = []


class UnavailabilityResponse(BaseModel):
    id: uuid.UUID
    party_id: uuid.UUID
    start_date: dt.date
    end_date: dt.date
    reason: Optional[str] = None
    note: Optional[str] = None
    created_at: dt.datetime


class UnavailabilityList(BaseModel):
    party_id: uuid.UUID
    periods: list[UnavailabilityResponse] = []


class UnavailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: dt.date
    end_date: dt.date
    reason: Optional[str] = Field(default=None, max_length=255)
    note: Optional[str] = None


class UnavailabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    reason: Optional[str] = Field(default=None, max_length=255)
    note: Optional[str] = None
