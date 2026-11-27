"""Team members as crew (R2): the Team page's crew column, and a member's
own crew profile and schedule.

**No shape in this file carries a rate.** Day rates are edited on the
contact, by whoever may edit crew rates today; the Team page and the
member's own pages never show one. `test_team_crew_api.py` walks every
`/me` response for a rate-like key.
"""

import uuid
from datetime import date, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from ..models.company import CompanyRole

CrewState = Literal["not_crew", "crew", "needs_link"]
RoleSetBy = Literal["admin", "self"]


class TeamContact(BaseModel):
    """A person contact, as the Team page names it."""

    id: uuid.UUID
    name: str
    email: Optional[str] = None


class TeamRole(BaseModel):
    """One crew role (a catalog labor item), without any rate."""

    id: uuid.UUID
    item_id: uuid.UUID
    name: str
    #: False while self-reported and not yet confirmed by an admin.
    confirmed: bool
    set_by: RoleSetBy


class TeamMemberResponse(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str
    #: The membership role here; null for a linked contact whose login no
    #: longer has access ("no active login").
    role: Optional[CompanyRole] = None
    has_active_login: bool
    crew_state: CrewState
    #: The contact linked to this login, if any.
    contact: Optional[TeamContact] = None
    roles: list[TeamRole] = []
    #: Only with no link and EXACTLY one person contact with this email.
    suggestion: Optional[TeamContact] = None
    #: Every unlinked person contact with this email (the picker when there
    #: are several). Empty once linked.
    matches: list[TeamContact] = []


class TeamResponse(BaseModel):
    members: list[TeamMemberResponse]


class TeamCrewRequest(BaseModel):
    """"Works as crew": link the contact the admin confirmed, or create one
    from the login. `role_item_ids`, when sent, becomes the person's roles
    exactly — confirmed, because an admin chose them."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["link", "create"]
    party_id: Optional[uuid.UUID] = None
    role_item_ids: Optional[list[uuid.UUID]] = None


class FutureBookingResponse(BaseModel):
    assignment_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    strip_name: str
    status: str
    first_date: date
    last_date: date


class TeamStopResponse(BaseModel):
    """"Stops working as crew": nothing was cancelled; these stay as they are."""

    member: TeamMemberResponse
    future_bookings: list[FutureBookingResponse] = []


class LaborItemChoice(BaseModel):
    item_id: uuid.UUID
    name: str


class MyCrewResponse(BaseModel):
    """The caller's own crew profile. No rate, ever."""

    is_crew: bool
    crew_state: CrewState
    contact_name: Optional[str] = None
    roles: list[TeamRole] = []
    #: The roles one can pick: the catalog's active labor items.
    labor_items: list[LaborItemChoice] = []
    #: The needs-link sentence when a contact already has the caller's email
    #: and no admin has connected it yet.
    message: Optional[str] = None


class MyCrewSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_crew: bool


class MyCrewSwitchResponse(BaseModel):
    profile: MyCrewResponse
    #: When switching off: the bookings that stay as they are.
    future_bookings: list[FutureBookingResponse] = []


class MyCrewRoles(BaseModel):
    """The labor items I can do. Nothing else: no rate, no party id."""

    model_config = ConfigDict(extra="forbid")

    item_ids: list[uuid.UUID]


class MyScheduleDay(BaseModel):
    date: date
    call_time: Optional[time] = None
    wrap_time: Optional[time] = None
    location: Optional[str] = None


class MyScheduleEntry(BaseModel):
    """One of my assignments: a project's strip, my role, my days."""

    assignment_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    strip_name: str
    role_name: Optional[str] = None
    status: Literal["scheduled", "booked"]
    note: Optional[str] = None
    location: Optional[str] = None
    first_date: date
    last_date: date
    days: list[MyScheduleDay] = []


class MyScheduleResponse(BaseModel):
    #: Whether the caller is linked and crew; the menu hides My schedule
    #: otherwise, and the page says why.
    available: bool
    upcoming: list[MyScheduleEntry] = []
    past: list[MyScheduleEntry] = []
