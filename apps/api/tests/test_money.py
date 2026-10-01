"""Money: strings in, strings out, and rounding nothing can reach from outside.

Everything here runs against the REAL `Money` / `Quantity` annotations inside
a real Pydantic model and, where it matters, a real FastAPI request — not
against a helper that stands in for them. The failure these guard against is
specifically a value quietly changing on its way through a layer, so a test
that skipped a layer would be testing the wrong thing.
"""

import decimal
from decimal import ROUND_DOWN, Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apps.api.core.money import (
    AmountColumn,
    Money,
    QtyColumn,
    Quantity,
    quantize_amount,
    quantize_qty,
)


class Line(BaseModel):
    amount: Money
    quantity: Quantity


@pytest.fixture
def money_client():
    """A tiny app, so the assertions are about a real request/response cycle.

    Mounted on its own FastAPI instance rather than on `main.app`: this is
    about the ANNOTATION's behaviour, and hanging a test route off the real
    app would widen the API surface that `test_openapi_surface.py` pins.
    """
    app = FastAPI()

    @app.post("/echo", response_model=Line)
    def echo(line: Line) -> Line:
        return line

    return TestClient(app, raise_server_exceptions=False)


# ─── §4.1 — parsing and serialisation ───────────────────────────────────────


def test_a_json_number_is_refused(money_client):
    """The rule the whole module exists for.

    `12.3` in JSON is an IEEE-754 double and is really
    12.300000000000000710542735760100185871124267578125. The value is already
    lost by the time FastAPI hands it over, so the only honest answer is to
    refuse it and make the client send a string.
    """
    response = money_client.post("/echo", json={"amount": 12.3, "quantity": "1"})

    assert response.status_code == 422
    assert "string" in response.text


def test_a_json_number_is_refused_for_quantities_too(money_client):
    response = money_client.post("/echo", json={"amount": "1.00", "quantity": 2.5})

    assert response.status_code == 422


def test_an_integer_is_accepted(money_client):
    """An integer survives JSON intact, so there is nothing to refuse."""
    response = money_client.post("/echo", json={"amount": 5, "quantity": 3})

    assert response.status_code == 200
    assert response.json() == {"amount": "5", "quantity": "3"}


def test_a_string_round_trips_with_its_trailing_zeros(money_client):
    """`"12.30"` stays `"12.30"`.

    A scale that changes on the way through is the bug that renders an
    invoice line as `€12.3`. `Decimal` carries its own exponent, so keeping
    it costs nothing — it only has to not be thrown away.
    """
    response = money_client.post(
        "/echo", json={"amount": "12.30", "quantity": "1.5000"}
    )

    assert response.json() == {"amount": "12.30", "quantity": "1.5000"}


def test_a_large_exact_value_survives(money_client):
    """The value a double would visibly mangle. 16 digits before the point is
    what `NUMERIC(18,2)` allows."""
    response = money_client.post(
        "/echo", json={"amount": "12345678901234.56", "quantity": "0.0001"}
    )

    assert response.json()["amount"] == "12345678901234.56"


def test_a_negative_amount_is_fine(money_client):
    response = money_client.post("/echo", json={"amount": "-99.95", "quantity": "1"})

    assert response.json()["amount"] == "-99.95"


@pytest.mark.parametrize("hostile", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_values_are_refused(money_client, hostile):
    """All four are valid input to `Decimal()` and none is an amount.

    Left through, a `NaN` poisons every total it touches and never raises —
    it surfaces as a constraint violation at the database, three functions
    away from whatever produced it.
    """
    response = money_client.post("/echo", json={"amount": hostile, "quantity": "1"})

    assert response.status_code == 422


def test_a_boolean_is_not_an_amount_of_one(money_client):
    """`True` IS an `int` in Python. An unguarded isinstance check turns a
    JSON `true` into an amount of 1, silently."""
    response = money_client.post("/echo", json={"amount": True, "quantity": "1"})

    assert response.status_code == 422


@pytest.mark.parametrize("junk", ["", "   ", "12,30", "€12.30", "twelve", "1 2"])
def test_junk_strings_are_refused(money_client, junk):
    response = money_client.post("/echo", json={"amount": junk, "quantity": "1"})

    assert response.status_code == 422, f"accepted {junk!r}"


def test_the_openapi_schema_says_string_not_number():
    """What the generated TypeScript will say.

    If this drifts to `number`, the web app is handed `amount: number`, every
    call site converts, and the rule is undone at the type level before
    anybody writes a line of arithmetic.
    """
    schema = Line.model_json_schema()

    assert schema["properties"]["amount"]["type"] == "string"
    assert schema["properties"]["quantity"]["type"] == "string"


# ─── §4.1 — quantizing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.005", "0.01"),  # the half-up case, exactly on the boundary
        ("0.004", "0.00"),
        ("0.015", "0.02"),  # half-EVEN would give 0.02 here too …
        ("0.025", "0.03"),  # … and 0.02 here, which is the difference
        ("-0.005", "-0.01"),  # half-up is away from zero for negatives
        ("2.675", "2.68"),  # the classic float-rounding example
        ("12.30", "12.30"),
    ],
)
def test_quantize_amount_rounds_half_up_to_two_places(value, expected):
    assert quantize_amount(Decimal(value)) == Decimal(expected)
    assert str(quantize_amount(Decimal(value))) == expected


@pytest.mark.parametrize(
    "value,expected",
    [("1.00005", "1.0001"), ("1.00004", "1.0000"), ("2.5", "2.5000")],
)
def test_quantize_qty_keeps_four_places(value, expected):
    assert str(quantize_qty(Decimal(value))) == expected


# ─── §4.2 — the global decimal context cannot reach in ──────────────────────


@pytest.fixture
def hostile_global_context():
    """Set the global context to something that would give a wrong answer.

    ROUND_DOWN instead of ROUND_HALF_UP, so a function that used
    `getcontext()` would answer `0.00` where the right answer is `0.01`. The
    point is not that a library WOULD do this — it is that any library in the
    process CAN, at any time, and `quantize_amount`'s call site would look
    exactly the same either way.
    """
    original = decimal.getcontext()
    decimal.setcontext(decimal.Context(prec=6, rounding=ROUND_DOWN))
    yield
    decimal.setcontext(original)


def test_a_hostile_global_context_does_not_change_the_answer(hostile_global_context):
    assert decimal.getcontext().rounding == ROUND_DOWN

    assert quantize_amount(Decimal("0.005")) == Decimal("0.01")
    assert quantize_qty(Decimal("1.00005")) == Decimal("1.0001")


def test_the_global_context_is_genuinely_hostile(hostile_global_context):
    """The control.

    Without this, the test above would pass just as well against a global
    context that happened to round half-up anyway — proving nothing. This
    shows the same operation, taken through the global context, gives the
    WRONG answer, so the assertion above is a real difference.
    """
    with_global = Decimal("0.005").quantize(Decimal("0.01"))

    assert with_global == Decimal("0.00")
    assert quantize_amount(Decimal("0.005")) != with_global


def test_a_hostile_context_precision_does_not_truncate_a_large_amount(
    hostile_global_context,
):
    """`prec=6` globally would destroy a fourteen-digit amount."""
    assert quantize_amount(Decimal("12345678901234.555")) == Decimal(
        "12345678901234.56"
    )


# ─── Columns ────────────────────────────────────────────────────────────────


def test_the_column_helpers_match_the_documented_precision():
    """CLAUDE.md rule 1 names these two shapes; migrations will use them."""
    amount, qty = AmountColumn(), QtyColumn()

    assert (amount.precision, amount.scale) == (18, 2)
    assert (qty.precision, qty.scale) == (18, 4)
    assert amount.asdecimal and qty.asdecimal


def test_each_call_returns_a_fresh_type_object():
    """Shared SQLAlchemy type instances are shared mutable state — one table
    calling `.with_variant()` on it would surprise every other."""
    assert AmountColumn() is not AmountColumn()
