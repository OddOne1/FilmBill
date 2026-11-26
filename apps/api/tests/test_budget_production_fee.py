"""The production fee per budget, with a company preset (B1.2 part 2).

Against a real PostgreSQL. The fee is an ordinary `surcharge` adjustment
with `role = production_fee`, so every NUMBER here is the engine's; what
these tests pin is the ownership — the preset is copied onto a NEW budget and
is never a live link:

  * preset 8 % → a new blank budget starts with an 8 % fee; no preset → none;
  * 12 % on one budget leaves the preset (8 %) and the other budget (8 %)
    alone; Reset to preset puts 8 % back;
  * duplicating a budget copies its fee as it stands (12 %), not the preset;
  * **two budgets of one project at 8 % and 12 %**, on S1's sections
    (11 590.00 before the fee): 927.20 → net 12 517.20, and 1 390.80 → net
    12 980.80;
  * at most one fee per budget — the partial unique index refuses a second
    active one, the generic adjustment endpoints neither create nor touch one;
  * the S1 example keeps its own 8 % (net 12 517.20) whatever the preset says.

Mutations this file catches: changing one budget's fee changes the preset
(`test_one_budget_s_fee_leaves_the_preset_and_the_other_budget_alone`), and
a second production-fee adjustment accepted
(`test_the_schema_refuses_a_second_active_fee`).
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.project import BudgetAdjustment
from apps.api.services.permissions import PERMISSIONS

from .company_factories import auth_headers, grant, make_user
from .test_budget_tracking_api import (  # noqa: F401 — fixtures
    _amount,
    _headers,
    example,
    project,
    world,
)

S1_SECTIONS = Decimal("11590.00")


def _set_preset(pg_client, world, percent, kinds=()):
    response = pg_client.put(
        "/company/production-fee",
        headers=_headers(world),
        json={"percent": percent, "kinds": list(kinds)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _preset(pg_client, world):
    response = pg_client.get("/company/production-fee", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _new_budget(pg_client, world, project, name="Customer A"):
    response = pg_client.post(
        f"/projects/{project['id']}/budgets", headers=_headers(world), json={"name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sheet(pg_client, world, budget_id):
    response = pg_client.get(f"/budgets/{budget_id}", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _fee(pg_client, world, budget_id):
    return _sheet(pg_client, world, budget_id)["production_fee"]


def _set_fee(pg_client, world, budget_id, **body):
    return pg_client.put(
        f"/budgets/{budget_id}/production-fee", headers=_headers(world), json=body
    )


def _calc(pg_client, world, budget_id):
    response = pg_client.get(f"/budgets/{budget_id}/calculated", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


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


def _fee_amount(calc):
    (row,) = [row for row in calc["result"]["adjustments"] if row["label"] == "Production fee"]
    return row["amount"]


# ─── The preset and a new budget ────────────────────────────────────────────


def test_no_preset_by_default_and_a_new_budget_has_no_fee(pg_client, world, project):
    assert _preset(pg_client, world) == {"percent": None, "kinds": []}
    budget = _new_budget(pg_client, world, project)
    fee = _fee(pg_client, world, budget["id"])
    assert fee["enabled"] is False
    assert fee["percent"] is None
    assert fee["preset_percent"] is None
    assert fee["differs_from_preset"] is False
    assert _sheet(pg_client, world, budget["id"])["adjustments"] == []


def test_a_preset_is_where_a_new_budget_starts(pg_client, world, project, pg_db):
    _set_preset(pg_client, world, "8")
    budget = _new_budget(pg_client, world, project)

    sheet = _sheet(pg_client, world, budget["id"])
    fee = sheet["production_fee"]
    assert fee["enabled"] is True
    _amount(fee["percent"], "8", what="the new budget's fee")
    _amount(fee["preset_percent"], "8", what="the preset")
    assert fee["differs_from_preset"] is False
    (row,) = sheet["adjustments"]
    assert (row["role"], row["type"], row["mode"], row["base"], row["section_id"]) == (
        "production_fee", "surcharge", "percent", "subtotal", None,
    )
    assert row["id"] == fee["adjustment_id"]

    (event,) = _events(pg_db, "budget.production_fee_set")
    assert event.reason == "From the company preset"
    assert event.changes["enabled"] == [False, True]
    (preset_event,) = _events(pg_db, "company.default_production_fee_set")
    assert preset_event.changes["percent"][0] is None


def test_a_preset_on_certain_kinds_is_a_kinds_fee(pg_client, world, project):
    _set_preset(pg_client, world, "10", ["labor", "equipment_rental"])
    budget = _new_budget(pg_client, world, project)
    sheet = _sheet(pg_client, world, budget["id"])
    (row,) = sheet["adjustments"]
    assert row["base"] == "kinds"
    assert sorted(row["kinds"]) == ["equipment_rental", "labor"]
    assert sorted(sheet["production_fee"]["kinds"]) == ["equipment_rental", "labor"]


def test_one_budget_s_fee_leaves_the_preset_and_the_other_budget_alone(
    pg_client, world, project, pg_db
):
    _set_preset(pg_client, world, "8")
    first = _new_budget(pg_client, world, project, "Customer A")
    second = _new_budget(pg_client, world, project, "Customer B")

    response = _set_fee(pg_client, world, first["id"], enabled=True, percent="12")
    assert response.status_code == 200, response.text
    _amount(_preset(pg_client, world)["percent"], "8", what="the preset after")
    _amount(response.json()["percent"], "12", what="first budget's fee")
    assert response.json()["differs_from_preset"] is True
    other = _fee(pg_client, world, second["id"])
    _amount(other["percent"], "8", what="the other budget's fee")
    assert other["differs_from_preset"] is False

    events = [
        event for event in _events(pg_db, "budget.production_fee_set")
        if str(event.entity_id) == first["id"]
    ]
    assert [Decimal(str(value)) for value in events[-1].changes["percent"]] == [
        Decimal("8"),
        Decimal("12"),
    ]
    # The preset's own audit trail has only the one change that set it.
    assert len(_events(pg_db, "company.default_production_fee_set")) == 1


def test_reset_to_preset(pg_client, world, project, pg_db):
    _set_preset(pg_client, world, "8")
    budget = _new_budget(pg_client, world, project)
    _set_fee(pg_client, world, budget["id"], enabled=True, percent="12", kinds=["labor"])

    response = pg_client.post(
        f"/budgets/{budget['id']}/production-fee/reset", headers=_headers(world)
    )
    assert response.status_code == 200, response.text
    fee = response.json()
    _amount(fee["percent"], "8", what="after reset")
    assert fee["kinds"] == []
    assert fee["differs_from_preset"] is False
    assert _events(pg_db, "budget.production_fee_set")[-1].reason == (
        "Reset to the company preset"
    )
    (row,) = _sheet(pg_client, world, budget["id"])["adjustments"]
    assert row["base"] == "subtotal" and row["kinds"] == []


def test_switching_the_fee_off_and_on(pg_client, world, project):
    _set_preset(pg_client, world, "8")
    budget = _new_budget(pg_client, world, project)
    off = _set_fee(pg_client, world, budget["id"], enabled=False)
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["differs_from_preset"] is True
    assert _sheet(pg_client, world, budget["id"])["adjustments"] == []
    on = _set_fee(pg_client, world, budget["id"], enabled=True, percent="9")
    assert on.status_code == 200, on.text
    assert len(_sheet(pg_client, world, budget["id"])["adjustments"]) == 1


def test_duplicating_copies_the_fee_as_it_stands(pg_client, world, project):
    _set_preset(pg_client, world, "8")
    budget = _new_budget(pg_client, world, project)
    _set_fee(pg_client, world, budget["id"], enabled=True, percent="12")
    response = pg_client.post(
        f"/budgets/{budget['id']}/duplicate", headers=_headers(world), json={}
    )
    assert response.status_code == 201, response.text
    copy = _fee(pg_client, world, response.json()["id"])
    _amount(copy["percent"], "12", what="the copy's fee")
    assert copy["differs_from_preset"] is True


# ─── Numbers ────────────────────────────────────────────────────────────────


def test_two_budgets_of_one_project_at_8_and_12_percent(pg_client, world, example):
    """S1's sections are 11 590.00 before the fee. 8 % is 927.20 (net
    12 517.20); 12 % is 1 390.80 (net 12 980.80). Cost 5 380.00 either way."""
    eight = example["budget"]["id"]
    response = pg_client.post(
        f"/budgets/{eight}/duplicate", headers=_headers(world), json={"name": "Customer B"}
    )
    assert response.status_code == 201, response.text
    twelve = response.json()["id"]
    assert _set_fee(pg_client, world, twelve, enabled=True, percent="12").status_code == 200

    calc_8 = _calc(pg_client, world, eight)
    calc_12 = _calc(pg_client, world, twelve)
    _amount(calc_8["result"]["subtotal"], str(S1_SECTIONS), what="sections, 8 %")
    _amount(calc_12["result"]["subtotal"], str(S1_SECTIONS), what="sections, 12 %")
    _amount(_fee_amount(calc_8), "927.20", what="8 % fee")
    _amount(_fee_amount(calc_12), "1390.80", what="12 % fee")
    _amount(calc_8["result"]["net"], "12517.20", what="net at 8 %")
    _amount(calc_12["result"]["net"], "12980.80", what="net at 12 %")
    _amount(calc_8["result"]["cost"], "5380.00", what="cost at 8 %")
    _amount(calc_12["result"]["cost"], "5380.00", what="cost at 12 %, price only")


def test_the_example_keeps_its_own_fee_whatever_the_preset(pg_client, world, project):
    _set_preset(pg_client, world, "12")
    response = pg_client.post(
        f"/projects/{project['id']}/budgets/example", headers=_headers(world)
    )
    assert response.status_code == 201, response.text
    budget_id = response.json()["id"]
    fee = _fee(pg_client, world, budget_id)
    _amount(fee["percent"], "8", what="the example's fee")
    assert fee["differs_from_preset"] is True
    calc = _calc(pg_client, world, budget_id)
    _amount(calc["result"]["net"], "12517.20", what="S1 net")
    roles = [row["role"] for row in _sheet(pg_client, world, budget_id)["adjustments"]]
    assert roles == [None, "production_fee"]


# ─── One fee per budget ─────────────────────────────────────────────────────


def test_the_schema_refuses_a_second_active_fee(pg_client, world, example, pg_db):
    budget_id = example["budget"]["id"]
    (fee,) = [
        row for row in example["sheet"]["adjustments"] if row["role"] == "production_fee"
    ]
    original = pg_db.get(BudgetAdjustment, fee["id"])
    pg_db.add(
        BudgetAdjustment(
            company_id=original.company_id,
            budget_id=original.budget_id,
            section_id=None,
            position=999,
            label="A second fee",
            type="surcharge",
            mode="percent",
            value=Decimal("3"),
            base="subtotal",
            kinds=[],
            role="production_fee",
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.flush()
    pg_db.rollback()
    assert len(
        [row for row in _sheet(pg_client, world, budget_id)["adjustments"] if row["role"]]
    ) == 1


def test_setting_the_fee_twice_updates_the_one_row(pg_client, world, example):
    budget_id = example["budget"]["id"]
    for percent in ("10", "11"):
        assert _set_fee(pg_client, world, budget_id, enabled=True, percent=percent).status_code == 200
    fees = [row for row in _sheet(pg_client, world, budget_id)["adjustments"] if row["role"]]
    assert len(fees) == 1
    _amount(fees[0]["value"], "11", what="the one fee")


def test_the_generic_adjustment_endpoints_neither_create_nor_touch_the_fee(
    pg_client, world, example
):
    budget_id = example["budget"]["id"]
    response = pg_client.post(
        f"/budgets/{budget_id}/adjustments",
        headers=_headers(world),
        json={
            "label": "Sneaky fee", "type": "surcharge", "mode": "percent",
            "value": "5", "role": "production_fee",
        },
    )
    assert response.status_code == 422, response.text
    (fee,) = [row for row in example["sheet"]["adjustments"] if row["role"]]
    patched = pg_client.patch(
        f"/budgets/{budget_id}/adjustments/{fee['id']}",
        headers=_headers(world),
        json={"value": "50"},
    )
    assert patched.status_code == 409, patched.text
    assert "Production fee control" in patched.json()["detail"]
    removed = pg_client.post(
        f"/budgets/{budget_id}/adjustments/{fee['id']}/remove", headers=_headers(world)
    )
    assert removed.status_code == 409, removed.text
    _amount(_calc(pg_client, world, budget_id)["result"]["net"], "12517.20", what="net")


# ─── Refusals ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "percent": "101"},
        {"enabled": True, "percent": "-1"},
        {"enabled": True, "percent": "8", "kinds": ["equipment"]},
        {"enabled": True},
    ],
    ids=["over-100", "negative", "unknown-kind", "no-percent"],
)
def test_a_bad_fee_is_refused_and_nothing_changes(pg_client, world, example, body):
    budget_id = example["budget"]["id"]
    response = _set_fee(pg_client, world, budget_id, **body)
    assert response.status_code == 422, response.text
    _amount(_fee(pg_client, world, budget_id)["percent"], "8", what="unchanged")


def test_a_bad_preset_is_refused(pg_client, world):
    response = pg_client.put(
        "/company/production-fee", headers=_headers(world), json={"percent": "8", "kinds": ["nope"]}
    )
    assert response.status_code == 422, response.text
    assert _preset(pg_client, world)["percent"] is None


def test_a_locked_budget_refuses_a_fee_change(pg_client, world, example):
    budget_id = example["budget"]["id"]
    assert pg_client.post(
        f"/budgets/{budget_id}/lock", headers=_headers(world), json={}
    ).status_code == 200
    response = _set_fee(pg_client, world, budget_id, enabled=True, percent="12")
    assert response.status_code == 409, response.text
    reset = pg_client.post(
        f"/budgets/{budget_id}/production-fee/reset", headers=_headers(world)
    )
    assert reset.status_code == 409, reset.text
    _amount(_fee(pg_client, world, budget_id)["percent"], "8", what="unchanged")


# ─── Who may ────────────────────────────────────────────────────────────────


@pytest.fixture
def every_role(pg_db, world):
    users = {}
    for role in CompanyRole:
        user = make_user(pg_db, f"fee-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


@pytest.mark.parametrize("role", list(CompanyRole), ids=lambda r: r.value)
def test_setting_a_budget_s_fee_follows_the_budget_edit_matrix(
    pg_client, world, example, every_role, role
):
    response = pg_client.put(
        f"/budgets/{example['budget']['id']}/production-fee",
        headers=auth_headers(every_role[role], world["company"]),
        json={"enabled": True, "percent": "9"},
    )
    expected = 200 if role in PERMISSIONS["budgets.edit"] else 404
    assert response.status_code == expected, f"{role.value}: {response.text}"


@pytest.mark.parametrize("role", list(CompanyRole), ids=lambda r: r.value)
def test_the_preset_follows_the_company_settings_matrix(
    pg_client, world, every_role, role
):
    """Read AND write are `company.settings.edit` — owner and admin. The fee
    is kept off `GET /company`, which every member (Staff too) may read."""
    headers = auth_headers(every_role[role], world["company"])
    expected = 200 if role in PERMISSIONS["company.settings.edit"] else 404
    assert pg_client.get("/company/production-fee", headers=headers).status_code == expected
    response = pg_client.put(
        "/company/production-fee", headers=headers, json={"percent": "8", "kinds": []}
    )
    assert response.status_code == expected, f"{role.value}: {response.text}"
    company = pg_client.get("/company", headers=headers)
    if company.status_code == 200:
        assert "production_fee" not in company.text
