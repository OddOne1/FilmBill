"""The calculation engine's contract: validation, types, and the rules §5.6 fixes.

The cents live in `test_calc_fixtures.py` — this file is about the properties
a number alone cannot show: that a wrong input is refused with the value
named, that nothing in the output is a float, that the answer does not depend
on the ambient decimal context, and that the three rules the A1 prompt asked
for mutations against each have a test which FAILS when the rule is broken.

Each of those four rule tests is written so that it isolates its own rule:

  * `test_each_line_is_rounded_on_its_own` — seven 0.333 lines. Rounding the
    sum instead gives 2.33; rounding each gives 2.31. No other behaviour in
    the engine can produce either number.
  * `test_vat_is_per_category_on_the_sum_of_nets` — S6's eight lines. Per
    category: 500.46. Per line: 500.49. Three cents apart, and nothing else
    moves them.
  * `test_a_percent_adjustment_uses_its_declared_base` — a discount then a
    compound fee then a plain fee, whose three base amounts are 1 000, 900
    and 1 000. Any single wrong base reading changes one of them.
  * `test_a_third_level_of_nesting_is_refused` — and the depth-2 case beside
    it, so the limit is not satisfied by refusing everything.
"""

import decimal
from decimal import ROUND_DOWN, Context, Decimal
from pathlib import Path
import ast
import json

import pytest

from apps.api.billing.calc import (
    Adjustment,
    CalcDocument,
    CalcError,
    Discount,
    Group,
    Line,
    MAX_GROUP_DEPTH,
    PERIOD_UNITS,
    UNALLOCATED_TAX_REF,
    apply_vat,
    calculate,
    display_row_for_group,
    document_from_data,
)

CALC_PACKAGE = Path(__file__).resolve().parents[1] / "billing" / "calc"
FIXTURES = Path(__file__).parent / "fixtures" / "calc"


def line(**fields) -> Line:
    """A `Line` with every number already a `Decimal`.

    A helper rather than `line_from_data` where the test is about the
    dataclass rather than about parsing — the parser has its own tests below.
    """
    defaults = {
        "id": "l1",
        "unit_price": Decimal("100"),
        "quantity": Decimal("1"),
        "period_value": Decimal("1"),
        "period_unit": "day",
        "factor": Decimal("1"),
        "unit_cost": Decimal("0"),
    }
    return Line(**{**defaults, **fields})


def doc(*children, adjustments=()) -> CalcDocument:
    return CalcDocument(children=children, adjustments=tuple(adjustments))


# ─── §5.6, rule 1: each line is rounded on its own ──────────────────────────


def test_each_line_is_rounded_on_its_own():
    """Seven lines of 0.333 are 2.31, not 2.33.

    The mutation this exists for is "round the group sum instead of each
    line". 7 × 0.333 is 2.331, which rounds to 2.33; 7 × round(0.333) is
    7 × 0.33 = 2.31. §5.6 is explicit that the line is the rounding unit, and
    the two answers differ on any document with small repeated lines — which
    is every per-diem and every km allowance.
    """
    result = calculate(
        doc(
            *[
                line(id=f"s{index}", unit_price=Decimal("0.333"), period_unit="piece")
                for index in range(7)
            ]
        )
    )

    assert [entry.net for entry in result.children] == [Decimal("0.33")] * 7
    assert result.net == Decimal("2.31"), (
        "the lines were not rounded individually: 2.33 is the sum rounded "
        "once, 2.31 is seven roundings of 0.333 (SCOPE §5.6)"
    )


def test_a_group_subtotal_is_the_sum_of_already_rounded_lines():
    """The same rule one level down, where a group could re-round."""
    group = Group(
        id="g",
        title="Per diem",
        children=tuple(
            line(id=f"s{index}", unit_price=Decimal("0.333"), period_unit="piece")
            for index in range(7)
        ),
    )
    result = calculate(doc(group))
    assert result.children[0].subtotal == Decimal("2.31")
    assert result.net == Decimal("2.31")


def test_half_up_not_half_even():
    """2 499.975 is 2 499.98, and 0.125 is 0.13.

    Python's default is ROUND_HALF_EVEN, which would give 0.12 for the
    second one. `core/rounding.quantize_amount`'s docstring explains why an
    invoice uses half up; this asserts it through the engine, where it
    matters, rather than only at the helper.
    """
    s6 = calculate(
        doc(
            line(
                id="big",
                quantity=Decimal("3"),
                period_value=Decimal("2.5"),
                unit_price=Decimal("333.33"),
            )
        )
    )
    assert s6.net == Decimal("2499.98")

    halves = calculate(
        doc(line(id="h", unit_price=Decimal("0.125"), period_unit="piece"))
    )
    assert halves.net == Decimal("0.13"), "half-even would give 0.12"


# ─── §5.6, rule 2: VAT per category on the sum ──────────────────────────────


def test_vat_is_per_category_on_the_sum_of_nets():
    """S6's totals, and the number a per-line implementation would give.

    Per category on the sum: 2 502.29 × 20 % = 500.458 → **500.46**.
    Per line: 2 499.98 × 20 % = 499.996 → 500.00, plus seven times
    (0.33 × 20 % = 0.066 → 0.07) = 0.49, total **500.49**.

    Both numbers are computed here, so the test states the difference rather
    than only the right answer — the mutation that taxes each line produces
    500.49 and this assertion names it.
    """
    document = document_from_data(json.loads((FIXTURES / "s6-rounding.json").read_text())["document"])
    result = calculate(document)
    vat = apply_vat(result, {"AT-20": Decimal("20")})

    per_line = sum(
        (Decimal(entry.net) * Decimal("20") / Decimal("100")).quantize(
            Decimal("0.01"), rounding=decimal.ROUND_HALF_UP
        )
        for entry in result.lines.values()
    )
    assert per_line == Decimal("500.49"), "the per-line figure moved; re-derive it"
    assert vat.vat == Decimal("500.46"), (
        f"VAT came to {vat.vat}; 500.49 is what taxing each line separately "
        f"gives, and §5.6 says per category on the SUM of nets"
    )
    assert vat.gross == Decimal("3002.75")


def test_vat_is_computed_per_category_and_not_on_the_document_total():
    """Two categories at different rates. One blended rate cannot do this."""
    result = calculate(
        doc(
            line(id="standard", unit_price=Decimal("1000"), tax_ref="AT-20"),
            line(id="reduced", unit_price=Decimal("1000"), tax_ref="AT-10"),
        )
    )
    vat = apply_vat(result, {"AT-20": Decimal("20"), "AT-10": Decimal("10")})

    assert {entry.tax_ref: entry.vat for entry in vat.by_tax_ref} == {
        "AT-20": Decimal("200.00"),
        "AT-10": Decimal("100.00"),
    }
    assert vat.vat == Decimal("300.00")
    assert vat.gross == Decimal("2300.00")


def test_a_missing_vat_rate_is_refused_and_names_the_category():
    result = calculate(doc(line(id="l1", tax_ref="AT-20")))
    with pytest.raises(CalcError, match="AT-20"):
        apply_vat(result, {})


def test_a_float_vat_rate_is_refused():
    """A rate that arrived as a float has already lost its value."""
    result = calculate(doc(line(id="l1", tax_ref="AT-20")))
    with pytest.raises(CalcError, match="float"):
        apply_vat(result, {"AT-20": 20.0})  # type: ignore[dict-item]


def test_a_rate_is_a_percentage_and_out_of_range_is_refused():
    """`Decimal("0.2")` is 0.2 %, and is almost certainly meant as 20 %.

    Accepted, it would produce a VAT of 25.03 on S1 and nothing would look
    obviously wrong. Refused, it costs a sentence. The one it cannot catch is
    a rate between 0 and 100 that is simply the wrong rate, which is a
    decision and not a unit error.
    """
    result = calculate(doc(line(id="l1", tax_ref="AT-20")))
    vat = apply_vat(result, {"AT-20": Decimal("0.2")})
    assert vat.vat == Decimal("0.20"), (
        "0.2 is accepted as 0.2 %; the guard below is about the out-of-range "
        "case, and this documents that a plausible-but-wrong rate is the "
        "caller's problem"
    )
    with pytest.raises(CalcError, match="between 0 and 100"):
        apply_vat(result, {"AT-20": Decimal("120")})


# ─── §5.4: adjustment bases ─────────────────────────────────────────────────


def test_a_percent_adjustment_uses_its_declared_base():
    """`subtotal` is the subtotal BEFORE this container's own adjustments.

    The mutation this exists for is "apply a percent adjustment to the wrong
    base". The three base amounts below are 1 000.00, 900.00 and 1 000.00; a
    reading that used the running total everywhere would give 1 000, 900 and
    972, and one that used the original subtotal everywhere would give
    1 000, 1 000, 1 000. Each wrong reading changes at least one of them.
    """
    result = calculate(
        doc(
            line(id="l1", unit_price=Decimal("1000"), period_unit="flat"),
            adjustments=[
                Adjustment(
                    label="Volume discount",
                    type="discount",
                    mode="percent",
                    value=Decimal("10"),
                    base="subtotal",
                ),
                Adjustment(
                    label="Production fee",
                    type="surcharge",
                    mode="percent",
                    value=Decimal("8"),
                    base="subtotal_incl_previous",
                ),
                Adjustment(
                    label="Insurance",
                    type="surcharge",
                    mode="percent",
                    value=Decimal("5"),
                    base="subtotal",
                ),
            ],
        )
    )

    assert [entry.base_amount for entry in result.adjustments] == [
        Decimal("1000.00"),
        Decimal("900.00"),
        Decimal("1000.00"),
    ]
    assert [entry.amount for entry in result.adjustments] == [
        Decimal("-100.00"),
        Decimal("72.00"),
        Decimal("50.00"),
    ]
    assert result.net == Decimal("1022.00")


def test_a_kind_filtered_adjustment_sees_only_the_kinds_it_names():
    result = calculate(
        doc(
            line(id="crew", unit_price=Decimal("2000"), kind="labor", period_unit="flat"),
            line(id="taxi", unit_price=Decimal("300"), kind="travel", period_unit="flat"),
            adjustments=[
                Adjustment(
                    label="Fee",
                    type="surcharge",
                    mode="percent",
                    value=Decimal("8"),
                    base="kinds",
                    kinds=("labor",),
                )
            ],
        )
    )
    assert result.adjustments[0].base_amount == Decimal("2000.00")
    assert result.adjustments[0].amount == Decimal("160.00")
    assert result.net == Decimal("2460.00")


def test_a_parent_groups_base_includes_its_childrens_adjustments():
    """Two levels, each with a discount. The outer base is 2 700, not 3 000.

    The structural bug this is about: building a group's base from the raw
    line nets underneath it rather than from its children's subtotals. On a
    flat document the two agree, which is why every other test here would
    pass with it.
    """
    inner = Group(
        id="inner",
        title="Camera",
        children=(
            line(id="alexa", unit_price=Decimal("1200"), period_value=Decimal("2")),
            line(id="lenses", unit_price=Decimal("300"), period_value=Decimal("2")),
        ),
        adjustments=(
            Adjustment(
                label="Camera discount",
                type="discount",
                mode="percent",
                value=Decimal("10"),
            ),
        ),
    )
    outer = Group(
        id="outer",
        title="Production",
        children=(inner,),
        adjustments=(
            Adjustment(
                label="Outer discount",
                type="discount",
                mode="percent",
                value=Decimal("5"),
            ),
        ),
    )
    result = calculate(doc(outer))
    outer_result = result.children[0]

    assert outer_result.subtotal_before_adjustments == Decimal("2700.00")
    assert outer_result.adjustments[0].base_amount == Decimal("2700.00")
    assert outer_result.adjustments[0].amount == Decimal("-135.00")
    assert result.net == Decimal("2565.00")
    # The per-category map has to agree with the subtotal at every level,
    # or the pro-rata weights used one level up are wrong.
    assert sum(outer_result.net_by_tax_ref.values()) == outer_result.subtotal
    assert sum(result.net_by_tax_ref.values()) == result.net


def test_an_allocation_always_sums_to_the_adjustment_it_split():
    """The property, over a split that cannot come out even.

    100.00 across three equal categories is 33.3333 each, which rounds to
    33.33 and leaves a cent. The cent is given to the heaviest share — the
    tie is broken by the category key, so 'C' — and the sum is exact.
    """
    result = calculate(
        doc(
            line(id="a", unit_price=Decimal("100"), tax_ref="A", period_unit="flat"),
            line(id="b", unit_price=Decimal("100"), tax_ref="B", period_unit="flat"),
            line(id="c", unit_price=Decimal("100"), tax_ref="C", period_unit="flat"),
            adjustments=[
                Adjustment(
                    label="Handling",
                    type="surcharge",
                    mode="amount",
                    value=Decimal("100"),
                )
            ],
        )
    )
    shares = result.adjustments[0].by_tax_ref
    assert shares == {
        "A": Decimal("33.33"),
        "B": Decimal("33.33"),
        "C": Decimal("33.34"),
    }
    assert sum(shares.values()) == result.adjustments[0].amount
    assert sum(result.net_by_tax_ref.values()) == result.net


def test_an_adjustment_with_nothing_to_allocate_against_is_parked_and_warned():
    """An amount surcharge on an empty document.

    It belongs to the total and to no tax category. Parked under
    `__unallocated__` with a warning, and `apply_vat` then refuses — which is
    right: there is no rate that could apply to it, and answering 0 % would
    invent one.
    """
    result = calculate(
        doc(
            adjustments=[
                Adjustment(
                    label="Minimum fee",
                    type="surcharge",
                    mode="amount",
                    value=Decimal("250"),
                )
            ]
        )
    )
    assert result.net == Decimal("250.00")
    assert result.net_by_tax_ref == {UNALLOCATED_TAX_REF: Decimal("250.00")}
    assert [warning.code for warning in result.warnings] == [
        "adjustment_not_allocatable"
    ]
    assert result.warnings[0].message
    with pytest.raises(CalcError, match="unallocated"):
        apply_vat(result, {"AT-20": Decimal("20")})


def test_adjustments_never_touch_the_cost_side():
    """A group discount reduces the net and leaves the cost where it was.

    This is the decision recorded in `engine._line_result`: the gaffer still
    costs what the gaffer costs. Asserted at the group level too, because a
    cost that moved with the discount would make `margin_percent` constant
    across any percentage discount — the one number that has to react.
    """
    without = calculate(
        doc(
            Group(
                id="g",
                children=(
                    line(
                        id="l1",
                        unit_price=Decimal("1000"),
                        unit_cost=Decimal("600"),
                        period_unit="flat",
                    ),
                ),
            )
        )
    )
    with_discount = calculate(
        doc(
            Group(
                id="g",
                children=(
                    line(
                        id="l1",
                        unit_price=Decimal("1000"),
                        unit_cost=Decimal("600"),
                        period_unit="flat",
                    ),
                ),
                adjustments=(
                    Adjustment(
                        label="Discount",
                        type="discount",
                        mode="percent",
                        value=Decimal("10"),
                    ),
                ),
            )
        )
    )

    assert without.cost == with_discount.cost == Decimal("600.00")
    assert without.net == Decimal("1000.00")
    assert with_discount.net == Decimal("900.00")
    assert without.margin_percent == Decimal("40.00")
    assert with_discount.margin_percent == Decimal("33.33")


# ─── §5.4: nesting depth ────────────────────────────────────────────────────


def test_two_levels_of_nesting_are_allowed():
    """The limit is not satisfied by refusing everything."""
    inner = Group(id="inner", children=(line(id="l1"),))
    outer = Group(id="outer", children=(inner,))
    result = calculate(doc(outer))
    assert result.net == Decimal("100.00")
    assert MAX_GROUP_DEPTH == 2


def test_a_third_level_of_nesting_is_refused():
    deepest = Group(id="deepest", children=(line(id="l1"),))
    middle = Group(id="middle", children=(deepest,))
    top = Group(id="top", children=(middle,))

    with pytest.raises(CalcError) as error:
        CalcDocument(children=(top,))

    message = str(error.value)
    assert "deepest" in message, "the refusal must name the group that is too deep"
    assert "2" in message, "and say what the limit is"


def test_the_depth_check_counts_from_the_document_and_not_from_a_group():
    """A group that is legal on its own is illegal two levels down.

    `Group` deliberately does not validate its own depth — it cannot know
    where it has been put — so the check has to be the document's. A test
    that only ever built the tree top-down would not notice the difference.
    """
    legal_on_its_own = Group(id="g2", children=(Group(id="g3", children=()),))
    CalcDocument(children=(legal_on_its_own,))  # depth 1 and 2: fine

    with pytest.raises(CalcError, match="g3"):
        CalcDocument(children=(Group(id="g1", children=(legal_on_its_own,)),))


def test_duplicate_ids_are_refused():
    """The result is indexed by id, so two `l1`s would silently become one."""
    with pytest.raises(CalcError, match="l1"):
        CalcDocument(children=(line(id="l1"), line(id="l1")))

    with pytest.raises(CalcError, match="shared"):
        CalcDocument(
            children=(
                Group(id="shared", children=(line(id="l1"),)),
                line(id="shared"),
            )
        )


# ─── Validation, with the value named ───────────────────────────────────────


def test_an_unknown_period_unit_is_refused_and_named():
    with pytest.raises(CalcError) as error:
        line(id="l1", period_unit="days")
    message = str(error.value)
    assert "'days'" in message
    assert "l1" in message
    for unit in PERIOD_UNITS:
        assert unit in message, "the refusal lists what IS allowed"


def test_a_factor_of_zero_or_less_is_refused():
    for bad in ("0", "-1"):
        with pytest.raises(CalcError, match="greater than zero"):
            line(id="l1", factor=Decimal(bad))
    assert calculate(doc(line(id="l1", factor=Decimal("2.8")))).net == Decimal("280.00")


def test_a_discount_percentage_outside_zero_to_a_hundred_is_refused():
    with pytest.raises(CalcError, match="between 0 and 100"):
        Discount(mode="percent", value=Decimal("101"))
    with pytest.raises(CalcError, match="cannot be negative"):
        Discount(mode="percent", value=Decimal("-1"))
    assert Discount(mode="percent", value=Decimal("100")).value == Decimal("100")


def test_a_negative_quantity_needs_the_correction_flag():
    with pytest.raises(CalcError, match="allow_negative"):
        line(id="l1", quantity=Decimal("-1"))

    corrected = line(id="l1", quantity=Decimal("-1"), allow_negative=True)
    assert calculate(doc(corrected)).net == Decimal("-100.00")


def test_a_discount_larger_than_the_line_is_refused_unless_it_is_a_correction():
    """The case the quantity check cannot see.

    A 200.00 discount on a 120.00 taxi is a typo, and the only place it
    becomes visible is after the arithmetic — so the guard lives there, and
    the message says both numbers.
    """
    over = line(
        id="taxi",
        unit_price=Decimal("120"),
        period_unit="flat",
        discount=Discount(mode="amount", value=Decimal("200")),
    )
    with pytest.raises(CalcError) as error:
        calculate(doc(over))
    assert "200" in str(error.value) and "120" in str(error.value)

    allowed = line(
        id="taxi",
        unit_price=Decimal("120"),
        period_unit="flat",
        discount=Discount(mode="amount", value=Decimal("200")),
        allow_negative=True,
    )
    assert calculate(doc(allowed)).net == Decimal("-80.00")


def test_a_negative_period_is_refused():
    with pytest.raises(CalcError, match="cannot be negative"):
        line(id="l1", period_value=Decimal("-3"))


def test_an_adjustment_value_is_always_positive():
    with pytest.raises(CalcError, match="always positive"):
        Adjustment(label="x", type="discount", mode="percent", value=Decimal("-5"))


def test_a_kinds_base_without_kinds_and_kinds_without_the_base_are_both_refused():
    with pytest.raises(CalcError, match="names no kinds"):
        Adjustment(label="x", type="surcharge", mode="percent", value=Decimal("8"), base="kinds")
    with pytest.raises(CalcError, match="would be ignored"):
        Adjustment(
            label="x",
            type="surcharge",
            mode="percent",
            value=Decimal("8"),
            base="subtotal",
            kinds=("labor",),
        )


def test_an_unknown_adjustment_base_is_refused_and_named():
    with pytest.raises(CalcError, match="'group_subtotal'"):
        Adjustment(
            label="x", type="surcharge", mode="percent", value=Decimal("8"),
            base="group_subtotal",
        )


def test_calculate_refuses_anything_that_is_not_a_document():
    with pytest.raises(CalcError, match="document_from_data"):
        calculate({"children": []})  # type: ignore[arg-type]


# ─── No expression language ─────────────────────────────────────────────────


def test_a_quantity_that_names_something_is_refused_and_never_evaluated():
    """SCOPE's globals are resolved to numbers BEFORE the engine is called.

    The engine has no evaluator and must not grow one: a budget template is
    user input, so an expression language over it is an evaluator over user
    input. `"crew_days"` and `"2 * 3"` are both just strings that are not
    numbers, and both are refused by the one conversion in the system.
    """
    for expression in ("crew_days", "2 * 3", "shoot_days + 1", "=A1*B1"):
        with pytest.raises(CalcError, match="not a number"):
            document_from_data(
                {"children": [{"id": "l1", "quantity": expression, "unit_price": "100"}]}
            )


def test_the_engine_exposes_no_eval_hook():
    """No `eval`, `exec`, `compile` or `__import__` anywhere in the package.

    A source scan, and it is honest about being one: the behavioural half is
    the test above, which proves a name is refused rather than resolved. This
    one exists to catch an evaluator added later for a different reason, in a
    place the test above would not reach.
    """
    forbidden = {"eval", "exec", "compile", "__import__", "literal_eval"}
    offences: list[str] = []
    for path in sorted(CALC_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                offences.append(f"{path.name}:{node.lineno} {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offences.append(f"{path.name}:{node.lineno} .{node.attr}")
    assert not offences, "the calc package must contain no evaluator:\n" + "\n".join(
        offences
    )


# ─── No floats ──────────────────────────────────────────────────────────────


def test_no_float_appears_anywhere_in_the_calc_package():
    """The source scan half of CLAUDE.md rule 1 for this package.

    A float literal or a `float(...)` call in here would make one number in
    one path binary, and the symptom would be a cent that moves depending on
    the input rather than an error. The behavioural half is the test below.
    """
    offences: list[str] = []
    for path in sorted(CALC_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offences.append(f"{path.name}:{node.lineno} literal {node.value!r}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            ):
                offences.append(f"{path.name}:{node.lineno} float(...) call")
    assert not offences, (
        "money is Decimal, never float (CLAUDE.md rule 1):\n" + "\n".join(offences)
    )


def test_the_float_scan_would_notice_one():
    """Prove the scan can fail — it is a scan, so it needs its own guard."""
    source = "rate = 0.2\nvalue = float('1')\n"
    tree = ast.parse(source)
    floats = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    ]
    assert len(floats) == 1 and len(calls) == 1, (
        "the scan above cannot see a float literal or a float() call, so its "
        "silence means nothing"
    )


def test_every_number_in_a_result_is_a_decimal():
    """The behavioural half: walk a real result and check every type.

    Written over the dataclass fields rather than a hand-listed set of
    attributes, so a field added later is covered the day it arrives rather
    than the day somebody remembers this test.
    """
    import dataclasses

    data = json.loads((FIXTURES / "s1-quote.json").read_text())
    result = calculate(document_from_data(data["document"]))
    vat = apply_vat(result, {"AT-20": Decimal("20")})

    seen = 0

    def check(node, where: str) -> None:
        nonlocal seen
        if isinstance(node, bool) or node is None or isinstance(node, str):
            return
        if isinstance(node, float):
            raise AssertionError(f"{where} is a float: {node!r}")
        if isinstance(node, Decimal):
            seen += 1
            return
        if isinstance(node, int):
            raise AssertionError(f"{where} is a plain int: {node!r}")
        if dataclasses.is_dataclass(node):
            for field in dataclasses.fields(node):
                check(getattr(node, field.name), f"{where}.{field.name}")
            return
        if isinstance(node, dict):
            for key, value in node.items():
                check(value, f"{where}[{key!r}]")
            return
        if isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                check(value, f"{where}[{index}]")
            return

    check(result, "result")
    check(vat, "vat")
    assert seen > 50, f"only {seen} numbers were checked; the walk is not reaching them"


def test_every_money_amount_in_a_result_carries_two_decimal_places():
    """Rounded, and rounded to the right scale.

    `Decimal("12517.2")` and `Decimal("12517.20")` compare equal, so the
    fixtures cannot tell them apart — and a value that reaches the API with
    one decimal place serialises as `"12517.2"`, which `core/money`'s
    docstring calls a bug report waiting to be filed.
    """
    data = json.loads((FIXTURES / "s1-quote.json").read_text())
    result = calculate(document_from_data(data["document"]))

    for name, value in (
        ("subtotal", result.subtotal),
        ("net", result.net),
        ("cost", result.cost),
        ("margin", result.margin),
    ):
        assert value.as_tuple().exponent == -2, f"{name} is {value}, not 2 dp"
    for line_id, entry in result.lines.items():
        for name in ("net", "cost_net", "gross", "discount_amount"):
            value = getattr(entry, name)
            assert value.as_tuple().exponent == -2, (
                f"line {line_id} {name} is {value}, not 2 dp"
            )


# ─── The ambient decimal context ────────────────────────────────────────────


def test_a_hostile_global_decimal_context_changes_nothing():
    """S1 under `prec=5, ROUND_DOWN`. Still 12 517.20.

    Python's decimal context is global, mutable and thread-local, so any
    library in the process can change it. `core/rounding.exact_arithmetic`
    installs FilmBill's own for the length of `calculate` and `apply_vat`,
    which is what makes this test pass — without it, `prec=5` truncates
    every addition to five significant digits and the net comes out 12 517.
    """
    data = json.loads((FIXTURES / "s1-quote.json").read_text())
    document = document_from_data(data["document"])

    before = decimal.getcontext()
    try:
        decimal.setcontext(Context(prec=5, rounding=ROUND_DOWN))
        result = calculate(document)
        vat = apply_vat(result, {"AT-20": Decimal("20")})
    finally:
        decimal.setcontext(before)

    assert result.net == Decimal("12517.20")
    assert vat.vat == Decimal("2503.44")
    assert vat.gross == Decimal("15020.64")


def test_the_engine_leaves_the_global_context_as_it_found_it():
    """The block restores what it replaced; a caller's context is theirs."""
    marker = Context(prec=11, rounding=ROUND_DOWN)
    before = decimal.getcontext()
    try:
        decimal.setcontext(marker)
        calculate(doc(line(id="l1")))
        after = decimal.getcontext()
        assert after.prec == 11 and after.rounding == ROUND_DOWN
    finally:
        decimal.setcontext(before)


# ─── §5.4's uniform-days rule ───────────────────────────────────────────────


def test_a_uniform_group_may_show_days():
    group = Group(
        id="camera",
        title="Camera",
        children=(
            line(id="alexa", unit_price=Decimal("1200"), period_value=Decimal("2")),
            line(id="lenses", unit_price=Decimal("300"), period_value=Decimal("2")),
        ),
        adjustments=(
            Adjustment(
                label="Group discount", type="discount", mode="percent",
                value=Decimal("10"),
            ),
        ),
    )
    row = display_row_for_group(group)
    assert row.uniform_period is True
    assert (row.quantity, row.period_value, row.period_unit) == (
        Decimal("1"), Decimal("2"), "day",
    )
    assert row.price_per_period == Decimal("1350.00")
    assert row.total == Decimal("2700.00")
    assert row.exact is True
    assert row.warnings == ()


def test_a_mixed_group_shows_one_times_the_total_and_warns():
    group = Group(
        id="camera",
        title="Camera",
        children=(
            line(id="alexa", unit_price=Decimal("1200"), period_value=Decimal("2")),
            line(id="drone", unit_price=Decimal("300"), period_value=Decimal("1")),
        ),
    )
    row = display_row_for_group(group)
    assert row.uniform_period is False
    assert row.period_value is None and row.period_unit is None
    assert row.price_per_period == row.total == Decimal("2700.00")
    assert [warning.code for warning in row.warnings] == ["mixed_days_in_group"]
    assert "different numbers of days" in row.warnings[0].message


def test_the_same_number_of_different_units_is_not_uniform():
    """1 day and 1 flat share a number and are not the same row.

    The rule compares the PAIR. Comparing `period_value` alone would call
    this group uniform and print "1 × 1 day × € 400" for a group that is a
    day of crew and a flat taxi fare.
    """
    group = Group(
        id="g",
        title="Mixed units",
        children=(
            line(id="crew", unit_price=Decimal("300"), period_unit="day"),
            line(id="taxi", unit_price=Decimal("100"), period_unit="flat"),
        ),
    )
    row = display_row_for_group(group)
    assert row.uniform_period is False
    assert row.period_unit is None


def test_a_collapsed_price_that_does_not_multiply_back_says_so():
    group = Group(
        id="kit",
        title="Kit",
        children=(line(id="k1", unit_price=Decimal("50"), period_value=Decimal("3")),),
        adjustments=(
            Adjustment(
                label="Kit allowance", type="discount", mode="amount",
                value=Decimal("50"),
            ),
        ),
    )
    row = display_row_for_group(group)
    assert row.total == Decimal("100.00")
    assert row.price_per_period == Decimal("33.33")
    assert row.exact is False
    assert [warning.code for warning in row.warnings] == ["collapsed_price_not_exact"]


def test_an_empty_group_has_a_row_and_no_days():
    row = display_row_for_group(Group(id="empty", title="Nothing yet"))
    assert row.total == Decimal("0")
    assert row.period_value is None
    assert row.warnings == ()


def test_display_row_refuses_a_result_for_a_different_group():
    """A mismatched pair would silently render one group's numbers on another."""
    a = Group(id="a", children=(line(id="l1"),))
    b = Group(id="b", children=(line(id="l2"),))
    result = calculate(doc(a, b))
    with pytest.raises(CalcError, match="'b'"):
        display_row_for_group(a, result.children[1])


def test_a_mixed_group_also_warns_from_calculate():
    """The warning is on the result, not only on the display row.

    The editor warning §5.4 describes appears while somebody is building the
    group, before anything asks for a collapsed row. If the only way to get
    it were `display_row_for_group`, the editor would be silent.
    """
    group = Group(
        id="camera",
        title="Camera",
        children=(
            line(id="alexa", unit_price=Decimal("1200"), period_value=Decimal("2")),
            line(id="drone", unit_price=Decimal("300"), period_value=Decimal("1")),
        ),
    )
    result = calculate(doc(group))
    assert [warning.code for warning in result.warnings] == ["mixed_days_in_group"]
    assert result.warnings[0].where == "group Camera"


# ─── Parsing ────────────────────────────────────────────────────────────────


def test_an_unknown_field_is_refused_rather_than_ignored():
    """A typo in a field name must not be a field that silently did nothing.

    `unitprice` accepted and ignored would price the line at 0.00 and say
    nothing, which is the shape of every "the quote came out wrong and
    nobody knows why" report.
    """
    with pytest.raises(CalcError, match="unitprice"):
        document_from_data({"children": [{"id": "l1", "unitprice": "100"}]})
    with pytest.raises(CalcError, match="discount_percent"):
        document_from_data(
            {"children": [{"id": "l1", "unit_price": "100", "discount_percent": "5"}]}
        )


def test_a_child_says_whether_it_is_a_group_and_an_empty_group_survives():
    """An empty group is a real thing in the editor and must round-trip."""
    document = document_from_data(
        {
            "children": [
                {"type": "group", "id": "g1", "title": "Empty"},
                {"type": "line", "id": "l1", "unit_price": "100"},
            ]
        }
    )
    assert isinstance(document.children[0], Group)
    assert isinstance(document.children[1], Line)
    assert calculate(document).net == Decimal("100.00")


def test_a_line_without_an_id_is_refused():
    with pytest.raises(CalcError, match="non-empty id"):
        document_from_data({"children": [{"unit_price": "100"}]})


def test_an_integer_is_accepted_and_a_float_is_not():
    """An integer survives a JSON round trip exactly; a float does not."""
    document = document_from_data(
        {"children": [{"id": "l1", "unit_price": 100, "quantity": 2}]}
    )
    assert calculate(document).net == Decimal("200.00")
    with pytest.raises(CalcError, match="not a JSON number"):
        document_from_data({"children": [{"id": "l1", "unit_price": 100.5}]})


def test_kind_and_tax_ref_are_opaque_to_the_engine():
    """Neither is validated against a vocabulary, and that is the point.

    `kind` belongs to the catalog and `tax_ref` to the tax module; both can
    only be checked against a company's own rows, which the engine has no
    access to by design. A closed list here would mean the engine had to be
    changed to add a tax code.
    """
    document = document_from_data(
        {
            "children": [
                {
                    "id": "l1",
                    "unit_price": "100",
                    "kind": "something_invented_in_2029",
                    "tax_ref": "XX-7.5-whatever",
                }
            ]
        }
    )
    result = calculate(document)
    assert result.net_by_tax_ref == {"XX-7.5-whatever": Decimal("100.00")}
    assert apply_vat(result, {"XX-7.5-whatever": Decimal("7.5")}).vat == Decimal("7.50")


def test_a_line_with_no_tax_ref_groups_under_the_empty_category():
    """What a budget looks like: no tax anywhere, and the core path is enough."""
    result = calculate(doc(line(id="l1")))
    assert result.net_by_tax_ref == {"": Decimal("100.00")}
    assert result.net == Decimal("100.00")
