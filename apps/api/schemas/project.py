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
