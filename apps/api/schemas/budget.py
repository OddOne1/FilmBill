"""The wire shapes for the Budget.

Three things are deliberate.

**The calculated view reuses `CalcPreviewResponse` whole.** A budget's
numbers are the engine's numbers in the engine's own shape, so the web app's
row renderer is the same one `/calc/preview` feeds and there is no second
place for "what is a line's net" to be answered. The budget adds the top
sheet and the factor explanations beside it, not instead of it.

**Money is a string in both directions** (rule 1), through `Money` and
`Quantity` from `core/money.py`. A JSON number is refused rather than
rounded.

**A line's rates are not optional on the wire even though the catalog may
supply them.** `POST .../lines` with an `item_id` and no prices takes the
catalog's; with prices, takes those. The copy happens once, in the service,
and nothing re-prices the line afterwards — which is why the response carries
`catalog_difference` as a report and never as a correction.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..billing.calc import ADJUSTMENT_BASES, ADJUSTMENT_MODES, ADJUSTMENT_TYPES
from ..core.money import Money, Quantity
from ..models.project import FACTOR_SOURCES
from .calc import CalcPreviewResponse, PeriodUnit

FactorSource = Literal["manual", "catalog"]
DiscountMode = Literal["percent", "amount"]
AdjustmentType = Literal["discount", "surcharge"]
AdjustmentMode = Literal["percent", "amount"]
AdjustmentBase = Literal["subtotal", "subtotal_incl_previous", "kinds"]

# Written out for the OpenAPI generator, asserted so they cannot drift from
# the engine's and the model's own vocabularies — `schemas/calc.py`'s pattern.
assert set(FactorSource.__args__) == set(FACTOR_SOURCES), (
    "schemas/budget.py lists different factor sources from models/project.py"
)
assert set(AdjustmentType.__args__) == set(ADJUSTMENT_TYPES), (
    "schemas/budget.py lists different adjustment types from billing/calc"
)
assert set(AdjustmentMode.__args__) == set(ADJUSTMENT_MODES), (
    "schemas/budget.py lists different adjustment modes from billing/calc"
)
assert set(AdjustmentBase.__args__) == set(ADJUSTMENT_BASES), (
    "schemas/budget.py lists different adjustment bases from billing/calc"
)


# ─── Rows ───────────────────────────────────────────────────────────────────


class CatalogDifference(BaseModel):
    """"This line's rates are no longer the price list's."

    A report and never a correction: nothing re-prices a line from the
    catalog, because a budget that moved when somebody edited an item would
    be a budget nobody could compare with last week's.
    """

    item_name: str
    catalog_unit_price: Optional[Money] = None
    catalog_unit_cost: Optional[Money] = None


ExtraStatus = Literal["proposed", "accepted", "declined"]


class BudgetLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    position: int
    item_id: Optional[uuid.UUID] = None
    description: str
    kind: str
    quantity: Quantity
    #: Names a `budget_globals.key`. When set, the stored `quantity` is not
    #: what the line is calculated with — the global's value is.
    quantity_ref: Optional[str] = None
    period_value: Quantity
    period_ref: Optional[str] = None
    period_unit: str
    factor: Quantity
    factor_source: str
    unit_price: Money
    unit_cost: Money
    discount_mode: Optional[str] = None
    discount_value: Optional[Quantity] = None
    tax_ref: str
    tags: list[str] = Field(default_factory=list)
    allow_negative: bool = False
    notes: Optional[str] = None
    is_active: bool = True
    catalog_difference: Optional[CatalogDifference] = None
    #: B1.1 extras: set on a line in the Extras section, null elsewhere.
    extra_status: Optional[ExtraStatus] = None
    extra_decided_on: Optional[date] = None
    extra_note: Optional[str] = None


class BudgetSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    budget_id: uuid.UUID
    #: The user-visible account number — `1100`. Free text, because the chart
    #: of accounts arrives in P6.
    code: Optional[str] = None
    name: str
    parent_id: Optional[uuid.UUID] = None
    position: int
    is_active: bool = True
    #: B1.1: the reserved Extras section — managed on the Extras tab.
    is_extras: bool = False


class BudgetGlobalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    budget_id: uuid.UUID
    key: str
    label: str
    value: Quantity
    unit: Optional[str] = None
    source: str
    #: True for `days:<type>`: the value is COUNTED from the project's days
    #: and the screen shows it read-only.
    is_computed: bool = False
    #: How many active lines point at this key. What makes "you cannot remove
    #: a global a line uses" explicable before somebody tries it.
    referenced_by: int = 0
    is_active: bool = True


class BudgetAdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    budget_id: uuid.UUID
    #: NULL means document level.
    section_id: Optional[uuid.UUID] = None
    position: int
    label: str
    type: str
    mode: str
    value: Quantity
    base: str
    kinds: list[str] = Field(default_factory=list)
    is_active: bool = True


class BudgetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    currency: str
    notes: Optional[str] = None
    is_primary: bool = False
    is_active: bool = True
    locked_at: Optional[datetime] = None
    is_locked: bool = False
    #: B1.1: the production's end. A closed budget is always locked.
    closed_at: Optional[datetime] = None
    closed_by: Optional[uuid.UUID] = None
    close_note: Optional[str] = None
    is_closed: bool = False
    section_count: int = 0
    line_count: int = 0
    created_at: datetime
    updated_at: datetime


class BudgetSheetResponse(BaseModel):
    """The whole editable sheet in one request.

    One request rather than four, because every screen that shows one of
    these shows all of them, and four round trips to render a spreadsheet is
    four chances for a half-loaded screen.
    """

    budget: BudgetResponse
    sections: list[BudgetSectionResponse]
    lines: list[BudgetLineResponse]
    globals: list[BudgetGlobalResponse]
    adjustments: list[BudgetAdjustmentResponse]


# ─── The calculated view ────────────────────────────────────────────────────


class TopSheetRow(BaseModel):
    """One top-level section on the top sheet."""

    section_id: str
    code: Optional[str] = None
    name: str
    price: Money
    cost: Money
    margin: Money
    margin_percent: Optional[Money] = None
    #: This section's share of the sections' total — of the subtotal BEFORE
    #: the budget's own adjustments, so the shares add up to 100 % and a
    #: production fee is visible as its own row in the totals rather than
    #: diluted across the sections it is charged on. Null when the subtotal
    #: is zero, which is undefined rather than 0 %.
    share_of_subtotal: Optional[Money] = None


class FactorExplanation(BaseModel):
    """Why a catalog factor is the number it is.

    Carried with the number because "2.8" is not something anybody can
    disagree with, and "2.8, from the 5-day row, because the table has no 6"
    is. `rule` is `billing/catalog/factors`'s own vocabulary: `exact`,
    `next_lower`, `highest_listed`, `below_lowest`, `no_table`.
    """

    line_id: str
    days: int
    factor: Quantity
    matched_days: Optional[int] = None
    rule: str
    group_name: Optional[str] = None


class CalcTotals(BaseModel):
    """One engine run's totals. The budget computes no VAT (D72), so the
    tax side is the net per tax category, as on the top sheet."""

    net: Money
    cost: Money
    margin: Money
    margin_percent: Optional[Money] = None
    net_by_tax_ref: dict[str, Money] = Field(default_factory=dict)


class BudgetTotals(BaseModel):
    """B1.1: `baseline` is everything except extras — what the locked
    quote said; `with_extras` adds the ACCEPTED extras and is the same run
    as `result`. Two engine runs over two documents; nothing is added up."""

    baseline: CalcTotals
    with_extras: CalcTotals


class ExtraFigures(BaseModel):
    """One extra's own price, cost and margin, whatever its status."""

    line_id: uuid.UUID
    description: str
    status: ExtraStatus
    decided_on: Optional[date] = None
    note: Optional[str] = None
    price: Money
    cost: Money
    margin: Money
    margin_percent: Optional[Money] = None


class BudgetCalculatedResponse(BaseModel):
    """Every number the sheet and the top sheet show.

    `result` is the engine's own output, in the shape `/calc/preview` returns
    it — the same renderer, the same rounding, one implementation. **The web
    app renders these and computes nothing** (rule 2).
    """

    budget_id: uuid.UUID
    currency: str
    result: CalcPreviewResponse
    top_sheet: list[TopSheetRow]
    factors: list[FactorExplanation] = Field(default_factory=list)
    totals: BudgetTotals
    extras: list[ExtraFigures] = Field(default_factory=list)


# ─── Writes ─────────────────────────────────────────────────────────────────


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    notes: Optional[str] = None
    #: The company currency is the only one A2 writes. Accepted so the field
    #: exists on the wire and refused when it differs, rather than silently
    #: ignored — a budget that said USD and was calculated in EUR would be
    #: worse than a refusal.
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    is_primary: bool = False


class BudgetUpdate(BaseModel):
    """A budget's own fields. Editable even while locked — "Estimate v3
    (approved 24.11.)" is exactly the kind of thing somebody wants to write
    on a frozen sheet. The lock stops the NUMBERS moving."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    notes: Optional[str] = None


class BudgetDuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)


class BudgetSectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, max_length=32)
    #: A top-level section when omitted. A section inside a section inside a
    #: section is refused with a sentence naming the engine's group limit.
    parent_id: Optional[uuid.UUID] = None
    position: Optional[int] = None


class BudgetSectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, max_length=32)
    parent_id: Optional[uuid.UUID] = None
    position: Optional[int] = None


class BudgetLineCreate(BaseModel):
    """A line, from the catalog or free-text.

    With `item_id` and no rates, the item's rates for the line's period unit
    are COPIED onto the line. With rates, the rates given win: somebody who
    typed a price meant it.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: uuid.UUID
    item_id: Optional[uuid.UUID] = None
    description: Optional[str] = Field(default=None, max_length=500)
    kind: Optional[str] = Field(default=None, max_length=24)
    quantity: Quantity = Decimal("1")
    quantity_ref: Optional[str] = Field(default=None, max_length=64)
    period_value: Quantity = Decimal("1")
    period_ref: Optional[str] = Field(default=None, max_length=64)
    period_unit: Optional[PeriodUnit] = None
    factor: Quantity = Decimal("1")
    factor_source: Optional[FactorSource] = None
    unit_price: Optional[Money] = None
    unit_cost: Optional[Money] = None
    discount_mode: Optional[DiscountMode] = None
    discount_value: Optional[Quantity] = None
    tax_ref: Optional[str] = Field(default=None, max_length=64)
    tags: list[str] = Field(default_factory=list)
    allow_negative: bool = False
    notes: Optional[str] = None
    position: Optional[int] = None

    @model_validator(mode="after")
    def _needs_something_to_call_itself(self):
        if self.item_id is None and not (self.description or "").strip():
            raise ValueError(
                "a line needs either an item or a description — a row with "
                "neither is a row nobody reading the budget can identify."
            )
        return self


class BudgetLineUpdate(BaseModel):
    """Every editable field of a line, including where it sits.

    `section_id` and `position` are in here rather than on a separate
    `/move` endpoint: the sheet's drag-and-drop and its cell editing are the
    same act to the person doing them, and two endpoints would mean two
    audit actions for one visible change. The audit action is
    `budget_line.moved` when either of them changes.
    """

    model_config = ConfigDict(extra="forbid")

    section_id: Optional[uuid.UUID] = None
    position: Optional[int] = None
    description: Optional[str] = Field(default=None, max_length=500)
    kind: Optional[str] = Field(default=None, max_length=24)
    quantity: Optional[Quantity] = None
    quantity_ref: Optional[str] = Field(default=None, max_length=64)
    period_value: Optional[Quantity] = None
    period_ref: Optional[str] = Field(default=None, max_length=64)
    period_unit: Optional[PeriodUnit] = None
    factor: Optional[Quantity] = None
    factor_source: Optional[FactorSource] = None
    unit_price: Optional[Money] = None
    unit_cost: Optional[Money] = None
    discount_mode: Optional[DiscountMode] = None
    discount_value: Optional[Quantity] = None
    tax_ref: Optional[str] = Field(default=None, max_length=64)
    tags: Optional[list[str]] = None
    allow_negative: Optional[bool] = None
    notes: Optional[str] = None
    #: Explicitly clearing a ref or a discount. `null` in a PATCH body is
    #: indistinguishable from "not sent" once Pydantic has parsed it, so the
    #: fields that can be cleared are named here instead.
    clear: list[
        Literal[
            "quantity_ref",
            "period_ref",
            "discount",
            "item_id",
            "notes",
            "tax_ref",
        ]
    ] = Field(default_factory=list)


class BudgetGlobalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    value: Quantity = Decimal("0")
    unit: Optional[str] = Field(default=None, max_length=32)
    #: `manual`, or `days:<type>` for a value counted from the project's
    #: production days. A closed vocabulary — there is no formula field and
    #: no place one could be added (D70).
    source: str = "manual"


class BudgetGlobalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    #: Refused for a computed global: its value is the day count, and a
    #: typed-over one would disagree with the Days tab the moment anybody
    #: looked.
    value: Optional[Quantity] = None
    unit: Optional[str] = Field(default=None, max_length=32)


class BudgetAdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=255)
    type: AdjustmentType
    mode: AdjustmentMode
    value: Quantity
    base: AdjustmentBase = "subtotal"
    kinds: list[str] = Field(default_factory=list)
    #: NULL — the default — puts it on the whole budget.
    section_id: Optional[uuid.UUID] = None
    position: Optional[int] = None


class BudgetAdjustmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: Optional[AdjustmentType] = None
    mode: Optional[AdjustmentMode] = None
    value: Optional[Quantity] = None
    base: Optional[AdjustmentBase] = None
    kinds: Optional[list[str]] = None
    position: Optional[int] = None


class BudgetLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Written into the audit event. Optional, because requiring a reason for
    #: every lock is how a field becomes "x".
    reason: Optional[str] = Field(default=None, max_length=1000)


# ─── Extras (B1.1) ──────────────────────────────────────────────────────────


class BudgetExtraCreate(BaseModel):
    """Propose an extra: a line in the Extras section, from the catalog or
    free text, with what the customer was told. Starts as `proposed`."""

    model_config = ConfigDict(extra="forbid")

    item_id: Optional[uuid.UUID] = None
    description: Optional[str] = Field(default=None, max_length=500)
    kind: Optional[str] = Field(default=None, max_length=24)
    quantity: Quantity = Decimal("1")
    period_value: Quantity = Decimal("1")
    period_unit: Optional[PeriodUnit] = None
    factor: Quantity = Decimal("1")
    unit_price: Optional[Money] = None
    unit_cost: Optional[Money] = None
    tax_ref: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _needs_something_to_call_itself(self):
        if self.item_id is None and not (self.description or "").strip():
            raise ValueError("an extra needs either an item or a description.")
        return self


class BudgetExtraUpdate(BaseModel):
    """Change a proposed or accepted extra. A change to an accepted one is
    allowed and audited with the before and the after."""

    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = Field(default=None, max_length=500)
    kind: Optional[str] = Field(default=None, max_length=24)
    quantity: Optional[Quantity] = None
    period_value: Optional[Quantity] = None
    period_unit: Optional[PeriodUnit] = None
    factor: Optional[Quantity] = None
    unit_price: Optional[Money] = None
    unit_cost: Optional[Money] = None
    tax_ref: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)


class BudgetExtraDecision(BaseModel):
    """Accept or decline. Declining needs a note (what was agreed); the
    day defaults to today."""

    model_config = ConfigDict(extra="forbid")

    decided_on: Optional[date] = None
    note: Optional[str] = Field(default=None, max_length=2000)


# ─── Closing (B1.1) ─────────────────────────────────────────────────────────

CloseState = Literal["clean", "within_estimate", "over_estimate"]


class CloseCheckLine(BaseModel):
    """A line the close check lists, with what the screen links to."""

    line_id: uuid.UUID
    section_id: uuid.UUID
    description: str
    estimate_cost: Money
    actual: Money
    committed_open: Money
    efc: Money
    variance: Money


class CloseCheckEntry(BaseModel):
    """An order or invoice the close check lists."""

    kind: Literal["commitment", "actual"]
    id: uuid.UUID
    section_id: uuid.UUID
    line_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    amount: Money


class CloseCheckResponse(BaseModel):
    """What does not add up before a budget is closed.

    `state` is `clean` (nothing listed), `within_estimate` (something is
    listed but the total forecast is at or below the total plan — money moved
    between lines) or `over_estimate`. `digest` is what `POST .../close`
    needs back to prove the person read THIS list.
    """

    budget_id: uuid.UUID
    state: CloseState
    estimate_cost: Money
    efc: Money
    variance: Money
    lines_over: list[CloseCheckLine]
    lines_under_not_finished: list[CloseCheckLine]
    finished_with_open_commitment: list[CloseCheckLine]
    open_commitments: list[CloseCheckEntry]
    open_commitments_total: Money
    unallocated: list[CloseCheckEntry]
    unallocated_total: Money
    digest: str


class BudgetCloseRequest(BaseModel):
    """Close a locked budget. Over discrepancies, all three are needed."""

    model_config = ConfigDict(extra="forbid")

    acknowledge: bool = False
    digest: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=2000)


class BudgetReopenRequest(BaseModel):
    """Reopening a closed budget always says why."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


# ─── CSV import ─────────────────────────────────────────────────────────────


class BudgetImportRowResponse(BaseModel):
    """One row of the preview table, and of the applied result.

    `action` is `create`, `update` or `error`. An error row is reported and
    skipped rather than failing the file, for A1's reason: one typo in row 40
    of a 200-row sheet must not waste the other 199.
    """

    line_number: int
    action: str
    message: str
    section_code: str
    description: str


class BudgetImportPreviewResponse(BaseModel):
    rows: list[BudgetImportRowResponse]
    creates: int
    updates: int
    errors: int
    #: A fingerprint of exactly what the preview plans. Sent back with the
    #: apply, which is refused if the plan has changed in between — applying
    #: something other than what was shown is the one thing an import must
    #: not do.
    digest: str
    ignored_columns: list[str] = Field(default_factory=list)
    delimiter: str = ";"


class BudgetImportResultResponse(BudgetImportPreviewResponse):
    """What was applied. The same shape as the preview, on purpose: the test
    that proves "applied equals previewed" compares the two directly."""

    applied: bool = True
