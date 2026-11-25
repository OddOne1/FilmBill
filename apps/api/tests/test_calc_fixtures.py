"""The golden fixtures, run against the real engine.

Every file in `tests/fixtures/calc/*.json` is a scenario: the document the
engine is given, and the cents it must produce. The scenarios are data files
rather than Python because a rounding rule written as an expression is
reviewable by a programmer, and the same rule as a table of inputs and
expected cents is reviewable by Mathias and by a Steuerberater — which is the
only review that settles whether S1 is right.

**What keeps this from being a check satisfied by its own absence**
(CLAUDE.md 17b): three guards, each on a different way the walk could go
quiet.

  * `test_the_fixture_directory_is_not_empty` — a glob that matched nothing
    would collect zero parametrised cases and the file would report green.
  * `test_every_fixture_names_the_numbers_it_is_about` — a file with an empty
    `expect` would be collected, asserted nothing, and pass.
  * `test_the_comparison_notices_a_wrong_number` — the comparison helper is
    run against a deliberately wrong expectation and has to fail. Without it,
    a typo in `_assert_amounts` would make every scenario pass.

Scenario files are also listed by name in
`test_the_scenarios_the_prompt_asked_for_are_all_here`, so a fixture that is
deleted or renamed is a failure rather than a silently smaller suite.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from apps.api.billing.calc import (
    CalcError,
    apply_vat,
    calculate,
    display_row_for_group,
    document_from_data,
)

FIXTURES = Path(__file__).parent / "fixtures" / "calc"


def _files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _groups_by_id(nodes, found: Optional[dict] = None) -> dict:
    """Every `GroupResult` in a result tree, keyed by id."""
    found = {} if found is None else found
    for node in nodes:
        if hasattr(node, "children"):
            found[node.id] = node
            _groups_by_id(node.children, found)
    return found


def _group_inputs_by_id(nodes, found: Optional[dict] = None) -> dict:
    """Every input `Group`, keyed by id — for `display_row_for_group`."""
    found = {} if found is None else found
    for node in nodes:
        if hasattr(node, "children"):
            found[node.id] = node
            _group_inputs_by_id(node.children, found)
    return found


def _amount(expected: Any) -> Optional[Decimal]:
    return None if expected is None else Decimal(str(expected))


def _assert_amount(actual: Any, expected: Any, *, what: str) -> None:
    """One number, compared as a `Decimal` and reported in full.

    `Decimal("2700.00") == Decimal("2700")` is True, which is what we want:
    the fixtures are about VALUE, and the scale a `Decimal` happens to carry
    out of a multiplication is `quantize_amount`'s business and is asserted
    separately in `test_calc_engine.py`.
    """
    if expected is None:
        assert actual is None, f"{what}: expected no value, got {actual!r}"
        return
    assert actual is not None, f"{what}: expected {expected}, got None"
    assert Decimal(actual) == Decimal(str(expected)), (
        f"{what}: expected {expected}, got {actual}"
    )


def _assert_adjustments(actual, expected: list, *, where: str) -> None:
    assert len(actual) == len(expected), (
        f"{where}: expected {len(expected)} adjustment(s), got "
        f"{[a.label for a in actual]}"
    )
    for result, want in zip(actual, expected):
        assert result.label == want["label"], (
            f"{where}: adjustment order — expected {want['label']!r}, got "
            f"{result.label!r}"
        )
        for field in ("base_amount", "amount"):
            if field in want:
                _assert_amount(
                    getattr(result, field),
                    want[field],
                    what=f"{where} adjustment {result.label!r} {field}",
                )
        if "by_tax_ref" in want:
            got = {ref: str(value) for ref, value in result.by_tax_ref.items()}
            assert set(got) == set(want["by_tax_ref"]), (
                f"{where} adjustment {result.label!r}: allocated to "
                f"{sorted(got)}, expected {sorted(want['by_tax_ref'])}"
            )
            for ref, value in want["by_tax_ref"].items():
                _assert_amount(
                    result.by_tax_ref[ref],
                    value,
                    what=f"{where} adjustment {result.label!r} share of {ref}",
                )
            # The property the whole allocation exists for.
            assert sum(result.by_tax_ref.values()) == result.amount, (
                f"{where} adjustment {result.label!r}: its per-category "
                f"shares sum to {sum(result.by_tax_ref.values())} but the "
                f"adjustment is {result.amount}"
            )


def _assert_warnings(actual, expected: list, *, where: str) -> None:
    got = [(warning.code, warning.where) for warning in actual]
    want = [(entry["code"], entry.get("where")) for entry in expected]
    assert got == want, f"{where}: warnings {got}, expected {want}"
    for warning in actual:
        assert warning.message, (
            f"{where}: warning {warning.code!r} carries no sentence, and a "
            f"client that does not know the code would then show a silent "
            f"state (CLAUDE.md 17c)"
        )


def _check(path: Path) -> int:
    """Run one scenario. Returns how many numbers it compared.

    The count is returned so `test_every_fixture_names_the_numbers_it_is
    _about` can prove the file asserted something, rather than trusting that
    a scenario with an empty `expect` would have failed somewhere.
    """
    data = _load(path)
    document = document_from_data(data["document"])
    result = calculate(document)
    expect = data["expect"]
    compared = 0

    for field in ("subtotal", "net", "cost", "margin", "margin_percent"):
        if field in expect:
            _assert_amount(
                getattr(result, field), expect[field], what=f"{path.name} {field}"
            )
            compared += 1

    if "net_by_tax_ref" in expect:
        got = dict(result.net_by_tax_ref)
        assert set(got) == set(expect["net_by_tax_ref"]), (
            f"{path.name}: tax categories {sorted(got)}, expected "
            f"{sorted(expect['net_by_tax_ref'])}"
        )
        for ref, value in expect["net_by_tax_ref"].items():
            _assert_amount(got[ref], value, what=f"{path.name} net of {ref}")
            compared += 1
        # Stated once here rather than in every file: the per-category nets
        # add up to the document net. An allocation that drifted would show
        # up as a cent here and nowhere else.
        assert sum(got.values()) == result.net, (
            f"{path.name}: per-category nets sum to {sum(got.values())}, "
            f"document net is {result.net}"
        )

    if "warnings" in expect:
        _assert_warnings(result.warnings, expect["warnings"], where=path.name)
        compared += 1

    if "adjustments" in expect:
        _assert_adjustments(
            result.adjustments, expect["adjustments"], where=f"{path.name} document"
        )
        compared += 1

    for line_id, fields in expect.get("lines", {}).items():
        assert line_id in result.lines, (
            f"{path.name}: no line {line_id!r} in the result; it has "
            f"{sorted(result.lines)}"
        )
        line = result.lines[line_id]
        for field, value in fields.items():
            _assert_amount(
                getattr(line, field), value, what=f"{path.name} line {line_id} {field}"
            )
            compared += 1

    groups = _groups_by_id(result.children)
    for group_id, fields in expect.get("groups", {}).items():
        assert group_id in groups, (
            f"{path.name}: no group {group_id!r} in the result; it has "
            f"{sorted(groups)}"
        )
        group = groups[group_id]
        for field, value in fields.items():
            if field == "uniform_period":
                assert group.uniform_period is value, (
                    f"{path.name} group {group_id}: uniform_period is "
                    f"{group.uniform_period}, expected {value}"
                )
            elif field == "adjustments":
                _assert_adjustments(
                    group.adjustments, value, where=f"{path.name} group {group_id}"
                )
            elif field == "net_by_tax_ref":
                for ref, amount in value.items():
                    _assert_amount(
                        group.net_by_tax_ref[ref],
                        amount,
                        what=f"{path.name} group {group_id} net of {ref}",
                    )
            else:
                _assert_amount(
                    getattr(group, field),
                    value,
                    what=f"{path.name} group {group_id} {field}",
                )
            compared += 1

    group_inputs = _group_inputs_by_id(document.children)
    for group_id, fields in expect.get("display_rows", {}).items():
        row = display_row_for_group(group_inputs[group_id], groups[group_id])
        for field, value in fields.items():
            if field == "warnings":
                _assert_warnings(
                    row.warnings, value, where=f"{path.name} row {group_id}"
                )
            elif field in ("uniform_period", "exact"):
                assert getattr(row, field) is value, (
                    f"{path.name} row {group_id}: {field} is "
                    f"{getattr(row, field)}, expected {value}"
                )
            elif field == "period_unit":
                assert row.period_unit == value, (
                    f"{path.name} row {group_id}: period_unit is "
                    f"{row.period_unit!r}, expected {value!r}"
                )
            else:
                _assert_amount(
                    getattr(row, field),
                    value,
                    what=f"{path.name} row {group_id} {field}",
                )
            compared += 1

    if "vat" in expect:
        want = expect["vat"]
        rates = {ref: Decimal(value) for ref, value in want["rates"].items()}
        vat = apply_vat(result, rates)
        for field in ("net", "vat", "gross"):
            if field in want:
                _assert_amount(
                    getattr(vat, field), want[field], what=f"{path.name} VAT {field}"
                )
                compared += 1
        if "by_tax_ref" in want:
            got = {entry.tax_ref: entry for entry in vat.by_tax_ref}
            assert set(got) == set(entry["tax_ref"] for entry in want["by_tax_ref"])
            for entry in want["by_tax_ref"]:
                total = got[entry["tax_ref"]]
                for field in ("net", "rate", "vat", "gross"):
                    if field in entry:
                        _assert_amount(
                            getattr(total, field),
                            entry[field],
                            what=f"{path.name} VAT {entry['tax_ref']} {field}",
                        )
                        compared += 1
        # §5.6: VAT per category on the SUM of nets, and the parts add up.
        assert sum(entry.vat for entry in vat.by_tax_ref) == vat.vat
        assert vat.net + vat.vat == vat.gross

    return compared


# ─── The scenarios ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.stem)
def test_the_engine_produces_the_fixture_numbers(path: Path):
    """Every scenario in the directory, to the cent."""
    assert _check(path) > 0, f"{path.name} asserted nothing"


# ─── The guards on the guards ───────────────────────────────────────────────


#: Every scenario the A1 prompt asked for, by file stem. Listed so that a
#: renamed or deleted fixture is a FAILURE and not a quietly shorter suite —
#: the parametrised test above would simply collect one case fewer.
EXPECTED_SCENARIOS = frozenset(
    {
        "s1-quote",
        "s6-rounding",
        "compound-adjustments",
        "kind-filtered-fee",
        "mixed-tax-refs",
        "negative-correction",
        "zero-net-margin",
        "uniform-days-row",
        "mixed-days-row",
        "collapsed-price-not-exact",
        "nested-groups",
        "line-discounts",
        # B1.2: a line opting out of an adjustment's role.
        "role-exempt-line",
        "role-exempt-kinds-fee",
    }
)


def test_the_fixture_directory_is_not_empty():
    """A glob that matched nothing would make the suite above vacuous."""
    files = _files()
    assert len(files) >= len(EXPECTED_SCENARIOS), (
        f"only found {[p.name for p in files]} under {FIXTURES}"
    )


def test_the_scenarios_the_prompt_asked_for_are_all_here():
    present = {path.stem for path in _files()}
    assert EXPECTED_SCENARIOS <= present, (
        f"missing fixture(s): {sorted(EXPECTED_SCENARIOS - present)}"
    )


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.stem)
def test_every_fixture_names_the_numbers_it_is_about(path: Path):
    """A file with an empty `expect` would pass the suite above silently."""
    data = _load(path)
    assert data.get("why", "").strip(), (
        f"{path.name} has no `why` — a fixture nobody can read the point of "
        f"is a number nobody will dare change"
    )
    assert data.get("expect"), f"{path.name} expects nothing"
    assert "net" in data["expect"], (
        f"{path.name} does not pin a document net, which every scenario has"
    )
    assert _check(path) >= 2, f"{path.name} compared fewer than two numbers"


def test_the_comparison_notices_a_wrong_number(tmp_path: Path):
    """Prove `_check` can fail. The probe-can-fail guard for this file.

    S1 with its net moved by one cent. If this passes, every scenario above
    is passing for a reason that has nothing to do with the engine.
    """
    data = _load(FIXTURES / "s1-quote.json")
    data["expect"]["net"] = "12517.21"
    probe = tmp_path / "s1-wrong.json"
    probe.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(AssertionError, match="12517.21"):
        _check(probe)


def test_the_comparison_notices_a_wrong_vat(tmp_path: Path):
    """The same, for the half of the engine the core path never reaches.

    `apply_vat` is a separate call, so a `_check` that silently skipped the
    `vat` block would leave every VAT number in every fixture unasserted and
    nothing else would notice.
    """
    data = _load(FIXTURES / "s6-rounding.json")
    data["expect"]["vat"]["vat"] = "500.45"
    probe = tmp_path / "s6-wrong.json"
    probe.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(AssertionError, match="500.45"):
        _check(probe)


def test_the_comparison_notices_a_missing_warning(tmp_path: Path):
    """And for the warnings, which are an empty list in most fixtures.

    `warnings: []` is the commonest expectation in the directory, and an
    `_assert_warnings` that compared nothing would satisfy all of them.
    """
    data = _load(FIXTURES / "mixed-days-row.json")
    data["expect"]["warnings"] = []
    probe = tmp_path / "mixed-wrong.json"
    probe.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(AssertionError, match="mixed_days_in_group"):
        _check(probe)


def test_every_amount_in_every_fixture_is_written_as_a_string():
    """No JSON numbers in the fixture files, input or expectation.

    A JSON number is a binary float. `core/rounding.to_decimal` refuses one
    on the way in, so a fixture that used `1200` unquoted would fail — but an
    EXPECTATION written as `12517.2` would be parsed by Python's `json` as a
    float, converted by `Decimal(str(...))`, and would quietly compare
    against `12517.2` instead of `12517.20`. This is the only test in the
    file that is about the files rather than the engine, and it is here
    because the files are the specification.
    """
    offences: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, float):
            offences.append(f"{where} = {node!r}")
        elif isinstance(node, Mapping):
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    for path in _files():
        walk(_load(path), path.name)

    assert not offences, (
        "these fixture values are JSON numbers and must be quoted strings:\n"
        + "\n".join(offences)
    )


def test_a_fixture_document_with_a_json_number_is_refused():
    """The guard behind the test above, as behaviour rather than a scan.

    Proves the engine's own refusal is real, so the scan is a convenience
    that names the file and not the only thing standing between a float and
    a total.
    """
    with pytest.raises(CalcError, match="not a JSON number"):
        document_from_data(
            {
                "children": [
                    {"id": "l1", "unit_price": 1200.5, "period_unit": "day"}
                ]
            }
        )
