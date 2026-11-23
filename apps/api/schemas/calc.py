"""The wire shape of a calculation. Request in, result out, no state.

**These models are a transport, not a second set of rules.** They say what
JSON is well-formed; `billing/calc/model.py` says what a document MAY be, and
it is the only thing that says so. The handler hands the validated JSON to
`document_from_data`, which refuses a third nesting level, a duplicate id, an
unknown `period_unit` and a value that names something instead of being a
number — with the sentences the engine wrote. If those rules were restated
here as Pydantic constraints there would be two answers to "how deep may a
group go", and the one the API gave would be whichever file somebody
remembered to change.

What these models DO carry is types, because rule 10 generates the web app's
types from this OpenAPI document and a `dict[str, Any]` request body would
generate nothing. So the shape is typed and the rules stay in the engine.

**Money is a string in both directions** (rule 1): `"12517.20"`, never
`12517.2`. `Money` and `Quantity` parse and serialise it; a JSON number in a
request is refused rather than rounded, because a float that arrived from a
browser has already lost whatever it lost.
"""

from decimal import Decimal
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..billing.calc import ADJUSTMENT_BASES, PERIOD_UNITS, child_type
from ..core.money import Money, Quantity

PeriodUnit = Literal[
    "day", "flat", "half_day", "hour", "km", "month", "night", "piece", "week"
]
AdjustmentType = Literal["discount", "surcharge"]
AdjustmentMode = Literal["percent", "amount"]
AdjustmentBase = Literal["subtotal", "subtotal_incl_previous", "kinds"]

# The Literals above are written out because `Literal[*tuple]` is not a thing
# the OpenAPI generator can see through — but they must not be allowed to
# drift from the engine's own vocabulary, so the agreement is asserted at
# import time rather than left to a reviewer.
assert set(PeriodUnit.__args__) == set(PERIOD_UNITS), (
    "schemas/calc.py lists different period units from billing/calc"
)
assert set(AdjustmentBase.__args__) == set(ADJUSTMENT_BASES), (
    "schemas/calc.py lists different adjustment bases from billing/calc"
)


class CalcDiscount(BaseModel):
    """A discount on ONE line. `percent` of its own gross, or an amount."""

    model_config = ConfigDict(extra="forbid")

    mode: AdjustmentMode
    value: Money


class CalcLine(BaseModel):
    """One line: price × quantity × periods × factor, less its own discount."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["line"] = "line"
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=500)
    unit_price: Money
    quantity: Quantity = Decimal("1")
    period_value: Quantity = Decimal("1")
    period_unit: PeriodUnit = "day"
    #: The rental factor already looked up for `period_value` days. The
    #: engine does not read a factor table — `billing/catalog/factors.py`
    #: does that, and the number lands here.
    factor: Quantity = Decimal("1")
    unit_cost: Money = Decimal("0")
    discount: Optional[CalcDiscount] = None
    #: Which VAT bucket this line's net belongs in. An opaque key — the
    #: engine never looks up a rate, it only groups by this string and
    #: `apply_vat` is given the rates separately.
    tax_ref: str = Field(default="", max_length=64)
    #: What sort of thing this is, for a `kinds`-based adjustment to filter
    #: on. The catalog's item kind in practice; any string here.
    kind: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list)
    #: A line may be negative only when it says so — a correction, a credit.
    #: Off by default so a typed minus sign is a refusal, not a surprise.
    allow_negative: bool = False


class CalcAdjustment(BaseModel):
    """A discount or surcharge on a whole group, or on the document."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=500)
    type: AdjustmentType
    mode: AdjustmentMode
    value: Money
    base: AdjustmentBase = "subtotal"
    #: Only for `base: "kinds"` — the line kinds this one applies to.
    kinds: list[str] = Field(default_factory=list)


class CalcGroup(BaseModel):
    """A block of lines, or of lines and one more level of groups."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["group"] = "group"
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=4000)
    children: list["CalcChild"] = Field(default_factory=list)
    adjustments: list[CalcAdjustment] = Field(default_factory=list)


def _tag_child(value: object) -> object:
    """Fill in `type` from the shape, using the engine's own rule.

    `billing/calc/model.child_type` is that rule and this is its only other
    caller: a child with no `type` but with `children` is a group, exactly as
    the engine reads it. Without this the API would refuse every document the
    engine accepts from a curl command or a fixture file, which would make
    the golden fixtures unusable as request bodies — and those fixtures are
    how `POST /calc/preview` is tested against the S1 and S6 numbers.
    """
    if isinstance(value, dict) and value.get("type") is None:
        return {**value, "type": child_type(value)}
    return value


#: Discriminated on `type`, so a malformed child is reported as "this is not
#: a line" rather than as every field of both shapes being wrong at once.
CalcChild = Annotated[
    Union[CalcLine, CalcGroup],
    Field(discriminator="type"),
    BeforeValidator(_tag_child),
]

CalcGroup.model_rebuild()


class CalcDocumentRequest(BaseModel):
    """The whole thing to calculate. No id, no number, nothing stored."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="document", max_length=64)
    title: str = Field(default="", max_length=500)
    children: list[CalcChild] = Field(default_factory=list)
    adjustments: list[CalcAdjustment] = Field(default_factory=list)


class CalcPreviewRequest(BaseModel):
    """A document and, optionally, the VAT rates to apply to its buckets."""

    model_config = ConfigDict(extra="forbid")

    document: CalcDocumentRequest
    #: `tax_ref → percent`, as a string: `{"AT-20": "20.00"}`. Omitted, the
    #: response carries nets only and `vat` is null — which is the honest
    #: answer for a planning screen that has not resolved tax yet, and is
    #: why VAT is a separate step in the engine rather than part of it.
    vat_rates: Optional[dict[str, Money]] = None


# ─── The result ─────────────────────────────────────────────────────────────


class CalcWarningResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    where: Optional[str] = None


class CalcLineResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["line"] = "line"
    id: str
    label: str
    net: Money
    cost_net: Money
    margin: Money
    #: Null when the net is zero — a margin percentage of a zero net is not
    #: 0 % and not 100 %, it is undefined, and a screen showing "0 %" there
    #: would be stating something false.
    margin_percent: Optional[Money] = None
    tax_ref: str
    kind: str
    period_unit: str
    period_value: Quantity
    #: Before its own discount. The discount itself is the next field.
    gross: Money
    discount_amount: Money


class CalcAdjustmentResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    type: str
    mode: str
    base: str
    kinds: list[str]
    #: What the percentage was taken of, or what the amount was allocated
    #: over. Shown because "−10 %" with no base is a number nobody can
    #: check.
    base_amount: Money
    #: Signed: a discount is negative.
    amount: Money
    #: How the amount was split across the VAT buckets. Sums to `amount`
    #: exactly — largest remainder, not rounding each share on its own.
    by_tax_ref: dict[str, Money]


class CalcGroupResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: Literal["group"] = "group"
    id: str
    title: str
    description: str
    children: list["CalcChildResult"]
    subtotal_before_adjustments: Money
    adjustments: list[CalcAdjustmentResult]
    subtotal: Money
    cost_subtotal: Money
    margin: Money
    margin_percent: Optional[Money] = None
    #: Whether every line in it runs for the same period — what makes a
    #: collapsed "3 days × €X" row honest.
    uniform_period: bool
    net_by_tax_ref: dict[str, Money]


CalcChildResult = Annotated[
    Union[CalcLineResult, CalcGroupResult], Field(discriminator="type")
]

CalcGroupResult.model_rebuild()


class TaxRefTotalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tax_ref: str
    net: Money
    #: Null when no rate was supplied for this bucket.
    rate: Optional[Money] = None
    vat: Optional[Money] = None
    gross: Optional[Money] = None


class CalcVatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    by_tax_ref: list[TaxRefTotalResponse]
    net: Money
    vat: Money
    gross: Money


class CalcPreviewResponse(BaseModel):
    """Every number a screen needs, computed in one place.

    The web app renders these. It does not add them up, re-round them, or
    derive one from another — CLAUDE.md rule 2, and the reason
    `POST /calc/preview` exists at all.
    """

    model_config = ConfigDict(from_attributes=True)

    children: list["CalcChildResult"]
    subtotal: Money
    adjustments: list[CalcAdjustmentResult]
    net: Money
    cost: Money
    margin: Money
    margin_percent: Optional[Money] = None
    net_by_tax_ref: dict[str, Money]
    warnings: list[CalcWarningResponse]
    #: Present only when `vat_rates` was supplied.
    vat: Optional[CalcVatResponse] = None


CalcPreviewResponse.model_rebuild()


class GroupDisplayRowResponse(BaseModel):
    """One collapsed row for a group: "3 days × €1,250.00 = €3,750.00".

    `exact` is false when the price per period does not multiply back to the
    total — and the screen then has to show the lines instead. It is not a
    rounding tolerance to be ignored.
    """

    model_config = ConfigDict(from_attributes=True)

    group_id: str
    title: str
    quantity: Quantity
    period_value: Optional[Quantity] = None
    period_unit: Optional[str] = None
    price_per_period: Money
    total: Money
    uniform_period: bool
    exact: bool
    warnings: list[CalcWarningResponse]
