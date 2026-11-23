"""The wire shapes for the catalog.

Three things worth knowing before reading it.

**`kind` is a closed enum and `category` is not.** A kind decides behaviour —
which account an item posts to, whether `rebilled_cost` means anything, what
a `kinds`-based adjustment catches — so it is a fixed list and the API
refuses anything else. A category is the company's own filing and is a tree
they build; nothing in the engine reads it. See SCOPE §4.1.

**`default_tax_class` is a KEY, not an id.** `"standard"`, `"reduced_10"`.
The column holds the key for the same reason the document snapshot will
(rule 4): a key stays readable in a snapshot taken two years ago whether or
not the row it pointed at still exists. A key that no active class matches is
refused on write, and reported — not silently kept — if the class is
deactivated afterwards (`services/tax_references.py`).

**Rates come as a list, not as `day_rate` / `hour_rate` fields.** An item can
be billed per day and costed per week; flattening that into named fields
would cap it at the two periods somebody thought of first.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..billing.catalog import ITEM_KINDS, SYSTEM_UNIT_KEYS
from ..core.money import Money, Quantity
from ..models.catalog import RATE_TYPES

ItemKind = Literal[
    "labor",
    "equipment_rental",
    "space_rental",
    "travel",
    "per_diem",
    "storage",
    "product",
    "software_license",
    "fee",
    "other",
]
RateType = Literal["bill", "cost"]
PeriodUnit = Literal[
    "day", "flat", "half_day", "hour", "km", "month", "night", "piece", "week"
]

# Written out above, because the OpenAPI generator cannot see through
# `Literal[*ITEM_KINDS]`, and checked here, so the two cannot drift. A kind
# that exists in the engine's vocabulary but not in this enum would be a kind
# the API refuses to store and nothing would say why.
assert set(ItemKind.__args__) == set(ITEM_KINDS), (
    "schemas/catalog.py lists different item kinds from billing/catalog"
)
assert set(RateType.__args__) == set(RATE_TYPES), (
    "schemas/catalog.py lists different rate types from models/catalog.py"
)
assert set(PeriodUnit.__args__) == set(SYSTEM_UNIT_KEYS), (
    "schemas/catalog.py's period units and the seeded system units disagree"
)


# ─── Units ──────────────────────────────────────────────────────────────────


class UnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    label_de: str
    label_en: Optional[str] = None
    period_unit: str
    #: A seeded unit. Its key and `period_unit` are fixed; its labels are
    #: not — a company that says "Drehtag" rather than "Tag" may say so.
    is_system: bool
    position: int
    is_active: bool


class UnitWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_de: str = Field(min_length=1, max_length=64)
    label_en: Optional[str] = Field(default=None, max_length=64)
    position: Optional[int] = None
    is_active: Optional[bool] = None


class UnitCreate(UnitWrite):
    key: str = Field(min_length=1, max_length=32)
    period_unit: PeriodUnit


# ─── Categories ─────────────────────────────────────────────────────────────


class ItemCategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    parent_id: Optional[uuid.UUID] = None
    position: int
    is_active: bool
    #: `Equipment › Camera › Cinema`. Served because a flat list of category
    #: names is ambiguous the moment two parents have a "Camera" under them,
    #: and the screen should not have to rebuild the tree to label a row.
    path: str = ""
    #: 1 for a root. The tree screen greys "add sub-category" at
    #: `MAX_CATEGORY_DEPTH` rather than letting somebody find out by being
    #: refused (17c).
    depth: int = 1
    #: How many items point at it. A category with items cannot be deleted —
    #: nothing in the catalog can — but it can be deactivated, and knowing
    #: the number first is the difference between an informed click and a
    #: surprise.
    item_count: int = 0


class ItemCategoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    parent_id: Optional[uuid.UUID] = None
    position: Optional[int] = None
    is_active: Optional[bool] = None


class ItemCategoryCreate(ItemCategoryWrite):
    #: Fixed at create. A category's kind decides which items may sit in it,
    #: so changing it later would reclassify everything inside without
    #: saying so.
    kind: ItemKind


# ─── Rental factor tables ───────────────────────────────────────────────────


class RentalFactorWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(ge=1, le=3650)
    factor: Quantity


class RentalFactorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    days: int
    factor: Quantity


class RentalFactorGroupWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=4000)
    is_active: Optional[bool] = None


class RentalFactorGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    notes: Optional[str] = None
    is_active: bool
    factors: list[RentalFactorResponse] = Field(default_factory=list)


class FactorLookupResponse(BaseModel):
    """What this table charges for `days` days, and how it got there.

    `matched_days` is the row that was used, which is the whole answer to
    "why does 6 days cost the same as 5": it says `5`. The lookup rule is
    [Guessing] and recorded as an open question (SCOPE D71) — a response
    that only gave the factor would make it impossible to see which rule
    produced it.
    """

    days: int
    factor: Quantity
    matched_days: Optional[int] = None
    #: `exact` · `next_lower` · `below_lowest`.
    rule: str
    #: `factor × the day rate` is the price; this is `factor` restated as the
    #: number of days actually charged, which is how the trade talks about
    #: it ("a week is five days").
    billable_days: Quantity


# ─── Rates ──────────────────────────────────────────────────────────────────


class ItemRateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_type: RateType
    period_unit: PeriodUnit
    amount: Money

    @field_validator("amount")
    @classmethod
    def _not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError(
                "a rate cannot be negative. A reduction belongs on the line "
                "or the group as a discount, where it is visible as one."
            )
        return value


class ItemRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rate_type: str
    period_unit: str
    amount: Money
    currency: str


# ─── Items ──────────────────────────────────────────────────────────────────


class ItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: Optional[str] = None
    name_de: str
    name_en: Optional[str] = None
    kind: str
    category_id: Optional[uuid.UUID] = None
    category_path: Optional[str] = None
    description_internal: Optional[str] = None
    description_customer: Optional[str] = None
    unit_id: Optional[uuid.UUID] = None
    unit_key: Optional[str] = None
    default_tax_class: Optional[str] = None
    rental_factor_group_id: Optional[uuid.UUID] = None
    rental_factor_group_name: Optional[str] = None
    revenue_account: Optional[str] = None
    expense_account: Optional[str] = None
    #: Only meaningful on a `travel` item (SCOPE §4.1). Stored as given on
    #: others, reported as a warning on write.
    rebilled_cost: bool = False
    notes: Optional[str] = None
    is_active: bool
    rates: list[ItemRateResponse] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ItemWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Optional[str] = Field(default=None, max_length=64)
    name_de: str = Field(min_length=1, max_length=255)
    name_en: Optional[str] = Field(default=None, max_length=255)
    kind: ItemKind
    category_id: Optional[uuid.UUID] = None
    description_internal: Optional[str] = Field(default=None, max_length=4000)
    description_customer: Optional[str] = Field(default=None, max_length=4000)
    unit_id: Optional[uuid.UUID] = None
    default_tax_class: Optional[str] = Field(default=None, max_length=32)
    rental_factor_group_id: Optional[uuid.UUID] = None
    revenue_account: Optional[str] = Field(default=None, max_length=32)
    expense_account: Optional[str] = Field(default=None, max_length=32)
    rebilled_cost: bool = False
    notes: Optional[str] = Field(default=None, max_length=4000)
    is_active: Optional[bool] = None
    #: Omitted, the rates are left alone; given, they REPLACE the set. An
    #: empty list is therefore "this item has no rates", which is a thing
    #: somebody may well mean.
    rates: Optional[list[ItemRateWrite]] = None

    @field_validator("sku")
    @classmethod
    def _blank_sku_is_none(cls, value: Optional[str]) -> Optional[str]:
        """`""` becomes None, so the partial unique index still applies.

        `uq_items_company_sku` is `WHERE sku IS NOT NULL`, so two items with
        an empty-string SKU would collide while two with no SKU do not —
        and "no SKU" is what an empty box in a form means.
        """
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class ItemCreate(ItemWrite):
    pass


class ItemPage(BaseModel):
    items: list[ItemResponse]
    total: int
    limit: int
    offset: int


# ─── The CSV import ─────────────────────────────────────────────────────────


class ImportRowResponse(BaseModel):
    """One line of the file and what it would do. `error` rows are skipped.

    `message` is a sentence, in the second person, naming the column — this
    table is the whole user interface for a failed import and
    "validation error" in it would be useless.
    """

    line_number: int
    #: `create` · `update` · `error`.
    action: str
    message: str
    sku: Optional[str] = None
    name_de: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    bill_rate_day: Optional[Money] = None
    cost_rate_day: Optional[Money] = None
    tax_class: Optional[str] = None
    active: Optional[bool] = None
    #: The item this row would update, when it matched one by SKU.
    item_id: Optional[uuid.UUID] = None


class ImportPreviewResponse(BaseModel):
    """What the file would do, computed and shown before anything is written.

    `digest` is the handle the apply call must send back. See
    `services/catalog_service.apply_import`: it is how "the import applies
    exactly what the preview showed" is enforced rather than asserted.
    """

    rows: list[ImportRowResponse]
    created: int
    updated: int
    errors: int
    digest: str
    delimiter: str
    #: Columns in the file that the importer does not know. Reported so
    #: somebody can see their "Notizen" column was not read.
    ignored_columns: list[str] = Field(default_factory=list)


class ImportApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The digest from the preview. Optional in the schema and sent by the
    #: web app always; an apply without it is allowed for a script that has
    #: no preview step, and is the only way to skip the check.
    digest: Optional[str] = Field(default=None, max_length=64)


class ImportApplyResponse(ImportPreviewResponse):
    """The same table, after the fact. `applied` is the count that landed."""

    applied: int
