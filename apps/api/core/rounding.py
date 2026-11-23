"""The arithmetic underneath `money.py` — and the only rounding in FilmBill.

**Why this file exists separately from `core/money.py`.** CLAUDE.md rule 1
says rounding happens in one place; principle P1 and
`tests/test_billing_import_boundary.py` say `apps/api/billing/` imports
neither SQLAlchemy nor FastAPI. Those two rules collided the moment the
calculation engine arrived: `money.py` imports `sqlalchemy.Numeric` and
`pydantic`, so `billing/calc` cannot import it — and a calc engine with its
own private `quantize` would be a second rounding implementation, which is
the thing rule 1 exists to prevent.

So the arithmetic moved down here, where it depends on nothing but
`decimal`, and `money.py` re-exports every name it used to own. Nothing that
imported `core.money.quantize_amount` has to change, there is still exactly
one `ROUND_HALF_UP` context in the system, and `billing/calc` can reach it.

**Every operation runs in an explicit context, not just the rounding.**
`money.py`'s docstring explains why `getcontext()` is never used: Python's
decimal context is global, mutable and thread-local, so any library anywhere
in the process can change FilmBill's rounding from a distance. That argument
applies to multiplication and division as much as to `quantize` — a line net
is four multiplications before it is rounded once, and a pro-rata allocation
is a division. `multiply` and `divide` here therefore go through
`_CONTEXT.multiply` / `_CONTEXT.divide` rather than the `*` and `/`
operators, which would read the global context for precision and for the
rounding of an inexact result.
"""

from contextlib import contextmanager
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_UP,
    localcontext,
)
from typing import Any, Iterator

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


def to_decimal(value: Any, *, what: str) -> Decimal:
    """The one conversion. Strings and ints in, `Decimal` out, floats refused.

    A JSON number is refused rather than coerced: JSON numbers are IEEE-754
    doubles, so the request has already lost the value before FilmBill sees
    it — `12.3` is really 12.30000000000000426… and no care downstream gets
    the original back. Refusing it makes the client send `"12.3"`, which is
    exact. An integer IS accepted, because an integer survives the round trip.

    `bool` is checked before `int` on purpose: in Python `True` IS an `int`,
    so an unguarded `isinstance(value, int)` silently turns a JSON `true`
    into an amount of 1.
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


# ─── Unrounded arithmetic, still in the one context ─────────────────────────


def multiply(*values: Decimal) -> Decimal:
    """The product of `values`, computed in `_CONTEXT`. Not rounded.

    Variadic because the thing that needs it is a line net —
    `quantity × period_value × factor × unit_price` — and rule 1 says that
    product is rounded ONCE, at the end (SCOPE §5.3). Folding the
    multiplications here keeps every intermediate in the explicit context
    and keeps the call site a single expression, so there is nowhere for an
    accidental `quantize` to appear halfway through.

    An empty call is `Decimal("1")`, the multiplicative identity, so a caller
    building a product from an optional list needs no special case.
    """
    product = Decimal(1)
    for value in values:
        product = _CONTEXT.multiply(product, value)
    return product


def divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    """`numerator / denominator` in `_CONTEXT`. Not rounded.

    Raises `DivisionByZero` rather than returning `Infinity` — the context
    traps it. The caller is expected to have decided what a zero denominator
    means (see `billing/calc`'s allocation, where a zero base is a named
    case) rather than to discover it as a `NaN` in a total.
    """
    return _CONTEXT.divide(numerator, denominator)


@contextmanager
def exact_arithmetic() -> Iterator[Context]:
    """Install FilmBill's decimal context for the duration of a block.

    `multiply`, `divide` and `percent_of` carry the context on their own, but
    a calculation is not only multiplications: `subtotal += line.net` and
    `net - cost` are plain operators, and a plain operator reads the GLOBAL
    context for its precision. The default global precision is 28 significant
    digits, which is enough for any real budget — and "enough for any real
    budget" is not the claim this project wants to be making about money,
    because the global context is mutable from a distance by any library in
    the process and nothing at the call site would show it had changed.

    So the engine's public entry points open this block and every operator
    inside them, named or not, runs at `prec=34` with `ROUND_HALF_UP` and
    the overflow and division-by-zero traps armed.
    `test_calc_engine.py::test_a_hostile_global_decimal_context_changes_nothing`
    sets `prec=5, ROUND_DOWN` globally and asserts S1 still comes to
    12 517.20 — without the block that test fails, which is the only
    reason to believe the block is doing anything.
    """
    with localcontext(_CONTEXT) as context:
        yield context


def percent_of(value: Decimal, percent: Decimal) -> Decimal:
    """`value × percent / 100`, in `_CONTEXT`. Not rounded.

    A named function rather than `multiply(value, percent) / 100` at four
    call sites, because "is this rate a fraction or a percentage" is the
    question that produces a 2 000 % VAT line, and the name answers it: a
    rate of 20 % is `Decimal("20")` here, the same spelling the tax engine's
    `format_rate` and the `tax_codes.rate` column use.
    """
    return divide(multiply(value, percent), Decimal(100))
