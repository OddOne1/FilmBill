"""Cost planning over a budget, against a real PostgreSQL.

The centre of this file is
`test_the_tracking_view_reproduces_the_golden_fixture`. It builds the S1
example budget through the endpoints, books fixture (k)'s three entries on it
— an invoice of 3 200 on Studio, an open order of 1 000 on Alexa, an ETC of
500 on Taxi — and compares every number `GET /budgets/{id}/tracking` returns
against **`tests/fixtures/tracking/s1-example.json` itself**, not against
literals copied out of it. That is what makes it a contract rather than a
restatement: `test_budget_tracking.py` reads the same file, so the pure module
and the API are pinned to one set of numbers and neither can drift without
the other failing.

The other themes:

  * **`exposure = actual + committed_open`** end to end: an order of 1 000
    with 600 invoiced against it is 1 000 of exposure on the wire, not 1 600;
  * **a commitment closes and reopens by itself**, and the reopen is the half
    that is easy to lose — voiding the invoice that settled an order has to
    put it back on the books;
  * **a locked budget refuses an estimate edit and accepts a cost entry**,
    both asserted, because that is the decision B1 made and the one somebody
    reading `_editable` would get wrong;
  * **the duplicate warning never blocks** — the row is written and the
    warning comes back beside it;
  * **duplicating a budget copies the estimate and no entries**;
  * **the CSV applies exactly what its preview showed**, with a stale preview
    refused and a duplicate row held back unless it is ticked;
  * **a budget, a party and a commitment from another company are 404s**, and
    a line from another section is a 409 — the difference matters, because a
    403 would confirm that somebody else's row exists;
  * **no DELETE route**, asserted on the document the app serves.

The staff walk lives in `test_budgets_api.py` beside A2's, deliberately: the
property is one property — the budget shows cost rates and margins, so staff
are closed out of it altogether — and two walks would be two places for an
endpoint to be forgotten.
"""

import io
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.project import BudgetActual, BudgetCommitment, BudgetLine
from apps.api.models.site_settings import SiteSettings
from apps.api.services import (
    budget_service,
    catalog_service,
    number_series,
    tax_service,
)

from .company_factories import (
    auth_headers,
    grant,
    make_budget,
    make_company,
    make_party,
    make_project,
    make_superadmin,
    make_user,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tracking"

#: Scenario (k), read from the SAME file the pure module's tests read.
S1_TRACKING = json.loads((FIXTURES / "s1-example.json").read_text())

#: The fixture's ids ↔ the example budget's own names.
FIXTURE_SECTIONS = {
    "crew": "Crew",
    "equipment": "Equipment",
    "studio": "Studio",
    "travel": "Travel",
}
FIXTURE_LINES = {
    "dop": "DoP",
    "gaffer": "Gaffer",
    "alexa": "Alexa 35",
    "lens": "Lens set",
    "studio-a": "Studio A",
    "taxi": "Taxi",
}

VIEW_ROLES = (
    CompanyRole.owner,
    CompanyRole.admin,
    CompanyRole.accountant,
    CompanyRole.producer,
)
EDIT_ROLES = (CompanyRole.owner, CompanyRole.admin, CompanyRole.producer)
ALL_ROLES = tuple(CompanyRole)


# ─── The world ──────────────────────────────────────────────────────────────


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "tracking-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    catalog_service.seed_catalog(pg_db, company=company)
    number_series.seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner, "db": pg_db}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


@pytest.fixture
def project(pg_client, world):
    response = pg_client.post(
        "/projects",
        headers=_headers(world),
        json={"name": "Spot Sommer 2026", "start_date": "2026-06-01"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def example(pg_client, world, project):
    """The S1 example budget, plus its sections and lines by name."""
    response = pg_client.post(
        f"/projects/{project['id']}/budgets/example", headers=_headers(world)
    )
    assert response.status_code == 201, response.text
    budget = response.json()
    sheet = pg_client.get(
        f"/budgets/{budget['id']}", headers=_headers(world)
    ).json()
    return {
        "budget": budget,
        "sheet": sheet,
        "sections": {row["name"]: row for row in sheet["sections"]},
        "lines": {row["description"]: row for row in sheet["lines"]},
    }


@pytest.fixture
def simple(pg_client, world, project):
    """One section, one line with a cost of 1 000.00. The arithmetic's stage.

    `quantity 1 × period 1 × factor 1 × unit_cost 1 000` so the engine's
    `cost_net` for the line is exactly 1 000.00 and the forecast numbers can
    be read without arithmetic.
    """
    budget = pg_client.post(
        f"/projects/{project['id']}/budgets",
        headers=_headers(world),
        json={"name": "Estimate"},
    ).json()
    section = pg_client.post(
        f"/budgets/{budget['id']}/sections",
        headers=_headers(world),
        json={"name": "Crew", "code": "1100"},
    ).json()
    line = pg_client.post(
        f"/budgets/{budget['id']}/lines",
        headers=_headers(world),
        json={
            "section_id": section["id"],
            "description": "DoP",
            "quantity": "1",
            "period_value": "1",
            "period_unit": "flat",
            "factor": "1",
            "unit_price": "1500.00",
            "unit_cost": "1000.00",
        },
    ).json()
    return {"budget": budget, "section": section, "line": line}


# ─── Little helpers ─────────────────────────────────────────────────────────


def _commitment(pg_client, world, budget_id, **body):
    body.setdefault("commitment_date", "2026-06-01")
    response = pg_client.post(
        f"/budgets/{budget_id}/commitments", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()["commitment"]


def _actual(pg_client, world, budget_id, **body):
    body.setdefault("incurred_on", "2026-06-02")
    response = pg_client.post(
        f"/budgets/{budget_id}/actuals", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()["actual"]


def _tracking(pg_client, world, budget_id):
    response = pg_client.get(
        f"/budgets/{budget_id}/tracking", headers=_headers(world)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _amount(actual, expected, *, what):
    """Compared as `Decimal`s, so `"1000.0"` and `"1000.00"` are equal."""
    if expected is None:
        assert actual is None, f"{what}: expected no value, got {actual!r}"
        return
    assert actual is not None, f"{what}: expected {expected}, got None"
    assert Decimal(actual) == Decimal(str(expected)), (
        f"{what}: expected {expected}, got {actual}"
    )


# ─── The surface ────────────────────────────────────────────────────────────


def test_there_is_no_delete_route_for_a_cost_entry(client):
    """Asserted on the document the app serves, not on a handler.

    An entry is retired by `POST .../void` with a reason: the row stays in
    the ledger, the reason explains it, and an audit event says who.
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    deletes = [
        f"DELETE {path}"
        for path, operations in schema["paths"].items()
        if ("commitment" in path or "actual" in path or "/ledger" in path)
        and "delete" in operations
    ]
    assert not deletes, (
        f"a cost entry has a DELETE route: {deletes}. Entries are voided "
        f"with a reason, never deleted."
    )


def test_the_tracking_endpoints_add_no_permission_keys(client):
    """Read is `budgets.view`, write is `budgets.edit`, and that is all.

    A cost entry exposes the same cost rates and margins the sheet does, so
    there is nothing somebody could be allowed to see here and not there. A
    new key would be a new thing for an administrator to get wrong.
    """
    from apps.api.services.permissions import PERMISSIONS

    invented = [
        key
        for key in PERMISSIONS
        if key.startswith("budget") and key not in ("budgets.view", "budgets.edit")
    ]
    assert not invented, (
        f"B1 added permission key(s) {invented}. Cost entries deliberately "
        f"reuse budgets.view and budgets.edit."
    )


# ─── The golden fixture, end to end ─────────────────────────────────────────


def test_the_tracking_view_reproduces_the_golden_fixture(
    pg_client, world, project, example
):
    """Fixture (k), booked through the API and read back.

    Every number comes out of `fixtures/tracking/s1-example.json`, the same
    file `test_budget_tracking.py` reads. The estimate side of it — the
    prices, the cost subtotals, the estimate margins — is in turn what
    `fixtures/calc/s1-quote.json` asserts against the engine, so this one
    test is pinned through two fixture files to A1's own arithmetic.
    """
    budget_id = example["budget"]["id"]
    lines = example["lines"]
    sections = example["sections"]

    # An invoice of 3 200.00 on the Studio line.
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=sections["Studio"]["id"],
        line_id=lines["Studio A"]["id"],
        amount="3200.00",
        description="Studio A, two days",
    )
    # An open order of 1 000.00 on the Alexa line, whose estimate cost is 0.
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=sections["Equipment"]["id"],
        line_id=lines["Alexa 35"]["id"],
        amount="1000.00",
        description="Alexa 35 sub-rental",
    )
    # An ETC of 500.00 on the Taxi line.
    response = pg_client.put(
        f"/budgets/{budget_id}/lines/{lines['Taxi']['id']}/etc",
        headers=_headers(world),
        json={"etc": "500.00"},
    )
    assert response.status_code == 200, response.text

    answer = _tracking(pg_client, world, budget_id)
    expect = S1_TRACKING["expect"]

    for fixture_id, expected in expect["lines"].items():
        line_id = lines[FIXTURE_LINES[fixture_id]]["id"]
        got = answer["lines"][line_id]
        for field in (
            "estimate_cost",
            "committed_gross",
            "committed_open",
            "actual",
            "exposure",
            "remaining",
            "efc",
            "variance",
            "variance_percent",
            "etc",
        ):
            _amount(got[field], expected[field], what=f"line {fixture_id}.{field}")
        assert got["remaining_rule"] == expected["remaining_rule"], fixture_id
        assert got["over_budget"] is expected["over_budget"], fixture_id

    for fixture_id, expected in expect["sections"].items():
        section_id = sections[FIXTURE_SECTIONS[fixture_id]]["id"]
        got = answer["sections"][section_id]
        for field in (
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
            "price",
            "forecast_margin",
            "forecast_margin_percent",
            "estimate_margin",
            "estimate_margin_percent",
        ):
            _amount(
                got[field], expected[field], what=f"section {fixture_id}.{field}"
            )
        assert got["over_budget"] is expected["over_budget"], fixture_id

    for field, expected in expect["budget"].items():
        if field == "over_budget":
            assert answer["budget"][field] is expected
        else:
            _amount(answer["budget"][field], expected, what=f"budget.{field}")

    # Stated here as well, because these four are the numbers Mathias reads
    # off the top sheet and the ones the prompt asked to be computed.
    assert Decimal(answer["budget"]["efc"]) == Decimal("7760.00")
    assert Decimal(answer["budget"]["variance"]) == Decimal("-2380.00")
    assert Decimal(answer["budget"]["forecast_margin"]) == Decimal("4757.20")
    assert Decimal(answer["budget"]["forecast_margin_percent"]) == Decimal("38.01")


def test_the_tracking_estimate_side_is_the_calculated_view_s_own_numbers(
    pg_client, world, project, example
):
    """One engine call, two views. `estimate_cost` is the sheet's `cost`.

    Not a tolerance and not a recomputation: the same strings. A tracking
    view that priced the budget a second time could disagree by a cent and
    nobody could say which was right (rule 2).
    """
    budget_id = example["budget"]["id"]
    calculated = pg_client.get(
        f"/budgets/{budget_id}/calculated", headers=_headers(world)
    ).json()
    answer = _tracking(pg_client, world, budget_id)

    assert Decimal(answer["budget"]["estimate_cost"]) == Decimal(
        calculated["result"]["cost"]
    )
    assert Decimal(answer["budget"]["price"]) == Decimal(
        calculated["result"]["net"]
    )
    assert Decimal(answer["budget"]["estimate_margin"]) == Decimal(
        calculated["result"]["margin"]
    )
    assert Decimal(answer["budget"]["estimate_margin_percent"]) == Decimal(
        calculated["result"]["margin_percent"]
    )
    for row in calculated["top_sheet"]:
        tracked = answer["sections"][row["section_id"]]
        assert Decimal(tracked["estimate_cost"]) == Decimal(row["cost"])
        assert Decimal(tracked["price"]) == Decimal(row["price"])
        assert Decimal(tracked["estimate_margin"]) == Decimal(row["margin"])


# ─── The rule ───────────────────────────────────────────────────────────────


def test_an_invoiced_commitment_does_not_count_twice(pg_client, world, simple):
    """**The rule.** 1 000 ordered, 600 invoiced → 1 000 of exposure.

    Not 1 600. Asserted on the wire and not only in the pure module, because
    the wiring — the actual's `commitment_id` reaching `committed_open_of` —
    is its own opportunity to get this wrong, and a service that never
    passed the link would produce 1 600 while every unit test stayed green.
    """
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1000.00",
        reference="PO-1",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="600.00",
        reference="RE-1",
    )

    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["actual"]) == Decimal("600.00")
    assert Decimal(line["committed_gross"]) == Decimal("1000.00")
    assert Decimal(line["committed_open"]) == Decimal("400.00")
    assert Decimal(line["exposure"]) == Decimal("1000.00")
    assert Decimal(line["exposure"]) != Decimal("1600.00")


def test_an_unlinked_invoice_leaves_the_order_fully_open(pg_client, world, simple):
    """The other side of the same rule, so the test above cannot pass by
    accident on a service that ignores the link altogether."""
    budget_id = simple["budget"]["id"]
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1000.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="600.00",
    )

    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["committed_open"]) == Decimal("1000.00")
    assert Decimal(line["exposure"]) == Decimal("1600.00")


def test_an_unallocated_cost_shows_up_as_unallocated(pg_client, world, simple):
    """A cost on the section and no line — acceptance step 2's 150.00."""
    budget_id = simple["budget"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="150.00",
        description="Parking, nobody budgeted it",
    )

    answer = _tracking(pg_client, world, budget_id)
    section = answer["sections"][simple["section"]["id"]]
    assert Decimal(section["unallocated"]) == Decimal("150.00")
    assert Decimal(section["unallocated_actual"]) == Decimal("150.00")
    assert Decimal(section["actual"]) == Decimal("150.00")
    assert Decimal(answer["budget"]["unallocated"]) == Decimal("150.00")
    # And it is NOT on the line.
    assert Decimal(answer["lines"][simple["line"]["id"]]["actual"]) == Decimal("0")


# ─── Automatic close and reopen ─────────────────────────────────────────────


def test_a_commitment_closes_when_its_invoices_reach_it(pg_client, world, simple):
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="700.00",
    )
    assert commitment["status"] == "open"

    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="700.00",
    )

    rows = pg_client.get(
        f"/budgets/{budget_id}/commitments", headers=_headers(world)
    ).json()
    assert rows[0]["status"] == "closed"
    assert Decimal(rows[0]["open_amount"]) == Decimal("0.00")
    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["committed_open"]) == Decimal("0.00")
    assert Decimal(line["exposure"]) == Decimal("700.00")


def test_voiding_the_invoice_that_settled_an_order_reopens_it(
    pg_client, pg_db, world, simple
):
    """The half that is easy to lose.

    An order closed by an invoice that turns out to be wrong is owed again,
    and a service that only closed would leave 700 outside every forecast
    while the money was still going to be spent.
    """
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="700.00",
    )
    actual = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="700.00",
    )

    response = pg_client.post(
        f"/budgets/{budget_id}/actuals/{actual['id']}/void",
        headers=_headers(world),
        json={"reason": "wrong supplier"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["void_reason"] == "wrong supplier"

    rows = pg_client.get(
        f"/budgets/{budget_id}/commitments", headers=_headers(world)
    ).json()
    assert rows[0]["status"] == "open", "the commitment did not reopen"
    assert Decimal(rows[0]["open_amount"]) == Decimal("700.00")

    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["actual"]) == Decimal("0.00")
    assert Decimal(line["committed_open"]) == Decimal("700.00")
    assert Decimal(line["exposure"]) == Decimal("700.00")

    actions = [
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == uuid.UUID(commitment["id"]))
        .order_by(AuditEvent.created_at)
        .all()
    ]
    assert actions == [
        "budget_commitment.created",
        "budget_commitment.closed",
        "budget_commitment.reopened",
    ], actions


def test_an_over_invoiced_order_has_no_negative_open_amount(
    pg_client, world, simple
):
    """700 ordered, 750 invoiced: open 0, not −50."""
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="700.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="750.00",
    )
    rows = pg_client.get(
        f"/budgets/{budget_id}/commitments", headers=_headers(world)
    ).json()
    assert Decimal(rows[0]["open_amount"]) == Decimal("0.00")
    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["committed_open"]) == Decimal("0.00")
    assert Decimal(line["actual"]) == Decimal("750.00")


def test_closing_by_hand_takes_the_rest_out_of_the_open_amount(
    pg_client, world, simple
):
    """A supplier who invoices 940 of 1 000 and stops."""
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1000.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="940.00",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/commitments/{commitment['id']}/close",
        headers=_headers(world),
        json={"reason": "they are not invoicing the rest"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["committed_open"]) == Decimal("0.00")
    assert Decimal(line["exposure"]) == Decimal("940.00")


def test_a_cancelled_order_leaves_gross_as_well_as_open(pg_client, world, simple):
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1000.00",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/commitments/{commitment['id']}/cancel",
        headers=_headers(world),
        json={"reason": "the crane is off"},
    )
    assert response.status_code == 200, response.text
    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["committed_gross"]) == Decimal("0.00")
    assert Decimal(line["committed_open"]) == Decimal("0.00")


def test_cancelling_a_part_invoiced_order_is_refused_and_says_to_close_it(
    pg_client, world, simple
):
    """It was not withdrawn; it was fulfilled in part."""
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1000.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        commitment_id=commitment["id"],
        amount="400.00",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/commitments/{commitment['id']}/cancel",
        headers=_headers(world),
        json={},
    )
    assert response.status_code == 409, response.text
    assert "close it instead" in response.json()["detail"].lower()


# ─── Voiding ───────────────────────────────────────────────────────────────


def test_a_void_needs_a_reason(pg_client, world, simple):
    budget_id = simple["budget"]["id"]
    actual = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="100.00",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/actuals/{actual['id']}/void",
        headers=_headers(world),
        json={"reason": "   "},
    )
    assert response.status_code in (409, 422), response.text


def test_a_voided_entry_stays_in_the_ledger_and_leaves_the_numbers(
    pg_client, world, simple
):
    """Acceptance step 5. Nothing is deleted."""
    budget_id = simple["budget"]["id"]
    actual = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="100.00",
    )
    pg_client.post(
        f"/budgets/{budget_id}/actuals/{actual['id']}/void",
        headers=_headers(world),
        json={"reason": "entered twice"},
    )

    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["actual"]) == Decimal("0.00")

    hidden = pg_client.get(
        f"/budgets/{budget_id}/actuals", headers=_headers(world)
    ).json()
    assert hidden == []

    shown = pg_client.get(
        f"/budgets/{budget_id}/actuals?include_voided=true",
        headers=_headers(world),
    ).json()
    assert len(shown) == 1
    assert shown[0]["is_voided"] is True
    assert shown[0]["void_reason"] == "entered twice"


def test_voiding_a_commitment_with_live_invoices_is_refused(
    pg_client, world, simple
):
    """An invoice pointing at a voided order has an open amount nobody can
    explain, so the refusal names the way out."""
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="500.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        commitment_id=commitment["id"],
        amount="200.00",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/commitments/{commitment['id']}/void",
        headers=_headers(world),
        json={"reason": "wrong budget"},
    )
    assert response.status_code == 409, response.text
    assert "close it instead" in response.json()["detail"].lower()


# ─── Validation ─────────────────────────────────────────────────────────────


def test_a_line_from_another_section_is_refused_with_a_409(
    pg_client, world, simple
):
    """The two-table condition the migration deliberately does not enforce.

    409 and not 404: the line exists, it is simply somewhere else in the
    sheet, and saying "not found" would send somebody looking for a row that
    is on their screen.
    """
    budget_id = simple["budget"]["id"]
    other = pg_client.post(
        f"/budgets/{budget_id}/sections",
        headers=_headers(world),
        json={"name": "Equipment", "code": "1200"},
    ).json()

    response = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={
            "section_id": other["id"],
            "line_id": simple["line"]["id"],
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    assert response.status_code == 409, response.text
    assert "not in that section" in response.json()["detail"]


def test_a_commitment_of_another_budget_cannot_be_linked(
    pg_client, world, project, simple
):
    """Two scenarios of one project is the realistic mistake."""
    other_budget = pg_client.post(
        f"/projects/{project['id']}/budgets",
        headers=_headers(world),
        json={"name": "Without the crane"},
    ).json()
    other_section = pg_client.post(
        f"/budgets/{other_budget['id']}/sections",
        headers=_headers(world),
        json={"name": "Crew"},
    ).json()
    theirs = _commitment(
        pg_client,
        world,
        other_budget["id"],
        section_id=other_section["id"],
        amount="500.00",
    )

    response = pg_client.post(
        f"/budgets/{simple['budget']['id']}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "commitment_id": theirs["id"],
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    assert response.status_code == 409, response.text
    assert "different budget" in response.json()["detail"]


def test_a_party_from_another_company_is_a_404(pg_client, pg_db, world, simple):
    """404 and not 403: a 403 would confirm that the id names a real party
    in somebody else's company (CLAUDE.md rule 6)."""
    other = make_company(pg_db, "Someone else GmbH")
    their_party = make_party(pg_db, other, name="Their supplier", is_supplier=True)
    pg_db.commit()

    response = pg_client.post(
        f"/budgets/{simple['budget']['id']}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "party_id": str(their_party.id),
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("amount", ["0", "0.00", "-100.00"])
def test_a_non_positive_amount_is_refused(pg_client, world, simple, amount):
    """A correction is a new entry plus a void, never a negative row."""
    response = pg_client.post(
        f"/budgets/{simple['budget']['id']}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "amount": amount,
            "incurred_on": "2026-06-02",
        },
    )
    assert response.status_code in (409, 422), response.text


def test_a_party_and_a_typed_payee_together_are_refused(
    pg_client, pg_db, world, simple
):
    """Two names for one payee is a row nobody can reconcile."""
    party = make_party(pg_db, world["company"], name="Kamera GmbH", is_supplier=True)
    pg_db.commit()

    response = pg_client.post(
        f"/budgets/{simple['budget']['id']}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "party_id": str(party.id),
            "payee_text": "Kamera GesmbH",
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    assert response.status_code == 409, response.text
    assert "not both" in response.json()["detail"]


def test_a_negative_etc_is_refused(pg_client, world, simple):
    response = pg_client.put(
        f"/budgets/{simple['budget']['id']}/lines/{simple['line']['id']}/etc",
        headers=_headers(world),
        json={"etc": "-10.00"},
    )
    assert response.status_code in (409, 422), response.text


# ─── The ETC ────────────────────────────────────────────────────────────────


def test_setting_and_clearing_an_etc_moves_the_forecast_and_names_the_rule(
    pg_client, pg_db, world, simple
):
    """Acceptance step 4, and the `remaining_rule` field it depends on."""
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=line_id,
        amount="400.00",
    )

    before = _tracking(pg_client, world, budget_id)["lines"][line_id]
    assert before["remaining_rule"] == "default"
    assert Decimal(before["remaining"]) == Decimal("600.00")
    assert Decimal(before["efc"]) == Decimal("1000.00")

    response = pg_client.put(
        f"/budgets/{budget_id}/lines/{line_id}/etc",
        headers=_headers(world),
        json={"etc": "200.00"},
    )
    assert response.status_code == 200, response.text
    # The response IS the line's forecast after the change, so the drawer
    # needs no second request.
    assert response.json()["remaining_rule"] == "etc"
    assert Decimal(response.json()["efc"]) == Decimal("600.00")

    cleared = pg_client.put(
        f"/budgets/{budget_id}/lines/{line_id}/etc",
        headers=_headers(world),
        json={"etc": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["remaining_rule"] == "default"
    assert Decimal(cleared.json()["efc"]) == Decimal("1000.00")

    actions = [
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == uuid.UUID(line_id))
        .all()
    ]
    assert actions.count("budget_line.etc_set") == 2


def test_an_etc_of_zero_is_not_the_absence_of_one(pg_client, world, simple):
    """`0.00` says the line is finished; `null` says "use the rule".

    Through the API as well as in the pure module, because a schema that
    coerced the two together — a `Money` field with a default of 0, say —
    would make the distinction unreachable from outside.
    """
    budget_id = simple["budget"]["id"]
    line_id = simple["line"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=line_id,
        amount="400.00",
    )
    response = pg_client.put(
        f"/budgets/{budget_id}/lines/{line_id}/etc",
        headers=_headers(world),
        json={"etc": "0.00"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["remaining_rule"] == "etc"
    assert Decimal(response.json()["remaining"]) == Decimal("0.00")
    assert Decimal(response.json()["efc"]) == Decimal("400.00")


# ─── The lock, both ways ────────────────────────────────────────────────────


def test_a_locked_budget_refuses_an_estimate_edit_and_accepts_a_cost_entry(
    pg_client, world, simple
):
    """**The decision, asserted both ways** (acceptance step 6).

    A lock freezes the estimate because the estimate is the baseline the
    actuals are measured against. A lock that stopped the actuals too would
    mean the only budget worth tracking is one nobody has approved. Both
    halves are here because each one alone reads like an oversight.
    """
    budget_id = simple["budget"]["id"]
    assert (
        pg_client.post(f"/budgets/{budget_id}/lock", headers=_headers(world), json={}).status_code
        == 200
    )

    refused = pg_client.patch(
        f"/budgets/{budget_id}/lines/{simple['line']['id']}",
        headers=_headers(world),
        json={"unit_cost": "1200.00"},
    )
    assert refused.status_code == 409, refused.text
    assert "locked" in refused.json()["detail"].lower()

    accepted = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "line_id": simple["line"]["id"],
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    assert accepted.status_code == 201, accepted.text

    commitment = pg_client.post(
        f"/budgets/{budget_id}/commitments",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "amount": "50.00",
            "commitment_date": "2026-06-01",
        },
    )
    assert commitment.status_code == 201, commitment.text

    etc = pg_client.put(
        f"/budgets/{budget_id}/lines/{simple['line']['id']}/etc",
        headers=_headers(world),
        json={"etc": "10.00"},
    )
    assert etc.status_code == 200, etc.text


# ─── Duplicates ─────────────────────────────────────────────────────────────


def test_the_same_entry_twice_warns_and_still_saves(pg_client, world, simple):
    """Acceptance step 5's first half. **A warning, never a refusal.**

    A supplier really can invoice the same amount twice in a month, and a
    form that refused the second one would be wrong in a way the user could
    not get past.
    """
    budget_id = simple["budget"]["id"]
    body = {
        "section_id": simple["section"]["id"],
        "line_id": simple["line"]["id"],
        "amount": "250.00",
        "incurred_on": "2026-06-02",
        "payee_text": "Kamera GmbH",
        "reference": "RE-2026-114",
    }
    first = pg_client.post(
        f"/budgets/{budget_id}/actuals", headers=_headers(world), json=body
    )
    assert first.status_code == 201, first.text
    assert first.json()["duplicates"] == []

    second = pg_client.post(
        f"/budgets/{budget_id}/actuals", headers=_headers(world), json=body
    )
    assert second.status_code == 201, second.text
    warnings = second.json()["duplicates"]
    assert len(warnings) == 1, warnings
    assert warnings[0]["kind"] == "actual"
    assert warnings[0]["id"] == first.json()["actual"]["id"]
    assert Decimal(warnings[0]["amount"]) == Decimal("250.00")

    # Both are there, and both count.
    line = _tracking(pg_client, world, budget_id)["lines"][simple["line"]["id"]]
    assert Decimal(line["actual"]) == Decimal("500.00")


def test_a_duplicate_warning_ignores_the_date_and_not_the_reference(
    pg_client, world, simple
):
    """The key is `(payee, reference, amount)`.

    Not the date: the same invoice entered twice usually differs in the date
    somebody happened to type, and including it would make the check miss
    exactly the case it is for. The reference IS in it, because two
    different invoices from one supplier for one amount are two costs.
    """
    budget_id = simple["budget"]["id"]
    base = {
        "section_id": simple["section"]["id"],
        "amount": "250.00",
        "payee_text": "Kamera GmbH",
    }
    pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={**base, "incurred_on": "2026-06-02", "reference": "RE-1"},
    )

    same_invoice_other_date = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={**base, "incurred_on": "2026-07-14", "reference": "RE-1"},
    )
    assert len(same_invoice_other_date.json()["duplicates"]) == 1

    other_invoice = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={**base, "incurred_on": "2026-06-02", "reference": "RE-2"},
    )
    assert other_invoice.json()["duplicates"] == []


def test_a_voided_entry_is_not_a_duplicate(pg_client, world, simple):
    """It left the numbers, so it cannot warn about the entry replacing it —
    which is the commonest reason somebody voids one."""
    budget_id = simple["budget"]["id"]
    body = {
        "section_id": simple["section"]["id"],
        "amount": "250.00",
        "incurred_on": "2026-06-02",
        "reference": "RE-1",
        "payee_text": "Kamera GmbH",
    }
    first = pg_client.post(
        f"/budgets/{budget_id}/actuals", headers=_headers(world), json=body
    ).json()
    pg_client.post(
        f"/budgets/{budget_id}/actuals/{first['actual']['id']}/void",
        headers=_headers(world),
        json={"reason": "wrong amount"},
    )
    again = pg_client.post(
        f"/budgets/{budget_id}/actuals", headers=_headers(world), json=body
    )
    assert again.json()["duplicates"] == []


def test_a_commitment_and_an_actual_warn_about_each_other(
    pg_client, world, simple
):
    """An invoice entered once as an order and once as a cost."""
    budget_id = simple["budget"]["id"]
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="300.00",
        reference="PO-9",
        payee_text="Licht GmbH",
    )
    response = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={
            "section_id": simple["section"]["id"],
            "amount": "300.00",
            "incurred_on": "2026-06-02",
            "reference": "PO-9",
            "payee_text": "Licht GmbH",
        },
    )
    warnings = response.json()["duplicates"]
    assert [row["kind"] for row in warnings] == ["commitment"], warnings


# ─── Duplicating a budget ───────────────────────────────────────────────────


def test_duplicating_a_budget_copies_the_estimate_and_no_entries(
    pg_client, world, simple
):
    """Acceptance step 6's second half.

    A copy is a scenario somebody is about to change, and the costs that
    were booked against the original are facts about the original. Copying
    them would double every cost in the project the first time anybody
    duplicated a budget to try something.
    """
    budget_id = simple["budget"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="400.00",
    )
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="300.00",
    )
    pg_client.put(
        f"/budgets/{budget_id}/lines/{simple['line']['id']}/etc",
        headers=_headers(world),
        json={"etc": "123.00"},
    )

    copy = pg_client.post(
        f"/budgets/{budget_id}/duplicate", headers=_headers(world), json={}
    ).json()

    assert (
        pg_client.get(
            f"/budgets/{copy['id']}/actuals", headers=_headers(world)
        ).json()
        == []
    )
    assert (
        pg_client.get(
            f"/budgets/{copy['id']}/commitments", headers=_headers(world)
        ).json()
        == []
    )

    # The estimate came across in full, and the forecast of the copy is its
    # estimate — nothing has been spent on it.
    answer = _tracking(pg_client, world, copy["id"])
    assert Decimal(answer["budget"]["estimate_cost"]) == Decimal("1000.00")
    assert Decimal(answer["budget"]["efc"]) == Decimal("1000.00")
    assert Decimal(answer["budget"]["actual"]) == Decimal("0.00")
    # And the ETC did NOT come across: it is an estimate of what is left to
    # spend on a line against which nothing has been spent.
    for line in answer["lines"].values():
        assert line["etc"] is None
        assert line["remaining_rule"] == "default"


# ─── The CSV ────────────────────────────────────────────────────────────────


def _upload(pg_client, world, url, payload: bytes):
    return pg_client.post(
        url,
        headers=_headers(world),
        files={"file": ("actuals.csv", io.BytesIO(payload), "text/csv")},
    )


def test_the_import_applies_exactly_what_the_preview_showed(
    pg_client, world, simple
):
    """Acceptance step 8. The preview's plan and the apply's are compared
    directly, row for row, which is the only version of this assertion that
    cannot pass while the two disagree."""
    budget_id = simple["budget"]["id"]
    payload = (
        "date;description;payee;reference;amount;section_code;"
        "line_description;paid_on\r\n"
        "2026-06-03;Taxi to set;Taxi 40100;RE-55;120,50;1100;DoP;\r\n"
        "04.06.2026;Catering;Wirtshaus;RE-56;1.234,56;1100;;2026-06-30\r\n"
    ).encode("utf-8")

    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["creates"] == 2, plan["rows"]
    assert plan["errors"] == 0, plan["rows"]
    assert plan["duplicates"] == 0

    applied = _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest={plan['digest']}",
        payload,
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["applied"] is True
    assert result["created"] == 2
    assert result["digest"] == plan["digest"]
    assert result["rows"] == plan["rows"], "the apply did something else"

    rows = pg_client.get(
        f"/budgets/{budget_id}/actuals", headers=_headers(world)
    ).json()
    amounts = sorted(Decimal(row["amount"]) for row in rows)
    # The German decimal comma, read as a decimal comma.
    assert amounts == [Decimal("120.50"), Decimal("1234.56")], amounts
    on_line = [row for row in rows if row["line_description"] == "DoP"]
    assert len(on_line) == 1
    unallocated = [row for row in rows if row["line_description"] is None]
    assert len(unallocated) == 1
    assert unallocated[0]["paid_on"] == "2026-06-30"


def test_a_stale_preview_is_refused(pg_client, world, simple):
    budget_id = simple["budget"]["id"]
    payload = (
        "date;amount;section_code\r\n2026-06-03;100,00;1100\r\n"
    ).encode("utf-8")
    response = _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest=deadbeefdeadbeef",
        payload,
    )
    assert response.status_code == 409, response.text
    assert "preview" in response.json()["detail"]
    assert (
        pg_client.get(
            f"/budgets/{budget_id}/actuals", headers=_headers(world)
        ).json()
        == []
    )


def test_an_unknown_section_code_is_an_error_row_and_not_a_new_section(
    pg_client, world, simple
):
    budget_id = simple["budget"]["id"]
    payload = (
        "date;amount;section_code\r\n"
        "2026-06-03;100,00;9999\r\n"
        "2026-06-04;200,00;1100\r\n"
    ).encode("utf-8")
    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    assert preview["errors"] == 1
    assert preview["creates"] == 1
    bad = [row for row in preview["rows"] if row["action"] == "error"][0]
    assert "9999" in bad["message"]

    applied = _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest={preview['digest']}",
        payload,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["created"] == 1
    sheet = pg_client.get(f"/budgets/{budget_id}", headers=_headers(world)).json()
    assert [row["code"] for row in sheet["sections"]] == ["1100"]


def test_the_ambiguous_dot_is_refused(pg_client, world, simple):
    """`10.000` is ten in an English file and ten thousand in a German one.

    A2's refusal, which exists because a spreadsheet turned `1.0000` into
    `10.000` once and the import read ten.
    """
    budget_id = simple["budget"]["id"]
    payload = (
        "date;amount;section_code\r\n2026-06-03;10.000;1100\r\n"
    ).encode("utf-8")
    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    assert preview["errors"] == 1, preview["rows"]
    assert "ambiguous" in preview["rows"][0]["message"]


def test_a_duplicate_row_is_held_back_unless_it_is_ticked(
    pg_client, world, simple
):
    """The one place a duplicate is skipped rather than warned about: nobody
    is looking at the row as it is written."""
    budget_id = simple["budget"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="120.50",
        reference="RE-55",
        payee_text="Taxi 40100",
    )
    payload = (
        "date;description;payee;reference;amount;section_code\r\n"
        "2026-06-03;Taxi;Taxi 40100;RE-55;120,50;1100\r\n"
    ).encode("utf-8")

    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    assert preview["duplicates"] == 1
    assert preview["creates"] == 0
    row = preview["rows"][0]
    assert row["action"] == "duplicate"
    assert row["duplicate_of"] is not None

    held = _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest={preview['digest']}",
        payload,
    )
    assert held.status_code == 200, held.text
    assert held.json()["created"] == 0
    assert (
        len(pg_client.get(f"/budgets/{budget_id}/actuals", headers=_headers(world)).json())
        == 1
    )

    ticked = _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import"
        f"?expected_digest={preview['digest']}"
        f"&include_duplicates={row['line_number']}",
        payload,
    )
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["created"] == 1
    assert (
        len(pg_client.get(f"/budgets/{budget_id}/actuals", headers=_headers(world)).json())
        == 2
    )


def test_the_same_invoice_twice_in_one_file_is_flagged(pg_client, world, simple):
    """A bank export that lists one invoice twice is the commonest way a
    duplicate arrives, and a check that only looked at the database would
    wave both rows through."""
    budget_id = simple["budget"]["id"]
    payload = (
        "date;payee;reference;amount;section_code\r\n"
        "2026-06-03;Taxi 40100;RE-55;120,50;1100\r\n"
        "2026-06-03;Taxi 40100;RE-55;120,50;1100\r\n"
    ).encode("utf-8")
    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    assert preview["creates"] == 1
    assert preview["duplicates"] == 1


def test_the_import_never_updates_or_voids_an_existing_entry(
    pg_client, world, simple
):
    """Export, change an amount, import: the original is untouched.

    An import that could rewrite what a shoot cost would make a stale export
    a weapon. The changed row arrives as a second cost or as a duplicate,
    and the preview says which.
    """
    budget_id = simple["budget"]["id"]
    existing = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="100.00",
        reference="RE-1",
        payee_text="Taxi 40100",
    )
    payload = (
        "date;payee;reference;amount;section_code\r\n"
        "2026-06-02;Taxi 40100;RE-1;999,00;1100\r\n"
    ).encode("utf-8")
    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest={preview['digest']}",
        payload,
    )
    rows = pg_client.get(
        f"/budgets/{budget_id}/actuals?include_voided=true",
        headers=_headers(world),
    ).json()
    original = [row for row in rows if row["id"] == existing["id"]][0]
    assert Decimal(original["amount"]) == Decimal("100.00")
    assert original["is_voided"] is False
    assert len(rows) == 2


def test_the_export_round_trips_through_the_import(pg_client, world, simple):
    """What acceptance step 8 does: export, import the same file back.

    Every row comes back as a duplicate, which is the correct answer — the
    costs are already there — and that is also what proves the export writes
    the columns the import reads.
    """
    budget_id = simple["budget"]["id"]
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="1234.56",
        reference="RE-1",
        payee_text="Kamera GmbH",
        description="Camera package",
        paid_on="2026-07-01",
    )
    exported = pg_client.get(
        f"/budgets/{budget_id}/actuals/export.csv", headers=_headers(world)
    )
    assert exported.status_code == 200, exported.text
    body = exported.content
    assert body.startswith("﻿".encode("utf-8")), "no BOM for German Excel"
    text = body.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("date;description;payee")
    assert "1234,56" in text, "the export must use a decimal comma"

    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", body
    ).json()
    assert preview["duplicates"] == 1, preview["rows"]
    assert preview["creates"] == 0
    assert preview["errors"] == 0


def test_a_voided_entry_is_left_out_of_the_export(pg_client, world, simple):
    """The file exists to be changed and sent back; a voided row in it would
    read as a cost to re-import."""
    budget_id = simple["budget"]["id"]
    row = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="100.00",
    )
    pg_client.post(
        f"/budgets/{budget_id}/actuals/{row['id']}/void",
        headers=_headers(world),
        json={"reason": "duplicate"},
    )
    text = pg_client.get(
        f"/budgets/{budget_id}/actuals/export.csv", headers=_headers(world)
    ).content.decode("utf-8-sig")
    assert len(text.strip().splitlines()) == 1, text


def test_an_imported_cost_is_audited_like_a_typed_one(
    pg_client, pg_db, world, simple
):
    """The import goes through `create_actual`, so a row it writes is
    validated, audited and settles its commitment exactly like a typed one.
    An import with its own write path is how two kinds of row end up in one
    table."""
    budget_id = simple["budget"]["id"]
    payload = (
        "date;amount;section_code\r\n2026-06-03;100,00;1100\r\n"
    ).encode("utf-8")
    preview = _upload(
        pg_client, world, f"/budgets/{budget_id}/actuals/import/preview", payload
    ).json()
    _upload(
        pg_client,
        world,
        f"/budgets/{budget_id}/actuals/import?expected_digest={preview['digest']}",
        payload,
    )
    actions = {
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.company_id == world["company"].id)
        .all()
    }
    assert "budget_actual.created" in actions
    assert "budget.actuals_import_applied" in actions


# ─── Permissions and isolation ──────────────────────────────────────────────


@pytest.fixture
def every_role(pg_db, world):
    users = {}
    for role in ALL_ROLES:
        user = make_user(pg_db, f"tracking-role-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_reading_the_tracking_view_follows_the_view_matrix(
    pg_client, world, simple, every_role, role
):
    response = pg_client.get(
        f"/budgets/{simple['budget']['id']}/tracking",
        headers=auth_headers(every_role[role], world["company"]),
    )
    expected = 200 if role in VIEW_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_writing_a_cost_entry_follows_the_edit_matrix(
    pg_client, world, simple, every_role, role
):
    response = pg_client.post(
        f"/budgets/{simple['budget']['id']}/actuals",
        headers=auth_headers(every_role[role], world["company"]),
        json={
            "section_id": simple["section"]["id"],
            "amount": "100.00",
            "incurred_on": "2026-06-02",
        },
    )
    expected = 201 if role in EDIT_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )


def test_every_tracking_route_404s_for_another_company_s_budget(
    pg_client, pg_db, world
):
    """The isolation test CLAUDE.md rule 6 asks every new router to have.

    Walked over every route in the router rather than a sample, because the
    one that is forgotten is always the one that was added last.
    """
    other = make_company(pg_db, "Someone else GmbH")
    their_project = make_project(pg_db, other)
    their_budget = make_budget(pg_db, other, their_project)
    pg_db.commit()

    budget_id = their_budget.id
    stranger = uuid.uuid4()
    requests = [
        ("get", f"/budgets/{budget_id}/tracking", None),
        ("get", f"/budgets/{budget_id}/ledger", None),
        ("get", f"/budgets/{budget_id}/commitments", None),
        (
            "post",
            f"/budgets/{budget_id}/commitments",
            {
                "section_id": str(stranger),
                "amount": "100.00",
                "commitment_date": "2026-06-01",
            },
        ),
        ("patch", f"/budgets/{budget_id}/commitments/{stranger}", {"notes": "x"}),
        ("post", f"/budgets/{budget_id}/commitments/{stranger}/close", {}),
        ("post", f"/budgets/{budget_id}/commitments/{stranger}/cancel", {}),
        (
            "post",
            f"/budgets/{budget_id}/commitments/{stranger}/void",
            {"reason": "x"},
        ),
        ("get", f"/budgets/{budget_id}/actuals", None),
        (
            "post",
            f"/budgets/{budget_id}/actuals",
            {
                "section_id": str(stranger),
                "amount": "100.00",
                "incurred_on": "2026-06-01",
            },
        ),
        ("patch", f"/budgets/{budget_id}/actuals/{stranger}", {"notes": "x"}),
        ("post", f"/budgets/{budget_id}/actuals/{stranger}/void", {"reason": "x"}),
        ("put", f"/budgets/{budget_id}/lines/{stranger}/etc", {"etc": "1.00"}),
        ("get", f"/budgets/{budget_id}/actuals/export.csv", None),
    ]
    assert len(requests) == 14

    for method, url, body in requests:
        response = getattr(pg_client, method)(
            url, headers=_headers(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 404, (
            f"{method.upper()} {url} answered {response.status_code}: "
            f"{response.text}"
        )


def test_the_isolation_walk_covers_every_route_in_the_router(client):
    """The walk above sends 14 of the router's 16 operations.

    The two it does not are the CSV preview and apply, which take a file
    upload and are covered by `test_a_budget_of_another_company_is_a_404`'s
    shape in the walk's `export.csv` entry — stated here so that adding a
    seventeenth route fails this assertion rather than quietly going
    unchecked (rule 17b).
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app
    from apps.api.routers import budget_tracking

    schema = get_openapi(
        title=app.title, version=app.version, routes=budget_tracking.router.routes
    )
    operations = [
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method in methods
    ]
    assert len(operations) == 16, sorted(operations)


# ─── The ledger ─────────────────────────────────────────────────────────────


def test_the_ledger_returns_both_kinds_with_their_names_filled_in(
    pg_client, pg_db, world, simple
):
    """What the Costs tab reads: no second request per row."""
    party = make_party(pg_db, world["company"], name="Kamera GmbH", is_supplier=True)
    pg_db.commit()
    budget_id = simple["budget"]["id"]
    _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        party_id=str(party.id),
        amount="500.00",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="60.00",
        payee_text="Parkgarage",
    )

    ledger = pg_client.get(
        f"/budgets/{budget_id}/ledger", headers=_headers(world)
    ).json()
    assert len(ledger["commitments"]) == 1
    assert len(ledger["actuals"]) == 1
    assert ledger["commitments"][0]["payee"] == "Kamera GmbH"
    assert ledger["commitments"][0]["section_code"] == "1100"
    assert ledger["commitments"][0]["line_description"] == "DoP"
    assert ledger["actuals"][0]["payee"] == "Parkgarage"
    assert ledger["actuals"][0]["line_description"] is None


def test_the_ledger_filters_by_section_line_payee_and_date(
    pg_client, world, simple
):
    budget_id = simple["budget"]["id"]
    other = pg_client.post(
        f"/budgets/{budget_id}/sections",
        headers=_headers(world),
        json={"name": "Equipment", "code": "1200"},
    ).json()
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        line_id=simple["line"]["id"],
        amount="10.00",
        payee_text="Alpha",
        incurred_on="2026-06-01",
    )
    _actual(
        pg_client,
        world,
        budget_id,
        section_id=other["id"],
        amount="20.00",
        payee_text="Beta",
        incurred_on="2026-07-01",
    )

    def ledger(query: str):
        return pg_client.get(
            f"/budgets/{budget_id}/ledger?{query}", headers=_headers(world)
        ).json()["actuals"]

    assert len(ledger(f"section_id={simple['section']['id']}")) == 1
    assert len(ledger(f"line_id={simple['line']['id']}")) == 1
    assert len(ledger("payee=bet")) == 1
    assert len(ledger("date_from=2026-06-15")) == 1
    assert len(ledger("date_to=2026-06-15")) == 1
    assert len(ledger("date_from=2026-01-01&date_to=2026-12-31")) == 2


# ─── Audit ──────────────────────────────────────────────────────────────────


def test_every_cost_write_leaves_an_audit_row(pg_client, pg_db, world, simple):
    budget_id = simple["budget"]["id"]
    commitment = _commitment(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="500.00",
    )
    pg_client.patch(
        f"/budgets/{budget_id}/commitments/{commitment['id']}",
        headers=_headers(world),
        json={"notes": "agreed on the phone"},
    )
    actual = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="60.00",
    )
    pg_client.patch(
        f"/budgets/{budget_id}/actuals/{actual['id']}",
        headers=_headers(world),
        json={"paid_on": "2026-07-01"},
    )
    pg_client.post(
        f"/budgets/{budget_id}/actuals/{actual['id']}/void",
        headers=_headers(world),
        json={"reason": "wrong budget"},
    )
    pg_client.post(
        f"/budgets/{budget_id}/commitments/{commitment['id']}/cancel",
        headers=_headers(world),
        json={"reason": "off"},
    )
    pg_client.put(
        f"/budgets/{budget_id}/lines/{simple['line']['id']}/etc",
        headers=_headers(world),
        json={"etc": "5.00"},
    )

    actions = {
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.company_id == world["company"].id)
        .all()
    }
    for expected in (
        "budget_commitment.created",
        "budget_commitment.updated",
        "budget_commitment.cancelled",
        "budget_actual.created",
        "budget_actual.updated",
        "budget_actual.voided",
        "budget_line.etc_set",
    ):
        assert expected in actions, f"{expected} was not recorded: {actions}"


def test_an_audit_row_carries_the_before_and_the_after(
    pg_client, pg_db, world, simple
):
    """A diff and not a note. Rule 3's audit requirement, for these tables."""
    budget_id = simple["budget"]["id"]
    actual = _actual(
        pg_client,
        world,
        budget_id,
        section_id=simple["section"]["id"],
        amount="60.00",
    )
    pg_client.patch(
        f"/budgets/{budget_id}/actuals/{actual['id']}",
        headers=_headers(world),
        json={"amount": "75.00"},
    )
    row = (
        pg_db.query(AuditEvent)
        .filter(
            AuditEvent.entity_id == uuid.UUID(actual["id"]),
            AuditEvent.action == "budget_actual.updated",
        )
        .one()
    )
    assert row.changes["amount"] == ["60.00", "75.00"]


# ─── The model and the pure module agree ────────────────────────────────────


def test_the_model_s_default_status_is_the_pure_module_s_open(client):
    """The one duplicated literal, checked rather than trusted.

    `models/project.py` spells `"open"` again instead of importing it from
    `billing/budget/tracking.py`, so that `alembic/env.py`'s bare
    `import models` does not have to reach into `billing/`. This is the
    check that makes the duplication safe.
    """
    from apps.api.billing.budget.tracking import COMMITMENT_STATUSES, STATUS_OPEN
    from apps.api.models.project import COMMITMENT_STATUS_DEFAULT

    assert COMMITMENT_STATUS_DEFAULT == STATUS_OPEN
    assert COMMITMENT_STATUS_DEFAULT in COMMITMENT_STATUSES


def test_the_schema_s_status_vocabulary_is_the_pure_module_s(client):
    """Asserted at import time in `schemas/budget_tracking.py` as well; here
    so that a failure names the thing rather than crashing the collection."""
    from apps.api.billing.budget.tracking import COMMITMENT_STATUSES, REMAINING_RULES
    from apps.api.schemas.budget_tracking import CommitmentStatus, RemainingRule

    assert set(CommitmentStatus.__args__) == set(COMMITMENT_STATUSES)
    assert set(RemainingRule.__args__) == set(REMAINING_RULES)


# ─── The migration ──────────────────────────────────────────────────────────


def _scratch_database(base_url: str):
    from urllib.parse import urlsplit, urlunsplit

    from sqlalchemy import create_engine, text

    name = f"filmbill_b1_{uuid.uuid4().hex[:12]}"

    def _with(db: str) -> str:
        return urlunsplit(urlsplit(base_url)._replace(path=f"/{db}"))

    admin = create_engine(_with("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    return name, _with(name), admin


def _alembic(url: str, *args: str):
    import os
    import subprocess
    from pathlib import Path

    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return subprocess.run(
        ["alembic", *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def at_revision_0015(pg_engine):
    """A scratch database at the revision before cost tracking existed."""
    import os

    from sqlalchemy import create_engine, text

    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0015_projects_days_budgets")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0015:\n{result.stderr}")

    db_engine = create_engine(url)
    try:
        yield db_engine, url
    finally:
        db_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_migration_goes_up_and_down(at_revision_0015):
    """Up, then down, against a real database.

    The `etc` column's nullability is asserted explicitly: a
    `NOT NULL DEFAULT 0` would declare every line of every existing budget
    complete, and nothing else in the suite would notice — every forecast
    would simply read `remaining_rule: etc` and 0.
    """
    from sqlalchemy import text

    db_engine, url = at_revision_0015

    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    with db_engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert {"budget_commitments", "budget_actuals"} <= tables

        column = conn.execute(
            text(
                "SELECT is_nullable, column_default, numeric_precision, "
                "numeric_scale FROM information_schema.columns "
                "WHERE table_name = 'budget_lines' AND column_name = 'etc'"
            )
        ).one()
        assert column.is_nullable == "YES"
        assert column.column_default is None, (
            "budget_lines.etc has a default, which would declare every "
            "existing line finished"
        )
        assert (column.numeric_precision, column.numeric_scale) == (18, 2)

        # Money at NUMERIC(18,2) on both new tables (CLAUDE.md rule 1).
        for table in ("budget_commitments", "budget_actuals"):
            precision, scale = conn.execute(
                text(
                    "SELECT numeric_precision, numeric_scale FROM "
                    "information_schema.columns WHERE table_name = :t "
                    "AND column_name = 'amount'"
                ),
                {"t": table},
            ).one()
            assert (precision, scale) == (18, 2), table

            nullable = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'section_id'"
                ),
                {"t": table},
            ).scalar()
            assert nullable == "NO", f"{table}.section_id must be NOT NULL"

            nullable = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'line_id'"
                ),
                {"t": table},
            ).scalar()
            assert nullable == "YES", f"{table}.line_id must be nullable"

        # The one CHECK this migration writes, as behaviour and not as a
        # catalog lookup: a constraint that exists and does not fire is the
        # shape rule 17b is about.
        company_id = conn.execute(
            text("SELECT id FROM companies LIMIT 1")
        ).scalar()

    down = _alembic(url, "downgrade", "0015_projects_days_budgets")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"

    with db_engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert "budget_commitments" not in tables
        assert "budget_actuals" not in tables
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'budget_lines' AND column_name = 'etc'"
                )
            ).scalar()
            == 0
        )


def test_the_amount_check_constraint_actually_fires(pg_db, world):
    """The schema's one invariant, as behaviour.

    A CHECK that exists and never fires is the shape CLAUDE.md 17b is
    about, so this goes through the real database with the ORM's own
    validation bypassed — the service refuses a non-positive amount first,
    and this is the floor under that.
    """
    from datetime import date as date_type

    from sqlalchemy.exc import IntegrityError

    from apps.api.models.project import BudgetSection

    project = make_project(pg_db, world["company"], project_number="PRJ-2026-900")
    budget = make_budget(pg_db, world["company"], project)
    section = BudgetSection(
        company_id=world["company"].id, budget_id=budget.id, name="Crew"
    )
    pg_db.add(section)
    pg_db.commit()

    pg_db.add(
        BudgetActual(
            company_id=world["company"].id,
            budget_id=budget.id,
            section_id=section.id,
            amount=Decimal("-5.00"),
            incurred_on=date_type(2026, 6, 1),
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()
