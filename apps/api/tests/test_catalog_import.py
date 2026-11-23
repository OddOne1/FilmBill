"""The CSV item import, without a database.

The whole rule set lives in `billing/catalog/csv_import.py` precisely so that
it can be asserted row by row here: a German price list with a decimal comma,
a semicolon separator, a BOM from Excel and one bad row in the middle is four
properties of one file, and checking them through four HTTP requests against
a real Postgres would be slower and would prove less.

What the service layer adds — who the categories and units actually are, and
the writing — is in `test_catalog_api.py`. The agreement between the two is
the digest, and the test that `preview` and `apply` produce the same plan is
there, against a real database, because that is the only place it means
anything.
"""

from decimal import Decimal

import pytest

from apps.api.billing.catalog import (
    ACTION_CREATE,
    ACTION_ERROR,
    ACTION_UPDATE,
    COLUMNS,
    REQUIRED_COLUMNS,
    ImportContext,
    ImportFormatError,
    detect_delimiter,
    parse_active,
    parse_decimal,
    plan_from_file,
    read_rows,
)

#: One company's existing catalog, as the planner sees it. The ids are
#: strings and obviously fake: the planner never dereferences them, it only
#: carries them through to the service, and a test that used real UUIDs here
#: would be hiding that fact.
CONTEXT = ImportContext(
    items_by_sku={"CAM-001": "item-cam-001"},
    categories_by_name={"kamera": "cat-kamera", "personal": "cat-personal"},
    units_by_key={"day": "unit-day", "tag": "unit-day", "stunde": "unit-hour"},
    tax_class_keys=frozenset({"standard", "reduced_10"}),
)

GOOD_FILE = (
    "sku;name_de;name_en;kind;category;unit;bill_rate_day;cost_rate_day;tax_class;active\n"
    "CAM-001;Arri Alexa 35;Arri Alexa 35;equipment_rental;Kamera;Tag;1.200,00;600,00;standard;ja\n"
    "CAM-002;Cooke S4 Satz;Cooke S4 set;equipment_rental;Kamera;Tag;450,00;180,00;standard;ja\n"
    "CREW-001;Oberbeleuchter;Gaffer;labor;Personal;Tag;550,00;420,00;standard;ja\n"
)


def _plan(text, context=CONTEXT):
    return plan_from_file(text, context)


def _by_line(plan):
    return {row.line_number: row for row in plan.rows}


# ─── Reading the file ───────────────────────────────────────────────────────


def test_a_german_price_list_is_read_as_written():
    """Semicolons, decimal commas, thousands dots, and `ja`."""
    plan = _plan(GOOD_FILE)
    assert plan.delimiter == ";"
    assert [row.action for row in plan.rows] == [
        ACTION_UPDATE,  # CAM-001 exists
        ACTION_CREATE,
        ACTION_CREATE,
    ]
    alexa = _by_line(plan)[2]
    assert alexa.values.bill_rate_day == Decimal("1200.00")
    assert alexa.values.cost_rate_day == Decimal("600.00")
    assert alexa.values.active is True
    assert alexa.item_id == "item-cam-001"
    assert alexa.category_id == "cat-kamera"
    assert alexa.unit_id == "unit-day"


def test_a_comma_separated_file_with_dot_decimals_works_too():
    text = (
        "sku,name_de,kind,bill_rate_day\n"
        "CAM-003,Sony Venice,equipment_rental,980.00\n"
    )
    plan = _plan(text)
    assert plan.delimiter == ","
    assert _by_line(plan)[2].values.bill_rate_day == Decimal("980.00")


def test_a_tab_separated_file_works():
    text = "sku\tname_de\tkind\tbill_rate_day\nCAM-004\tRed Komodo\tequipment_rental\t700\n"
    plan = _plan(text)
    assert plan.delimiter == "\t"
    assert _by_line(plan)[2].values.bill_rate_day == Decimal("700.00")


def test_an_excel_bom_does_not_hide_the_sku_column():
    """A BOM left in place makes `sku` arrive as `\\ufeffsku`, and the file
    is then refused for a missing column that is visibly present."""
    plan = _plan("﻿" + GOOD_FILE)
    assert [row.action for row in plan.rows].count(ACTION_ERROR) == 0


def test_a_windows_1252_file_is_read_rather_than_refused():
    payload = GOOD_FILE.replace("Oberbeleuchter", "Oberbeleuchter Müller").encode("cp1252")
    plan = _plan(payload)
    assert "Müller" in _by_line(plan)[4].values.name_de


def test_german_column_headings_are_understood():
    text = (
        "Artikelnummer;Bezeichnung;Art;Kategorie;Einheit;Tagessatz;Aktiv\n"
        "CAM-009;Teradek Bolt;equipment_rental;Kamera;Tag;90,00;ja\n"
    )
    plan = _plan(text)
    row = _by_line(plan)[2]
    assert row.action == ACTION_CREATE
    assert row.values.name_de == "Teradek Bolt"
    assert row.values.bill_rate_day == Decimal("90.00")


def test_headers_are_forgiven_their_spacing_and_case():
    text = "SKU; Name DE ;Kind;Bill-Rate-Day\nX-1;Thing;fee;10,00\n"
    plan = _plan(text)
    assert _by_line(plan)[2].action == ACTION_CREATE


def test_a_column_the_importer_does_not_know_is_reported_not_ignored_silently():
    text = GOOD_FILE.replace(
        "sku;name_de", "notizen;sku;name_de"
    ).replace("\nCAM-001;", "\nirgendwas;CAM-001;").replace(
        "\nCAM-002;", "\nnoch was;CAM-002;"
    ).replace("\nCREW-001;", "\nund das;CREW-001;")
    plan = _plan(text)
    assert plan.ignored_columns == ("notizen",)


def test_trailing_blank_lines_are_not_rows():
    plan = _plan(GOOD_FILE + "\n\n;;;;;;;;;\n")
    assert len(plan.rows) == 3


@pytest.mark.parametrize("missing", REQUIRED_COLUMNS)
def test_a_missing_required_column_refuses_the_whole_file(missing):
    """Not a per-row error: a file with no `kind` column has no good rows in
    it, and reporting two hundred identical row errors buries the one fact
    that matters."""
    header = ";".join(name for name in COLUMNS if name != missing)
    body = ";".join("x" for name in COLUMNS if name != missing)
    with pytest.raises(ImportFormatError) as caught:
        _plan(f"{header}\n{body}\n")
    assert missing in str(caught.value)
    # And it says how it read the file, because a wrongly-guessed delimiter
    # is the other reason a column looks missing.
    assert "separator" in str(caught.value)


def test_an_empty_file_is_refused_with_a_sentence():
    with pytest.raises(ImportFormatError) as caught:
        _plan("")
    assert "empty" in str(caught.value)


def test_a_header_with_no_rows_under_it_is_refused():
    with pytest.raises(ImportFormatError) as caught:
        _plan("sku;name_de;kind\n")
    assert "no data rows" in str(caught.value)


def test_a_file_of_undecodable_bytes_says_what_to_do():
    with pytest.raises(ImportFormatError) as caught:
        read_rows(b"\x80\x81\x82" + b"\xfe" * 10 + "sku".encode("utf-16"))
    assert "CSV UTF-8" in str(caught.value)


def test_the_delimiter_is_detected_on_a_one_row_file():
    """`csv.Sniffer` has almost nothing to go on here, and guessing `,` for a
    semicolon file turns every row into one column — which then reports as
    ten missing columns."""
    assert detect_delimiter("sku;name_de;kind\nA;B;fee\n") == ";"
    assert detect_delimiter("sku,name_de,kind\nA,B,fee\n") == ","


# ─── Amounts and flags ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("850", "850.00"),
        ("850,00", "850.00"),
        ("850.00", "850.00"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("€ 1.200,00", "1200.00"),
        ("1 200,00", "1200.00"),
        ("0", "0.00"),
    ],
)
def test_amounts_in_either_convention(raw, expected):
    assert parse_decimal(raw, column="bill_rate_day") == Decimal(expected)


def test_a_blank_amount_is_none_and_not_zero():
    """The difference matters: `None` leaves the rate alone on an update,
    and `0.00` would set the day rate to nothing."""
    assert parse_decimal("", column="bill_rate_day") is None
    assert parse_decimal("   ", column="bill_rate_day") is None


def test_an_amount_is_never_a_float():
    assert isinstance(parse_decimal("1.234,56", column="x"), Decimal)


def test_an_unreadable_amount_says_what_a_readable_one_looks_like():
    with pytest.raises(ValueError) as caught:
        parse_decimal("ab 850", column="bill_rate_day")
    assert "850,00" in str(caught.value)


@pytest.mark.parametrize("raw", ["ja", "JA", "1", "true", "yes", "x"])
def test_yes_words(raw):
    assert parse_active(raw) is True


@pytest.mark.parametrize("raw", ["nein", "0", "false", "no"])
def test_no_words(raw):
    assert parse_active(raw) is False


def test_a_blank_active_column_leaves_the_item_as_it_is():
    assert parse_active("") is None


def test_an_unreadable_active_value_says_what_to_write():
    with pytest.raises(ValueError) as caught:
        parse_active("vielleicht")
    assert "ja/nein" in str(caught.value)


# ─── Per-row refusals ───────────────────────────────────────────────────────


def test_a_row_with_no_sku_is_an_error_and_says_why_twice_running_matters():
    plan = _plan("sku;name_de;kind\n;Etwas;fee\n")
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert "running the file twice" in row.message


def test_a_row_with_no_name_is_an_error():
    plan = _plan("sku;name_de;kind\nX-9;;fee\n")
    assert _by_line(plan)[2].action == ACTION_ERROR
    assert "no name" in _by_line(plan)[2].message


def test_an_unknown_kind_lists_the_kinds_and_points_at_the_category_column():
    plan = _plan("sku;name_de;kind\nX-9;Kamerawagen;kamerawagen\n")
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert "equipment_rental" in row.message
    assert "category column" in row.message


def test_an_unknown_category_is_an_error_and_is_not_created():
    """A category created from a typo is how a catalog grows "Kamera",
    "Kameras" and "kamera" in one afternoon."""
    plan = _plan("sku;name_de;kind;category\nX-9;Dolly;equipment_rental;Kamras\n")
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert "Kamras" in row.message
    assert "does not create categories" in row.message


def test_an_unknown_unit_is_an_error():
    plan = _plan("sku;name_de;kind;unit\nX-9;Dolly;equipment_rental;Woche\n")
    assert _by_line(plan)[2].action == ACTION_ERROR
    assert "Woche" in _by_line(plan)[2].message


def test_an_unknown_tax_class_is_an_error_naming_where_they_come_from():
    plan = _plan("sku;name_de;kind;tax_class\nX-9;Dolly;equipment_rental;super_reduced\n")
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert "super_reduced" in row.message
    assert "region pack" in row.message


def test_a_negative_rate_is_refused_and_called_what_it_is_not():
    plan = _plan("sku;name_de;kind;bill_rate_day\nX-9;Rabatt;fee;-100,00\n")
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert "not a discount" in row.message


def test_the_same_sku_twice_in_one_file_refuses_both_rows():
    """Applying both would let the second win silently, so neither does."""
    text = (
        "sku;name_de;kind\n"
        "X-9;Erste Fassung;fee\n"
        "X-9;Zweite Fassung;fee\n"
    )
    plan = _plan(text)
    assert _by_line(plan)[2].action == ACTION_CREATE
    assert _by_line(plan)[3].action == ACTION_ERROR
    assert "line 2 of this file" in _by_line(plan)[3].message


def test_a_bad_row_does_not_take_the_good_rows_with_it():
    text = GOOD_FILE + "X-BAD;Kaputt;nonexistent_kind\n"
    plan = _plan(text)
    assert len(plan.creates) == 2
    assert len(plan.updates) == 1
    assert len(plan.errors) == 1
    assert plan.counts == {ACTION_CREATE: 2, ACTION_UPDATE: 1, ACTION_ERROR: 1}


def test_an_error_row_still_says_which_item_it_was_about():
    """`values` is None for an error; `sku` and `name` are not.

    Without this the preview table shows "line 5: that is not an item kind"
    with an empty SKU and an empty name, about one row of two hundred — and
    the only way to find it is to open the file and count. The line number
    alone is not the handle people use; the article number is.
    """
    plan = _plan(
        "sku;name_de;kind\nX-9;Kamerawagen;kamerawagen\n"
    )
    row = _by_line(plan)[2]
    assert row.action == ACTION_ERROR
    assert row.values is None
    assert (row.sku, row.name) == ("X-9", "Kamerawagen")


def test_a_row_with_no_sku_still_carries_its_name():
    """The one error row that genuinely has no SKU. The name is all there
    is, so it had better be there."""
    row = _by_line(_plan("sku;name_de;kind\n;Namenloser Posten;fee\n"))[2]
    assert row.sku is None
    assert row.name == "Namenloser Posten"


def test_a_create_and_an_update_carry_them_too():
    """Set on every row, not only on the ones that failed — a preview table
    reads one field per column, not two with a condition."""
    plan = _plan(GOOD_FILE)
    assert all(row.sku for row in plan.rows)
    assert all(row.name for row in plan.rows)


def test_carrying_the_sku_does_not_change_the_digest():
    """The digest is over what the plan would DO. These two fields are for
    the table, so adding them must not invalidate a preview in a browser —
    and the only way to be sure is to pin the value."""
    assert _plan(GOOD_FILE).digest == _plan(GOOD_FILE).digest
    plan = _plan(GOOD_FILE)
    assert len(plan.digest) == 16
    assert all(character in "0123456789abcdef" for character in plan.digest)


def test_every_row_is_accounted_for_as_exactly_one_action():
    """No "skip", no silent pass. A row the preview did not account for is a
    row somebody will later believe was imported."""
    plan = _plan(GOOD_FILE + "X-BAD;Kaputt;nonexistent_kind\n;Ohne SKU;fee\n")
    assert len(plan.rows) == 5
    assert len(plan.creates) + len(plan.updates) + len(plan.errors) == 5
    assert all(row.action in (ACTION_CREATE, ACTION_UPDATE, ACTION_ERROR) for row in plan.rows)


# ─── The digest ─────────────────────────────────────────────────────────────


def test_the_same_file_and_the_same_catalog_give_the_same_digest():
    assert _plan(GOOD_FILE).digest == _plan(GOOD_FILE).digest


def test_a_changed_amount_changes_the_digest():
    changed = GOOD_FILE.replace("1.200,00", "1.300,00")
    assert _plan(changed).digest != _plan(GOOD_FILE).digest


def test_an_item_appearing_in_the_catalog_changes_the_digest():
    """This is the case the digest exists for: between the preview and the
    apply, somebody in another tab creates CAM-002, and the plan silently
    turns from a create into an update."""
    after = ImportContext(
        items_by_sku={**CONTEXT.items_by_sku, "CAM-002": "item-cam-002"},
        categories_by_name=CONTEXT.categories_by_name,
        units_by_key=CONTEXT.units_by_key,
        tax_class_keys=CONTEXT.tax_class_keys,
    )
    before_plan = _plan(GOOD_FILE)
    after_plan = _plan(GOOD_FILE, after)
    assert [row.action for row in before_plan.rows] != [
        row.action for row in after_plan.rows
    ]
    assert before_plan.digest != after_plan.digest


def test_a_reworded_error_message_does_not_invalidate_a_preview():
    """The digest is over what the plan would DO. An error row changes
    nothing either way, so it is not in it — otherwise a wording change in
    this repository would invalidate every preview open in a browser."""
    plan = _plan(GOOD_FILE + "X-BAD;Kaputt;nonexistent_kind\n")
    same_actions = _plan(GOOD_FILE + "X-ALSO-BAD;Auch kaputt;nope\n")
    assert plan.digest == same_actions.digest


def test_the_digest_is_stable_across_processes():
    """`Decimal("1200.00")` has to render the same way every time, which is
    the reason `default=str` is in the digest and a float never would be."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    program = (
        "from apps.api.billing.catalog import ImportContext, plan_from_file\n"
        f"context = ImportContext(items_by_sku={dict(CONTEXT.items_by_sku)!r},"
        f" categories_by_name={dict(CONTEXT.categories_by_name)!r},"
        f" units_by_key={dict(CONTEXT.units_by_key)!r},"
        f" tax_class_keys=frozenset({sorted(CONTEXT.tax_class_keys)!r}))\n"
        f"print(plan_from_file({GOOD_FILE!r}, context).digest)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _plan(GOOD_FILE).digest
