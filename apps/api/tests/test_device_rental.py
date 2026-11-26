"""A device's rental prices are its catalog item's rates (B2b-2 part 1).

Real PostgreSQL throughout. The thing this file is about is ONE SOURCE OF
TRUTH: what the device page shows and writes is the linked equipment item's
day rates and factor table, so every test that writes on one side reads the
other side back — the catalog item from the database, and a budget line
made from that item.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.catalog import Item, ItemCategory, Unit
from apps.api.models.company import CompanyRole
from apps.api.models.device import Device
from apps.api.models.site_settings import SiteSettings
from apps.api.services import catalog_service, number_series, tax_service
from apps.api.services.permissions import PERMISSIONS

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_item,
    make_rental_factor_group,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "rental-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    catalog_service.seed_catalog(pg_db, company=company)
    number_series.seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world, user=None, company=None):
    return auth_headers(user or world["owner"], company or world["company"])


def _member(pg_db, world, role, email):
    user = make_user(pg_db, email)
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


def _setup(pg_client, world, device_id, headers=None):
    return pg_client.post(f"/devices/{device_id}/rental/setup", headers=headers or _h(world))


def _put(pg_client, world, device_id, body, headers=None):
    return pg_client.put(f"/devices/{device_id}/rental", headers=headers or _h(world), json=body)


def _item_rates(pg_db, item_id) -> dict:
    pg_db.expire_all()
    item = pg_db.get(Item, item_id)
    return {(rate.rate_type, rate.period_unit): rate.amount for rate in item.rates}


def _actions(pg_db, entity_id) -> list[str]:
    return [
        event.action
        for event in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ]


# ─── Set up ─────────────────────────────────────────────────────────────────


def test_set_up_creates_exactly_one_day_priced_equipment_item_and_links_it(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    before = pg_db.query(Item).filter(Item.company_id == world["company"].id).count()

    response = _setup(pg_client, world, device.id)
    assert response.status_code == 200, response.text
    rental = response.json()["rental"]
    assert rental["linked"] is True and rental["item_name"] == "Alexa 35"

    pg_db.expire_all()
    items = pg_db.query(Item).filter(Item.company_id == world["company"].id).all()
    assert len(items) == before + 1
    item = pg_db.get(Item, rental["item_id"])
    assert pg_db.get(Device, device.id).catalog_item_id == item.id
    assert item.kind == "equipment_rental"
    assert pg_db.get(Unit, item.unit_id).key == "day"
    category = pg_db.get(ItemCategory, item.category_id)
    assert (category.kind, category.parent_id) == ("equipment_rental", None)
    assert "item.created" in _actions(pg_db, item.id)
    linked = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == device.id, AuditEvent.action == "device.catalog_linked")
        .one()
    )
    assert linked.changes["item_created"] == [None, True]

    # Twice is refused: the device already has its item.
    again = _setup(pg_client, world, device.id)
    assert again.status_code == 409
    assert "already has its rental price" in again.json()["detail"]
    pg_db.expire_all()
    assert pg_db.query(Item).filter(Item.company_id == world["company"].id).count() == before + 1


def test_an_unlinked_item_of_the_same_name_is_used_instead_of_a_duplicate(pg_client, pg_db, world):
    existing = make_item(pg_db, world["company"], sku=None, name_de="alexa  35", bill_rate_day="900.00")
    device = make_device(pg_db, world["company"], name="Alexa 35")
    before = pg_db.query(Item).filter(Item.company_id == world["company"].id).count()

    offered = pg_client.get(f"/devices/{device.id}", headers=_h(world)).json()["rental"]
    assert offered["linked"] is False
    assert offered["existing_item"] == {"id": str(existing.id), "name": "alexa  35"}

    response = _setup(pg_client, world, device.id)
    assert response.status_code == 200, response.text
    rental = response.json()["rental"]
    assert rental["item_id"] == str(existing.id)
    assert rental["price_per_day"] == "900.00"
    pg_db.expire_all()
    assert pg_db.query(Item).filter(Item.company_id == world["company"].id).count() == before
    event = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == device.id, AuditEvent.action == "device.catalog_linked")
        .one()
    )
    assert event.changes["item_created"] == [None, False]


def test_an_item_another_device_already_uses_is_not_offered(pg_client, pg_db, world):
    item = make_item(pg_db, world["company"], sku=None, name_de="Alexa 35")
    first = make_device(pg_db, world["company"], name="Alexa 35")
    first.catalog_item_id = item.id
    first.is_active = False
    second = make_device(pg_db, world["company"], name="Alexa 35")
    pg_db.commit()
    rental = pg_client.get(f"/devices/{second.id}", headers=_h(world)).json()["rental"]
    assert rental["existing_item"] is None
    made = _setup(pg_client, world, second.id).json()["rental"]
    assert made["item_id"] != str(item.id)


# ─── One source of truth ────────────────────────────────────────────────────


def test_the_page_writes_the_items_rates_and_a_new_budget_line_takes_them(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    group = make_rental_factor_group(pg_db, world["company"])
    item_id = _setup(pg_client, world, device.id).json()["rental"]["item_id"]

    first = _put(
        pg_client,
        world,
        device.id,
        {"price_per_day": "1200.00", "cost_per_day": "300.00", "rental_factor_group_id": str(group.id)},
    )
    assert first.status_code == 200, first.text
    rental = first.json()["rental"]
    assert (rental["price_per_day"], rental["cost_per_day"]) == ("1200.00", "300.00")
    assert rental["rental_factor_group_name"] == group.name
    # The catalog item itself, from the database.
    assert _item_rates(pg_db, item_id) == {("bill", "day"): Decimal("1200.00"), ("cost", "day"): Decimal("300.00")}
    # And through the catalog's own API: the same numbers.
    catalog = pg_client.get(f"/catalog/items/{item_id}", headers=_h(world)).json()
    assert {(r["rate_type"], r["amount"]) for r in catalog["rates"]} == {("bill", "1200.00"), ("cost", "300.00")}
    assert catalog["rental_factor_group_id"] == str(group.id)

    project = pg_client.post(
        "/projects", headers=_h(world), json={"name": "Spot", "start_date": "2026-06-01"}
    ).json()
    budget = pg_client.post(
        f"/projects/{project['id']}/budgets", headers=_h(world), json={"name": "Estimate"}
    ).json()
    section = pg_client.post(
        f"/budgets/{budget['id']}/sections", headers=_h(world), json={"name": "Camera", "code": "1200"}
    ).json()

    def new_line():
        response = pg_client.post(
            f"/budgets/{budget['id']}/lines",
            headers=_h(world),
            json={"section_id": section["id"], "item_id": item_id},
        )
        assert response.status_code == 201, response.text
        return response.json()

    old = new_line()
    assert (old["unit_price"], old["unit_cost"]) == ("1200.00", "300.00")

    # Changed on the device page: 1200 → 1450 per day, cost 300 → 350.
    changed = _put(pg_client, world, device.id, {"price_per_day": "1450.00", "cost_per_day": "350.00"})
    assert changed.status_code == 200, changed.text
    fresh = new_line()
    assert (fresh["unit_price"], fresh["unit_cost"]) == ("1450.00", "350.00")
    sheet = pg_client.get(f"/budgets/{budget['id']}", headers=_h(world)).json()
    kept = next(line for line in sheet["lines"] if line["id"] == old["id"])
    assert kept["unit_price"] == "1200.00"  # lines copy rates and never follow (A2)
    assert kept["catalog_difference"]["catalog_unit_price"] == "1450.00"


def test_only_the_fields_sent_change_and_other_rates_survive(pg_client, pg_db, world):
    item = make_item(pg_db, world["company"], sku=None, name_de="Alexa 35", bill_rate_day="1000.00", cost_rate_day="200.00")
    from apps.api.models.catalog import ItemRate

    pg_db.add(ItemRate(company_id=world["company"].id, item_id=item.id, rate_type="bill", period_unit="week", amount=Decimal("4000.00"), currency="EUR"))
    device = make_device(pg_db, world["company"], name="Alexa 35")
    device.catalog_item_id = item.id
    pg_db.commit()

    response = _put(pg_client, world, device.id, {"price_per_day": "1100.00"})
    assert response.status_code == 200, response.text
    assert _item_rates(pg_db, item.id) == {
        ("bill", "day"): Decimal("1100.00"),
        ("cost", "day"): Decimal("200.00"),
        ("bill", "week"): Decimal("4000.00"),
    }
    cleared = _put(pg_client, world, device.id, {"cost_per_day": None})
    assert cleared.json()["rental"]["cost_per_day"] is None
    assert ("cost", "day") not in _item_rates(pg_db, item.id)


def test_the_rate_change_is_the_catalogs_own_event_and_not_duplicated(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    item_id = _setup(pg_client, world, device.id).json()["rental"]["item_id"]
    _put(pg_client, world, device.id, {"price_per_day": "800.00"})
    events = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "item.rates_changed", AuditEvent.entity_id == item_id)
        .all()
    )
    assert len(events) == 1
    assert events[0].changes["bill/day"] == [None, "800.00"]
    assert "device.updated" not in _actions(pg_db, device.id)


def test_without_a_linked_item_the_rates_cannot_be_written(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Lamp")
    response = _put(pg_client, world, device.id, {"price_per_day": "50.00"})
    assert response.status_code == 422
    assert "Set up rental price" in response.json()["detail"]


def test_a_negative_rate_and_a_retired_factor_table_are_refused(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    _setup(pg_client, world, device.id)
    negative = _put(pg_client, world, device.id, {"price_per_day": "-5.00"})
    assert negative.status_code == 422 and "cannot be negative" in negative.json()["detail"]
    retired = make_rental_factor_group(pg_db, world["company"], name="Alt", is_active=False)
    refused = _put(pg_client, world, device.id, {"rental_factor_group_id": str(retired.id)})
    assert refused.status_code == 422 and "retired" in refused.json()["detail"]


# ─── Permissions ────────────────────────────────────────────────────────────


def test_rates_need_catalog_edit_as_well_as_devices_edit(pg_client, pg_db, world, monkeypatch):
    """A producer whose role holds `devices.edit` but not `catalog.edit`.

    No role is like that by default, so the matrix is changed for this test —
    on the dict `has_permission` reads, not on a copy of it.
    """
    device = make_device(pg_db, world["company"], name="Alexa 35")
    _setup(pg_client, world, device.id)
    _put(pg_client, world, device.id, {"price_per_day": "1000.00"})
    producer = _member(pg_db, world, CompanyRole.producer, "rental-producer@example.com")
    monkeypatch.setitem(PERMISSIONS, "catalog.edit", {CompanyRole.owner, CompanyRole.admin})

    headers = _h(world, producer)
    read = pg_client.get(f"/devices/{device.id}", headers=headers)
    assert read.status_code == 200
    rental = read.json()["rental"]
    assert rental["can_edit"] is False
    assert "catalog edit permission" in rental["edit_blocked_reason"]
    assert rental["price_per_day"] == "1000.00"

    refused = _put(pg_client, world, device.id, {"price_per_day": "1.00"}, headers=headers)
    assert refused.status_code == 404
    item_id = rental["item_id"]
    assert _item_rates(pg_db, item_id) == {("bill", "day"): Decimal("1000.00")}

    other = make_device(pg_db, world["company"], name="Venice 2")
    assert _setup(pg_client, world, other.id, headers=headers).status_code == 404
    pg_db.expire_all()
    assert pg_db.get(Device, other.id).catalog_item_id is None

    # The very same requests from the owner succeed: the guard is the key.
    assert _put(pg_client, world, device.id, {"price_per_day": "2.00"}).status_code == 200


@pytest.mark.parametrize("role", [CompanyRole.staff], ids=["staff"])
def test_staff_and_roles_without_budgets_view_get_no_price_and_no_cost(pg_client, pg_db, world, role):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    _setup(pg_client, world, device.id)
    _put(pg_client, world, device.id, {"price_per_day": "1357.00", "cost_per_day": "246.00"})
    member = _member(pg_db, world, role, f"rental-{role.value}@example.com")

    response = pg_client.get(f"/devices/{device.id}", headers=_h(world, member))
    assert response.status_code == 200
    rental = response.json()["rental"]
    # The positive marker: withheld, and the item named.
    assert rental["rates_visible"] is False and rental["item_name"] == "Alexa 35"
    assert rental["price_per_day"] is None and rental["cost_per_day"] is None
    for secret in ("1357", "246.00"):
        assert secret not in response.text
    assert _setup(pg_client, world, device.id, headers=_h(world, member)).status_code == 404
    assert _put(pg_client, world, device.id, {"price_per_day": "1.00"}, headers=_h(world, member)).status_code == 404


def test_the_accountant_sees_the_rates_but_cannot_change_them(pg_client, pg_db, world):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    _setup(pg_client, world, device.id)
    _put(pg_client, world, device.id, {"price_per_day": "1000.00", "cost_per_day": "250.00"})
    accountant = _member(pg_db, world, CompanyRole.accountant, "rental-accountant@example.com")
    rental = pg_client.get(f"/devices/{device.id}", headers=_h(world, accountant)).json()["rental"]
    assert (rental["price_per_day"], rental["cost_per_day"]) == ("1000.00", "250.00")
    assert rental["can_edit"] is False and "gear edit permission" in rental["edit_blocked_reason"]


def test_a_cost_is_refused_from_a_role_without_budgets_view(pg_client, pg_db, world, monkeypatch):
    device = make_device(pg_db, world["company"], name="Alexa 35")
    _setup(pg_client, world, device.id)
    producer = _member(pg_db, world, CompanyRole.producer, "rental-nobudget@example.com")
    monkeypatch.setitem(PERMISSIONS, "budgets.view", {CompanyRole.owner})
    headers = _h(world, producer)
    refused = _put(pg_client, world, device.id, {"cost_per_day": "100.00"}, headers=headers)
    assert refused.status_code == 422 and "cost per day" in refused.json()["detail"]
    allowed = _put(pg_client, world, device.id, {"price_per_day": "100.00"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["rental"]["cost_per_day"] is None


# ─── Isolation ──────────────────────────────────────────────────────────────


def test_another_companys_device_and_factor_table_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH", address_country="AT")
    catalog_service.seed_catalog(pg_db, company=other)
    theirs = make_device(pg_db, other, name="Alexa 35")
    their_group = make_rental_factor_group(pg_db, other, name="Theirs")
    pg_db.commit()

    assert _setup(pg_client, world, theirs.id).status_code == 404
    assert _put(pg_client, world, theirs.id, {"price_per_day": "1.00"}).status_code == 404
    pg_db.expire_all()
    assert pg_db.get(Device, theirs.id).catalog_item_id is None

    mine = make_device(pg_db, world["company"], name="Mine")
    _setup(pg_client, world, mine.id)
    response = _put(pg_client, world, mine.id, {"rental_factor_group_id": str(their_group.id)})
    assert response.status_code == 404

    # Their unlinked item of the same name is never offered to us.
    make_item(pg_db, other, sku=None, name_de="Venice 2")
    venice = make_device(pg_db, world["company"], name="Venice 2")
    assert pg_client.get(f"/devices/{venice.id}", headers=_h(world)).json()["rental"]["existing_item"] is None
