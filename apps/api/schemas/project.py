"""The wire shapes for projects and production days.

Two things worth knowing.

**The range request's field really is called `from`.** It is a Python keyword,
so the model field is `from_` with `alias="from"` — the alias is what the
OpenAPI document and therefore the generated web types carry, because the
screen's own vocabulary is "from … to …" and `from_date` in a JSON body would
be an artefact of the server's implementation language leaking into its API.

**Dates are annotated `dt.date`, not `date`.** A field called `date` with a
default — `date: Optional[date] = None` — makes Pydantic resolve the
annotation against the class namespace, where `date` is by then the default
`None`, and the field silently becomes `NoneType`: every date sent to it is
refused with "Input should be None". It passed `py_compile`, it passed
import, and it failed only when a request arrived (rule 17b: the harness was
kinder than production). Qualifying the module is the fix that cannot come
back.

**A day count is part of a project's response.** The Days tab needs it, the
Budget's `days:<type>` globals are counted from the same rows, and a screen
that had to fetch the days to say "5 shoot days" would be a screen that says
nothing until the second request lands.
"""

import datetime as dt
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .location import LocationRef

from ..models.project import PROJECT_DAY_TYPES, PROJECT_STATUSES

ProjectStatus = Literal["planning", "active", "wrapped", "archived"]
ProjectDayType = Literal["prep", "shoot", "travel", "wrap", "post", "office", "off"]

# Written out so the OpenAPI generator can see them, asserted so they cannot
# drift from the model's vocabulary — `schemas/calc.py`'s pattern and its
# reasoning.
assert set(ProjectStatus.__args__) == set(PROJECT_STATUSES), (
    "schemas/project.py lists different project statuses from models/project.py"
)
assert set(ProjectDayType.__args__) == set(PROJECT_DAY_TYPES), (
    "schemas/project.py lists different day types from models/project.py"
)


# ─── Days ───────────────────────────────────────────────────────────────────


class ProjectDayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    date: dt.date
    type: str
    location: Optional[str] = None
    #: L1. The day's own location record, if it has one.
    location_id: Optional[uuid.UUID] = None
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None
    is_active: bool = True
    created_at: dt.datetime
    updated_at: dt.datetime


class ProjectDayWrite(BaseModel):
    """One day, created on its own. `date` and `type` identify it."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date
    type: ProjectDayType = "shoot"
    location: Optional[str] = Field(default=None, max_length=255)
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None


class ProjectDayUpdate(BaseModel):
    """A day's editable fields. `date` and `type` can move too — a shoot day
    that slips a week is the same planned day on another date, and making
    somebody delete and re-add it would lose its call time and its notes."""

    model_config = ConfigDict(extra="forbid")

    date: Optional[dt.date] = None
    type: Optional[ProjectDayType] = None
    location: Optional[str] = Field(default=None, max_length=255)
    #: L1. A location of kind shoot or other for this day only; null clears
    #: it, and the day falls back to its strip's location.
    location_id: Optional[uuid.UUID] = None
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None


class ProjectDayRangeRequest(BaseModel):
    """"Add the 5th to the 12th as shoot days", in one transaction."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: dt.date = Field(alias="from")
    to: dt.date
    type: ProjectDayType = "shoot"
    #: Saturdays and Sundays left out. Public holidays are NOT considered —
    #: a holiday calendar is region-pack data that P7 brings (SCOPE §11.4),
    #: and guessing one now would put Austrian holidays on a German shoot.
    skip_weekends: bool = False


class ProjectDayRangeResponse(BaseModel):
    """What the range did, in numbers and in a sentence.

    `existing` and `skipped` are both reported because the number of days
    created is otherwise different from the number asked for with no
    explanation — rule 17c applied to a bulk action.
    """

    created: list[ProjectDayResponse]
    existing: list[dt.date]
    skipped: list[dt.date]
    detail: str


class ProjectDayList(BaseModel):
    days: list[ProjectDayResponse]
    #: `type → count` over the ACTIVE days, which is what a `days:<type>`
    #: budget global is counted from.
    counts: dict[str, int]


# ─── Projects ───────────────────────────────────────────────────────────────


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_number: str
    name: str
    client_party_id: Optional[uuid.UUID] = None
    #: Resolved for the list screen. A project list that showed a UUID in the
    #: client column would need a second request per row to be readable.
    client_name: Optional[str] = None
    description: Optional[str] = None
    status: str
    start_date: dt.date
    end_date: Optional[dt.date] = None
    notes: Optional[str] = None
    day_counts: dict[str, int] = Field(default_factory=dict)
    budget_count: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class ProjectPage(BaseModel):
    projects: list[ProjectResponse]
    total: int
    limit: int
    offset: int


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    #: Must be a party of the SAME company. The router checks it and answers
    #: 404 for another company's — a 422 naming the field would confirm that
    #: the id exists somewhere, which is the leak rule 6 exists to prevent.
    client_party_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    status: ProjectStatus = "planning"
    start_date: dt.date
    end_date: Optional[dt.date] = None
    notes: Optional[str] = None


class ProjectUpdate(BaseModel):
    """Everything but the status and the number.

    The status moves through `POST /projects/{id}/status`, which writes a
    `project.status_changed` event of its own: "this project went active on
    the 3rd" is a different question from "somebody corrected the name", and
    one audit action for both would make the first unanswerable.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    client_party_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    notes: Optional[str] = None


class ProjectStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ProjectStatus
    #: Written into the audit event. Optional, because requiring a reason for
    #: every status change is how a field becomes "x".
    reason: Optional[str] = Field(default=None, max_length=1000)


# ─── The calendar: strips over days (B2a) ───────────────────────────────────

#: How a new strip fills Saturdays and Sundays: as `off` days, or as days of
#: the strip's own type.
WeekendRule = Literal["off", "same"]


class CalendarDayResponse(BaseModel):
    """One day as the calendar draws it.

    `location`, `call_time` and `wrap_time` are the day's OWN values; the
    `effective_*` ones fall back to the strip's defaults, which is what a day
    with nothing of its own shows. `is_override` says the day's type is not
    its strip's main type — a travel day inside a shoot strip.
    """

    id: uuid.UUID
    block_id: Optional[uuid.UUID] = None
    date: dt.date
    type: str
    location: Optional[str] = None
    call_time: Optional[dt.time] = None
    wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None
    effective_location: Optional[str] = None
    #: L1. The location RECORDS: the day's own (`location_ref`), and the one
    #: it happens at (`effective_location_ref`) — its own, else its strip's,
    #: with `effective_location_source` saying which. An archived one is
    #: still returned, `is_active: false`.
    location_id: Optional[uuid.UUID] = None
    location_ref: Optional[LocationRef] = None
    effective_location_ref: Optional[LocationRef] = None
    effective_location_source: Optional[Literal["day", "strip"]] = None
    effective_call_time: Optional[dt.time] = None
    effective_wrap_time: Optional[dt.time] = None
    is_override: bool = False
    #: Notes, a location or a time of its own — what a resize asks about
    #: before it takes the day out.
    has_own_data: bool = False
    updated_at: dt.datetime


class CalendarStripResponse(BaseModel):
    """A strip, or a day outside any strip drawn as a one-day strip.

    `kind = "day"` is a day made through the old `/days` endpoints: its `id`
    is the DAY's id, it has no label or defaults, and it is edited through
    the day endpoints rather than the strip ones.
    """

    id: uuid.UUID
    kind: Literal["strip", "day"]
    type: str
    label: Optional[str] = None
    start_date: dt.date
    end_date: dt.date
    #: The B2a free text, unchanged — shown as "Old text" where set.
    location: Optional[str] = None
    #: L1. The strip's location record (a shooting location).
    location_id: Optional[uuid.UUID] = None
    location_ref: Optional[LocationRef] = None
    default_call_time: Optional[dt.time] = None
    default_wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None
    position: Optional[int] = None
    is_active: bool = True
    days: list[CalendarDayResponse]
    #: `type → days` inside this strip, counted by the API like every count.
    counts: dict[str, int] = Field(default_factory=dict)


class ProjectCalendarResponse(BaseModel):
    project_id: uuid.UUID
    project_start: dt.date
    project_end: Optional[dt.date] = None
    #: The range asked for, or the project's whole span when none was.
    range_start: Optional[dt.date] = None
    range_end: Optional[dt.date] = None
    strips: list[CalendarStripResponse]
    #: `type → distinct dates` for the WHOLE project, whatever the range —
    #: the same number the budget's `days:<type>` globals count. The screen
    #: never counts.
    counts: dict[str, int]


class BlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProjectDayType = "shoot"
    label: Optional[str] = Field(default=None, max_length=255)
    start_date: dt.date
    end_date: dt.date
    location: Optional[str] = Field(default=None, max_length=255)
    default_call_time: Optional[dt.time] = None
    default_wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None
    weekends: WeekendRule = "same"


class BlockUpdate(BaseModel):
    """A strip's own fields. Its dates move through `move` and `resize`,
    which say what they do to its days; changing the main type carries every
    day of the old main type along and leaves overrides alone."""

    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, max_length=255)
    type: Optional[ProjectDayType] = None
    location: Optional[str] = Field(default=None, max_length=255)
    #: L1. The strip's location: a location marked as a shooting location;
    #: null clears it. Moving or resizing the strip never touches it.
    location_id: Optional[uuid.UUID] = None
    default_call_time: Optional[dt.time] = None
    default_wrap_time: Optional[dt.time] = None
    notes: Optional[str] = None
    position: Optional[int] = None


class BlockMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: dt.date


class BlockResize(BaseModel):
    """New start and/or end. Growing adds days of the main type; shrinking
    takes days out, and asks first (`confirm`) when one of them has notes, a
    location or a time of its own."""

    model_config = ConfigDict(extra="forbid")

    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    confirm: bool = False


class BlockDayEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    #: NULL returns the day to the strip's main type.
    type: Optional[ProjectDayType] = None


class BlockDaysRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[BlockDayEntry] = Field(min_length=1, max_length=731)


class BlockDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: dt.date


class BlockChangeResponse(BaseModel):
    """What a strip action did.

    `applied = False` with `needs_confirm = True` is a resize that would take
    out days carrying data: nothing changed, `days_with_data` lists them,
    and sending the same request with `confirm: true` goes ahead.
    """

    applied: bool = True
    needs_confirm: bool = False
    detail: str
    strip: Optional[CalendarStripResponse] = None
    days_with_data: list[CalendarDayResponse] = Field(default_factory=list)
    added: int = 0
    removed: int = 0
    changed: int = 0
    #: The project's counts after the action, so the screen can update its
    #: header from this response rather than counting.
    counts: dict[str, int] = Field(default_factory=dict)


class BlockDaysLocation(BaseModel):
    """L1. "Set for selected days": these days of the strip get this
    location of their own; null gives them back the strip's."""

    model_config = ConfigDict(extra="forbid")

    dates: list[dt.date] = Field(min_length=1, max_length=731)
    location_id: Optional[uuid.UUID] = None

