"""Request and response shapes for number series.

Nothing here formats or increments a number — that is
`services/number_series.py`, and rule 2 says there is one engine. These are
the shapes it is asked through, including the preview, which exists precisely
so the web app can show the next number without knowing how one is built.
"""

import uuid
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: `Literal`, not `str`, so `"yearly" | "never"` reaches OpenAPI and therefore
#: `apps/web/types/api.gen.ts`. The radio buttons on the Number series screen
#: are then typed by the API rather than by a list somebody retyped.
ResetMode = Literal["yearly", "never"]

#: Short of the column's 64 so a formatted number cannot outgrow
#: `number_allocations.number`. `{YYYY}-{SEQ}` expands, and padding is a
#: minimum rather than a maximum, so the headroom is deliberate.
MAX_PATTERN = 48


class NumberSeriesBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN)
    reset: ResetMode = "yearly"
    padding: int = Field(default=4, ge=1, le=12)

    @field_validator("pattern")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class NumberSeriesCreate(NumberSeriesBase):
    document_type: str = Field(min_length=1, max_length=32)
    #: Where the counter starts. Settable on create because a company
    #: migrating from another system continues its old numbering rather than
    #: starting again at 1 — which would re-issue every number it has already
    #: used.
    next_value: int = Field(default=1, ge=1)


class NumberSeriesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    pattern: Optional[str] = Field(default=None, min_length=1, max_length=MAX_PATTERN)
    reset: Optional[ResetMode] = None
    padding: Optional[int] = Field(default=None, ge=1, le=12)
    #: Lowering it is refused when it would re-issue a number this series has
    #: already allocated — see the router. Raising it is always allowed and
    #: makes a deliberate gap, which is a decision somebody is entitled to
    #: take (a company that reserved a block of numbers in its old system).
    next_value: Optional[int] = Field(default=None, ge=1)
    is_active: Optional[bool] = None

    @field_validator("pattern")
    @classmethod
    def _trim(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None


class NumberWarningResponse(BaseModel):
    """One thing the caller should know about a number before using it.

    `code` as well as `message`, because P2's finalize flow has to branch on
    it — show a confirmation — and branching on prose is how a translation
    breaks a gate.
    """

    code: str
    message: str
    latest_document_date: Optional[date] = None
    latest_number: Optional[str] = None


class NumberPreviewResponse(BaseModel):
    number: str
    value: int
    period: str
    warnings: list[NumberWarningResponse] = []
    #: Whether taking this number would restart the counter at 1 in a later
    #: period. The screen uses it to say what "Restart every year" will
    #: actually do on the next boundary.
    starts_new_period: bool = False


class NumberPreviewRequest(BaseModel):
    """Ask what a series would issue — without issuing it.

    The pattern, padding and mode are the CANDIDATE values, so the screen can
    preview an unsaved edit. `series_id` supplies the counter and the history;
    without one the preview is a brand-new series, which is what the create
    form needs.
    """

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, max_length=MAX_PATTERN)
    reset: ResetMode = "yearly"
    padding: int = Field(default=4, ge=1, le=12)
    series_id: Optional[uuid.UUID] = None
    #: Optional. Absent means "today, in this company's timezone" — which is
    #: not the same as today in UTC, and is the difference between an invoice
    #: dated 31 December and one dated 1 January.
    document_date: Optional[date] = None

    @field_validator("pattern")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class NumberSeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_type: str
    name: str
    pattern: str
    reset: ResetMode
    next_value: int
    current_period: Optional[str] = None
    padding: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    #: How many numbers this series has issued. Served because it is what
    #: decides whether an edit is safe, and the screen should be able to say
    #: "this series has issued 412 numbers" before somebody changes its
    #: pattern.
    allocated_count: int = 0
    #: The highest number issued so far, or None. Same reason.
    latest_number: Optional[str] = None
