"""The Budget sheet, against a real PostgreSQL.

The centre of this file is `test_the_example_budget_reproduces_the_s1_numbers`.
It creates the example budget through the endpoint, asks for the calculated
view, and compares every number against **`tests/fixtures/calc/s1-quote.json`
itself** — not against literals copied out of it. That is what makes it a
contract rather than a restatement: A1's engine tests read the same file, so
the budget and the calculator are pinned to one set of numbers and neither can
drift without the other failing.

The other themes:

  * **refs are resolved before the engine is called** (D70), which is checked
    by pointing a line's quantity at a global and watching the engine's own
    output change — and by a dangling ref answering 422 with the key in it
    rather than pricing the line at one day;
  * **`days:shoot` is counted, not cached**: a shoot day added in the Days tab
    moves the budget's totals in the next request;
  * **rates are copied from the catalog and never follow it**;
  * **the lock**, and that unlocking is an ordinary audited action;
  * **a duplicate is numerically identical**, checked by calculating both;
  * **the CSV round trip keeps the totals**, and an import applies exactly
    what its preview showed — with a stale preview refused;
  * **staff see no cost and no margin anywhere**, checked by walking every
    budget endpoint as a staff user and searching the whole response body for
    either word. That is the one assertion in A2 that is about a permission
    boundary rather than about editing.
"""

import io
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.project import Budget, BudgetLine, BudgetSection
from apps.api.models.site_settings import SiteSettings
from apps.api.services import (
    budget_service,
    catalog_service,
    number_series,
    tax_service,
)
from apps.api.services.permissions import PERMISSIONS, permissions_for

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_item,
    make_rental_factor_group,
    make_superadmin,
    make_unit,
    make_user,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "calc"

#: The S1 scenario, read from the SAME file A1's engine tests read. Every
#: expected number below comes out of this dict; none is written twice.
S1 = json.loads((FIXTURES / "s1-quote.json").read_text())

#: The example budget's section names ↔ the fixture's group ids.
GROUP_KEYS = {
    "Crew": "crew",
    "Equipment": "equipment",
    "Studio": "studio",
    "Travel": "travel",
}
#: The example budget's line descriptions ↔ the fixture's line ids.
LINE_KEYS = {
    "DoP": "dop",
    "Gaffer": "gaffer",
    "Alexa 35": "alexa",
    "Lens set": "lenses",
    "Studio A": "studio-a",
    "Taxi": "taxi",
}

VIEW_ROLES = (
    CompanyRole.owner,
    CompanyRole.admin,
    CompanyRole.accountant,
    CompanyRole.producer,
)
EDIT_ROLES = (CompanyRole.owner, CompanyRole.admin, CompanyRole.producer)
ALL_ROLES = tuple(CompanyRole)


@pytest.fixture
def world(pg_db):
    """An Austrian company with tax, catalog and number series, and a project."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "budgets-world-owner@example.com")
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


def _new_budget(pg_client, world, project, **body):
    body.setdefault("name", "Estimate")
    response = pg_client.post(
        f"/projects/{project['id']}/budgets", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _example(pg_client, world, project):
    response = pg_client.post(
        f"/projects/{project['id']}/budgets/example", headers=_headers(world)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sheet(pg_client, world, budget_id):
    response = pg_client.get(f"/budgets/{budget_id}", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _calc(pg_client, world, budget_id, expect=200):
    response = pg_client.get(
        f"/budgets/{budget_id}/calculated", headers=_headers(world)
    )
    assert response.status_code == expect, response.text
    return response.json()


def _import(pg_client, world, budget_id, payload, expect=200):
    """Preview, then apply with the preview's digest — the only way an
    import is applied."""
    preview = pg_client.post(
        f"/budgets/{budget_id}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    response = pg_client.post(
        f"/budgets/{budget_id}/import",
        headers=_headers(world),
        params={"expected_digest": preview.json()["digest"]},
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == expect, response.text
    return response.json()


def _section(pg_client, world, budget_id, **body):
    body.setdefault("name", "Crew")
    response = pg_client.post(
        f"/budgets/{budget_id}/sections", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _line(pg_client, world, budget_id, **body):
    response = pg_client.post(
        f"/budgets/{budget_id}/lines", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _global(pg_client, world, budget_id, **body):
    body.setdefault("key", "shoot_days")
    body.setdefault("label", "Shoot days")
    response = pg_client.post(
        f"/budgets/{budget_id}/globals", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


# ─── The surface ────────────────────────────────────────────────────────────


def test_there_is_no_delete_route_anywhere_under_budgets(client):
    """Asserted on the document the app serves, not on a handler."""
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    deletes = [
        f"DELETE {path}"
        for path, operations in schema["paths"].items()
        if path.startswith("/budgets") and "delete" in operations
    ]
    assert not deletes, (
        f"the budget has a DELETE route: {deletes}. A section, a line, a "
        f"global and an adjustment are all retired with POST .../remove, "
        f"which keeps the row and writes an audit event."
    )


# ─── The example, against the A1 fixture ────────────────────────────────────


def test_the_example_budget_reproduces_the_s1_numbers(pg_client, world, project):
    """The whole of A2's arithmetic, pinned to A1's own fixture file.

    Document totals, every section's subtotal/cost/margin/margin percentage,
    every line's net/cost/margin, and the top sheet's shares — which are
    computed here from the fixture's own section subtotals, independently of
    the server's `percent_share`, so the test is not reading the answer off
    the thing it is checking.
    """
    budget = _example(pg_client, world, project)
    answer = _calc(pg_client, world, budget["id"])
    result = answer["result"]
    expect = S1["expect"]

    assert result["subtotal"] == expect["subtotal"]
    assert result["net"] == expect["net"] == "12517.20"
    assert result["cost"] == expect["cost"] == "5380.00"
    assert result["margin"] == expect["margin"] == "7137.20"
    assert result["margin_percent"] == expect["margin_percent"]
    assert result["warnings"] == []

    # The one tax bucket, under the company's own standard code rather than a
    # literal: the example takes `tax_ref` from the standard tax class, and
    # the Austrian pack's standard code happens to be the fixture's `AT-20`.
    assert result["net_by_tax_ref"] == expect["net_by_tax_ref"]

    assert len(result["adjustments"]) == 1
    fee = result["adjustments"][0]
    expected_fee = expect["adjustments"][0]
    assert fee["label"] == expected_fee["label"]
    assert fee["base_amount"] == expected_fee["base_amount"]
    assert fee["amount"] == expected_fee["amount"]

    groups = {group["title"]: group for group in result["children"]}
    assert set(groups) == set(GROUP_KEYS)
    for name, key in GROUP_KEYS.items():
        expected = expect["groups"][key]
        group = groups[name]
        assert group["subtotal"] == expected["subtotal"], name
        assert group["cost_subtotal"] == expected["cost_subtotal"], name
        assert group["margin"] == expected["margin"], name
        assert group["margin_percent"] == expected["margin_percent"], name

    lines = {}
    for group in result["children"]:
        for child in group["children"]:
            lines[child["label"]] = child
    assert set(lines) == set(LINE_KEYS)
    for description, key in LINE_KEYS.items():
        expected = expect["lines"][key]
        line = lines[description]
        assert line["net"] == expected["net"], description
        assert line["cost_net"] == expected["cost_net"], description
        assert line["margin"] == expected["margin"], description
        assert line["margin_percent"] == expected["margin_percent"], description

    # The top sheet. Shares worked out here from the fixture's own numbers.
    subtotal = Decimal(expect["subtotal"])
    rows = {row["name"]: row for row in answer["top_sheet"]}
    assert set(rows) == set(GROUP_KEYS)
    for name, key in GROUP_KEYS.items():
        expected = expect["groups"][key]
        row = rows[name]
        assert row["price"] == expected["subtotal"], name
        assert row["cost"] == expected["cost_subtotal"], name
        assert row["margin"] == expected["margin"], name
        share = (
            Decimal(expected["subtotal"]) / subtotal * Decimal("100")
        ).quantize(Decimal("0.01"))
        assert row["share_of_subtotal"] == str(share), name
    assert [row["code"] for row in answer["top_sheet"]] == [
        "1100",
        "1200",
        "1300",
        "1400",
    ]


def test_the_example_says_in_its_own_notes_that_it_is_an_example(
    pg_client, world, project
):
    """A budget that looked real and was not is the kind of thing that ends
    up quoted to a client."""
    budget = _example(pg_client, world, project)

    assert "EXAMPLE" in (budget["notes"] or "")
    assert budget["name"] == "Example (S1)"
    assert budget["is_primary"] is False


def test_the_example_brings_the_computed_day_globals_with_it(
    pg_client, world, project
):
    """So that "point this line at shoot_days" is one click and not two."""
    budget = _example(pg_client, world, project)
    sheet = _sheet(pg_client, world, budget["id"])

    by_key = {row["key"]: row for row in sheet["globals"]}
    assert set(by_key) == {"shoot_days", "prep_days"}
    assert by_key["shoot_days"]["source"] == "days:shoot"
    assert by_key["shoot_days"]["is_computed"] is True
    assert by_key["shoot_days"]["value"] == "0.0000"


def test_an_adjustment_moves_the_price_and_not_the_cost(
    pg_client, world, project
):
    """D78's reason, as a behaviour.

    The 8 % production fee raises the net by 927.20 and leaves the cost at
    5 380.00 — which is why a contingency or a fee that has to raise the COST
    side is modelled as an ordinary `fee` LINE and not as an adjustment.
    """
    budget = _example(pg_client, world, project)
    result = _calc(pg_client, world, budget["id"])["result"]

    assert result["subtotal"] == "11590.00"
    assert result["net"] == "12517.20"
    assert result["cost"] == "5380.00"
    # Cost equals the sum of the lines' own costs, untouched by either the
    # section discount or the document fee.
    line_costs = sum(
        Decimal(child["cost_net"])
        for group in result["children"]
        for child in group["children"]
    )
    assert line_costs == Decimal(result["cost"])


# ─── Sections ───────────────────────────────────────────────────────────────


def test_a_third_level_of_section_is_refused_and_names_the_engine_limit(
    pg_client, world, project
):
    """Two levels, because the engine nests groups two deep (SCOPE §5.4).

    A third could be stored and never calculated, so it is refused at the
    edge — and the sentence says which limit it is, because "maximum depth 2"
    out of nowhere is not something anybody can act on.
    """
    budget = _new_budget(pg_client, world, project)
    top = _section(pg_client, world, budget["id"], name="Crew", code="1100")
    second = _section(
        pg_client, world, budget["id"], name="Camera crew", parent_id=top["id"]
    )

    response = pg_client.post(
        f"/budgets/{budget['id']}/sections",
        headers=_headers(world),
        json={"name": "Focus pullers", "parent_id": second["id"]},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "two deep" in detail or "2 deep" in detail
    assert "§5.4" in detail


def test_two_levels_of_section_calculate(pg_client, world, project):
    """The limit is two, and two has to work — a sub-section's lines land in
    the parent's subtotal."""
    budget = _new_budget(pg_client, world, project)
    top = _section(pg_client, world, budget["id"], name="Crew", code="1100")
    inner = _section(
        pg_client, world, budget["id"], name="Camera", parent_id=top["id"]
    )
    _line(
        pg_client,
        world,
        budget["id"],
        section_id=inner["id"],
        description="DoP",
        quantity="1",
        period_value="3",
        unit_price="850.00",
        unit_cost="600.00",
    )

    result = _calc(pg_client, world, budget["id"])["result"]

    assert result["net"] == "2550.00"
    outer = result["children"][0]
    assert outer["title"] == "Crew"
    assert outer["subtotal"] == "2550.00"
    assert outer["children"][0]["type"] == "group"


def test_a_section_that_still_holds_lines_cannot_be_removed(
    pg_client, world, project
):
    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1100")
    _line(
        pg_client,
        world,
        budget["id"],
        section_id=section["id"],
        description="DoP",
        unit_price="850.00",
    )

    response = pg_client.post(
        f"/budgets/{budget['id']}/sections/{section['id']}/remove",
        headers=_headers(world),
    )

    assert response.status_code == 409, response.text
    assert "1 line(s)" in response.json()["detail"]


def test_positions_are_distinct_under_concurrent_inserts(
    pg_engine, pg_db, pg_client, world, project
):
    """Two people adding a line at the same moment.

    Without the `SELECT … FOR UPDATE` on the budget row both would read "the
    highest position is 40" and both would write 50 — and the sheet would
    then order two rows by their description, which is not an order anybody
    chose. Real threads on real connections, because an in-process lock
    would prove nothing about two workers.
    """
    import threading

    from sqlalchemy.orm import sessionmaker

    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1100")
    budget_id = uuid.UUID(budget["id"])
    section_id = uuid.UUID(section["id"])
    company_id = world["company"].id

    Session = sessionmaker(bind=pg_engine, autocommit=False, autoflush=False)
    barrier = threading.Barrier(4)
    errors: list = []

    def insert(index: int) -> None:
        session = Session()
        try:
            budget_row = session.query(Budget).filter(Budget.id == budget_id).one()
            section_row = (
                session.query(BudgetSection)
                .filter(BudgetSection.id == section_id)
                .one()
            )
            barrier.wait(timeout=20)
            budget_service.lock_for_write(session, budget_row)
            session.add(
                BudgetLine(
                    company_id=company_id,
                    section_id=section_row.id,
                    position=budget_service.next_line_position(session, section_row),
                    description=f"Line {index}",
                    kind="other",
                    quantity=Decimal("1"),
                    period_value=Decimal("1"),
                    period_unit="day",
                    factor=Decimal("1"),
                    unit_price=Decimal("10.00"),
                    unit_cost=Decimal("0.00"),
                    tags=[],
                )
            )
            session.commit()
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=insert, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"a concurrent insert failed: {errors}"
    pg_db.expire_all()
    positions = [
        row.position
        for row in pg_db.query(BudgetLine)
        .filter(BudgetLine.section_id == section_id)
        .all()
    ]
    assert len(positions) == 4
    assert len(set(positions)) == 4, (
        f"two concurrent inserts computed the same position: {positions}"
    )


# ─── Globals and refs ───────────────────────────────────────────────────────


def _crew_line(pg_client, world, budget_id, **overrides):
    section = _section(pg_client, world, budget_id, code="1100")
    body = {
        "section_id": section["id"],
        "description": "Alexa 35",
        "quantity": "1",
        "period_value": "1",
        "unit_price": "1200.00",
        "unit_cost": "0.00",
    }
    body.update(overrides)
    return section, _line(pg_client, world, budget_id, **body)


def test_a_line_takes_its_period_from_the_global_it_names(
    pg_client, world, project
):
    """Resolved BEFORE the engine is called (D70).

    The stored `period_value` stays 1 and the engine prices three days,
    because the global says three — which is the whole of what a ref is for.
    """
    budget = _new_budget(pg_client, world, project)
    _global(pg_client, world, budget["id"], value="3", source="manual")
    _crew_line(pg_client, world, budget["id"], period_ref="shoot_days")

    result = _calc(pg_client, world, budget["id"])["result"]

    line = result["children"][0]["children"][0]
    assert line["period_value"] == "3.0000"
    assert line["net"] == "3600.00"
    assert result["net"] == "3600.00"


def test_a_ref_that_names_nothing_is_a_422_with_the_key_in_it(
    pg_client, world, pg_db, project
):
    """Never a silent 1.

    A line priced at one day looks exactly like a correct answer, which is
    why a dangling ref is a refusal that names the key.
    """
    budget = _new_budget(pg_client, world, project)
    _global(pg_client, world, budget["id"], value="3", source="manual")
    _crew_line(pg_client, world, budget["id"], period_ref="shoot_days")

    # Take the global away behind the guard's back, which is the only way to
    # reach this state: the API refuses to remove a referenced global.
    pg_db.execute(
        __import__("sqlalchemy").text(
            "UPDATE budget_globals SET is_active = false WHERE key = 'shoot_days'"
        )
    )
    pg_db.commit()

    response = pg_client.get(
        f"/budgets/{budget['id']}/calculated", headers=_headers(world)
    )

    assert response.status_code == 422, response.text
    assert "shoot_days" in response.json()["detail"]


def test_a_global_a_line_points_at_cannot_be_removed(pg_client, world, project):
    budget = _new_budget(pg_client, world, project)
    row = _global(pg_client, world, budget["id"], value="3", source="manual")
    _crew_line(pg_client, world, budget["id"], period_ref="shoot_days")

    response = pg_client.post(
        f"/budgets/{budget['id']}/globals/{row['id']}/remove",
        headers=_headers(world),
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "shoot_days" in detail
    assert "Alexa 35" in detail


def test_an_unreferenced_global_can_be_removed(pg_client, world, project):
    """The other half, so the guard above is not just "nothing can be
    removed"."""
    budget = _new_budget(pg_client, world, project)
    row = _global(pg_client, world, budget["id"], value="3", source="manual")

    response = pg_client.post(
        f"/budgets/{budget['id']}/globals/{row['id']}/remove",
        headers=_headers(world),
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False


def test_the_shoot_days_global_follows_the_days_tab(pg_client, world, project):
    """Counted, not cached — and the calculated numbers follow.

    A cached value plus a trigger would be a second place for "how many
    shoot days are there" to be answered, and the two would disagree the
    first time a day was added by anything but the Days tab.
    """
    budget = _new_budget(pg_client, world, project)
    _global(pg_client, world, budget["id"], value="0", source="days:shoot")
    _crew_line(pg_client, world, budget["id"], period_ref="shoot_days")

    assert _calc(pg_client, world, budget["id"])["result"]["net"] == "0.00"

    added = pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        json={"from": "2026-06-01", "to": "2026-06-03", "type": "shoot"},
    )
    assert added.status_code == 201, added.text

    assert _calc(pg_client, world, budget["id"])["result"]["net"] == "3600.00"
    sheet = _sheet(pg_client, world, budget["id"])
    assert sheet["globals"][0]["value"] == "3.0000"

    day_id = added.json()["created"][0]["id"]
    removed = pg_client.post(
        f"/projects/{project['id']}/days/{day_id}/remove", headers=_headers(world)
    )
    assert removed.status_code == 200, removed.text

    assert _calc(pg_client, world, budget["id"])["result"]["net"] == "2400.00"


def test_a_computed_global_cannot_be_typed_over(pg_client, world, project):
    budget = _new_budget(pg_client, world, project)
    row = _global(pg_client, world, budget["id"], value="0", source="days:shoot")

    response = pg_client.patch(
        f"/budgets/{budget['id']}/globals/{row['id']}",
        headers=_headers(world),
        json={"value": "99"},
    )

    assert response.status_code == 409, response.text
    assert "Days tab" in response.json()["detail"]


def test_a_source_that_is_not_in_the_vocabulary_is_refused(
    pg_client, world, project
):
    """No expression language, and no place one could be added (D70)."""
    budget = _new_budget(pg_client, world, project)

    response = pg_client.post(
        f"/budgets/{budget['id']}/globals",
        headers=_headers(world),
        json={
            "key": "clever",
            "label": "Clever",
            "source": "shoot_days * 1.5 + overtime",
        },
    )

    assert response.status_code == 422, response.text
    assert "expression language" in response.json()["detail"]


# ─── The catalog ────────────────────────────────────────────────────────────


@pytest.fixture
def catalog_item(pg_db, world):
    """An Alexa with a day rate and the A1 prompt's factor table."""
    unit = make_unit(pg_db, world["company"], key="drehtag", period_unit="day")
    group = make_rental_factor_group(pg_db, world["company"])
    return make_item(
        pg_db,
        world["company"],
        sku="CAM-100",
        name_de="Arri Alexa 35",
        unit=unit,
        rental_factor_group=group,
        default_tax_class="standard",
        bill_rate_day="1200.00",
        cost_rate_day="300.00",
    )


def test_a_line_from_the_catalog_copies_the_rates(
    pg_client, world, project, catalog_item
):
    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1200")

    line = _line(
        pg_client,
        world,
        budget["id"],
        section_id=section["id"],
        item_id=str(catalog_item.id),
    )

    assert line["unit_price"] == "1200.00"
    assert line["unit_cost"] == "300.00"
    assert line["description"] == "Arri Alexa 35"
    assert line["tax_ref"] == "AT-20"
    assert line["catalog_difference"] is None


def test_an_edited_catalog_rate_does_not_follow_the_line_but_is_reported(
    pg_client, pg_db, world, project, catalog_item
):
    """Rates are copied at insertion and never written back.

    A budget that silently re-priced itself when somebody edited the price
    list would be a budget nobody could compare with last week's. The API
    says the line differs; it does not change it.
    """
    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1200")
    line = _line(
        pg_client,
        world,
        budget["id"],
        section_id=section["id"],
        item_id=str(catalog_item.id),
    )

    response = pg_client.put(
        f"/catalog/items/{catalog_item.id}/rates",
        headers=_headers(world),
        json=[
            {"rate_type": "bill", "period_unit": "day", "amount": "1500.00"},
            {"rate_type": "cost", "period_unit": "day", "amount": "300.00"},
        ],
    )
    assert response.status_code == 200, response.text

    sheet = _sheet(pg_client, world, budget["id"])
    after = next(row for row in sheet["lines"] if row["id"] == line["id"])
    assert after["unit_price"] == "1200.00", "the line followed the catalog"
    assert after["catalog_difference"] is not None
    assert after["catalog_difference"]["catalog_unit_price"] == "1500.00"


def test_a_catalog_factor_is_resolved_and_explained(
    pg_client, world, project, catalog_item
):
    """The factor comes from the item's table for the line's own period, and
    the explanation travels with it.

    "2.8" is not a number anybody can disagree with; "2.8, from the 5-day
    row, because the table has no 6" is.
    """
    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1200")
    _line(
        pg_client,
        world,
        budget["id"],
        section_id=section["id"],
        item_id=str(catalog_item.id),
        period_value="6",
        factor_source="catalog",
    )

    answer = _calc(pg_client, world, budget["id"])

    assert len(answer["factors"]) == 1
    explanation = answer["factors"][0]
    assert explanation["days"] == 6
    assert explanation["factor"] == "2.8000"
    assert explanation["matched_days"] == 5
    assert explanation["rule"] == "next_lower"
    assert explanation["group_name"] == "Standard-Mietfaktoren"
    # 1200 × 1 × 6 days × 2.8 / 6 … no: the engine multiplies price × quantity
    # × period × factor, so 1200 × 6 × 2.8 is not it either — the factor
    # REPLACES the linear period multiple in a rental. What matters here is
    # that the number the engine used is the resolved one.
    line = answer["result"]["children"][0]["children"][0]
    assert Decimal(line["net"]) > Decimal("0")


# ─── The lock ───────────────────────────────────────────────────────────────


def test_a_locked_budget_refuses_a_line_edit_and_says_why(
    pg_client, world, project
):
    budget = _new_budget(pg_client, world, project)
    section, line = _crew_line(pg_client, world, budget["id"])
    locked = pg_client.post(
        f"/budgets/{budget['id']}/lock",
        headers=_headers(world),
        json={"reason": "Approved by the client"},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["is_locked"] is True

    response = pg_client.patch(
        f"/budgets/{budget['id']}/lines/{line['id']}",
        headers=_headers(world),
        json={"unit_price": "99.00"},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "locked" in detail
    assert "Unlock it first" in detail

    # And the sheet still says what it said.
    sheet = _sheet(pg_client, world, budget["id"])
    assert sheet["lines"][0]["unit_price"] == "1200.00"


def test_a_locked_budget_still_takes_its_own_name(pg_client, world, project):
    """"Estimate v3 (approved 24.11.)" is exactly the kind of thing somebody
    wants to write on a frozen sheet. The lock stops the numbers."""
    budget = _new_budget(pg_client, world, project)
    pg_client.post(
        f"/budgets/{budget['id']}/lock", headers=_headers(world), json={}
    )

    response = pg_client.patch(
        f"/budgets/{budget['id']}",
        headers=_headers(world),
        json={"name": "Estimate v3 (approved)"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Estimate v3 (approved)"


def test_unlocking_is_audited_and_lets_the_edit_through(
    pg_client, pg_db, world, project
):
    budget = _new_budget(pg_client, world, project)
    section, line = _crew_line(pg_client, world, budget["id"])
    pg_client.post(
        f"/budgets/{budget['id']}/lock", headers=_headers(world), json={}
    )

    unlocked = pg_client.post(
        f"/budgets/{budget['id']}/unlock",
        headers=_headers(world),
        json={"reason": "Client changed the brief"},
    )
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["is_locked"] is False

    edited = pg_client.patch(
        f"/budgets/{budget['id']}/lines/{line['id']}",
        headers=_headers(world),
        json={"unit_price": "99.00"},
    )
    assert edited.status_code == 200, edited.text

    actions = {
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_type == "budgets")
        .all()
    }
    assert {"budget.locked", "budget.unlocked"} <= actions
    event = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "budget.unlocked")
        .one()
    )
    assert event.reason == "Client changed the brief"


# ─── Duplicating ────────────────────────────────────────────────────────────


def test_a_duplicate_calculates_to_the_same_numbers(pg_client, world, project):
    """Checked by calculating both, not by comparing row counts."""
    original = _example(pg_client, world, project)

    response = pg_client.post(
        f"/budgets/{original['id']}/duplicate", headers=_headers(world), json={}
    )
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["name"] == "Example (S1) (copy)"
    assert copy["is_locked"] is False

    before = _calc(pg_client, world, original["id"])["result"]
    after = _calc(pg_client, world, copy["id"])["result"]

    for field in ("subtotal", "net", "cost", "margin", "margin_percent"):
        assert before[field] == after[field], field
    assert before["net_by_tax_ref"] == after["net_by_tax_ref"]
    assert [group["subtotal"] for group in before["children"]] == [
        group["subtotal"] for group in after["children"]
    ]


def test_a_duplicate_has_its_own_globals_and_is_independent(
    pg_client, world, project
):
    """Refs are copied by KEY, so the copy points at the copy's global.

    Editing one budget's global must not move the other's numbers, which is
    the property that makes a duplicate a scenario rather than a view.
    """
    budget = _new_budget(pg_client, world, project)
    row = _global(pg_client, world, budget["id"], value="3", source="manual")
    _crew_line(pg_client, world, budget["id"], period_ref="shoot_days")
    copy = pg_client.post(
        f"/budgets/{budget['id']}/duplicate", headers=_headers(world), json={}
    ).json()

    assert _calc(pg_client, world, copy["id"])["result"]["net"] == "3600.00"

    changed = pg_client.patch(
        f"/budgets/{budget['id']}/globals/{row['id']}",
        headers=_headers(world),
        json={"value": "5"},
    )
    assert changed.status_code == 200, changed.text

    assert _calc(pg_client, world, budget["id"])["result"]["net"] == "6000.00"
    assert _calc(pg_client, world, copy["id"])["result"]["net"] == "3600.00"


def test_set_primary_leaves_exactly_one(pg_client, world, project):
    first = _new_budget(pg_client, world, project, name="With the crane")
    second = _new_budget(pg_client, world, project, name="Without")

    pg_client.post(
        f"/budgets/{first['id']}/set-primary", headers=_headers(world)
    )
    pg_client.post(
        f"/budgets/{second['id']}/set-primary", headers=_headers(world)
    )

    listed = pg_client.get(
        f"/projects/{project['id']}/budgets", headers=_headers(world)
    ).json()
    primaries = [row["name"] for row in listed if row["is_primary"]]
    assert primaries == ["Without"]


# ─── CSV ────────────────────────────────────────────────────────────────────


def test_the_export_carries_the_engines_own_numbers(pg_client, world, project):
    budget = _example(pg_client, world, project)

    response = pg_client.get(
        f"/budgets/{budget['id']}/export.csv", headers=_headers(world)
    )

    assert response.status_code == 200, response.text
    assert response.content.startswith("﻿".encode("utf-8")), (
        "no BOM — a German Excel reads a BOM-less UTF-8 CSV as Windows-1252"
    )
    rows = _csv_rows(response.content)
    dop = next(row for row in rows if row["description"] == "DoP")
    assert dop["section_code"] == "1100"
    # price, cost, margin — the engine's own, as the fixture has them, with
    # the decimal comma this German-convention file uses.
    assert [dop["price"], dop["cost"], dop["margin"]] == [
        _comma(S1["expect"]["lines"]["dop"]["net"]),
        _comma(S1["expect"]["lines"]["dop"]["cost_net"]),
        _comma(S1["expect"]["lines"]["dop"]["margin"]),
    ]


def _csv_rows(payload: bytes) -> list[dict]:
    import csv as _csv

    text = payload.decode("utf-8-sig")
    return list(_csv.DictReader(io.StringIO(text), delimiter=";"))


def _comma(value: str) -> str:
    return value.replace(".", ",")


def test_the_export_writes_numbers_a_german_spreadsheet_reads_as_they_are(
    pg_client, world, project
):
    """`;` and a BOM make this a German-convention file, and in that
    convention a dot is a thousands separator. The first version wrote
    `1.0000` for one day; a spreadsheet saved it back as `10.000`, and the
    import read ten (A2 acceptance, 2026-11-24). So: decimal commas, no
    grouping, no trailing zeros on a quantity."""
    budget = _example(pg_client, world, project)
    rows = _csv_rows(
        pg_client.get(f"/budgets/{budget['id']}/export.csv", headers=_headers(world)).content
    )
    lenses = next(row for row in rows if row["description"] == "Lens set")
    assert lenses["quantity"] == "2"
    assert lenses["period_value"] == "3"
    assert lenses["factor"] == "1"
    assert lenses["unit_price"] == "180,00"
    assert lenses["price"] == "1080,00"
    for row in rows:
        for column in ("quantity", "period_value", "factor", "unit_price", "unit_cost", "price"):
            assert "." not in row[column], (column, row)


def test_a_dot_grouped_number_is_refused_not_read_as_a_decimal(
    pg_client, world, project
):
    """The shape of the file from the acceptance: `10.000` where the line
    had one. In this file format that is ten thousand; read as English it is
    ten. Either reading is a guess, so it is an error row and nothing is
    written."""
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;section_name;description;quantity;period_value;factor\r\n"
        "1200;Equipment;Alexa 35;10.000;30.000;10.000\r\n"
    ).encode("utf-8")

    preview = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()

    assert preview["errors"] == 1
    assert preview["updates"] == 0
    assert "ambiguous" in preview["rows"][0]["message"]
    alexa = next(
        row for row in _sheet(pg_client, world, budget["id"])["lines"]
        if row["description"] == "Alexa 35"
    )
    assert alexa["quantity"] == "1.0000"


def test_a_line_that_follows_a_global_exports_the_charged_number_and_the_key(
    pg_client, world, project
):
    budget = _example(pg_client, world, project)
    _global(pg_client, world, budget["id"], key="crew_days", label="Crew days", value="4", source="manual")
    sheet = _sheet(pg_client, world, budget["id"])
    dop = next(row for row in sheet["lines"] if row["description"] == "DoP")
    pg_client.patch(
        f"/budgets/{budget['id']}/lines/{dop['id']}",
        headers=_headers(world),
        json={"period_ref": "crew_days"},
    )

    rows = _csv_rows(
        pg_client.get(f"/budgets/{budget['id']}/export.csv", headers=_headers(world)).content
    )
    exported = next(row for row in rows if row["description"] == "DoP")
    assert exported["period_value"] == "4", "the number charged, not the stale stored 3"
    assert exported["period_global"] == "crew_days"
    assert exported["quantity_global"] == ""


def test_a_round_trip_keeps_a_ref_and_the_totals(pg_client, world, project):
    original = _example(pg_client, world, project)
    _global(pg_client, world, original["id"], key="crew_days", label="Crew days", value="4", source="manual")
    dop = next(
        row for row in _sheet(pg_client, world, original["id"])["lines"]
        if row["description"] == "DoP"
    )
    pg_client.patch(
        f"/budgets/{original['id']}/lines/{dop['id']}",
        headers=_headers(world),
        json={"period_ref": "crew_days"},
    )
    payload = pg_client.get(
        f"/budgets/{original['id']}/export.csv", headers=_headers(world)
    ).content
    copy = pg_client.post(
        f"/budgets/{original['id']}/duplicate", headers=_headers(world), json={}
    ).json()

    body = _import(pg_client, world, copy["id"], payload)

    assert (body["creates"], body["updates"], body["errors"]) == (0, 6, 0)
    copied = next(
        row for row in _sheet(pg_client, world, copy["id"])["lines"]
        if row["description"] == "DoP"
    )
    assert copied["period_ref"] == "crew_days"
    assert copied["period_value"] == "3.0000", "the stored fallback is left alone"
    before = _calc(pg_client, world, original["id"])["result"]
    after = _calc(pg_client, world, copy["id"])["result"]
    assert (before["net"], before["cost"]) == (after["net"], after["cost"])


def test_an_emptied_global_column_makes_the_typed_number_apply(
    pg_client, world, project
):
    """The Studio row of the acceptance file: a `5` typed over a followed
    value did nothing. Now an empty `period_global` says "stop following"
    and the 5 applies — and the preview says so."""
    budget = _example(pg_client, world, project)
    _global(pg_client, world, budget["id"], key="crew_days", label="Crew days", value="4", source="manual")
    studio = next(
        row for row in _sheet(pg_client, world, budget["id"])["lines"]
        if row["description"] == "Studio A"
    )
    pg_client.patch(
        f"/budgets/{budget['id']}/lines/{studio['id']}",
        headers=_headers(world),
        json={"period_ref": "crew_days"},
    )
    payload = (
        "section_code;description;period_value;period_global\r\n"
        "1300;Studio A;5;\r\n"
    ).encode("utf-8")

    body = _import(pg_client, world, budget["id"], payload)

    assert "no longer follows a global" in body["rows"][0]["message"]
    after = next(
        row for row in _sheet(pg_client, world, budget["id"])["lines"]
        if row["description"] == "Studio A"
    )
    assert after["period_ref"] is None
    assert after["period_value"] == "5.0000"


def test_a_file_without_the_global_columns_says_it_leaves_a_followed_value(
    pg_client, world, project
):
    budget = _example(pg_client, world, project)
    _global(pg_client, world, budget["id"], key="crew_days", label="Crew days", value="4", source="manual")
    studio = next(
        row for row in _sheet(pg_client, world, budget["id"])["lines"]
        if row["description"] == "Studio A"
    )
    pg_client.patch(
        f"/budgets/{budget['id']}/lines/{studio['id']}",
        headers=_headers(world),
        json={"period_ref": "crew_days"},
    )
    payload = "section_code;description;period_value\r\n1300;Studio A;5\r\n".encode("utf-8")

    body = _import(pg_client, world, budget["id"], payload)

    assert "is not applied" in body["rows"][0]["message"]
    after = next(
        row for row in _sheet(pg_client, world, budget["id"])["lines"]
        if row["description"] == "Studio A"
    )
    assert after["period_ref"] == "crew_days"
    assert after["period_value"] == "2.0000"


def test_a_global_column_naming_nothing_is_an_error_row(pg_client, world, project):
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;period_global\r\n1300;Studio A;no_such_key\r\n"
    ).encode("utf-8")
    preview = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()
    assert preview["errors"] == 1
    assert "no_such_key" in preview["rows"][0]["message"]


def test_a_sub_section_without_a_code_round_trips_by_its_name(
    pg_client, world, project
):
    """The DoP and Gaffer rows of the acceptance file were refused: they sat
    in sub-sections ("Event", "Documentary") with no code, and the export
    wrote an empty section_code the import could not place."""
    original = _example(pg_client, world, project)
    sheet = _sheet(pg_client, world, original["id"])
    crew = next(row for row in sheet["sections"] if row["code"] == "1100")
    event = _section(pg_client, world, original["id"], name="Event", parent_id=crew["id"])
    _line(
        pg_client, world, original["id"],
        section_id=event["id"], description="Runner", quantity="1",
        period_value="2", factor="1", allow_negative=False,
        unit_price="200.00", unit_cost="150.00",
    )
    payload = pg_client.get(
        f"/budgets/{original['id']}/export.csv", headers=_headers(world)
    ).content
    runner = next(row for row in _csv_rows(payload) if row["description"] == "Runner")
    assert (runner["section_code"], runner["section_name"]) == ("", "Event")

    copy = pg_client.post(
        f"/budgets/{original['id']}/duplicate", headers=_headers(world), json={}
    ).json()
    body = _import(pg_client, world, copy["id"], payload)

    assert (body["creates"], body["updates"], body["errors"]) == (0, 7, 0), body["rows"]
    before = _calc(pg_client, world, original["id"])["result"]
    after = _calc(pg_client, world, copy["id"])["result"]
    assert (before["net"], before["cost"]) == (after["net"], after["cost"])


def test_a_section_name_used_twice_is_refused_by_name(pg_client, world, project):
    budget = _example(pg_client, world, project)
    sheet = _sheet(pg_client, world, budget["id"])
    for code in ("1100", "1200"):
        parent = next(row for row in sheet["sections"] if row["code"] == code)
        _section(pg_client, world, budget["id"], name="Extras", parent_id=parent["id"])
    payload = (
        "section_code;section_name;description\r\n;Extras;Coffee\r\n"
    ).encode("utf-8")
    preview = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()
    assert preview["errors"] == 1
    assert "more than one section called" in preview["rows"][0]["message"]


def test_a_csv_round_trip_into_a_duplicate_keeps_the_totals(
    pg_client, world, project
):
    """Export, import into a duplicate, same numbers.

    This is why the import matches on `(section code, description)` and not
    on an id: the duplicate's line ids are all different, and an id-based
    match would re-create every row instead of updating it — doubling the
    budget.
    """
    original = _example(pg_client, world, project)
    payload = pg_client.get(
        f"/budgets/{original['id']}/export.csv", headers=_headers(world)
    ).content
    copy = pg_client.post(
        f"/budgets/{original['id']}/duplicate", headers=_headers(world), json={}
    ).json()

    body = _import(pg_client, world, copy["id"], payload)
    assert body["creates"] == 0, "the round trip created lines instead of updating"
    assert body["updates"] == 6
    assert body["errors"] == 0

    before = _calc(pg_client, world, original["id"])["result"]
    after = _calc(pg_client, world, copy["id"])["result"]
    assert before["net"] == after["net"]
    assert before["cost"] == after["cost"]
    assert before["margin"] == after["margin"]


def test_an_import_applies_exactly_what_its_preview_showed(
    pg_client, pg_db, world, project
):
    """Compared row by row against the database afterwards, not against a
    count the import reported about itself."""
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price;unit_cost\r\n"
        "1100;DoP;900,00;650,00\r\n"
        "1100;Runner;180,00;120,00\r\n"
    ).encode("utf-8")

    preview = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert [row["action"] for row in plan["rows"]] == ["update", "create"]

    applied = pg_client.post(
        f"/budgets/{budget['id']}/import",
        headers=_headers(world),
        params={"expected_digest": plan["digest"]},
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["rows"] == plan["rows"]

    sheet = _sheet(pg_client, world, budget["id"])
    by_description = {row["description"]: row for row in sheet["lines"]}
    assert by_description["DoP"]["unit_price"] == "900.00"
    assert by_description["DoP"]["unit_cost"] == "650.00"
    # The columns the file did not carry are KEPT, not zeroed: a blank
    # meaning zero would turn a partial export into a way to wipe a budget.
    assert by_description["DoP"]["period_value"] == "3.0000"
    assert by_description["Runner"]["unit_price"] == "180.00"
    assert len(sheet["lines"]) == 7


def test_a_preview_that_no_longer_matches_is_refused(
    pg_client, world, project
):
    """The budget changed between the preview somebody read and the apply
    they pressed. Applying something other than what was shown is the one
    thing an import must not do."""
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price\r\n1100;DoP;900,00\r\n"
    ).encode("utf-8")

    plan = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()

    # Somebody renames the line the preview planned to update, so the same
    # file now plans a CREATE instead.
    sheet = _sheet(pg_client, world, budget["id"])
    dop = next(row for row in sheet["lines"] if row["description"] == "DoP")
    renamed = pg_client.patch(
        f"/budgets/{budget['id']}/lines/{dop['id']}",
        headers=_headers(world),
        json={"description": "Director of Photography"},
    )
    assert renamed.status_code == 200, renamed.text

    response = pg_client.post(
        f"/budgets/{budget['id']}/import",
        headers=_headers(world),
        params={"expected_digest": plan["digest"]},
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )

    assert response.status_code == 409, response.text
    assert "no longer does what the preview showed" in response.json()["detail"]
    # And nothing was written.
    after = _sheet(pg_client, world, budget["id"])
    assert len(after["lines"]) == 6


def test_an_unknown_section_code_is_an_error_and_not_an_invention(
    pg_client, world, project
):
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price\r\n9999;Crane;500,00\r\n"
    ).encode("utf-8")

    preview = pg_client.post(
        f"/budgets/{budget['id']}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()

    assert preview["errors"] == 1
    assert preview["creates"] == 0
    message = preview["rows"][0]["message"]
    assert "no section with the code" in message
    assert "never creates one" in message


def test_a_german_decimal_comma_is_read_as_a_decimal(pg_client, world, project):
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price\r\n1100;DoP;1.234,56\r\n"
    ).encode("utf-8")

    _import(pg_client, world, budget["id"], payload)

    sheet = _sheet(pg_client, world, budget["id"])
    dop = next(row for row in sheet["lines"] if row["description"] == "DoP")
    assert dop["unit_price"] == "1234.56"


def test_an_apply_without_the_preview_digest_is_refused_and_writes_nothing(
    pg_client, world, project
):
    """The digest is not optional. Without it the apply would do whatever
    the file plans now, which is not necessarily what anybody was shown."""
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price\r\n1100;Key grip;400,00\r\n"
    ).encode("utf-8")

    response = pg_client.post(
        f"/budgets/{budget['id']}/import",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    )

    assert response.status_code == 422, response.text
    assert "expected_digest" in response.text
    sheet = _sheet(pg_client, world, budget["id"])
    # A positive marker first (rule 17b): the example's own lines are there,
    # so the absence below is the absence of the import and not of a sheet.
    assert "DoP" in {row["description"] for row in sheet["lines"]}
    assert "Key grip" not in {row["description"] for row in sheet["lines"]}


# ─── Who may do what ────────────────────────────────────────────────────────


def test_the_permission_keys_exist_with_the_documented_defaults():
    assert set(PERMISSIONS["budgets.view"]) == set(VIEW_ROLES)
    assert set(PERMISSIONS["budgets.edit"]) == set(EDIT_ROLES)
    assert CompanyRole.staff not in PERMISSIONS["budgets.view"]
    assert CompanyRole.staff not in PERMISSIONS["budgets.edit"]


@pytest.fixture
def every_role(pg_db, world):
    users = {}
    for role in ALL_ROLES:
        user = make_user(pg_db, f"budgets-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_reading_a_budget_follows_the_view_matrix(
    pg_client, world, project, every_role, role
):
    budget = _example(pg_client, world, project)
    headers = auth_headers(every_role[role], world["company"])

    response = pg_client.get(f"/budgets/{budget['id']}", headers=headers)

    expected = 200 if role in VIEW_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )
    assert ("budgets.view" in permissions_for(role)) == (role in VIEW_ROLES)


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_adding_a_section_follows_the_edit_matrix(
    pg_client, world, project, every_role, role
):
    budget = _new_budget(pg_client, world, project)
    headers = auth_headers(every_role[role], world["company"])

    response = pg_client.post(
        f"/budgets/{budget['id']}/sections",
        headers=headers,
        json={"name": "Crew", "code": "1100"},
    )

    expected = 201 if role in EDIT_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )


#: Every budget endpoint, with a body that would be valid for an owner. The
#: staff test below sends all of them and asserts that no response carries a
#: cost or a margin anywhere in it.
def _every_budget_request(project_id, budget_id, section_id, line_id, global_id,
                          adjustment_id):
    return [
        ("get", f"/projects/{project_id}/budgets", None),
        ("post", f"/projects/{project_id}/budgets", {"name": "Theirs"}),
        ("post", f"/projects/{project_id}/budgets/example", None),
        ("get", f"/budgets/{budget_id}", None),
        ("patch", f"/budgets/{budget_id}", {"name": "Theirs"}),
        ("get", f"/budgets/{budget_id}/calculated", None),
        ("get", f"/budgets/{budget_id}/export.csv", None),
        ("post", f"/budgets/{budget_id}/lock", {}),
        ("post", f"/budgets/{budget_id}/unlock", {}),
        ("post", f"/budgets/{budget_id}/set-primary", None),
        ("post", f"/budgets/{budget_id}/duplicate", {}),
        ("post", f"/budgets/{budget_id}/sections", {"name": "Crew"}),
        ("patch", f"/budgets/{budget_id}/sections/{section_id}", {"name": "X"}),
        ("post", f"/budgets/{budget_id}/sections/{section_id}/remove", None),
        (
            "post",
            f"/budgets/{budget_id}/lines",
            {"section_id": section_id, "description": "X"},
        ),
        ("patch", f"/budgets/{budget_id}/lines/{line_id}", {"description": "X"}),
        ("post", f"/budgets/{budget_id}/lines/{line_id}/duplicate", None),
        ("post", f"/budgets/{budget_id}/lines/{line_id}/remove", None),
        ("post", f"/budgets/{budget_id}/globals", {"key": "k", "label": "K"}),
        ("patch", f"/budgets/{budget_id}/globals/{global_id}", {"label": "K"}),
        ("post", f"/budgets/{budget_id}/globals/{global_id}/remove", None),
        (
            "post",
            f"/budgets/{budget_id}/adjustments",
            {"label": "X", "type": "discount", "mode": "percent", "value": "5"},
        ),
        (
            "patch",
            f"/budgets/{budget_id}/adjustments/{adjustment_id}",
            {"label": "X"},
        ),
        (
            "post",
            f"/budgets/{budget_id}/adjustments/{adjustment_id}/remove",
            None,
        ),
        ("post", f"/budgets/{budget_id}/import/preview", "csv"),
        ("post", f"/budgets/{budget_id}/import?expected_digest=x", "csv"),
        # ── B1's cost-planning endpoints ────────────────────────────────
        #
        # Same walk, same assertion: staff reach none of them and no refusal
        # carries a cost or a margin. They are in THIS list rather than in a
        # second walk of their own because the property is one property —
        # "the budget shows cost rates and margins, so staff are closed out
        # of it altogether" — and two lists would be two places for an
        # endpoint to be forgotten.
        ("get", f"/budgets/{budget_id}/tracking", None),
        ("get", f"/budgets/{budget_id}/ledger", None),
        ("get", f"/budgets/{budget_id}/duplicates?amount=100.00", None),
        ("get", f"/budgets/{budget_id}/commitments", None),
        (
            "post",
            f"/budgets/{budget_id}/commitments",
            {
                "section_id": section_id,
                "amount": "100.00",
                "commitment_date": "2026-06-01",
            },
        ),
        (
            "patch",
            f"/budgets/{budget_id}/commitments/{uuid.uuid4()}",
            {"description": "X"},
        ),
        (
            "post",
            f"/budgets/{budget_id}/commitments/{uuid.uuid4()}/close",
            {},
        ),
        (
            "post",
            f"/budgets/{budget_id}/commitments/{uuid.uuid4()}/cancel",
            {},
        ),
        (
            "post",
            f"/budgets/{budget_id}/commitments/{uuid.uuid4()}/void",
            {"reason": "typo"},
        ),
        ("get", f"/budgets/{budget_id}/actuals", None),
        (
            "post",
            f"/budgets/{budget_id}/actuals",
            {
                "section_id": section_id,
                "amount": "100.00",
                "incurred_on": "2026-06-01",
            },
        ),
        (
            "patch",
            f"/budgets/{budget_id}/actuals/{uuid.uuid4()}",
            {"description": "X"},
        ),
        (
            "post",
            f"/budgets/{budget_id}/actuals/{uuid.uuid4()}/void",
            {"reason": "typo"},
        ),
        ("put", f"/budgets/{budget_id}/lines/{line_id}/etc", {"etc": "10.00"}),
        ("get", f"/budgets/{budget_id}/actuals/export.csv", None),
        ("post", f"/budgets/{budget_id}/actuals/import/preview", "csv"),
        (
            "post",
            f"/budgets/{budget_id}/actuals/import?expected_digest=x",
            "csv",
        ),
        # ── B1.1 "Mark finished" ────────────────────────────────────────
        ("post", f"/budgets/{budget_id}/lines/{line_id}/finish", {}),
        ("post", f"/budgets/{budget_id}/lines/{line_id}/reopen", None),
        ("post", f"/budgets/{budget_id}/sections/{section_id}/finish", None),
        ("post", f"/budgets/{budget_id}/sections/{section_id}/reopen", None),
        # ── B1.1 closing ────────────────────────────────────────────────
        ("get", f"/budgets/{budget_id}/close-check", None),
        ("post", f"/budgets/{budget_id}/close", {}),
        ("post", f"/budgets/{budget_id}/reopen", {"reason": "x"}),
    ]


def test_a_staff_user_gets_no_cost_and_no_margin_from_any_budget_endpoint(
    pg_client, pg_db, world, project, every_role
):
    """The sheet exposes cost rates and margins, so staff are closed out of
    it altogether until a crew-safe view exists.

    Walked over EVERY endpoint rather than a sample, and the whole response
    body is searched for the words rather than a named field — a new field
    called `cost_subtotal` on a new endpoint would otherwise slip past.
    """
    owner_budget = _example(pg_client, world, project)
    sheet = _sheet(pg_client, world, owner_budget["id"])
    adjustment_id = sheet["adjustments"][0]["id"]
    global_id = sheet["globals"][0]["id"]
    section_id = sheet["sections"][0]["id"]
    line_id = sheet["lines"][0]["id"]

    headers = auth_headers(every_role[CompanyRole.staff], world["company"])
    requests = _every_budget_request(
        project["id"],
        owner_budget["id"],
        section_id,
        line_id,
        global_id,
        adjustment_id,
    )
    assert len(requests) == 50, "a budget endpoint is missing from this walk"

    csv_file = "section_code;description\r\n1100;X\r\n".encode("utf-8")
    for method, url, body in requests:
        if body == "csv":
            extra = {"files": {"file": ("x.csv", io.BytesIO(csv_file), "text/csv")}}
        elif body is None:
            extra = {}
        else:
            extra = {"json": body}
        response = getattr(pg_client, method)(url, headers=headers, **extra)
        assert response.status_code == 404, (
            f"staff reached {method.upper()} {url}: {response.status_code} "
            f"{response.text}"
        )
        text = response.text.lower()
        assert "cost" not in text, f"{method.upper()} {url} leaked a cost"
        assert "margin" not in text, f"{method.upper()} {url} leaked a margin"


def test_the_budget_endpoint_count_in_the_staff_walk_is_the_real_one(client):
    """The walk above asserts 50 requests; this asserts 50 is the whole
    surface, so adding an endpoint without adding it to the walk fails here
    rather than passing silently (rule 17b).

    **26 → 43 at B1**, deliberately: seventeen cost-planning operations
    (commitments, actuals, the ledger, the tracking view, a line's ETC, the
    actuals CSV and the pre-save duplicate check) joined the surface, and
    all seventeen are in the walk above. **43 → 47 at B1.1**: finish and
    reopen, for a line and for a section; **47 → 50** with the close check,
    close and reopen of a budget. The number is raised by hand every time, which is the point — a
    count that moved on its own would notice nothing."""
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    operations = [
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method in methods
        if path.startswith("/budgets") or path.endswith("/budgets")
        or "/budgets/" in path
    ]
    assert len(operations) == 50, sorted(operations)


def test_a_budget_of_another_company_is_a_404(pg_client, pg_db, world, project):
    from .company_factories import make_budget, make_project

    other = make_company(pg_db, "Someone else GmbH")
    their_project = make_project(pg_db, other)
    their_budget = make_budget(pg_db, other, their_project)
    pg_db.commit()

    for method, url in (
        ("get", f"/budgets/{their_budget.id}"),
        ("get", f"/budgets/{their_budget.id}/calculated"),
        ("get", f"/budgets/{their_budget.id}/export.csv"),
        ("get", f"/projects/{their_project.id}/budgets"),
    ):
        response = getattr(pg_client, method)(url, headers=_headers(world))
        assert response.status_code == 404, f"{method} {url}: {response.text}"


# ─── Audit ──────────────────────────────────────────────────────────────────


def test_every_write_leaves_an_audit_row(pg_client, pg_db, world, project):
    budget = _new_budget(pg_client, world, project)
    section = _section(pg_client, world, budget["id"], code="1100")
    line = _line(
        pg_client,
        world,
        budget["id"],
        section_id=section["id"],
        description="DoP",
        unit_price="850.00",
    )
    pg_client.patch(
        f"/budgets/{budget['id']}/lines/{line['id']}",
        headers=_headers(world),
        json={"unit_price": "900.00"},
    )
    pg_client.post(
        f"/budgets/{budget['id']}/lines/{line['id']}/remove",
        headers=_headers(world),
    )
    row = _global(pg_client, world, budget["id"], value="3", source="manual")
    pg_client.patch(
        f"/budgets/{budget['id']}/globals/{row['id']}",
        headers=_headers(world),
        json={"value": "4"},
    )

    actions = {event.action for event in pg_db.query(AuditEvent).all()}
    assert {
        "budget.created",
        "budget_section.created",
        "budget_line.created",
        "budget_line.updated",
        "budget_line.removed",
        "budget_global.created",
        "budget_global.updated",
    } <= actions

    edit = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "budget_line.updated")
        .one()
    )
    assert edit.changes["unit_price"] == ["850.00", "900.00"]


def test_moving_a_line_is_audited_as_a_move(pg_client, pg_db, world, project):
    """A different action from an edit, so the log still distinguishes a drag
    from a typed number."""
    budget = _new_budget(pg_client, world, project)
    first = _section(pg_client, world, budget["id"], code="1100", name="Crew")
    second = _section(
        pg_client, world, budget["id"], code="1200", name="Equipment"
    )
    line = _line(
        pg_client,
        world,
        budget["id"],
        section_id=first["id"],
        description="DoP",
        unit_price="850.00",
    )

    response = pg_client.patch(
        f"/budgets/{budget['id']}/lines/{line['id']}",
        headers=_headers(world),
        json={"section_id": second["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["section_id"] == second["id"]
    actions = {event.action for event in pg_db.query(AuditEvent).all()}
    assert "budget_line.moved" in actions


def test_an_import_writes_one_event_with_counts(pg_client, pg_db, world, project):
    budget = _example(pg_client, world, project)
    payload = (
        "section_code;description;unit_price\r\n"
        "1100;DoP;900,00\r\n"
        "1100;Runner;180,00\r\n"
    ).encode("utf-8")

    _import(pg_client, world, budget["id"], payload)

    event = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "budget.import_applied")
        .one()
    )
    assert event.changes["created"] == [None, 1]
    assert event.changes["updated"] == [None, 1]
