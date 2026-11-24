"""The wire shapes for cost planning: commitments, actuals, and the forecast.

Four things are deliberate.

**Money is a string in both directions** (rule 1), through `Money` from
`core/money.py`. A JSON number is refused rather than rounded.

**`remaining_rule` is on the wire.** The default forecasting rule is
`[Guessing]` (SCOPE D83) and the screen says which rule produced a number, so
the field is part of the contract and not a detail: a sheet that showed an
EFC without saying whether somebody had typed it would be a sheet nobody
could argue with.

**The tracking response is a flat index, not a tree.** `lines` and `sections`
are keyed by id, like the engine's own `result.lines`, because the sheet
already draws a row per line and per section from `GET /budgets/{id}` and
only needs to look the numbers up. A second tree on the wire would be a
second place for the sheet's order to be decided.

**`duplicates` comes back WITH the created row, not instead of it.** The
warning never blocks: a supplier really can invoice the same amount twice in
a month. The row is already written when the list arrives, and the screen
shows it as a warning beside it.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..billing.budget import COMMITMENT_STATUSES, REMAINING_RULES
from ..core.money import Money

CommitmentStatus = Literal["open", "closed", "cancelled"]
RemainingRule = Literal["etc", "default"]
EntryKind = Literal["commitment", "actual"]

# Written out for the OpenAPI generator, asserted so they cannot drift from
# the pure module's own vocabulary — `schemas/budget.py`'s pattern.
assert set(CommitmentStatus.__args__) == set(COMMITMENT_STATUSES), (
    "schemas/budget_tracking.py lists different commitment statuses from "
    "billing/budget/tracking.py"
)
assert set(RemainingRule.__args__) == set(REMAINING_RULES), (
    "schemas/budget_tracking.py lists different remaining rules from "
    "billing/budget/tracking.py"
)


# ─── Entries, as they come back ─────────────────────────────────────────────


class BudgetEntryBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    budget_id: uuid.UUID
    section_id: uuid.UUID
    line_id: Optional[uuid.UUID] = None
    party_id: Optional[uuid.UUID] = None
    payee_text: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    amount: Money
    notes: Optional[str] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    #: The section's and the line's own words, so the ledger can be read
    #: without a second request per row. Filled by the router.
    section_name: Optional[str] = None
    section_code: Optional[str] = None
    line_description: Optional[str] = None
    #: The party's name where there is one, the typed text otherwise — the
    #: one string a ledger row shows under "Payee".
    payee: Optional[str] = None
    is_voided: bool = False


class BudgetCommitmentResponse(BudgetEntryBase):
    status: CommitmentStatus
    commitment_date: date
    #: What is still owed on this one order: its amount less the non-voided
    #: actuals linked to it, floored at zero. The API's, never the browser's.
    open_amount: Money
    #: How many actuals point at it, so the ledger can say "2 invoices" on
    #: the row rather than making somebody filter for them.
    actual_count: int = 0


class BudgetActualResponse(BudgetEntryBase):
    commitment_id: Optional[uuid.UUID] = None
    incurred_on: date
    paid_on: Optional[date] = None
    #: The reference of the commitment it settles, for the ledger row.
    commitment_reference: Optional[str] = None


class CostEntryDuplicate(BaseModel):
    """An entry that looks like the one just written. **Not** a refusal."""

    kind: EntryKind
    id: uuid.UUID
    amount: Money
    date: date
    payee: Optional[str] = None
    reference: Optional[str] = None
    description: Optional[str] = None


class BudgetCommitmentCreated(BaseModel):
    commitment: BudgetCommitmentResponse
    duplicates: list[CostEntryDuplicate] = Field(default_factory=list)


class BudgetActualCreated(BaseModel):
    actual: BudgetActualResponse
    duplicates: list[CostEntryDuplicate] = Field(default_factory=list)


class BudgetLedgerResponse(BaseModel):
    """The Costs tab's one list: both kinds, each in its own array.

    Two arrays rather than one merged list with a `kind` discriminator,
    because the two have different dates — `commitment_date` and
    `incurred_on` — and a merged list would have to invent a third column to
    sort on. The screen interleaves them; the API does not pretend they are
    the same row.
    """

    commitments: list[BudgetCommitmentResponse] = Field(default_factory=list)
    actuals: list[BudgetActualResponse] = Field(default_factory=list)


# ─── Writing ────────────────────────────────────────────────────────────────


class BudgetEntryWrite(BaseModel):
    """What a create accepts for either kind.

    `section_id` is required and `line_id` is not — every cost belongs
    somewhere in the account structure even when it matches no row. The
    service refuses a party AND a typed payee together: two names for one
    payee is a row nobody can reconcile.
    """

    section_id: uuid.UUID
    line_id: Optional[uuid.UUID] = None
    party_id: Optional[uuid.UUID] = None
    payee_text: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=128)
    #: Net, in the company currency, and > 0. A correction is a new entry
    #: plus a void, never a negative amount.
    amount: Money
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _positive(self):
        if self.amount is not None and Decimal(self.amount) <= Decimal("0"):
            raise ValueError(
                "an entry is always positive. To correct one, add the right "
                "entry and void the wrong one — both stay in the ledger."
            )
        return self


class BudgetCommitmentCreate(BudgetEntryWrite):
    commitment_date: date


class BudgetActualCreate(BudgetEntryWrite):
    incurred_on: date
    paid_on: Optional[date] = None
    commitment_id: Optional[uuid.UUID] = None


class BudgetEntryUpdate(BaseModel):
    """A PATCH. Every field optional; `None` on a nullable one clears it.

    `section_id` and `line_id` move together when either is sent, because
    the line has to be in the section — the service checks it and refuses
    the pair, not one of them.
    """

    section_id: Optional[uuid.UUID] = None
    line_id: Optional[uuid.UUID] = None
    party_id: Optional[uuid.UUID] = None
    payee_text: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=128)
    amount: Optional[Money] = None
    notes: Optional[str] = None


class BudgetCommitmentUpdate(BudgetEntryUpdate):
    commitment_date: Optional[date] = None


class BudgetActualUpdate(BudgetEntryUpdate):
    incurred_on: Optional[date] = None
    paid_on: Optional[date] = None
    commitment_id: Optional[uuid.UUID] = None


class VoidRequest(BaseModel):
    """A void always says why — it is in the ledger for ever."""

    reason: str = Field(min_length=1, max_length=500)


class CloseRequest(BaseModel):
    """Closing or cancelling by hand. The reason is optional here: the
    action itself is the statement, and the audit event records both."""

    reason: Optional[str] = Field(default=None, max_length=500)


class EtcRequest(BaseModel):
    """`null` clears the ETC and the default rule takes over.

    `"0.00"` is a different statement — "this line is finished" — and the
    forecast distinguishes them, which is why this is a nullable field and
    not a number with a sentinel.
    """

    etc: Optional[Money] = None


# ─── The forecast ───────────────────────────────────────────────────────────


class LineTrackingResponse(BaseModel):
    """One line's cost picture. Every number the API's, none the browser's."""

    id: uuid.UUID
    estimate_cost: Money
    committed_gross: Money
    committed_open: Money
    actual: Money
    #: `actual + committed_open` — spent, or owed. **Never**
    #: `actual + committed_gross`: an invoiced order must not count twice.
    exposure: Money
    remaining: Money
    #: `etc` when somebody typed one, `default` when the rule produced it.
    #: On the wire because the default rule is an open decision (SCOPE D83).
    remaining_rule: RemainingRule
    efc: Money
    #: `estimate_cost − efc`. **Positive means under budget.**
    variance: Money
    #: `None` when the estimate is zero — a percentage of nothing is a claim.
    variance_percent: Optional[Money] = None
    over_budget: bool
    etc: Optional[Money] = None


class RollupTrackingResponse(BaseModel):
    """A section's or the budget's totals, and what only a rollup has."""

    id: uuid.UUID
    estimate_cost: Money
    committed_gross: Money
    committed_open: Money
    actual: Money
    exposure: Money
    remaining: Money
    efc: Money
    variance: Money
    variance_percent: Optional[Money] = None
    over_budget: bool
    #: The exposure of entries that carry this section and no line.
    unallocated: Money
    unallocated_actual: Money
    unallocated_committed_open: Money
    #: The ESTIMATE-phase client price, from the calculation engine.
    price: Optional[Money] = None
    #: `price − efc`. Against the estimate price, which does not move when
    #: costs do — that is the whole point of the number.
    forecast_margin: Optional[Money] = None
    forecast_margin_percent: Optional[Money] = None
    #: `price − estimate_cost`, so the screen can show "was 57.02 %, now
    #: 38.01 %" without computing either.
    estimate_margin: Optional[Money] = None
    estimate_margin_percent: Optional[Money] = None


class BudgetTrackingResponse(BaseModel):
    budget: RollupTrackingResponse
    lines: dict[str, LineTrackingResponse] = Field(default_factory=dict)
    sections: dict[str, RollupTrackingResponse] = Field(default_factory=dict)


# ─── The actuals CSV ────────────────────────────────────────────────────────


class ActualImportRowResponse(BaseModel):
    """One row of the preview table, and of the applied result.

    `action` is `create`, `duplicate` or `error`. A `duplicate` row is held
    back unless its `line_number` is in the apply's `include_duplicates` —
    the preview says so in its own sentence, and an import that wrote it
    anyway would be applying something other than what was shown.
    """

    line_number: int
    action: str
    message: str
    section_code: str
    description: str
    duplicate_of: Optional[uuid.UUID] = None


class ActualImportPreviewResponse(BaseModel):
    rows: list[ActualImportRowResponse]
    creates: int
    duplicates: int
    errors: int
    #: A fingerprint of exactly what the preview plans, duplicates included.
    digest: str
    ignored_columns: list[str] = Field(default_factory=list)
    delimiter: str = ";"


class ActualImportResultResponse(ActualImportPreviewResponse):
    """What was applied. The same shape as the preview, on purpose: the test
    that proves "applied equals previewed" compares the two directly."""

    applied: bool = True
    #: How many entries were actually written — `creates` plus the
    #: duplicates the user ticked.
    created: int = 0
