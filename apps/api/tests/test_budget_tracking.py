"""The cost-tracking module, run against its golden fixtures.

Every file in `tests/fixtures/tracking/*.json` is a scenario: the entries a
budget carries, and the cents the forecast must produce. Data files rather
than Python for `fixtures/calc/README.md`'s reason — a forecasting rule as an
expression is reviewable by a programmer, the same rule as a table of inputs
and expected cents is reviewable by Mathias, and the default `remaining` rule
needed exactly that review (SCOPE D83; decided by D89 at B1.1).

**What keeps this from being a check satisfied by its own absence**
(CLAUDE.md 17b): four guards, each on a different way the walk could go quiet.

  * `test_the_fixture_directory_is_not_empty` — a glob that matched nothing
    would collect zero parametrised cases and report green.
  * `test_every_fixture_names_the_numbers_it_is_about` — a file with an empty
    `expect` would be collected, assert nothing, and pass.
  * `test_the_comparison_notices_a_wrong_number` — the comparison helper is
    run against a deliberately wrong expectation and has to fail. Without it
    a typo in `_assert_amount` would make every scenario pass.
  * `test_the_scenarios_the_prompt_asked_for_are_all_here` — the eleven
    scenarios the B1 prompt enumerated, by file name, so a deleted fixture is
    a failure rather than a silently smaller suite.

The scenarios also cover the two things this module exists to get right, and
they get their own named tests on top of the data-driven walk because a
reader looking for them should not have to open a JSON file:
`exposure = actual + committed_open` (never `committed_gross`) and the zero
floor on an over-invoiced commitment.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from apps.api.billing.budget.tracking import (
    REMAINING_RULES,
    RULE_DEFAULT,
    RULE_ETC,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_OPEN,
    Actual,
    BudgetInput,
    Commitment,
    LineInput,
    SectionInput,
    closes_commitment,
    committed_open_of,
    track_budget,
    track_line,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tracking"

#: The scenarios the B1 prompt enumerated, (a) to (k). Listed by file name so
#: that renaming or deleting one is a failure and not a quieter suite.
EXPECTED_FILES: tuple[str, ...] = (
    "a-estimate-only.json",
    "b-part-spent.json",
    "c-overspent.json",
    "d-open-commitment.json",
    "e-commitment-part-invoiced.json",
    "f-commitment-settled.json",
    "g-commitment-over-invoiced.json",
    "h-etc-override.json",
    "i-voided-entries.json",
    "j-unallocated-entries.json",
    "s1-example.json",
)

#: Every amount field a line result carries, so a scenario cannot assert four
#: of them and leave the rest unchecked by accident.
LINE_AMOUNTS: tuple[str, ...] = (
    "estimate_cost",
    "committed_gross",
    "committed_open",
    "actual",
    "exposure",
    "remaining",
    "efc",
    "variance",
    "variance_percent",
)
ROLLUP_AMOUNTS: tuple[str, ...] = (
    "estimate_cost",
    "committed_gross",
    "committed_open",
    "actual",
    "exposure",
    "remaining",
    "efc",
    "variance",
    "variance_percent",
    "unallocated",
    "unallocated_actual",
    "unallocated_committed_open",
    "price",
    "forecast_margin",
    "forecast_margin_percent",
    "estimate_margin",
    "estimate_margin_percent",
)


def _files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _decimal(raw: Any) -> Optional[Decimal]:
    return None if raw is None else Decimal(str(raw))


def _commitment(data: Mapping[str, Any]) -> Commitment:
    return Commitment(
        amount=Decimal(str(data["amount"])),
        id=data.get("id", ""),
        status=data.get("status", STATUS_OPEN),
        voided=bool(data.get("voided", False)),
    )


def _actual(data: Mapping[str, Any]) -> Actual:
    return Actual(
        amount=Decimal(str(data["amount"])),
        id=data.get("id", ""),
        voided=bool(data.get("voided", False)),
        commitment_id=data.get("commitment_id"),
    )


def _line(data: Mapping[str, Any]) -> LineInput:
    return LineInput(
        estimate_cost=Decimal(str(data["estimate_cost"])),
        id=data.get("id", ""),
        commitments=tuple(_commitment(row) for row in data.get("commitments", ())),
        actuals=tuple(_actual(row) for row in data.get("actuals", ())),
        etc=_decimal(data.get("etc")),
    )


def _section(data: Mapping[str, Any]) -> SectionInput:
    return SectionInput(
        id=data.get("id", ""),
        lines=tuple(_line(row) for row in data.get("lines", ())),
        children=tuple(_section(row) for row in data.get("children", ())),
        commitments=tuple(_commitment(row) for row in data.get("commitments", ())),
        actuals=tuple(_actual(row) for row in data.get("actuals", ())),
        price=_decimal(data.get("price")),
    )


def _budget(data: Mapping[str, Any]) -> BudgetInput:
    return BudgetInput(
        sections=tuple(_section(row) for row in data.get("sections", ())),
        id=data.get("id", ""),
        price=_decimal(data.get("price")),
    )


def _assert_amount(actual: Any, expected: Any, *, what: str) -> None:
    """One number, compared as a `Decimal` and reported in full.

    `None` is asserted as the absence of a number rather than as zero: a
    `variance_percent` on a zero estimate is undefined, and 0 would be a
    claim.
    """
    if expected is None:
        assert actual is None, f"{what}: expected no value, got {actual!r}"
        return
    assert actual is not None, f"{what}: expected {expected}, got None"
    assert Decimal(actual) == Decimal(str(expected)), (
        f"{what}: expected {expected}, got {actual}"
    )


def _assert_fields(
    result: Any, expected: Mapping[str, Any], fields: tuple[str, ...], *, what: str
) -> None:
    for name in fields:
        if name not in expected:
            continue
        _assert_amount(getattr(result, name), expected[name], what=f"{what}.{name}")


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.stem)
def test_fixture(path: Path) -> None:
    """One scenario: every number in `expect`, against the real module."""
    data = _load(path)
    result = track_budget(_budget(data["budget"]))
    expect = data["expect"]

    for line_id, expected in expect.get("lines", {}).items():
        assert line_id in result.lines, (
            f"{path.name}: no line result for {line_id!r}; "
            f"got {sorted(result.lines)}"
        )
        line = result.lines[line_id]
        _assert_fields(line, expected, LINE_AMOUNTS, what=f"line {line_id}")
        if "remaining_rule" in expected:
            assert line.remaining_rule == expected["remaining_rule"], (
                f"line {line_id}.remaining_rule: expected "
                f"{expected['remaining_rule']}, got {line.remaining_rule}"
            )
        if "over_budget" in expected:
            assert line.over_budget is expected["over_budget"], (
                f"line {line_id}.over_budget: expected "
                f"{expected['over_budget']}, got {line.over_budget}"
            )
        if "etc" in expected:
            _assert_amount(line.etc, expected["etc"], what=f"line {line_id}.etc")

    for section_id, expected in expect.get("sections", {}).items():
        assert section_id in result.sections, (
            f"{path.name}: no section result for {section_id!r}; "
            f"got {sorted(result.sections)}"
        )
        rollup = result.sections[section_id]
        _assert_fields(rollup, expected, ROLLUP_AMOUNTS, what=f"section {section_id}")
        if "over_budget" in expected:
            assert rollup.over_budget is expected["over_budget"], (
                f"section {section_id}.over_budget: expected "
                f"{expected['over_budget']}, got {rollup.over_budget}"
            )

    if "budget" in expect:
        _assert_fields(
            result.budget, expect["budget"], ROLLUP_AMOUNTS, what="budget"
        )
        if "over_budget" in expect["budget"]:
            assert result.budget.over_budget is expect["budget"]["over_budget"]

    for commitment_id, settled in (data.get("closes_commitment") or {}).items():
        found = _find_commitment(data["budget"], commitment_id)
        assert found is not None, (
            f"{path.name}: closes_commitment names {commitment_id!r}, which "
            f"is not in the scenario's input"
        )
        commitment, actuals = found
        assert closes_commitment(commitment, actuals) is settled, (
            f"{path.name}: closes_commitment({commitment_id}) should be "
            f"{settled}"
        )


def _find_commitment(data: Mapping[str, Any], commitment_id: str):
    """`(commitment, the actuals it could be settled by)` or `None`.

    The actuals that matter are the ones on the same line (or, for an
    unallocated commitment, the same section) — exactly the set the service
    passes when it decides whether to close a commitment.
    """
    def search(holder: Mapping[str, Any]):
        for row in holder.get("commitments", ()):
            if row.get("id") == commitment_id:
                return (
                    _commitment(row),
                    tuple(_actual(entry) for entry in holder.get("actuals", ())),
                )
        return None

    for section in data.get("sections", ()):
        found = search(section)
        if found is not None:
            return found
        for line in section.get("lines", ()):
            found = search(line)
            if found is not None:
                return found
        for child in section.get("children", ()):
            found = _find_commitment({"sections": [child]}, commitment_id)
            if found is not None:
                return found
    return None


# ─── The guards on the walk above ───────────────────────────────────────────


def test_the_fixture_directory_is_not_empty() -> None:
    """A glob that matched nothing would collect zero cases and pass."""
    files = _files()
    assert len(files) >= len(EXPECTED_FILES), (
        f"only found {[path.name for path in files]} under {FIXTURES}"
    )


def test_the_scenarios_the_prompt_asked_for_are_all_here() -> None:
    """(a) to (k), by name. A renamed fixture is a failure, not a gap."""
    present = {path.name for path in _files()}
    missing = [name for name in EXPECTED_FILES if name not in present]
    assert not missing, f"these scenarios are gone: {missing}"


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.stem)
def test_every_fixture_names_the_numbers_it_is_about(path: Path) -> None:
    """A file with an empty `expect` would assert nothing and pass."""
    data = _load(path)
    assert data.get("why", "").strip(), f"{path.name} has no `why`"
    expect = data.get("expect") or {}
    assert expect.get("lines") or expect.get("sections") or expect.get("budget"), (
        f"{path.name} expects nothing"
    )
    counted = sum(
        len(value)
        for key in ("lines", "sections")
        for value in [expect.get(key) or {}]
    ) + (1 if expect.get("budget") else 0)
    assert counted >= 2, (
        f"{path.name} asserts only {counted} result(s); a scenario states "
        f"its line AND the totals it rolls up into"
    )


def test_the_comparison_notices_a_wrong_number() -> None:
    """The helper, run against a deliberately wrong expectation.

    Without this, a typo in `_assert_amount` — comparing `str` to `Decimal`,
    say, or swallowing the assertion — would make every scenario above pass
    no matter what the module computed.
    """
    with pytest.raises(AssertionError):
        _assert_amount(Decimal("1000.00"), "999.99", what="probe")
    with pytest.raises(AssertionError):
        _assert_amount(None, "0.00", what="probe")
    with pytest.raises(AssertionError):
        _assert_amount(Decimal("0.00"), None, what="probe")
    # And it must PASS on an equal value written at a different scale, or
    # every fixture would have to guess the scale a sum comes out at.
    _assert_amount(Decimal("1000.0000"), "1000.00", what="probe")


def test_the_fixture_loader_builds_what_the_file_says() -> None:
    """The loader's own arithmetic, checked.

    A loader that dropped `voided` or `commitment_id` would turn scenarios
    (e) and (i) into scenarios about nothing, and they would still pass —
    the numbers would simply be the ones the module computes for the input
    it was actually given.
    """
    section = _section(
        {
            "id": "s",
            "price": "100.00",
            "lines": [
                {
                    "id": "l",
                    "estimate_cost": "10.00",
                    "etc": "3.00",
                    "commitments": [
                        {"id": "c", "amount": "5.00", "status": "cancelled"}
                    ],
                    "actuals": [
                        {
                            "id": "a",
                            "amount": "2.00",
                            "commitment_id": "c",
                            "voided": True,
                        }
                    ],
                }
            ],
        }
    )
    assert section.price == Decimal("100.00")
    line = section.lines[0]
    assert line.etc == Decimal("3.00")
    assert line.commitments[0].status == STATUS_CANCELLED
    assert line.commitments[0].voided is False
    assert line.actuals[0].voided is True
    assert line.actuals[0].commitment_id == "c"


# ─── The two rules this module exists to get right ──────────────────────────


def test_exposure_is_actual_plus_open_and_never_actual_plus_gross() -> None:
    """A commitment that has been invoiced must not count twice.

    Named rather than left to fixture (e) alone, because this is the single
    assertion the whole module is about: an order of 1 000 with 600 invoiced
    is 1 000 of exposure, not 1 600.
    """
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("2000.00"),
            commitments=(Commitment(id="c", amount=Decimal("1000.00")),),
            actuals=(Actual(id="a", amount=Decimal("600.00"), commitment_id="c"),),
        )
    )
    assert line.actual == Decimal("600.00")
    assert line.committed_gross == Decimal("1000.00")
    assert line.committed_open == Decimal("400.00")
    assert line.exposure == Decimal("1000.00")
    assert line.exposure == line.actual + line.committed_open
    assert line.exposure != line.actual + line.committed_gross


def test_an_unlinked_actual_does_not_settle_any_commitment() -> None:
    """An invoice with no commitment of its own leaves the order fully open.

    The difference between "600 was invoiced against this order" and "600 was
    spent on this line and the order is still outstanding" — which is a real
    1 600 of exposure and the one case where the two spellings of the rule
    agree in direction.
    """
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("2000.00"),
            commitments=(Commitment(id="c", amount=Decimal("1000.00")),),
            actuals=(Actual(id="a", amount=Decimal("600.00")),),
        )
    )
    assert line.committed_open == Decimal("1000.00")
    assert line.exposure == Decimal("1600.00")


def test_an_over_invoiced_commitment_has_no_negative_open_amount() -> None:
    """The floor, on its own and across two commitments.

    One commitment over-invoiced by 50 beside another of 300 that is
    untouched: the open total is 300, not 250. A negative open amount would
    net off against its neighbour and understate the section.
    """
    assert committed_open_of(
        (
            Commitment(id="c1", amount=Decimal("700.00")),
            Commitment(id="c2", amount=Decimal("300.00")),
        ),
        (Actual(id="a", amount=Decimal("750.00"), commitment_id="c1"),),
    ) == Decimal("300.00")


def test_a_closed_commitment_is_in_gross_and_not_in_open() -> None:
    """Settled, so nothing is owed — but it was still ordered."""
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("1000.00"),
            commitments=(
                Commitment(id="c", amount=Decimal("400.00"), status=STATUS_CLOSED),
            ),
            actuals=(Actual(id="a", amount=Decimal("400.00"), commitment_id="c"),),
        )
    )
    assert line.committed_gross == Decimal("400.00")
    assert line.committed_open == Decimal("0.00")


def test_a_cancelled_commitment_is_in_neither() -> None:
    """Withdrawn, so it was never ordered and nothing is owed.

    The reason `cancelled` is a status and not `voided`: both leave
    `committed_open` at zero, and only one of them leaves `committed_gross`
    at zero too.
    """
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("1000.00"),
            commitments=(
                Commitment(id="c", amount=Decimal("400.00"), status=STATUS_CANCELLED),
            ),
        )
    )
    assert line.committed_gross == Decimal("0.00")
    assert line.committed_open == Decimal("0.00")
    assert line.exposure == Decimal("0.00")


def test_a_zero_estimate_gives_no_variance_percent() -> None:
    """`None`, not zero and not a `ZeroDivisionError`.

    The Alexa line of the S1 example is this case — equipment owned outright,
    estimate cost 0.00, and a 1 000 order against it.
    """
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("0.00"),
            commitments=(Commitment(id="c", amount=Decimal("1000.00")),),
        )
    )
    assert line.variance == Decimal("-1000.00")
    assert line.variance_percent is None
    assert line.over_budget is True


def test_an_etc_of_zero_is_not_the_absence_of_an_etc() -> None:
    """`Decimal("0")` means "nothing more to spend"; `None` means "guess".

    The `if line.etc is not None` that makes this true is the kind of check
    a refactor turns into a truthiness test, and the difference only shows up
    on the one line somebody has declared finished.
    """
    with_zero = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("1000.00"),
            actuals=(Actual(id="a", amount=Decimal("400.00")),),
            etc=Decimal("0"),
        )
    )
    assert with_zero.remaining == Decimal("0.00")
    assert with_zero.remaining_rule == RULE_ETC
    assert with_zero.efc == Decimal("400.00")

    without = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("1000.00"),
            actuals=(Actual(id="a", amount=Decimal("400.00")),),
        )
    )
    assert without.remaining == Decimal("600.00")
    assert without.remaining_rule == RULE_DEFAULT


def test_every_line_reports_which_rule_produced_its_remaining() -> None:
    """`remaining_rule` is always one of the two, never empty."""
    result = track_budget(_budget(_load(FIXTURES / "s1-example.json")["budget"]))
    assert result.lines
    for line_id, line in result.lines.items():
        assert line.remaining_rule in REMAINING_RULES, (
            f"{line_id} reports rule {line.remaining_rule!r}"
        )


def test_efc_equals_actual_plus_remaining_at_every_level() -> None:
    """The identity, asserted on every fixture.

    It is what makes the five phase columns add up on screen: a section whose
    EFC was not its own actual plus its own remaining would show a sheet
    whose rows did not sum to its total, and the first person to notice would
    be the one presenting it to a client.
    """
    for path in _files():
        result = track_budget(_budget(_load(path)["budget"]))
        for line_id, line in result.lines.items():
            assert line.efc == line.actual + line.remaining, (
                f"{path.name}: line {line_id} EFC {line.efc} != "
                f"{line.actual} + {line.remaining}"
            )
        for section_id, rollup in result.sections.items():
            assert rollup.efc == rollup.actual + rollup.remaining, (
                f"{path.name}: section {section_id} EFC {rollup.efc} != "
                f"{rollup.actual} + {rollup.remaining}"
            )
        assert result.budget.efc == result.budget.actual + result.budget.remaining, (
            f"{path.name}: budget EFC does not equal actual + remaining"
        )
        assert result.budget.exposure == (
            result.budget.actual + result.budget.committed_open
        ), f"{path.name}: budget exposure is not actual + committed_open"


def test_a_sub_section_rolls_up_into_its_parent() -> None:
    """Two levels, which is as deep as a budget nests (SCOPE §5.4).

    The parent's totals contain the child's, and the child keeps its own row
    in `sections` — the sheet draws both.
    """
    result = track_budget(
        BudgetInput(
            id="b",
            price=Decimal("1000.00"),
            sections=(
                SectionInput(
                    id="parent",
                    price=Decimal("1000.00"),
                    lines=(
                        LineInput(
                            id="own",
                            estimate_cost=Decimal("100.00"),
                            actuals=(Actual(id="a1", amount=Decimal("40.00")),),
                        ),
                    ),
                    children=(
                        SectionInput(
                            id="child",
                            lines=(
                                LineInput(
                                    id="nested",
                                    estimate_cost=Decimal("200.00"),
                                    actuals=(
                                        Actual(id="a2", amount=Decimal("250.00")),
                                    ),
                                ),
                            ),
                            actuals=(Actual(id="a3", amount=Decimal("10.00")),),
                        ),
                    ),
                ),
            ),
        )
    )
    child = result.sections["child"]
    parent = result.sections["parent"]
    assert child.estimate_cost == Decimal("200.00")
    assert child.actual == Decimal("260.00")
    assert child.unallocated == Decimal("10.00")
    assert parent.estimate_cost == Decimal("300.00")
    assert parent.actual == Decimal("300.00")
    # The child's unallocated 10 is the parent's too — a sub-section's
    # unbudgeted cost is unbudgeted in the section above it as well.
    assert parent.unallocated == Decimal("10.00")
    assert result.budget.estimate_cost == Decimal("300.00")
    assert result.budget.forecast_margin == Decimal("1000.00") - result.budget.efc


def test_the_forecast_margin_uses_the_estimate_price_and_not_the_cost() -> None:
    """Price in, price out. Costs move the margin; they never move the price.

    Two budgets with the same price and different costs: the price side of
    both forecast margins is identical, and the difference is entirely the
    EFC. A margin that re-derived the price from costs would make a
    cost overrun invisible, which is the one thing this number is for.
    """
    def budget_with(cost: str, actual: str):
        return BudgetInput(
            id="b",
            price=Decimal("1000.00"),
            sections=(
                SectionInput(
                    id="s",
                    price=Decimal("1000.00"),
                    lines=(
                        LineInput(
                            id="l",
                            estimate_cost=Decimal(cost),
                            actuals=(Actual(id="a", amount=Decimal(actual)),),
                        ),
                    ),
                ),
            ),
        )

    lean = track_budget(budget_with("400.00", "400.00")).budget
    blown = track_budget(budget_with("400.00", "900.00")).budget
    assert lean.price == blown.price == Decimal("1000.00")
    assert lean.estimate_margin == blown.estimate_margin == Decimal("600.00")
    assert lean.forecast_margin == Decimal("600.00")
    assert blown.forecast_margin == Decimal("100.00")
    assert blown.forecast_margin_percent == Decimal("10.00")


def test_a_budget_with_no_price_reports_no_margin() -> None:
    """`None`, not zero: a margin against a price nobody gave is a claim."""
    result = track_budget(
        BudgetInput(
            id="b",
            sections=(
                SectionInput(id="s", lines=(LineInput(id="l", estimate_cost=Decimal("5")),)),
            ),
        )
    )
    assert result.budget.price is None
    assert result.budget.forecast_margin is None
    assert result.budget.forecast_margin_percent is None
    assert result.sections["s"].forecast_margin is None


def test_sums_come_back_at_two_decimal_places() -> None:
    """The only rounding in this module, and it has to happen.

    Entries are already 2 dp, so nothing here can produce a third place —
    but a sum of Decimals carries the scale of its widest term, and a
    screen that formatted `1200.0000` beside `1200.00` is a screen nobody
    trusts.
    """
    line = track_line(
        LineInput(
            id="l",
            estimate_cost=Decimal("1200.0000"),
            actuals=(Actual(id="a", amount=Decimal("400.0000")),),
        )
    )
    for name in ("estimate_cost", "actual", "remaining", "efc", "variance"):
        value = getattr(line, name)
        assert value == value.quantize(Decimal("0.01")), f"{name} is {value!r}"
        assert -value.as_tuple().exponent == 2, f"{name} is {value!r}"
