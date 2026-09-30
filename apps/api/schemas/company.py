"""Request and response shapes for companies and memberships.

Money is not here yet — nothing in P0b-1 carries an amount — but the rule it
will follow is set by `default_currency` being a plain ISO code and by nothing
in this file inventing a numeric type. When amounts arrive (P0b-2) they
serialise as strings, via `core/money.py` (CLAUDE.md rule 1).
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ..models.company import CompanyRole


class CompanyBase(BaseModel):
    """The editable face of a company. Shared by create and update.

    Every field except `legal_name` is optional in both directions, and the
    update schema below turns the rest optional too — a settings form that
    PATCHes one field must not be able to blank the other twenty by omitting
    them.
    """

    legal_name: str = Field(min_length=1, max_length=255)
    trading_name: Optional[str] = Field(default=None, max_length=255)
    legal_form: Optional[str] = Field(default=None, max_length=64)
    register_number: Optional[str] = Field(default=None, max_length=64)
    register_court: Optional[str] = Field(default=None, max_length=255)
    vat_id: Optional[str] = Field(default=None, max_length=32)
    tax_number: Optional[str] = Field(default=None, max_length=64)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_zip: Optional[str] = Field(default=None, max_length=32)
    address_city: Optional[str] = Field(default=None, max_length=255)
    #: ISO 3166-1 alpha-2, upper-cased on the way in so that "at" and "AT"
    #: cannot both exist as the key a region pack is looked up by.
    address_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=64)
    website: Optional[str] = Field(default=None, max_length=255)
    default_currency: str = Field(default="EUR", min_length=3, max_length=3)
    default_language: str = Field(default="de", min_length=2, max_length=5)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("address_country", "default_currency")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    """Every field optional, including `legal_name`.

    Not `CompanyBase` with defaults: a PATCH needs to distinguish "not sent"
    from "sent as the default", and a subclass carrying `default_currency =
    "EUR"` would silently reset a company that trades in CHF whenever anyone
    saved the address form.
    """

    model_config = ConfigDict(extra="forbid")

    legal_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    trading_name: Optional[str] = Field(default=None, max_length=255)
    legal_form: Optional[str] = Field(default=None, max_length=64)
    register_number: Optional[str] = Field(default=None, max_length=64)
    register_court: Optional[str] = Field(default=None, max_length=255)
    vat_id: Optional[str] = Field(default=None, max_length=32)
    tax_number: Optional[str] = Field(default=None, max_length=64)
    address_street: Optional[str] = Field(default=None, max_length=255)
    address_zip: Optional[str] = Field(default=None, max_length=32)
    address_city: Optional[str] = Field(default=None, max_length=255)
    address_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=64)
    website: Optional[str] = Field(default=None, max_length=255)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    default_language: Optional[str] = Field(default=None, min_length=2, max_length=5)
    fiscal_year_start_month: Optional[int] = Field(default=None, ge=1, le=12)
    timezone: Optional[str] = Field(default=None, max_length=64)
    #: Which company roles must have 2FA here. `tax_advisor` may be listed
    #: and is redundant — that role always requires it (§4).
    require_2fa_roles: Optional[list[CompanyRole]] = None
    #: Whether this company's tax advisor also gets reports and exports.
    tax_advisor_reports: Optional[bool] = None

    @field_validator("address_country", "default_currency")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    trading_name: Optional[str] = None
    legal_form: Optional[str] = None
    register_number: Optional[str] = None
    register_court: Optional[str] = None
    vat_id: Optional[str] = None
    tax_number: Optional[str] = None
    address_street: Optional[str] = None
    address_zip: Optional[str] = None
    address_city: Optional[str] = None
    address_country: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    default_currency: str
    default_language: str
    fiscal_year_start_month: int
    timezone: str
    logo_s3_key: Optional[str] = None
    require_2fa_roles: list[CompanyRole] = []
    tax_advisor_reports: bool = False
    created_at: datetime
    archived_at: Optional[datetime] = None


class CompanySummary(BaseModel):
    """One entry in the company switcher.

    Carries the caller's own role and the permissions it implies, so the web
    app can render navigation without a second request per company. That list
    is presentation only — every endpoint runs its own `require()`, so a
    client that ignored it would just be a client whose extra menu entries
    all answer 404.
    """

    id: uuid.UUID
    legal_name: str
    trading_name: Optional[str] = None
    display_name: str
    default_currency: str
    role: CompanyRole
    permissions: list[str]
    #: When the caller's own access to this company lapses. NULL for the
    #: usual case; the switcher shows it for a tax advisor so an access that
    #: is about to end is not a surprise.
    expires_at: Optional[datetime] = None


class MemberResponse(BaseModel):
    """A membership, as the Members screen shows it."""

    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    role: CompanyRole
    status: str
    granted_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    #: Whether this row grants anything right now. Computed rather than left
    #: to the client to derive from the two timestamps above, because "is
    #: this person in" is the question the screen asks and two clients
    #: deriving it separately is how they come to disagree.
    is_active: bool
    #: Whether this person must have 2FA because of a role they hold, and
    #: whether they actually have it. Shown together so an owner can see at a
    #: glance that the advisor they invited has not finished enrolling.
    two_factor_enabled: bool = False


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: CompanyRole
    #: Used only when the address has no account yet, to address the invite.
    name: Optional[str] = Field(default=None, max_length=255)
    #: When this access should lapse. The reason the column exists: a tax
    #: advisor is engaged for a period, not forever.
    expires_at: Optional[datetime] = None


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[CompanyRole] = None
    #: Present-but-null means "clear the expiry"; absent means "leave it".
    #: Pydantic cannot express that difference in the type, so the router
    #: reads `model_fields_set` — see `update_member`.
    expires_at: Optional[datetime] = None
