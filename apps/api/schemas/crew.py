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

CrewSort = Literal["name", "-name", "role", "-role", "created", "-created"]


class CrewMemberResponse(BaseModel):
    id: uuid.UUID
    name: str
    #: The party's `tags`: free text such as "DoP", "Gaffer", "Sound".
    roles: list[str] = []
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
    created_at: datetime


class CrewPage(BaseModel):
    members: list[CrewMemberResponse]
    total: int


class CrewCreate(BaseModel):
    """Quick add. The name is the only thing required."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    roles: list[str] = []
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None


class CrewDuplicateWarning(BaseModel):
    """"This might be somebody you already have." Never blocking."""

    party_id: uuid.UUID
    name: str
    reason: str
    detail: str


class CrewCreateResponse(BaseModel):
    member: CrewMemberResponse
    duplicates: list[CrewDuplicateWarning] = []


class CrewRoleSuggestions(BaseModel):
    #: Roles already given to somebody in the crew list, most used first.
    used: list[str] = []
    #: Names of the catalog's active labor items, as extra suggestions.
    catalog: list[str] = []


class CrewImportRowResponse(BaseModel):
    line_number: int
    #: `create`, `duplicate` or `error` in a preview; after an apply also
    #: `created` and `skipped`.
    action: str
    name: Optional[str] = None
    roles: list[str] = []
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
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
