"""Money and quantities on the wire and in the database.

CLAUDE.md rule 1, made enforceable rather than aspirational: amounts are
`Decimal`, never float; the database stores `NUMERIC(18,2)` for money and
`NUMERIC(18,4)` for quantities and rates; the API serialises both as JSON
**strings**; and `core/rounding.py` holds the only rounding in the system.

**The arithmetic lives one file down, in `core/rounding.py`.** This module is
the Pydantic and SQLAlchemy surface — the annotations and the column types —
and it imports both of those libraries, which is exactly why the rounding
could not stay here: `apps/api/billing/` may import neither (principle P1,
`tests/test_billing_import_boundary.py`), and the calculation engine lives
there and has to round. Splitting it was the only way to keep rule 1 literally
true instead of having a second `quantize` in the engine. Every name this
module used to own is re-exported below, so no caller changed.

Three decisions worth the space, because each one is a bug that has shipped
in other billing systems:

**1. A JSON number is refused, not coerced.** `{"amount": 12.3}` is a 422.
That looks unfriendly until you notice what accepting it would mean: JSON
numbers are IEEE-754 doubles, so the request has already lost the value
before FilmBill sees it — `12.3` is really 12.30000000000000426... and no
amount of care downstream gets the original back. Refusing it makes the
client send `"12.3"`, which is exact. An integer IS accepted, because an
integer survives the round trip intact. (`rounding.to_decimal`.)

**2. Trailing zeros are preserved.** `"12.30"` stays `"12.30"` and does not
become `"12.3"`. `Decimal` carries its own exponent, so the scale a value
arrived with is information — it is the difference between "twelve euros
thirty" and "twelve point three of something". An invoice line that renders
`€12.3` is a bug report waiting to be filed.

**3. The rounding context is explicit and local.** Never `getcontext()`.
Python's decimal context is global, mutable, and thread-local — which means
any library, anywhere in the process, can change FilmBill's rounding from a
distance and leave no trace at the call site. Passing an explicit `Context`
to `quantize` makes that impossible; `test_money.py` proves it by setting a
hostile global context and asserting the answer does not move.
"""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema
from sqlalchemy import Numeric

from .rounding import (
    AMOUNT_PLACES,
    QTY_PLACES,
    DecimalParseError,
    divide,
    multiply,
    percent_of,
    quantize_amount,
    quantize_qty,
    to_decimal,
)

#: Re-exported so that `from ..core.money import quantize_amount` keeps
#: working and there is one obvious import for both halves of the subject.
#: The implementations are in `core/rounding.py` — see this module's
#: docstring for why they are not here.
__all__ = [
    "AMOUNT_PLACES",
    "QTY_PLACES",
    "AmountColumn",
    "DecimalParseError",
    "Money",
    "QtyColumn",
    "Quantity",
    "divide",
    "multiply",
    "percent_of",
    "quantize_amount",
    "quantize_qty",
    "to_decimal",
]


# ─── Parsing ────────────────────────────────────────────────────────────────


def _parse_amount(value: Any) -> Decimal:
    return to_decimal(value, what="An amount")


def _parse_qty(value: Any) -> Decimal:
    return to_decimal(value, what="A quantity")


def _serialise(value: Decimal) -> str:
    """`Decimal` → the string that goes on the wire.

    `format(value, "f")` rather than `str(value)`: `str` uses scientific
    notation for values `Decimal` chooses to hold that way — `Decimal("1E+2")`
    prints as `"1E+2"` — and no client parses that as money. `"f"` always
    gives plain positional digits, and preserves the scale (see docstring
    point 2).
    """
    return format(value, "f")


#: Shared by both annotations so the OpenAPI document — and therefore the
#: generated TypeScript — says `string` rather than `number`. Without this the
#: web would be handed `amount: number` and the entire rule would be undone at
#: the type level before anybody wrote a line of code.
_JSON_SCHEMA = {
    "type": "string",
    "description": (
        "A decimal number as a string. Sent and received as a string so no "
        "value passes through a binary float."
    ),
}

#: An amount of money. `NUMERIC(18,2)` in the database, a string on the wire.
Money = Annotated[
    Decimal,
    BeforeValidator(_parse_amount),
    PlainSerializer(_serialise, return_type=str, when_used="json"),
    WithJsonSchema({**_JSON_SCHEMA, "examples": ["1250.00"]}),
]

#: A quantity, rate or unit price. `NUMERIC(18,4)`, a string on the wire.
Quantity = Annotated[
    Decimal,
    BeforeValidator(_parse_qty),
    PlainSerializer(_serialise, return_type=str, when_used="json"),
    WithJsonSchema({**_JSON_SCHEMA, "examples": ["1.5000"]}),
]


# ─── Columns ────────────────────────────────────────────────────────────────


def AmountColumn() -> Numeric:
    """`NUMERIC(18,2)` — every money column in the system.

    A function rather than a module-level instance: SQLAlchemy type objects
    are shared when reused, which works today and is the kind of shared
    mutable that eventually gets a `.with_variant()` called on it by one
    table and surprises forty others.

    18 digits with 2 after the point leaves 16 before it — more than any
    production budget, and small enough to stay exact in `NUMERIC`.
    """
    return Numeric(precision=18, scale=AMOUNT_PLACES, asdecimal=True)


def QtyColumn() -> Numeric:
    """`NUMERIC(18,4)` — quantities, rates, unit prices, percentages."""
    return Numeric(precision=18, scale=QTY_PLACES, asdecimal=True)
