"""The wire shapes for Settings → Company → Tax.

Two things are deliberate and worth reading before editing.

**The rate is a string on the wire.** `Quantity` from `core/money.py`, which
means `NUMERIC(18,4)` in the database, a `Decimal` in Python and `"20.0000"`
in JSON. CLAUDE.md rule 1: no rate passes through a binary float, and the
generated TypeScript must say `string` or the rule is undone at the type level
before anybody writes a line of code.

**`TaxCodeUpdate` has no `code` and no `category`** — not "ignores them",
does not have them, and `extra="forbid"` turns sending one into a 422 naming
the field. A tax code is snapshotted onto every document line that uses it
(SCOPE §5.3, rule 3), so renaming `AT-20` would silently restate issued
invoices. The rate IS editable, because a rate legitimately changes on a
date and the snapshot is what keeps history true.
"""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..billing.tax import BUYER_TYPES, SUPPLY_KINDS
from ..core.money import Quantity
from .auth import JsonObject

#: `{lang: text}` for the legal notes. A plain `dict[str, str]` is enough
#: here — unlike `JsonObject`, the value type is known, so Pydantic emits
#: `additionalProperties: {type: string}` and openapi-typescript reads it
#: correctly as `Record<string, string>`.
LegalNotes = dict[str, str]


class TaxCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    rate: Quantity
    category: str
    legal_notes: LegalNotes = {}
    notes_reviewed: bool = False
    #: Empty in P1a. The AT UVA / DE UStVA targets arrive with the accounting
    #: core in P6 (SCOPE §6.1); the field is served now so the screen and the
    #: generated types do not change shape then.
    reporting_mapping: JsonObject = {}
    revenue_account: Optional[str] = None
    is_active: bool = True
    #: Which region pack seeded this, or null for one the user created. The
    #: screen shows it, because "FilmBill put this here" and "we added this"
    #: are different things to a reviewer.
    origin_pack: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    #: Sentences the SERVER wants shown after a write. Empty on every read.
    #:
    #: It exists for one message with real consequences: a rate edit applies
    #: to documents created afterwards and changes nothing about the ones
    #: already issued (rule 3 — a line snapshots the code it used). Carried
    #: here rather than written into the screen, for the same reason a
    #: proposal's warnings are: the server owns the sentence, and an API
    #: consumer that is not this web app can read it too.
    notices: list[str] = []


class TaxCodeUpdate(BaseModel):
    """What may be changed. `code` and `category` are absent on purpose."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    rate: Optional[Quantity] = None
    legal_notes: Optional[LegalNotes] = None
    notes_reviewed: Optional[bool] = None
    revenue_account: Optional[str] = Field(default=None, max_length=32)
    is_active: Optional[bool] = None

    @field_validator("legal_notes")
    @classmethod
    def _notes_are_sane(cls, value: Optional[LegalNotes]) -> Optional[LegalNotes]:
        """Language keys look like language codes, and texts are not blank.

        A blank text is refused rather than stored: an empty string would
        print as a missing note on a document while reading as a present one
        in the editor. Removing a note means removing its key.
        """
        if value is None:
            return value
        for lang, text in value.items():
            if not (2 <= len(lang) <= 5) or not lang.replace("-", "").isalpha():
                raise ValueError(
                    f"{lang!r} is not a language code. Use two letters, "
                    "optionally with a region (de, en, de-AT)."
                )
            if not text.strip():
                raise ValueError(
                    f"The {lang} note is blank. Remove the language instead "
                    "of saving an empty text — an empty note prints as "
                    "nothing and reads as present."
                )
        return value


class TaxClassResponse(BaseModel):
    """A class, with its code resolved for display.

    `code` and `rate` are served alongside `code_id` so the picker can read
    "Standard rate — AT-20, 20 %" from one request. The web never works out a
    rate from a class (rule 2); it is told.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    label: str
    code_id: uuid.UUID
    code: str
    rate: Quantity
    category: str
    code_is_active: bool
    #: The class's own flag. A class and its code are a 1:1 pair and the two
    #: move together, so these agree — `code_is_active` stays separate
    #: because the screen reads the pair and a disagreement is a bug worth
    #: being able to see.
    is_active: bool = True
    position: int = 0
    origin_pack: Optional[str] = None
    #: True when this class is the company's own rather than a pack's. The
    #: screen shows it, because "FilmBill put this here" and "we added this"
    #: are different things to somebody reviewing a tax table.
    is_custom: bool = False


class TaxRuleResponse(BaseModel):
    """Read-only in P1a. Editing rules is a later step (SCOPE §6.3)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    name: str
    conditions: JsonObject = {}
    outcome: JsonObject = {}
    origin_pack: Optional[str] = None
    is_active: bool = True


class TaxClassUpdate(BaseModel):
    """What may be changed on a class. `key` is absent on purpose.

    The key is what an ITEM stores, so renaming it would silently re-tax
    every item in that class. The label is the editable part.

    `is_active` here is how a RATE is retired: it sets the class and its code
    together, because they are a 1:1 pair and a live code behind a dead class
    is a state nobody can explain.
    """

    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_active: Optional[bool] = None


class TaxRateCreate(BaseModel):
    """Add a domestic rate: a class and its `S` code, in one go.

    No `category`: a rate is always `S` (standard-rated — which is what a
    reduced rate is too). The EN 16931 categories are a system list and a
    user never invents one, because a category nobody recognises is an
    e-invoice the recipient's system rejects.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=255)
    #: A percent above 0 and below 100. A string on the wire (rule 1).
    rate: Quantity
    #: Optional. The default is `<COUNTRY>-<rate>` — `AT-21`, `FR-4.5` — with
    #: a numeric suffix if that is taken.
    code: Optional[str] = Field(default=None, min_length=1, max_length=20)


class TaxRateResponse(BaseModel):
    """Both halves of what was created, so the screen needs no second call."""

    tax_code: TaxCodeResponse
    tax_class: TaxClassResponse


class TaxNoTaxCaseCreate(BaseModel):
    """Add a rate-0 code in one of the six no-tax categories. No class.

    A no-tax case is a property of a SITUATION — this customer, this supply,
    this document — not of an item, which is why it gets no class and is
    chosen as an override instead.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=255)
    category: Literal["Z", "E", "AE", "K", "G", "O"]
    code: Optional[str] = Field(default=None, min_length=1, max_length=20)
    #: `{lang: text}`. The user's own wording, and theirs to be right about —
    #: it lands with `notes_reviewed` false like everything else.
    legal_notes: LegalNotes = {}


class NoTaxCategoryOption(BaseModel):
    """One of the six, with the line the dialog shows beside it."""

    category: str
    #: `{lang: one plain sentence}`. Served rather than written into the web
    #: app so that the explanation a user chooses from lives next to the
    #: definition it explains (services/tax_service.NO_TAX_CATEGORIES).
    labels: dict[str, str]


# ─── Proposing ──────────────────────────────────────────────────────────────


class TaxOverrideRequest(BaseModel):
    """An item or party override, as the screen offers it.

    `code` is required for `kind="code"` and meaningless otherwise, which the
    validator enforces — an override with no code would otherwise be accepted
    and then refused by the engine as "names a code this company does not
    have", which is a confusing way to say "you left the field empty".
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["code", "domestic_for_foreign_business"]
    code: Optional[str] = Field(default=None, max_length=32)

    @field_validator("code")
    @classmethod
    def _strip(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value else None


class TaxProposeRequest(BaseModel):
    """Ask what tax a sale would carry. Changes nothing.

    The SELLER is not in here. The country and the small-business flag come
    from the active company, so a client cannot ask "what would this be if we
    were Austrian" — the answer would be a rate for a company that does not
    exist, and somebody would put it on an invoice.
    """

    model_config = ConfigDict(extra="forbid")

    buyer_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    buyer_type: Literal["business", "private"] = "business"
    buyer_vat_verified: bool = False
    supply_kind: Literal["goods", "services"] = "services"
    item_class: str = Field(default="standard", min_length=1, max_length=32)
    item_override: Optional[TaxOverrideRequest] = None
    party_override: Optional[TaxOverrideRequest] = None
    #: Which language the LEGAL NOTE comes back in. Not the language of
    #: `reasons` or of the warnings, which are English — see the engine.
    lang: str = Field(default="en", min_length=2, max_length=5)

    @field_validator("buyer_country")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value and value.strip() else None


class TaxWarningResponse(BaseModel):
    """A code to branch on and a sentence to show.

    Both, so the web renders the explanation without owning a copy of it. A
    warning whose text lived only in the frontend would be invisible to every
    other consumer of this API.
    """

    code: str
    text: str


class TaxWouldHaveAppliedResponse(BaseModel):
    """What the rules alone would have produced, when an override changed it.

    Null unless an override actually changed the answer — an override that
    picks the same code the rules did is not a change, and P2 must not warn
    about it.
    """

    treatment: Optional[str] = None
    treatment_label: Optional[str] = None
    code: Optional[str] = None
    rate: Optional[Quantity] = None
    applied_rule: Optional[str] = None
    applied_rule_position: Optional[int] = None
    legal_note: Optional[str] = None


class TaxProposalResponse(BaseModel):
    """The proposal. `blocking` means "do not use this", not "a guess"."""

    #: Echoed back, because the screen shows it read-only and a company
    #: without one is the whole reason `blocking` exists.
    seller_country: Optional[str] = None
    treatment: Optional[str] = None
    #: Localised here rather than in the web app: a treatment's name belongs
    #: beside its definition (billing/tax/treatments.py), and two screens
    #: spelling "Innergemeinschaftliche Lieferung" differently is the outcome
    #: of letting the client own it.
    treatment_label: Optional[str] = None
    code: Optional[str] = None
    rate: Optional[Quantity] = None
    legal_note: Optional[str] = None
    #: A rule name, or `override:item` / `override:party`.
    applied_rule: Optional[str] = None
    applied_rule_position: Optional[int] = None
    reasons: list[str] = []
    warnings: list[TaxWarningResponse] = []
    blocking: bool = False
    would_have_applied: Optional[TaxWouldHaveAppliedResponse] = None


#: Served so the screen's selectors are built from the API's own vocabulary
#: rather than from a copy in TypeScript that drifts (rule 10's reasoning,
#: applied to enumerations the generator cannot infer from a Literal alone).
BUYER_TYPE_VALUES: tuple[str, ...] = BUYER_TYPES
SUPPLY_KIND_VALUES: tuple[str, ...] = SUPPLY_KINDS
