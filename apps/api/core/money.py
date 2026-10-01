"""Money and quantities. The only place FilmBill rounds anything.

CLAUDE.md rule 1, made enforceable rather than aspirational: amounts are
`Decimal`, never float; the database stores `NUMERIC(18,2)` for money and
`NUMERIC(18,4)` for quantities and rates; the API serialises both as JSON
**strings**; and the two `quantize_*` functions here are the only rounding in
the system.

Three decisions worth the space, because each one is a bug that has shipped
in other billing systems:

**1. A JSON number is refused, not coerced.** `{"amount": 12.3}` is a 422.
That looks unfriendly until you notice what accepting it would mean: JSON
numbers are IEEE-754 doubles, so the request has already lost the value
before FilmBill sees it — `12.3` is really 12.30000000000000426... and no
amount of care downstream gets the original back. Refusing it makes the
client send `"12.3"`, which is exact. An integer IS accepted, because an
integer survives the round trip intact.

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

from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_UP,
)
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema
from sqlalchemy import Numeric

# ─── Scales ─────────────────────────────────────────────────────────────────

#: Decimal places for money. Two, matching `NUMERIC(18,2)`.
AMOUNT_PLACES = 2
#: Decimal places for quantities, rates and unit prices. Four, matching
#: `NUMERIC(18,4)` — a day rate split across three productions, or a
#: percentage carried to four places, needs the room that money does not.
QTY_PLACES = 4

_AMOUNT_EXPONENT = Decimal(1).scaleb(-AMOUNT_PLACES)  # Decimal("0.01")
_QTY_EXPONENT = Decimal(1).scaleb(-QTY_PLACES)  # Decimal("0.0001")

#: The one context every rounding in FilmBill happens in.
#:
#: `prec=34` is IEEE-754 decimal128's precision: comfortably more than
#: `NUMERIC(18,x)` can hold, so the precision limit is the database's and not
#: an invisible second one here.
#:
#: The traps matter as much as the rounding. Without them an overflow or a
#: division by zero yields `Infinity` or `NaN` and keeps going, and a `NaN`
#: total reaches the database as a constraint violation three functions
#: later, where nothing says what produced it. Trapped, it raises where it
#: happened.
_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_UP,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


# ─── Parsing ────────────────────────────────────────────────────────────────


class DecimalParseError(ValueError):
    """A value that cannot be a money or quantity. Surfaces as a 422."""


def _to_decimal(value: Any, *, what: str) -> Decimal:
    """The one conversion. Strings and ints in, `Decimal` out, floats refused.

    `bool` is checked before `int` on purpose: in Python `True` IS an `int`,
    so an unguarded `isinstance(value, int)` silently turns a JSON `true` into
    an amount of 1.
    """
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, bool):
        raise DecimalParseError(f"{what} must be a string or an integer, not a boolean")
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise DecimalParseError(f"{what} must not be empty")
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            raise DecimalParseError(f"{what} is not a number: {value!r}") from None
    elif isinstance(value, float):
        # The whole point of this module. See the module docstring.
        raise DecimalParseError(
            f"{what} must be sent as a string, not a JSON number — a JSON "
            f"number is a binary float and has already lost the exact value "
            f"(got {value!r})"
        )
    else:
        raise DecimalParseError(
            f"{what} must be a string or an integer, not {type(value).__name__}"
        )

    # "NaN", "Infinity" and "sNaN" are all valid input to `Decimal()`. They
    # are not valid amounts, and left through they would poison every total
    # they touch without ever raising.
    if not parsed.is_finite():
        raise DecimalParseError(f"{what} must be a finite number, not {parsed}")
    return parsed


def _parse_amount(value: Any) -> Decimal:
    return _to_decimal(value, what="An amount")


def _parse_qty(value: Any) -> Decimal:
    return _to_decimal(value, what="A quantity")


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


# ─── Rounding ───────────────────────────────────────────────────────────────


def quantize_amount(value: Decimal) -> Decimal:
    """Round to 2 dp, half up, in the local context. The only money rounding.

    Half UP, not Python's default half-EVEN. Banker's rounding is the better
    choice for long statistical sums and the wrong one for an invoice: the
    rule a customer, an auditor and every other billing system in Austria
    expect is that 0.005 becomes 0.01, every time, not "sometimes, depending
    on the digit before it".
    """
    return value.quantize(_AMOUNT_EXPONENT, rounding=ROUND_HALF_UP, context=_CONTEXT)


def quantize_qty(value: Decimal) -> Decimal:
    """Round to 4 dp, half up, in the local context."""
    return value.quantize(_QTY_EXPONENT, rounding=ROUND_HALF_UP, context=_CONTEXT)


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
