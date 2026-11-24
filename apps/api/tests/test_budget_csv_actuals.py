"""The actuals CSV planner, without a database.

`billing/budget/csv_actuals.py` is pure, so the parsing rules are checked
here on plain data and the end-to-end preview-equals-apply contract is in
`test_budget_tracking_api.py` against a real PostgreSQL.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.api.billing.budget.csv_actuals import (
    ACTION_CREATE,
    ACTION_DUPLICATE,
    ACTION_ERROR,
    ActualImportContext,
    duplicate_key,
    export_actuals_csv,
    plan_actuals_from_file,
)
from apps.api.billing.catalog.csv_import import ImportFormatError

CONTEXT = ActualImportContext(
    sections_by_code={"1100": "sec-crew", "1400": "sec-travel"},
    sections_by_name={"crew": "sec-crew", "travel": "sec-travel"},
    lines_by_key={("sec-crew", "dop"): "line-dop"},
    parties_by_name={"kamera gmbh": "party-kamera"},
)

HEADER = "date;description;payee;reference;amount;section_code;line_description;paid_on\r\n"


def _plan(body: str, context: ActualImportContext = CONTEXT):
    return plan_actuals_from_file((HEADER + body).encode("utf-8"), context)


def test_a_german_row_is_read_as_german():
    plan = _plan("24.11.2026;Kamera;Kamera GmbH;RE-1;1.234,56;1100;DoP;30.11.2026\r\n")
    row = plan.rows[0]
    assert row.action == ACTION_CREATE, row.message
    assert row.values.amount == Decimal("1234.56")
    assert row.values.incurred_on == date(2026, 11, 24)
    assert row.values.paid_on == date(2026, 11, 30)
    assert row.line_id == "line-dop"
    # A payee that names a party is linked, and the typed text is dropped —
    # an entry never carries both.
    assert row.values.party_id == "party-kamera"
    assert row.values.payee_text == ""


def test_an_unknown_payee_stays_free_text_and_is_not_an_error():
    plan = _plan("2026-11-24;;Parkgarage Wien;;12,00;1400;;\r\n")
    row = plan.rows[0]
    assert row.action == ACTION_CREATE
    assert row.values.party_id is None
    assert row.values.payee_text == "Parkgarage Wien"


def test_an_unmatched_line_is_an_unallocated_cost_and_not_an_error():
    plan = _plan("2026-11-24;;;;50,00;1100;Steadicam;\r\n")
    row = plan.rows[0]
    assert row.action == ACTION_CREATE
    assert row.line_id is None
    assert row.section_id == "sec-crew"
    assert "unallocated" in row.message


@pytest.mark.parametrize(
    "amount, fragment",
    [("10.000", "ambiguous"), ("-5,00", "always positive"), ("0", "always positive"), ("abc", "not a number")],
)
def test_bad_amounts_are_error_rows_with_a_sentence(amount, fragment):
    plan = _plan(f"2026-11-24;;;;{amount};1100;;\r\n")
    assert plan.rows[0].action == ACTION_ERROR
    assert fragment in plan.rows[0].message


def test_a_bad_date_is_an_error_row():
    plan = _plan("next tuesday;;;;5,00;1100;;\r\n")
    assert plan.rows[0].action == ACTION_ERROR
    assert "date" in plan.rows[0].message


def test_a_file_without_a_section_column_is_refused_whole():
    with pytest.raises(ImportFormatError):
        plan_actuals_from_file(b"date;amount\r\n2026-11-24;5,00\r\n", CONTEXT)


def test_an_existing_entry_makes_the_row_a_duplicate():
    context = ActualImportContext(
        sections_by_code=CONTEXT.sections_by_code,
        existing_by_key={duplicate_key("Taxi", "RE-9", Decimal("20.00")): "actual-1"},
    )
    plan = _plan("2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n", context)
    assert plan.rows[0].action == ACTION_DUPLICATE
    assert plan.rows[0].duplicate_of == "actual-1"


def test_the_digest_covers_duplicates_and_ignores_messages():
    """A duplicate row is a decision the user makes on the preview, so it is
    in the fingerprint; an error row changes nothing, so it is not."""
    one = _plan("2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n")
    with_error = _plan(
        "2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n"
        "2026-11-24;;;;5,00;9999;;\r\n"
    )
    assert [row.action for row in one.rows] == [ACTION_CREATE, ACTION_DUPLICATE]
    assert one.digest == with_error.digest
    single = _plan("2026-11-24;;Taxi;RE-9;20,00;1100;;\r\n")
    assert single.digest != one.digest


def test_the_export_is_german_excel_shaped():
    payload = export_actuals_csv(
        [{"date": "2026-11-24", "amount": "1234,56", "section_code": "1100"}]
    )
    assert payload.startswith("﻿".encode("utf-8"))
    text = payload.decode("utf-8-sig")
    assert text.splitlines()[0] == HEADER.strip()
    assert "1234,56" in text
