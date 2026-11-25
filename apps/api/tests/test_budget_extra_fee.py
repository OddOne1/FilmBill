"""The production fee on an extra, chosen per extra (B1.2 part 2b).

Against a real PostgreSQL. Mathias's decision (2026-11-25): each extra has an
"add production fee" choice, ticked by default; unticked, the extra is billed
at its own price. The S1 example budget, LOCKED, with its 8 % production fee
(role `production_fee`), and an extra of price 500.00 / cost 300.00:

  * **ticked**, accepted — with extras 13 057.20: the extra adds 540.00
    (500.00 plus 8 %). Cost 5 680.00, margin 7 377.20.
  * **unticked**, accepted — with extras 13 017.20: the extra adds 500.00.
    Cost 5 680.00, margin 7 337.20.
  * the baseline is 12 517.20 either way — the quote as locked.
  * a budget WITHOUT a production fee: the checkbox changes nothing — the
    extra adds 500.00 ticked or not (sections 11 590.00 + 500.00).
  * duplicating a budget copies no extras (B1.1), so there is no choice to
    copy.

Mutations this file catches: an unticked extra still carries the fee
(`test_an_unticked_extra_is_billed_at_its_own_price`) and a ticked extra
does not (`test_a_ticked_extra_carries_the_fee`).
"""

import pytest

from apps.api.models.audit import AuditEvent

from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _amount,
    _headers,
    example,
    project,
    world,
)

BASELINE = "12517.20"


def _calc(pg_client, world, budget_id):
    response = pg_client.get(f"/budgets/{budget_id}/calculated", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _propose(pg_client, world, budget_id, **body):
    body.setdefault("description", "Second shoot day for the drone")
    body.setdefault("unit_price", "500.00")
    body.setdefault("unit_cost", "300.00")
    response = pg_client.post(
        f"/budgets/{budget_id}/extras", headers=_headers(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept(pg_client, world, budget_id, line_id):
    response = pg_client.post(
        f"/budgets/{budget_id}/extras/{line_id}/accept", headers=_headers(world), json={}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _lock(pg_client, world, budget_id):
    response = pg_client.post(f"/budgets/{budget_id}/lock", headers=_headers(world), json={})
    assert response.status_code == 200, response.text


@pytest.fixture
def locked(pg_client, world, example):
    _lock(pg_client, world, example["budget"]["id"])
    return example


def _totals(calc):
    return calc["totals"]["baseline"], calc["totals"]["with_extras"]


def test_an_extra_is_ticked_by_default(pg_client, world, locked):
    extra = _propose(pg_client, world, locked["budget"]["id"])
    assert extra["apply_production_fee"] is True


def test_a_ticked_extra_carries_the_fee(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id, apply_production_fee=True)
    _accept(pg_client, world, budget_id, extra["id"])
    baseline, with_extras = _totals(_calc(pg_client, world, budget_id))
    _amount(baseline["net"], BASELINE, what="baseline, ticked")
    _amount(with_extras["net"], "13057.20", what="with extras, ticked (+540.00)")
    _amount(with_extras["cost"], "5680.00", what="cost, ticked")
    _amount(with_extras["margin"], "7377.20", what="margin, ticked")


def test_an_unticked_extra_is_billed_at_its_own_price(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id, apply_production_fee=False)
    assert extra["apply_production_fee"] is False
    _accept(pg_client, world, budget_id, extra["id"])
    calc = _calc(pg_client, world, budget_id)
    baseline, with_extras = _totals(calc)
    _amount(baseline["net"], BASELINE, what="baseline, unticked")
    _amount(with_extras["net"], "13017.20", what="with extras, unticked (+500.00)")
    _amount(with_extras["cost"], "5680.00", what="cost, unticked")
    _amount(with_extras["margin"], "7337.20", what="margin, unticked")
    # The fee itself is still 8 % of the ORDINARY lines — 927.20 — and its
    # whole amount lands on the standard category with the rest.
    (fee,) = [row for row in calc["result"]["adjustments"] if row["label"] == "Production fee"]
    _amount(fee["base_amount"], "11590.00", what="fee base, without the extra")
    _amount(fee["amount"], "927.20", what="fee, unticked extra")
    # The extra's own figures do not change with the choice.
    (row,) = calc["extras"]
    _amount(row["price"], "500.00", what="extra price")


def test_changing_the_choice_on_an_accepted_extra_is_audited(
    pg_client, pg_db, world, locked
):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id)
    _accept(pg_client, world, budget_id, extra["id"])
    response = pg_client.patch(
        f"/budgets/{budget_id}/extras/{extra['id']}",
        headers=_headers(world),
        json={"apply_production_fee": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["apply_production_fee"] is False
    _amount(_totals(_calc(pg_client, world, budget_id))[1]["net"], "13017.20", what="after untick")

    pg_db.expire_all()
    (event,) = [
        event for event in pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "budget_extra.updated").all()
        if str(event.entity_id) == extra["id"]
    ]
    assert event.changes["apply_production_fee"] == [True, False]
    assert event.reason == "changed after the customer accepted it"


def test_a_budget_without_a_fee_is_unaffected_by_the_choice(pg_client, world, example):
    budget_id = example["budget"]["id"]
    response = pg_client.put(
        f"/budgets/{budget_id}/production-fee", headers=_headers(world), json={"enabled": False}
    )
    assert response.status_code == 200, response.text
    _lock(pg_client, world, budget_id)
    ticked = _propose(pg_client, world, budget_id, description="Ticked")
    unticked = _propose(pg_client, world, budget_id, description="Unticked", apply_production_fee=False)
    _accept(pg_client, world, budget_id, ticked["id"])
    baseline, with_one = _totals(_calc(pg_client, world, budget_id))
    _amount(baseline["net"], "11590.00", what="baseline without a fee")
    _amount(with_one["net"], "12090.00", what="ticked extra, no fee")
    _accept(pg_client, world, budget_id, unticked["id"])
    _amount(_totals(_calc(pg_client, world, budget_id))[1]["net"], "12590.00", what="both, no fee")


def test_duplicating_a_budget_copies_no_extras(pg_client, world, locked):
    budget_id = locked["budget"]["id"]
    extra = _propose(pg_client, world, budget_id, apply_production_fee=False)
    _accept(pg_client, world, budget_id, extra["id"])
    response = pg_client.post(
        f"/budgets/{budget_id}/duplicate", headers=_headers(world), json={}
    )
    assert response.status_code == 201, response.text
    copy = pg_client.get(f"/budgets/{response.json()['id']}", headers=_headers(world)).json()
    assert [row for row in copy["lines"] if row["extra_status"]] == []
    assert not any(section["is_extras"] for section in copy["sections"])
    _amount(_calc(pg_client, world, response.json()["id"])["result"]["net"], BASELINE, what="copy")


def test_an_ordinary_line_cannot_take_the_choice(pg_client, world, example):
    budget_id = example["budget"]["id"]
    response = pg_client.post(
        f"/budgets/{budget_id}/lines",
        headers=_headers(world),
        json={
            "section_id": example["sections"]["Crew"]["id"],
            "description": "Runner",
            "unit_price": "100.00",
            "apply_production_fee": False,
        },
    )
    assert response.status_code == 422, response.text
