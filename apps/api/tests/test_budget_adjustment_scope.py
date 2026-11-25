"""Adjustments by kind and by section (B1.2 part 1), against a real PostgreSQL.

The engine has always understood `base = "kinds"` and section-level
adjustments; what B1.2 adds is a screen that can ask for them and an API
that refuses a kind that does not exist. These tests pin the NUMBERS on the
S1 example budget (SCOPE §12), so the screen's promise — "an equipment
discount takes 10 % of the equipment price and nothing else" — is checked on
the real document the sheet is priced from:

  * S1: Crew 3 990.00 (after its 5 % discount), Equipment 4 680.00, Studio
    2 800.00, Travel 120.00 → sections 11 590.00, 8 % fee 927.20, net
    12 517.20, cost 5 380.00.
  * **Equipment discount 10 %, whole budget, kind equipment_rental** — the
    base is the equipment lines' price, 4 680.00, so 468.00 off; net
    12 049.20; Crew untouched at 3 990.00; cost still 5 380.00 (adjustments
    act on price only, D78). The fee is on the subtotal before document
    adjustments, so it stays 927.20.
  * **A sub-section** "Studio B" (one line, 1 000.00) under Studio: 10 % on
    Studio B takes 100.00 and leaves Studio A alone; 10 % on Studio (the
    parent) takes 380.00 — Studio A's 2 800.00 and Studio B's 1 000.00.
  * **A kind filter that matches nothing** warns, and changes nothing.

Mutations this file catches: the kind filter ignored (a discount hits crew),
a section discount leaking into a sibling section, a discount applying to
cost.
"""

from decimal import Decimal

from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _amount,
    _headers,
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


def _adjust(pg_client, world, budget_id, **body):
    body.setdefault("type", "discount")
    body.setdefault("mode", "percent")
    return pg_client.post(
        f"/budgets/{budget_id}/adjustments", headers=_headers(world), json=body
    )


def _top(calc, name):
    (row,) = [row for row in calc["top_sheet"] if row["name"] == name]
    return row


def _group(children, title):
    for child in children:
        if child.get("type") == "group":
            if child["title"] == title:
                return child
            found = _group(child["children"], title)
            if found is not None:
                return found
    return None


def _studio_b(pg_client, world, example):
    """A sub-section under Studio, with one 1 000.00 line of cost 600.00."""
    budget_id = example["budget"]["id"]
    response = pg_client.post(
        f"/budgets/{budget_id}/sections",
        headers=_headers(world),
        json={"name": "Studio B", "parent_id": example["sections"]["Studio"]["id"]},
    )
    assert response.status_code == 201, response.text
    section = response.json()
    response = pg_client.post(
        f"/budgets/{budget_id}/lines",
        headers=_headers(world),
        json={
            "section_id": section["id"],
            "description": "Studio B",
            "kind": "space_rental",
            "period_unit": "flat",
            "unit_price": "1000.00",
            "unit_cost": "600.00",
            "tax_ref": example["lines"]["DoP"]["tax_ref"],
        },
    )
    assert response.status_code == 201, response.text
    return section


# ─── By kind ────────────────────────────────────────────────────────────────


def test_an_equipment_discount_takes_ten_percent_of_equipment_only(
    pg_client, world, example
):
    budget_id = example["budget"]["id"]
    before = _calc(pg_client, world, budget_id)
    _amount(before["result"]["net"], S1_NET, what="S1 net before")

    response = _adjust(
        pg_client, world, budget_id,
        label="Equipment discount", value="10", base="kinds",
        kinds=["equipment_rental"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["kinds"] == ["equipment_rental"]

    after = _calc(pg_client, world, budget_id)
    (applied,) = [
        row for row in after["result"]["adjustments"] if row["label"] == "Equipment discount"
    ]
    _amount(applied["base_amount"], "4680.00", what="equipment base")
    _amount(applied["amount"], "-468.00", what="equipment discount")
    _amount(after["result"]["net"], "12049.20", what="net after equipment discount")
    # Crew untouched: its section price is still 3 990.00, and the fee —
    # 8 % of the subtotal before document adjustments — still 927.20.
    _amount(_top(after, "Crew")["price"], "3990.00", what="Crew price")
    _amount(_top(after, "Equipment")["price"], "4680.00", what="Equipment section price")
    (fee,) = [row for row in after["result"]["adjustments"] if row["label"] == "Production fee"]
    _amount(fee["amount"], "927.20", what="fee")
    # Price only (D78): no cost moved anywhere.
    _amount(after["result"]["cost"], S1_COST, what="cost after equipment discount")
    _amount(_top(after, "Equipment")["cost"], _top(before, "Equipment")["cost"], what="Equipment cost")
    _amount(
        after["result"]["margin"],
        str(Decimal("12049.20") - Decimal(S1_COST)),
        what="margin",
    )
    # The tax side follows the engine: one category, so its net is the net.
    assert sum(Decimal(v) for v in after["result"]["net_by_tax_ref"].values()) == Decimal(
        "12049.20"
    )


def test_a_crew_discount_is_the_same_thing_for_labor(pg_client, world, example):
    """The symmetric quick-add: 5 % on labor over the whole budget.

    The crew lines' price is 3 990.00 AFTER the Crew section's own 5 %, and
    a document-level kind filter reads the lines' own nets — the DoP and
    Gaffer at 2 550.00 + 1 650.00 = 4 200.00. 5 % of that is 210.00. Said
    out loud because it is the one place "crew price" has two readings.
    """
    budget_id = example["budget"]["id"]
    response = _adjust(
        pg_client, world, budget_id,
        label="Crew discount (labour)", value="5", base="kinds", kinds=["labor"],
    )
    assert response.status_code == 201, response.text
    after = _calc(pg_client, world, budget_id)
    (applied,) = [
        row for row in after["result"]["adjustments"] if row["label"] == "Crew discount (labour)"
    ]
    _amount(applied["base_amount"], "4200.00", what="labor base")
    _amount(applied["amount"], "-210.00", what="labor discount")
    _amount(_top(after, "Equipment")["price"], "4680.00", what="Equipment untouched")
    _amount(after["result"]["net"], "12307.20", what="net")


def test_a_kind_filter_that_matches_nothing_warns_and_changes_nothing(
    pg_client, world, example
):
    budget_id = example["budget"]["id"]
    response = _adjust(
        pg_client, world, budget_id,
        label="Storage discount", value="10", base="kinds", kinds=["storage"],
    )
    assert response.status_code == 201, response.text
    after = _calc(pg_client, world, budget_id)
    _amount(after["result"]["net"], S1_NET, what="net")
    warnings = [
        row for row in after["result"]["warnings"]
        if row["code"] == "kind_filter_matched_nothing"
    ]
    assert len(warnings) == 1, after["result"]["warnings"]
    assert "Storage discount" in warnings[0]["where"]


def test_an_unknown_kind_is_refused_and_nothing_is_stored(pg_client, world, example):
    budget_id = example["budget"]["id"]
    response = _adjust(
        pg_client, world, budget_id,
        label="Typo", value="10", base="kinds", kinds=["equipment"],
    )
    assert response.status_code == 422, response.text
    assert "equipment_rental" in response.json()["detail"]
    sheet = pg_client.get(f"/budgets/{budget_id}", headers=_headers(world)).json()
    assert [row["label"] for row in sheet["adjustments"]] == ["Crew discount", "Production fee"]


def test_kinds_without_the_kinds_base_are_refused(pg_client, world, example):
    budget_id = example["budget"]["id"]
    response = _adjust(
        pg_client, world, budget_id,
        label="Half-meant", value="10", base="subtotal", kinds=["labor"],
    )
    assert response.status_code == 422, response.text
    response = _adjust(
        pg_client, world, budget_id, label="Empty", value="10", base="kinds", kinds=[],
    )
    assert response.status_code == 422, response.text


def test_switching_a_kind_filter_back_to_everything(pg_client, world, example):
    budget_id = example["budget"]["id"]
    created = _adjust(
        pg_client, world, budget_id,
        label="Equipment discount", value="10", base="kinds",
        kinds=["equipment_rental"],
    ).json()
    response = pg_client.patch(
        f"/budgets/{budget_id}/adjustments/{created['id']}",
        headers=_headers(world),
        json={"base": "subtotal", "kinds": []},
    )
    assert response.status_code == 200, response.text
    after = _calc(pg_client, world, budget_id)
    (applied,) = [
        row for row in after["result"]["adjustments"] if row["label"] == "Equipment discount"
    ]
    _amount(applied["amount"], "-1159.00", what="10 % of 11 590.00")


# ─── By section ─────────────────────────────────────────────────────────────


def test_a_discount_on_a_sub_section_only(pg_client, world, example):
    budget_id = example["budget"]["id"]
    studio_b = _studio_b(pg_client, world, example)
    response = _adjust(
        pg_client, world, budget_id,
        label="Studio B discount", value="10", section_id=studio_b["id"],
    )
    assert response.status_code == 201, response.text

    after = _calc(pg_client, world, budget_id)
    studio = _group(after["result"]["children"], "Studio")
    sub = _group(after["result"]["children"], "Studio B")
    _amount(sub["subtotal_before_adjustments"], "1000.00", what="Studio B before")
    _amount(sub["subtotal"], "900.00", what="Studio B after")
    # Studio A's own 2 800.00 plus Studio B's 900.00 — Studio itself has no
    # adjustment of its own.
    _amount(studio["subtotal"], "3700.00", what="Studio")
    # Siblings untouched.
    _amount(_top(after, "Equipment")["price"], "4680.00", what="Equipment")
    _amount(_top(after, "Crew")["price"], "3990.00", what="Crew")
    # Cost: Studio A 2 200.00 + Studio B 600.00, the discount does not move it.
    _amount(studio["cost_subtotal"], "2800.00", what="Studio cost")


def test_a_discount_on_a_section_covers_its_sub_sections(pg_client, world, example):
    budget_id = example["budget"]["id"]
    _studio_b(pg_client, world, example)
    response = _adjust(
        pg_client, world, budget_id,
        label="Studio discount", value="10",
        section_id=example["sections"]["Studio"]["id"],
    )
    assert response.status_code == 201, response.text
    after = _calc(pg_client, world, budget_id)
    studio = _group(after["result"]["children"], "Studio")
    (applied,) = studio["adjustments"]
    _amount(applied["base_amount"], "3800.00", what="Studio A + Studio B")
    _amount(applied["amount"], "-380.00", what="Studio discount")
    _amount(studio["subtotal"], "3420.00", what="Studio after")


def test_a_section_discount_does_not_leak_into_a_sibling(pg_client, world, example):
    budget_id = example["budget"]["id"]
    response = _adjust(
        pg_client, world, budget_id,
        label="Equipment section discount", value="10",
        section_id=example["sections"]["Equipment"]["id"],
    )
    assert response.status_code == 201, response.text
    after = _calc(pg_client, world, budget_id)
    _amount(_top(after, "Equipment")["price"], "4212.00", what="Equipment after")
    for name, price in (("Crew", "3990.00"), ("Studio", "2800.00"), ("Travel", "120.00")):
        _amount(_top(after, name)["price"], price, what=f"{name} untouched")
    _amount(after["result"]["cost"], S1_COST, what="cost")


def test_a_section_and_a_kind_filter_combine(pg_client, world, example):
    """Equipment-only inside one section: 10 % of the Studio section's
    equipment, when the only equipment in the budget is elsewhere, is
    nothing — and says so — while the same filter on Equipment takes 468.00.
    """
    budget_id = example["budget"]["id"]
    _adjust(
        pg_client, world, budget_id,
        label="Studio equipment", value="10", base="kinds",
        kinds=["equipment_rental"], section_id=example["sections"]["Studio"]["id"],
    )
    _adjust(
        pg_client, world, budget_id,
        label="Equipment equipment", value="10", base="kinds",
        kinds=["equipment_rental"], section_id=example["sections"]["Equipment"]["id"],
    )
    after = _calc(pg_client, world, budget_id)
    _amount(_top(after, "Studio")["price"], "2800.00", what="Studio")
    _amount(_top(after, "Equipment")["price"], "4212.00", what="Equipment")
    assert [
        row["where"] for row in after["result"]["warnings"]
        if row["code"] == "kind_filter_matched_nothing"
    ] == ["group Studio: Studio equipment"]
