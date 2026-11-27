"""The crew list: people in the parties list with the `is_crew` flag (B2b).

A crew member is not a resource of its own — it is a `Party` — so these
shapes are a narrow VIEW of one: the five things the crew page shows and the
quick-add form asks for. Anything more is the party's own detail page, one
click away, through the ordinary party endpoints.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..core.money import Money

#: R2. The crew list's Team / Freelance filter.
CrewKind = Literal["team", "freelance"]
CrewSort = Literal["name", "-name", "role", "-role", "created", "-created", "rate", "-rate"]
RateSource = Literal["own", "typical", "none"]


class CrewRoleResponse(BaseModel):
    """One of a person's roles: a catalog labor item, and what the person
    costs per day in it. Every number is null, and `rate_source` too, for a
    member without `budgets.view` — see `rates_visible` beside it."""

    id: uuid.UUID
    item_id: uuid.UUID
    #: The labor item's name.
    name: str
    notes: Optional[str] = None
    own_day_rate: Optional[Money] = None
    #: The item's cost rate per day.
    typical_day_rate: Optional[Money] = None
    #: Own if set, else typical, else null.
    effective_day_rate: Optional[Money] = None
    rate_source: Optional[RateSource] = None
    #: own − typical, when both exist; negative when the person is cheaper.
    difference_to_typical: Optional[Money] = None
    #: R2. False for a role a team member set for themselves that no admin
    #: has confirmed yet ("self-reported"); `set_by` says who wrote it.
    confirmed: bool = True
    set_by: Literal["admin", "self"] = "admin"


class CrewTagSuggestion(BaseModel):
    """"Use 'Gaffer' as a role": a tag that matches a labor item."""

    tag: str
    item_id: uuid.UUID
    item_name: str


class PartyCrewRolesResponse(BaseModel):
    party_id: uuid.UUID
    is_crew: bool
    rates_visible: bool
    roles: list[CrewRoleResponse] = []
    tag_suggestions: list[CrewTagSuggestion] = []


class CrewRoleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    #: Net, company currency, per day. Empty = the typical rate.
    own_day_rate: Optional[Money] = None
    notes: Optional[str] = None


class CrewRoleUpdate(BaseModel):
    """Only the fields sent change; `own_day_rate: null` clears the own rate."""

    model_config = ConfigDict(extra="forbid")

    own_day_rate: Optional[Money] = None
    notes: Optional[str] = None


class CrewRoleFromTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)


class CrewMemberResponse(BaseModel):
    id: uuid.UUID
    name: str
    #: P1b-4. Null for somebody whose name has not been split yet.
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    #: The party's `tags`: free text such as "Drone pilot" that is not (yet)
    #: a catalog labor item. The roles that ARE labor items are `crew_roles`.
    roles: list[str] = []
    #: R1. The person's roles from the catalog, with rates for `budgets.view`.
    crew_roles: list[CrewRoleResponse] = []
    rates_visible: bool = False
    #: R1. The effective rate of the role filtered by, or the person's lowest
    #: and highest over all roles; null without `budgets.view`.
    rate_low: Optional[Money] = None
    rate_high: Optional[Money] = None
    #: R1. With a role filter: whether that role's rate is the person's own
    #: or the typical one.
    rate_source: Optional[RateSource] = None
    #: The primary email and phone, if any.
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True
    #: The other hats, so the list can say "also a supplier".
    is_customer: bool = False
    is_supplier: bool = False
    #: Only ever set when the party is a customer or a supplier too: crew
    #: alone consumes no number.
    party_number: Optional[str] = None
    #: R2. Linked to a login of this company: a team member, shown with a
    #: "Team" chip. Rates, roles and booking are the same as for anybody.
    is_team_member: bool = False
    created_at: datetime


class CrewPage(BaseModel):
    members: list[CrewMemberResponse]
    total: int


class CrewCreate(BaseModel):
    """Quick add. A first or last name is the only thing required.

    P1b-4: `name` is the older single field, still accepted and stored with
    no parts; when a first or last name is given, `name` is derived.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, max_length=255)
    first_name: Optional[str] = Field(default=None, max_length=255)
    middle_name: Optional[str] = Field(default=None, max_length=255)
    last_name: Optional[str] = Field(default=None, max_length=255)
    roles: list[str] = []
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None
    #: R1. The person's own day rate in their first role that is a catalog
    #: labor item. Needs `budgets.view`.
    day_rate: Optional[Money] = None


class CrewDuplicateWarning(BaseModel):
    """"This might be somebody you already have." Never blocking."""

    party_id: uuid.UUID
    name: str
    reason: str
    detail: str


class CrewCreateResponse(BaseModel):
    member: CrewMemberResponse
    duplicates: list[CrewDuplicateWarning] = []
    #: R1. Roles typed that are no labor item in the catalog: kept as tags.
    unknown_roles: list[str] = []
    notes: list[str] = []


class CrewLaborItem(BaseModel):
    item_id: uuid.UUID
    name: str


class CrewRoleSuggestions(BaseModel):
    #: Roles already given to somebody in the crew list, most used first.
    used: list[str] = []
    #: Names of the catalog's active labor items, as extra suggestions.
    catalog: list[str] = []
    #: R1. The catalog's active labor items — the roles a filter or a
    #: person's role can be — by id and name.
    labor_items: list[CrewLaborItem] = []


class CrewImportRowResponse(BaseModel):
    line_number: int
    #: `create`, `duplicate` or `error` in a preview; after an apply also
    #: `created` and `skipped`.
    action: str
    name: Optional[str] = None
    #: P1b-4. Empty for a line that used the older single `name` column;
    #: such a person is created with the name as given, not split.
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    roles: list[str] = []
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    #: R1. Roles matched to catalog labor items, and the ones that stay tags.
    matched_roles: list[str] = []
    unknown_roles: list[str] = []
    #: R1. Null without `budgets.view` (the column is then not read at all).
    day_rate: Optional[Money] = None
    #: R1. Sentences about the roles and the rate; never blocking.
    role_notes: list[str] = []
    message: Optional[str] = None
    duplicate_of_party_id: Optional[uuid.UUID] = None
    duplicate_of_name: Optional[str] = None
    duplicate_of_line: Optional[int] = None
    duplicate_reason: Optional[str] = None
    #: After an apply: the party that line created.
    party_id: Optional[uuid.UUID] = None


class CrewImportPreviewResponse(BaseModel):
    rows: list[CrewImportRowResponse]
    created: int
    duplicates: int
    errors: int
    digest: str
    delimiter: str
    ignored_columns: list[str] = []


class CrewImportApplyResponse(BaseModel):
    rows: list[CrewImportRowResponse]
    created: int
    skipped: int
    errors: int
    digest: str
