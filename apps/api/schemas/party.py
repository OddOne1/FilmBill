"""The wire shapes for parties.

Two things are deliberate.

**`tax_override` is the engine's own shape**, not a translation of it:
`{"kind": "code", "code": "AT-20"}` or
`{"kind": "domestic_for_foreign_business"}`. The column stores that, the
resolver passes it straight to `propose_tax`, and there is no layer in
between that could disagree with the one in the tax router.

**Every resolved field carries its source.** `ResolvedFieldResponse` is a
value and a level, always — so the screen can grey an inherited value and
name where it came from. A value with no provenance is one somebody has to
guess the origin of, and on an invoice that guess is expensive.
"""

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models.party import BUSINESS_STATUSES, PARTY_TYPES
from .auth import JsonObject

PartyType = Literal["organisation", "department", "person"]
BusinessStatus = Literal["auto", "business", "private"]
AddressKind = Literal["billing", "delivery", "postal"]
ContactKind = Literal["email", "phone"]
ContactPurpose = Literal["general", "invoice"]
VatStatus = Literal[
    "valid",
    "invalid",
    "unavailable",
    "not_applicable",
    # P1b-2. `not_configured` is a deployment fact (the operator has not
    # supplied a credential the registry needs) and `not_checkable` a fact
    # about the world (no register exists we may query). Neither is a finding
    # about somebody's tax number, which is why neither is `invalid`.
    "not_configured",
    "not_checkable",
]


class PartyAddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    street: Optional[str] = None
    street2: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    is_default: bool = False


class PartyAddressWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AddressKind = "billing"
    street: Optional[str] = Field(default=None, max_length=255)
    street2: Optional[str] = Field(default=None, max_length=255)
    zip: Optional[str] = Field(default=None, max_length=32)
    city: Optional[str] = Field(default=None, max_length=255)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    is_default: bool = False

    @field_validator("country")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value and value.strip() else None


class PartyContactPointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    value: str
    label: Optional[str] = None
    purpose: str = "general"
    is_primary: bool = False


class PartyContactPointWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContactKind
    value: str = Field(min_length=1, max_length=255)
    label: Optional[str] = Field(default=None, max_length=64)
    purpose: ContactPurpose = "general"
    is_primary: bool = False


class PartyBankAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: Optional[str] = None
    #: Served in full to somebody holding `parties.view`, and MASKED in audit
    #: rows (D43's reasoning: an audit log must not become a way round the
    #: gate on the thing it describes).
    iban: str
    bic: Optional[str] = None
    bank_name: Optional[str] = None
    is_default: bool = False


class PartyBankAccountWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, max_length=64)
    iban: str = Field(min_length=5, max_length=42)
    bic: Optional[str] = Field(default=None, max_length=11)
    bank_name: Optional[str] = Field(default=None, max_length=255)
    is_default: bool = False


class PartyVatCheckResponse(BaseModel):
    """One VIES lookup, as stored. Append-only, so this never changes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Which identifier this was a check of. Nullable for the P1b rows the
    #: migration repointed when a party's number could not be matched.
    tax_id_id: Optional[uuid.UUID] = None
    vat_id: str
    #: Nullable now: a free-text identifier has no country.
    country: Optional[str] = None
    kind: str = "vat"
    #: Who answered — `vies`, `abn`, or `manual`. The last is a human
    #: confirmation and is shown as one, never as a registry check (D66).
    provider: str = "vies"
    trigger_kind: str = "manual"
    #: True only when the call carried our own VAT ID as requester AND a
    #: consultation reference came back. Everything else is a simple lookup,
    #: and the difference is what a tax advisor will ask about.
    qualified: bool = False
    registered_name: Optional[str] = None
    registered_address: Optional[str] = None
    name_match: Optional[str] = None
    address_match: Optional[str] = None
    registration_active: Optional[bool] = None
    gst_registered: Optional[bool] = None
    manual_note: Optional[str] = None
    evidence_reference: Optional[str] = None
    valid_until: Optional[datetime] = None
    change_detected: Optional[JsonObject] = None
    status: VatStatus
    #: Everything VIES returned, verbatim — the trader name and address as
    #: the member state has them, which is what the screen compares against
    #: the party's own with a "differs" marker.
    response: JsonObject = {}
    consultation_ref: Optional[str] = None
    checked_at: datetime
    #: A sentence for the screen. Not stored — composed from the status when
    #: the check runs, and empty when reading history.
    detail: str = ""
    #: Convenience for the chip: the name and address VIES gave, lifted out
    #: of `response` so the screen does not have to know its shape.
    trader_name: Optional[str] = None
    trader_address: Optional[str] = None


#: The seven kinds, as a Literal so the OpenAPI document lists them and the
#: generated TypeScript gets a union rather than `string`. Mirrors
#: `models.party.TAX_ID_KINDS`, and `test_party_tax_ids.py` asserts the two
#: agree — a drift would let the API accept a kind the normaliser cannot
#: handle.
TaxIdKind = Literal[
    "vat",
    "uid_ch",
    "abn",
    "ein",
    "tax_number",
    "company_register",
    "other",
]


class PartyTaxIdResponse(BaseModel):
    """One identifier, with its check state."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: TaxIdKind
    country: Optional[str] = None
    value: str
    #: What the user typed, kept verbatim. Normalisation is lossy and people
    #: recognise their own formatting.
    value_as_entered: Optional[str] = None
    is_primary: bool = False
    is_active: bool = True
    notes: Optional[str] = None
    #: The identifier this one replaced, when a value was changed. The
    #: history reads as a chain rather than as two unrelated numbers.
    replaces_id: Optional[uuid.UUID] = None

    # ── Served, not stored ───────────────────────────────────────────
    #: Whether any register can be asked about this kind at all. False for
    #: a Swiss UID (D61) and a US EIN — not an omission, a sourced decision,
    #: and the reason the screen offers a manual confirmation instead.
    verifiable: bool = False
    initial_checks_done: int = 0
    next_check_at: Optional[datetime] = None
    unavailable_streak: int = 0
    needs_attention: bool = False
    needs_attention_reason: Optional[str] = None
    #: The latest check, so a chip needs no second call.
    latest_check: Optional[PartyVatCheckResponse] = None
    created_at: datetime
    updated_at: datetime


class PartyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    parent_id: Optional[uuid.UUID] = None
    name: str
    trading_name: Optional[str] = None
    legal_form: Optional[str] = None
    register_number: Optional[str] = None
    register_court: Optional[str] = None
    title: Optional[str] = None
    position: Optional[str] = None
    is_customer: bool = False
    is_supplier: bool = False
    is_partner: bool = False
    tags: list[str] = []
    party_number: Optional[str] = None
    language: Optional[str] = None
    default_currency: Optional[str] = None
    payment_terms_days: Optional[int] = None
    payment_terms_text: Optional[str] = None
    po_reference: Optional[str] = None
    notes: Optional[str] = None
    business_status: str = "auto"
    tax_country: Optional[str] = None
    tax_status_note: Optional[str] = None
    recheck_interval_days: Optional[int] = None
    tax_override: Optional[JsonObject] = None
    einvoice_routing: JsonObject = {}
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    # ── Served, not stored ───────────────────────────────────────────
    #: Whether the latest check of this party's CURRENT VAT ID said `valid`
    #: (D56). Computed, because a stored flag would eventually describe a
    #: different number than the one in the field above it.
    vat_verified: bool = False
    #: The latest check, if there is one, so the chip needs no second call.
    latest_vat_check: Optional[PartyVatCheckResponse] = None
    #: `business`/`private` and why — `explicit`, `organisation`,
    #: `person_with_vat_id` or `person`. Derived by the resolver so the
    #: screen can show the reason rather than re-deriving it.
    buyer_type: Optional[str] = None
    buyer_type_reason: Optional[str] = None
    #: Sub-resources, served with the party because every screen that shows
    #: one shows all of them.
    #:
    #: `tax_ids` comes with the party because the Tax tab is one of the six
    #: a detail page renders, and a second request for it would be a second
    #: chance for a half-loaded screen.
    tax_ids: list[PartyTaxIdResponse] = []
    addresses: list[PartyAddressResponse] = []
    contact_points: list[PartyContactPointResponse] = []
    bank_accounts: list[PartyBankAccountResponse] = []
    #: Children, names only — the Hierarchy tab's list.
    children: list["PartyChild"] = []
    #: Warnings, never refusals: "this might be the same party you already
    #: have". A merge tool is later; blocking a create on a guess would be
    #: worse than a duplicate.
    duplicates: list["DuplicateWarning"] = []


class PartyChild(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    name: str
    position: Optional[str] = None
    party_number: Optional[str] = None
    is_active: bool = True


class DuplicateWarning(BaseModel):
    """"This might be one you already have." Never blocking."""

    party_id: uuid.UUID
    name: str
    #: `vat_id`, `email` or `name_and_zip` — so the screen can say which.
    reason: str
    detail: str


class PartyWrite(BaseModel):
    """Create and update share a shape; `type` is immutable after creation.

    `type` is absent from the update path because the tree's rules hang off
    it: turning a person into an organisation would have to re-validate its
    parent, its children and its number, and "delete it and make the right
    one" is both clearer and the only thing that keeps the history honest.
    """

    model_config = ConfigDict(extra="forbid")

    #: Optional here and REQUIRED on `PartyCreate`. A PATCH sends only what
    #: changed, so a required name would mean every edit had to re-send it —
    #: and a form that re-sends a field it did not touch is a form that
    #: eventually overwrites somebody else's change to it.
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    parent_id: Optional[uuid.UUID] = None
    trading_name: Optional[str] = Field(default=None, max_length=255)
    legal_form: Optional[str] = Field(default=None, max_length=64)
    register_number: Optional[str] = Field(default=None, max_length=64)
    register_court: Optional[str] = Field(default=None, max_length=255)
    title: Optional[str] = Field(default=None, max_length=64)
    position: Optional[str] = Field(default=None, max_length=255)
    is_customer: Optional[bool] = None
    is_supplier: Optional[bool] = None
    is_partner: Optional[bool] = None
    tags: Optional[list[str]] = None
    party_number: Optional[str] = Field(default=None, max_length=32)
    language: Optional[str] = Field(default=None, max_length=5)
    default_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    payment_terms_days: Optional[int] = Field(default=None, ge=0, le=365)
    payment_terms_text: Optional[str] = None
    po_reference: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = None
    business_status: Optional[BusinessStatus] = None
    tax_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    #: `vat_id` is NOT here. Identifiers are their own rows with their own
    #: endpoints (D59), because each kind normalises differently and a party
    #: PATCH that could still set a number would be a second write path for
    #: the thing this release exists to give one.
    #:
    #: Free text explaining a business that holds no number — the Austrian
    #: Kleinunternehmer case (D65).
    tax_status_note: Optional[str] = Field(default=None, max_length=500)
    #: Per-party override of the re-check interval, in days. None means the
    #: company's policy applies.
    recheck_interval_days: Optional[int] = Field(default=None, ge=1, le=3650)
    tax_override: Optional[JsonObject] = None
    einvoice_routing: Optional[JsonObject] = None

    @field_validator("tax_country", "default_currency")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value and value.strip() else None


class PartyCreate(PartyWrite):
    type: PartyType
    name: str = Field(min_length=1, max_length=255)


class PartyPage(BaseModel):
    """One page of the list, newest-relevant first."""

    parties: list[PartyResponse]
    total: int
    limit: int
    offset: int


class ResolvedFieldResponse(BaseModel):
    """A value and the level it came from. Both, always."""

    value: Optional[Any] = None
    #: `person`, `department` or `organisation`, or null when nothing in the
    #: chain had it.
    source: Optional[str] = None


class ResolvedRecipientResponse(BaseModel):
    """Exactly what a document addressed to this party would use.

    The shape P2 will snapshot. Every field carries its source so the screen
    can grey an inherited value and name its origin.
    """

    own_level: str
    ids: JsonObject = {}
    names: JsonObject = {}
    contact_name: ResolvedFieldResponse
    invoice_name: ResolvedFieldResponse
    billing_address: ResolvedFieldResponse
    vat_id: ResolvedFieldResponse
    vat_verified: ResolvedFieldResponse
    #: Which kind of identifier `vat_id` holds — `vat`, `uid_ch` or `abn`.
    #: Served so a screen can say "Swiss UID" rather than guessing from the
    #: string, and so a caller can tell an EU number from one that cannot
    #: carry a reverse charge (D59).
    tax_id_kind: ResolvedFieldResponse
    #: Why a business has no number at all (D65).
    tax_status_note: ResolvedFieldResponse
    #: The check `vat_verified` rests on, for P2 to snapshot (D64).
    tax_id_evidence: ResolvedFieldResponse
    tax_country: ResolvedFieldResponse
    business_status: ResolvedFieldResponse
    tax_override: ResolvedFieldResponse
    language: ResolvedFieldResponse
    default_currency: ResolvedFieldResponse
    payment_terms_days: ResolvedFieldResponse
    payment_terms_text: ResolvedFieldResponse
    po_reference: ResolvedFieldResponse
    email: ResolvedFieldResponse
    phone: ResolvedFieldResponse
    einvoice_routing: ResolvedFieldResponse
    buyer_type: str
    buyer_type_reason: str


class PartyTaxPreviewRequest(BaseModel):
    """Ask what tax a sale to this party would carry.

    The buyer side is NOT in here: it comes from the resolved recipient, so a
    caller cannot ask "what if this customer were German". The seller side
    comes from the company, as it does on `POST /company/tax/propose`.
    """

    model_config = ConfigDict(extra="forbid")

    supply_kind: Literal["goods", "services"] = "services"
    item_class: str = Field(default="standard", min_length=1, max_length=32)
    item_override: Optional[JsonObject] = None
    lang: str = Field(default="en", min_length=2, max_length=5)


PartyResponse.model_rebuild()


# ─── Tax identifiers (P1b-2) ────────────────────────────────────────────────


class PartyTaxIdCreate(BaseModel):
    """Adding an identifier.

    No `is_active`: a new identifier is active. Adding one already retired
    is not a thing anybody means to do, and offering it would be a field
    whose only use is a mistake.
    """

    model_config = ConfigDict(extra="forbid")

    kind: TaxIdKind
    value: str = Field(min_length=1, max_length=80)
    #: For `vat` the prefix usually supplies it; the free-text kinds have no
    #: way to know it, so it can be given.
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    #: Whether this becomes THE identifier the engine uses. Only one active
    #: VAT-like identifier per party may be primary, and the database
    #: enforces it; the endpoint moves the flag rather than refusing.
    is_primary: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class PartyTaxIdUpdate(BaseModel):
    """Changing one.

    **A changed `value` or `country` retires this identifier and creates a
    new one** linked back to it (D63), because the checks on the old row are
    evidence for invoices already issued and must not follow a different
    number. Everything else is an ordinary edit.
    """

    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = Field(default=None, min_length=1, max_length=80)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    is_primary: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class AcknowledgeRequest(BaseModel):
    """Clearing a flagged change, with a note saying what was done about it.

    The note is required and bounded below: "ok" tells the next person
    nothing, and the whole point of an acknowledgement is that somebody can
    see afterwards that a human looked and what they concluded.
    """

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=2000)


class ManualConfirmationRequest(BaseModel):
    """Recording that a human confirmed an identifier.

    For the kinds no register will answer about — a US EIN, a Swiss UID
    (D61), a company register number. It appends a check row with provider
    `manual`, and the screen shows it as "confirmed by <user> on <date>",
    never as a registry check.

    **It never verifies an EU VAT ID** (D66). For a `vat` the registry is
    reachable, so a human saying "trust me" in its place would put a reverse
    charge on an invoice with nothing behind it.
    """

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=2000)
    #: Where the evidence is — a letter reference, a file name, a case
    #: number. Free text because every company files differently, and a
    #: structured field would be left empty.
    evidence_reference: Optional[str] = Field(default=None, max_length=255)
    #: When this confirmation stops counting, if the person says so. The one
    #: date in the check table that is a claim about the future, and it is a
    #: person's claim rather than a registry's.
    valid_until: Optional[datetime] = None


class TaxIdCheckPolicyResponse(BaseModel):
    """The company's check policy, with the choices the UI offers."""

    initial_check_offsets: list[int]
    recheck_interval_days: Optional[int] = None
    max_check_age_days: Optional[int] = None
    #: Served so the screen does not hard-code them and drift from the
    #: validator. `null` in the list is "off".
    interval_choices: list[Optional[int]] = []
    #: The defaults, so a screen can say "90 days (the default)" and a user
    #: can tell what they have changed.
    defaults: JsonObject = {}


class TaxIdCheckPolicyUpdate(BaseModel):
    """Changing it. Validated as a combination, not field by field."""

    model_config = ConfigDict(extra="forbid")

    initial_check_offsets: Optional[list[int]] = None
    recheck_interval_days: Optional[int] = None
    max_check_age_days: Optional[int] = None
