"""Extras — charges agreed after the quote — against a real PostgreSQL.

B1.1 part 4. The centre is the S1 example budget, LOCKED, with an extra of
price 500.00 / cost 300.00 (flat, quantity 1, the company's standard tax
category — the same as every S1 line):

  * **proposed** — every total unchanged: net 12 517.20, cost 5 380.00.
  * **accepted** — `baseline` still 12 517.20 (the locked quote); the budget
    (`with_extras`, the same run as `result`) is the engine's document with
    the extra in it: sections 11 590.00 + 500.00 = 12 090.00, plus the S1
    budget's 8 % production fee charged on it like on any line = 13 057.20.
    Cost 5 680.00, margin 7 377.20. The budget computes no VAT (D72): the
    tax side is the net per tax category, and the standard category's net
    is 13 057.20. The extra's own figures: 500.00 / 300.00 / 200.00.
  * **declined** (a second extra) — nothing moves.

The three mutations this file has to catch: a declined extra counted
(`test_a_declined_extra_counts_in_nothing`), a proposed extra counted
(`test_the_s1_extra_proposed_then_accepted`), and an ordinary line editable
on a locked budget because it sits next to the Extras section
(`test_on_a_locked_budget_the_extra_is_editable_and_an_ordinary_line_is_not`).
"""

import io
from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.project import BudgetLine, BudgetSection

from .company_factories import make_budget, make_company, make_project
from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _actual,
    _amount,
    _headers,
    _tracking,
    example,
    project,
    world,
)

S1_NET = "12517.20"
S1_COST = "5380.00"


def _calc(pg_client, world, budget_id):
    response = pg_client.get(f"/budgets/{budget_id}/calculated", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _propose(pg_client, world, budget_id, **body):
    body.setdefault("description", "Second shoot day for the drone")
    body.setdefault("unit_price", "500.00")
    body.setdefault("unit_cost", "300.00")
    body.setdefault("note", "Offered by phone on 24.11.")
    response = pg_client.post(
        f"/budgets/{budget_id}/extras", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _decide(pg_client, world, budget_id, line_id, decision, **body):
    return pg_client.post(
        f"/budgets/{budget_id}/extras/{line_id}/{decision}",
        headers=_headers(world),
        json=body,
    )


def _lock(pg_client, world, budget_id):
    response = pg_client.post(f"/budgets/{budget_id}/lock", headers=_headers(world), json={})
    assert response.status_code == 200, response.text


def _events(pg_db, action):
    pg_db.expire_all()
    # Oldest first, by when each was written. Without an ORDER BY Postgres
    # returns heap order, which stops being insertion order once the
    # fixture's DELETEs free space for later rows to reuse — `[-1]` then
    # picks an older event (CI, 2026-11-26; test-harness-traps.md 20).
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == action)
        .order_by(AuditEvent.created_at)
        .all()
    )


@pytest.fixture
def locked(pg_client, world, example):
    _lock(pg_client, world, example["budget"]["id"])
    return example


def _standard_ref(example):
    return example["lines"]["DoP"]["tax_ref"]


# ─── The numbers ────────────────────────────────────────────────────────────


def test_the_s1_extra_proposed_then_accepted(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    assert extra["extra_status"] == "proposed"
    assert extra["tax_ref"] == _standard_ref(locked)

    proposed = _calc(pg_client, world, budget_id)
    for scope in ("baseline", "with_extras"):
        _amount(proposed["totals"][scope]["net"], S1_NET, what=f"{scope} net, proposed")
        _amount(proposed["totals"][scope]["cost"], S1_COST, what=f"{scope} cost, proposed")
    _amount(proposed["result"]["net"], S1_NET, what="result net, proposed")
    (row,) = proposed["extras"]
    assert row["status"] == "proposed"
    _amount(row["price"], "500.00", what="extra price")
    _amount(row["cost"], "300.00", what="extra cost")
    _amount(row["margin"], "200.00", what="extra margin")
    assert not any(line["name"] == "Extras" for line in proposed["top_sheet"])
    _amount(
        _tracking(pg_client, world, budget_id)["budget"]["estimate_cost"],
        S1_COST,
        what="tracking baseline, proposed",
    )

    response = _decide(pg_client, world, budget_id, extra["id"], "accept")
    assert response.status_code == 200, response.text
    assert response.json()["extra_status"] == "accepted"
    assert response.json()["extra_decided_on"] is not None

    accepted = _calc(pg_client, world, budget_id)
    _amount(accepted["totals"]["baseline"]["net"], S1_NET, what="baseline net")
    _amount(accepted["totals"]["baseline"]["cost"], S1_COST, what="baseline cost")
    with_extras = accepted["totals"]["with_extras"]
    _amount(with_extras["net"], "13057.20", what="with extras net")
    _amount(with_extras["cost"], "5680.00", what="with extras cost")
    _amount(with_extras["margin"], "7377.20", what="with extras margin")
    _amount(
        with_extras["net_by_tax_ref"][_standard_ref(locked)],
        "13057.20",
        what="standard tax category net",
    )
    _amount(accepted["result"]["net"], "13057.20", what="result = with extras")
    _amount(accepted["result"]["subtotal"], "12090.00", what="sections before the fee")
    extras_row = next(row for row in accepted["top_sheet"] if row["name"] == "Extras")
    _amount(extras_row["price"], "500.00", what="top sheet Extras price")
    _amount(extras_row["cost"], "300.00", what="top sheet Extras cost")

    tracking = _tracking(pg_client, world, budget_id)
    _amount(tracking["budget"]["estimate_cost"], "5680.00", what="tracking plan with extra")
    _amount(tracking["budget"]["price"], "13057.20", what="tracking price with extra")
    _amount(tracking["lines"][extra["id"]]["estimate_cost"], "300.00", what="extra plan")


def test_a_declined_extra_counts_in_nothing(pg_client, pg_db, world, locked):
    budget_id = locked["budget"]["id"]
    kept = _propose(pg_client, world, budget_id)
    assert _decide(pg_client, world, budget_id, kept["id"], "accept").status_code == 200
    dropped = _propose(
        pg_client, world, budget_id, description="Night shoot", unit_price="900.00", unit_cost="700.00"
    )
    response = _decide(
        pg_client, world, budget_id, dropped["id"], "decline", note="Client said no on 25.11."
    )
    assert response.status_code == 200, response.text
    assert response.json()["extra_status"] == "declined"

    calc = _calc(pg_client, world, budget_id)
    _amount(calc["totals"]["with_extras"]["net"], "13057.20", what="with extras, one declined")
    _amount(calc["totals"]["with_extras"]["cost"], "5680.00", what="cost, one declined")
    _amount(calc["totals"]["baseline"]["net"], S1_NET, what="baseline")
    statuses = {row["description"]: row["status"] for row in calc["extras"]}
    assert statuses == {"Second shoot day for the drone": "accepted", "Night shoot": "declined"}
    tracking = _tracking(pg_client, world, budget_id)
    assert dropped["id"] not in tracking["lines"]
    _amount(tracking["budget"]["estimate_cost"], "5680.00", what="tracking, one declined")
    (event,) = _events(pg_db, "budget_extra.declined")
    assert event.reason == "Client said no on 25.11."


def test_declining_needs_a_note_and_a_declined_extra_stays_as_it_was(
    pg_client, world, locked
):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    refused = _decide(pg_client, world, budget_id, extra["id"], "decline")
    assert refused.status_code == 409
    assert "needs a note" in refused.json()["detail"]
    assert _decide(pg_client, world, budget_id, extra["id"], "decline", note="Withdrawn.").status_code == 200
    edit = pg_client.patch(
        f"/budgets/{budget_id}/extras/{extra['id']}",
        headers=_headers(world),
        json={"unit_price": "600.00"},
    )
    assert edit.status_code == 409
    assert "Propose a new extra" in edit.json()["detail"]


# ─── The lock ───────────────────────────────────────────────────────────────


def test_on_a_locked_budget_the_extra_is_editable_and_an_ordinary_line_is_not(
    pg_client, pg_db, world, locked
):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    # The Extras section now sits next to the ordinary sections.
    ordinary = pg_client.patch(
        f"/budgets/{budget_id}/lines/{locked['lines']['DoP']['id']}",
        headers=_headers(world),
        json={"unit_price": "1.00"},
    )
    assert ordinary.status_code == 409, ordinary.text
    assert "was locked" in ordinary.json()["detail"]
    pg_db.expire_all()
    assert pg_db.get(BudgetLine, locked["lines"]["DoP"]["id"]).unit_price == Decimal("850.00")

    edited = pg_client.patch(
        f"/budgets/{budget_id}/extras/{extra['id']}",
        headers=_headers(world),
        json={"unit_price": "550.00"},
    )
    assert edited.status_code == 200, edited.text
    assert Decimal(edited.json()["unit_price"]) == Decimal("550.00")


def test_changing_an_accepted_extra_is_audited_with_before_and_after(
    pg_client, pg_db, world, locked
):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    _decide(pg_client, world, budget_id, extra["id"], "accept")
    pg_client.patch(
        f"/budgets/{budget_id}/extras/{extra['id']}",
        headers=_headers(world),
        json={"unit_price": "450.00"},
    )
    (event,) = _events(pg_db, "budget_extra.updated")
    assert event.changes["unit_price"] == ["500.00", "450.00"]
    assert event.reason == "changed after the customer accepted it"
    actions = {row.action for row in pg_db.query(AuditEvent).all()}
    assert {"budget_extra.proposed", "budget_extra.accepted", "budget_extra.updated"} <= actions


def test_the_sheet_endpoints_never_touch_the_extras_section(pg_client, world, example):
    """On an UNLOCKED budget, so the refusal is the Extras rule and not the lock."""
    budget_id = example["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    section_id = extra["section_id"]
    dop_id = example["lines"]["DoP"]["id"]
    attempts = [
        ("post", f"/budgets/{budget_id}/lines", {"section_id": section_id, "description": "Sneaky"}),
        ("patch", f"/budgets/{budget_id}/lines/{extra['id']}", {"unit_price": "1.00"}),
        ("post", f"/budgets/{budget_id}/lines/{extra['id']}/remove", None),
        ("post", f"/budgets/{budget_id}/lines/{extra['id']}/duplicate", None),
        ("patch", f"/budgets/{budget_id}/lines/{dop_id}", {"section_id": section_id}),
        ("patch", f"/budgets/{budget_id}/sections/{section_id}", {"name": "Renamed"}),
        ("post", f"/budgets/{budget_id}/sections/{section_id}/remove", None),
        ("post", f"/budgets/{budget_id}/sections", {"name": "Under extras", "parent_id": section_id}),
    ]
    for method, url, body in attempts:
        response = getattr(pg_client, method)(
            url, headers=_headers(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 409, f"{method} {url}: {response.status_code} {response.text}"
        assert "Extras tab" in response.json()["detail"], url


def test_one_extras_section_per_budget(pg_client, pg_db, world, locked):
    budget_id = locked["budget"]["id"]
    first = _propose(pg_client, world, budget_id)
    second = _propose(pg_client, world, budget_id, description="Teleprompter")
    assert first["section_id"] == second["section_id"]
    pg_db.expire_all()
    assert (
        pg_db.query(BudgetSection)
        .filter(BudgetSection.budget_id == budget_id, BudgetSection.is_extras.is_(True))
        .count()
        == 1
    )


# ─── Costs, duplicate, CSV, closed ──────────────────────────────────────────


def test_costs_are_booked_on_an_accepted_extra_only(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    refused = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={
            "section_id": extra["section_id"],
            "line_id": extra["id"],
            "amount": "250.00",
            "incurred_on": "2026-06-05",
        },
    )
    assert refused.status_code == 409
    assert "not accepted" in refused.json()["detail"]
    no_line = pg_client.post(
        f"/budgets/{budget_id}/actuals",
        headers=_headers(world),
        json={"section_id": extra["section_id"], "amount": "250.00", "incurred_on": "2026-06-05"},
    )
    assert no_line.status_code == 409

    _decide(pg_client, world, budget_id, extra["id"], "accept")
    _actual(
        pg_client, world, budget_id,
        section_id=extra["section_id"], line_id=extra["id"], amount="250.00",
    )
    line = _tracking(pg_client, world, budget_id)["lines"][extra["id"]]
    _amount(line["actual"], "250.00", what="spent on the extra")
    _amount(line["estimate_cost"], "300.00", what="planned: the extra's own cost")
    _amount(line["efc"], "300.00", what="forecast by the default rule")

    # An extra with costs on it cannot be declined.
    refused = _decide(pg_client, world, budget_id, extra["id"], "decline", note="Changed mind.")
    assert refused.status_code == 409
    assert "orders or invoices" in refused.json()["detail"]


def test_duplicating_copies_the_baseline_and_no_extras(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    _decide(pg_client, world, budget_id, extra["id"], "accept")
    copy = pg_client.post(
        f"/budgets/{budget_id}/duplicate", headers=_headers(world), json={}
    ).json()
    sheet = pg_client.get(f"/budgets/{copy['id']}", headers=_headers(world)).json()
    assert not any(row["is_extras"] for row in sheet["sections"])
    assert not any(row["extra_status"] for row in sheet["lines"])
    assert copy["is_locked"] is False and copy["is_closed"] is False
    calc = _calc(pg_client, world, copy["id"])
    _amount(calc["totals"]["with_extras"]["net"], S1_NET, what="copy net")
    assert calc["extras"] == []


def test_the_csv_export_lists_extras_with_their_status_and_the_import_refuses_them(
    pg_client, world, example
):
    import csv

    budget_id = example["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    exported = pg_client.get(f"/budgets/{budget_id}/export.csv", headers=_headers(world))
    rows = list(
        csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";")
    )
    by_description = {row["description"]: row for row in rows}
    assert by_description["Second shoot day for the drone"]["extra_status"] == "proposed"
    assert by_description["Second shoot day for the drone"]["section_name"] == "Extras"
    assert by_description["Second shoot day for the drone"]["price"] == "500,00"
    assert by_description["DoP"]["extra_status"] == ""

    payload = "section_name;description;unit_price\r\nExtras;Smuggled in;100,00\r\n".encode("utf-8")
    preview = pg_client.post(
        f"/budgets/{budget_id}/import/preview",
        headers=_headers(world),
        files={"file": ("lines.csv", io.BytesIO(payload), "text/csv")},
    ).json()
    assert preview["errors"] == 1
    assert "managed on the Extras tab" in preview["rows"][0]["message"]
    assert extra["id"]


def test_a_closed_budget_refuses_extras(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    check = pg_client.get(f"/budgets/{budget_id}/close-check", headers=_headers(world)).json()
    closed = pg_client.post(
        f"/budgets/{budget_id}/close",
        headers=_headers(world),
        json={"acknowledge": True, "digest": check["digest"], "note": "Closed for the extras test."},
    )
    assert closed.status_code == 200, closed.text
    response = pg_client.post(
        f"/budgets/{budget_id}/extras",
        headers=_headers(world),
        json={"description": "Too late", "unit_price": "100.00"},
    )
    assert response.status_code == 409
    assert "Reopen it first" in response.json()["detail"]


# ─── Isolation ──────────────────────────────────────────────────────────────


def test_the_extras_routes_404_for_another_company_s_budget(pg_client, pg_db, world):
    other = make_company(pg_db, "Someone else GmbH")
    their_budget = make_budget(pg_db, other, make_project(pg_db, other))
    pg_db.commit()
    stranger = "00000000-0000-0000-0000-000000000001"
    for method, url, body in (
        ("post", f"/budgets/{their_budget.id}/extras", {"description": "X", "unit_price": "1.00"}),
        ("patch", f"/budgets/{their_budget.id}/extras/{stranger}", {"description": "X"}),
        ("post", f"/budgets/{their_budget.id}/extras/{stranger}/accept", {}),
        ("post", f"/budgets/{their_budget.id}/extras/{stranger}/decline", {"note": "x"}),
    ):
        response = getattr(pg_client, method)(url, headers=_headers(world), json=body)
        assert response.status_code == 404, f"{method} {url}: {response.status_code}"


def test_an_extra_of_another_budget_is_a_404(pg_client, world, locked, project):
    extra = _propose(pg_client, world, locked["budget"]["id"])
    other = pg_client.post(
        f"/projects/{project['id']}/budgets", headers=_headers(world), json={"name": "B"}
    ).json()
    response = _decide(pg_client, world, other["id"], extra["id"], "accept")
    assert response.status_code == 404
