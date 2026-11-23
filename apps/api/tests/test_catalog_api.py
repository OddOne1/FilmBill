"""The catalog endpoints, against a real PostgreSQL.

Twenty-two endpoints and no DELETE, asserted on the OpenAPI document rather
than on a handler — the same shape as parties' and P1a-2's, and the same
reason: the way to keep "nothing in the catalog is ever deleted" true is for
there to be no route.

Four themes:

  * **the seeding**, because a company with no units and no categories is a
    company whose first item cannot be filed or priced;
  * **who may do what**, over the whole role ladder rather than a sample.
    View is everyone but the tax advisor — staff included, which is the one
    place this matrix is wider than parties' — and edit is owner, admin,
    producer;
  * **the import applies exactly what the preview showed**, which is checked
    by comparing the preview's own rows against the rows in the database
    afterwards, and by proving a stale preview is refused;
  * **an item is a referrer**, so deactivating the tax class it points at is
    refused with the item named. That is P1a-2's registry doing its job
    through a source registered by `services/catalog_service.py`.
"""

import io
import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.catalog import Item, ItemCategory, ItemRate, Unit
from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxClass
from apps.api.services import catalog_service, tax_service
from apps.api.services.permissions import PERMISSIONS, permissions_for

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_item,
    make_item_category,
    make_superadmin,
    make_user,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "calc"

#: The roles that may READ the price list, and the roles that may change it.
#: Written out rather than derived from `PERMISSIONS`, because a test that
#: read the same literal it is checking would pass for any matrix at all.
VIEW_ROLES = (
    CompanyRole.owner,
    CompanyRole.admin,
    CompanyRole.accountant,
    CompanyRole.producer,
    CompanyRole.staff,
)
EDIT_ROLES = (CompanyRole.owner, CompanyRole.admin, CompanyRole.producer)
ALL_ROLES = tuple(CompanyRole)


@pytest.fixture
def world(pg_db):
    """An Austrian company with its tax pack, its catalog seeded, one owner."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    # Not `catalog-owner@…`: the `every_role` fixture below creates one
    # user per role, and `CompanyRole.owner` would be exactly that
    # address — a unique-violation on `users.email` that fails as a
    # fixture error in twenty unrelated tests.
    owner = make_user(pg_db, "catalog-world-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    catalog_service.seed_catalog(pg_db, company=company)
    pg_db.commit()
    return {"company": company, "owner": owner, "db": pg_db}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _unit(pg_client, world, key="day"):
    units = pg_client.get("/catalog/units", headers=_headers(world)).json()
    return next(unit for unit in units if unit["key"] == key)


def _category(pg_client, world, kind="equipment_rental"):
    categories = pg_client.get(
        "/catalog/categories", headers=_headers(world)
    ).json()
    return next(row for row in categories if row["kind"] == kind)


def _create_item(pg_client, world, **body):
    body.setdefault("name_de", "Arri Alexa 35")
    body.setdefault("kind", "equipment_rental")
    response = pg_client.post("/catalog/items", headers=_headers(world), json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ─── The surface ────────────────────────────────────────────────────────────


def test_there_is_no_delete_route_anywhere_in_the_catalog(client):
    """Asserted on the document the app serves, not on a handler.

    A handler that refused DELETE would still be a route somebody could make
    work; the guarantee is that there is nothing to call. Includes `/calc`,
    which has nothing to delete either.
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    deletes = [
        f"DELETE {path}"
        for path, operations in schema["paths"].items()
        if (path.startswith("/catalog") or path.startswith("/calc"))
        and "delete" in operations
    ]
    assert not deletes, (
        f"the catalog has a DELETE route: {deletes}. An item that has been on "
        f"a quote is part of that quote's explanation; retiring is "
        f"POST .../deactivate."
    )


def test_the_catalog_has_routes_for_that_check_to_be_about(client):
    """A glob that matched nothing would make the assertion above vacuous."""
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    catalog_paths = [p for p in schema["paths"] if p.startswith("/catalog")]
    assert len(catalog_paths) >= 10, catalog_paths


def test_both_permission_keys_exist_and_nobody_outside_the_matrix_holds_them():
    assert "catalog.view" in PERMISSIONS
    assert "catalog.edit" in PERMISSIONS
    assert PERMISSIONS["catalog.view"] == set(VIEW_ROLES)
    assert PERMISSIONS["catalog.edit"] == set(EDIT_ROLES)
    # The tax advisor's access is the archive (D23/D43), not the live list.
    assert CompanyRole.tax_advisor not in PERMISSIONS["catalog.view"]


def test_the_permission_list_the_web_app_is_served_names_the_catalog():
    """The nav entry is rendered from this list (§3), so a key missing from
    it is a Catalog menu that never appears for anybody."""
    assert "catalog.view" in permissions_for(CompanyRole.staff)
    assert "catalog.edit" not in permissions_for(CompanyRole.staff)
    assert "catalog.edit" in permissions_for(CompanyRole.producer)


# ─── Seeding ────────────────────────────────────────────────────────────────


def test_a_new_company_gets_the_system_units_and_one_category_per_kind(pg_db):
    """Through `create_company`, which is where a real company comes from."""
    from apps.api.billing.catalog import ITEM_KINDS, SYSTEM_UNIT_KEYS
    from apps.api.services.company_service import create_company

    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    founder = make_user(pg_db, "founder@example.com")
    company = create_company(pg_db, owner=founder, legal_name="Neue Produktion OG")
    pg_db.commit()

    units = pg_db.query(Unit).filter(Unit.company_id == company.id).all()
    assert {unit.key for unit in units} == set(SYSTEM_UNIT_KEYS)
    assert all(unit.is_system for unit in units)
    # The labels are German, because that is the language of the office this
    # is built for — and they are editable, which `test_a_system_units_label
    # _can_be_changed` covers.
    assert {unit.label_de for unit in units} >= {"Tag", "Stunde", "Pauschal"}

    categories = (
        pg_db.query(ItemCategory)
        .filter(ItemCategory.company_id == company.id)
        .all()
    )
    assert {row.kind for row in categories} == set(ITEM_KINDS)
    assert all(row.parent_id is None for row in categories)


def test_seeding_twice_adds_nothing_and_overwrites_nothing(pg_db, world):
    company = world["company"]
    before = pg_db.query(Unit).filter(Unit.company_id == company.id).count()
    renamed = (
        pg_db.query(Unit)
        .filter(Unit.company_id == company.id, Unit.key == "day")
        .one()
    )
    renamed.label_de = "Drehtag"
    pg_db.commit()

    added = catalog_service.seed_catalog(pg_db, company=company)
    pg_db.commit()

    assert added == {"units": 0, "categories": 0}
    assert pg_db.query(Unit).filter(Unit.company_id == company.id).count() == before
    pg_db.refresh(renamed)
    assert renamed.label_de == "Drehtag"


def test_a_renamed_default_category_does_not_get_a_second_one(pg_db, world):
    """Matched on KIND, not on name — deliberately looser than the unit
    check beside it. Somebody who renamed "Equipment-Miete" to "Technik"
    must not get a second default category for that kind on the next seed."""
    company = world["company"]
    category = (
        pg_db.query(ItemCategory)
        .filter(
            ItemCategory.company_id == company.id,
            ItemCategory.kind == "equipment_rental",
        )
        .one()
    )
    category.name = "Technik"
    pg_db.commit()

    catalog_service.seed_catalog(pg_db, company=company)
    pg_db.commit()

    assert (
        pg_db.query(ItemCategory)
        .filter(
            ItemCategory.company_id == company.id,
            ItemCategory.kind == "equipment_rental",
        )
        .count()
        == 1
    )


def test_a_seeded_units_label_can_be_changed_but_its_key_cannot(pg_client, world):
    unit = _unit(pg_client, world)
    response = pg_client.patch(
        f"/catalog/units/{unit['id']}",
        headers=_headers(world),
        json={"label_de": "Drehtag"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["label_de"] == "Drehtag"
    assert response.json()["key"] == "day"
    # `key` is not a field on the write schema at all, so sending one is a
    # refusal rather than a silent no-op.
    refused = pg_client.patch(
        f"/catalog/units/{unit['id']}",
        headers=_headers(world),
        json={"label_de": "Drehtag", "key": "shootday"},
    )
    assert refused.status_code == 422


def test_a_unit_of_the_companys_own_needs_no_english_label(pg_client, world):
    """`units.label_en` is nullable, like `items.name_en`.

    A company inventing "Drehtag" need not also think of an English word for
    it. This is a test rather than a comment because the column was NOT NULL
    first, and the only symptom was a 500 on a form that looked complete.
    """
    response = pg_client.post(
        "/catalog/units",
        headers=_headers(world),
        json={"key": "drehtag", "label_de": "Drehtag", "period_unit": "day"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["label_en"] is None
    assert response.json()["is_system"] is False


def test_two_units_cannot_share_a_key(pg_client, world):
    """`day` is seeded, so this is the collision somebody actually hits."""
    response = pg_client.post(
        "/catalog/units",
        headers=_headers(world),
        json={"key": "day", "label_de": "Noch ein Tag", "period_unit": "day"},
    )
    assert response.status_code == 409
    assert "day" in response.json()["detail"]


def test_a_unit_in_use_cannot_be_switched_off(pg_client, world):
    unit = _unit(pg_client, world)
    _create_item(pg_client, world, unit_id=unit["id"])
    response = pg_client.patch(
        f"/catalog/units/{unit['id']}",
        headers=_headers(world),
        json={"label_de": "Tag", "is_active": False},
    )
    assert response.status_code == 409
    assert "1 active item" in response.json()["detail"]


# ─── Permissions ────────────────────────────────────────────────────────────


@pytest.fixture
def every_role(pg_db, world):
    """One user per company role, all in the same company."""
    people = {}
    for role in ALL_ROLES:
        user = make_user(
            pg_db,
            f"catalog-{role.value}@example.com",
            # The tax advisor has 2FA on, because the role requires it
            # unconditionally (`services/permissions.roles_requiring_2fa`)
            # and a member of it without 2FA would be pushed into enrolment
            # rather than refused for the permission this test is about.
            two_factor_enabled=role == CompanyRole.tax_advisor,
        )
        grant(pg_db, world["company"], user, role)
        people[role] = user
    pg_db.commit()
    return people


@pytest.mark.parametrize("role", ALL_ROLES)
def test_who_may_read_the_price_list(pg_client, world, every_role, role):
    response = pg_client.get(
        "/catalog/items",
        headers=auth_headers(every_role[role], world["company"]),
    )
    expected = 200 if role in VIEW_ROLES else 404
    assert response.status_code == expected, f"{role.value}: {response.text}"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_who_may_change_the_price_list(pg_client, world, every_role, role):
    response = pg_client.post(
        "/catalog/items",
        headers=auth_headers(every_role[role], world["company"]),
        json={"name_de": "Testposten", "kind": "fee"},
    )
    expected = 201 if role in EDIT_ROLES else 404
    assert response.status_code == expected, f"{role.value}: {response.text}"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_who_may_ask_the_engine(pg_client, world, every_role, role):
    """`catalog.view`, not an edit key: somebody planning a shoot needs these
    numbers and must not need the right to change prices to get them."""
    response = pg_client.post(
        "/calc/preview",
        headers=auth_headers(every_role[role], world["company"]),
        json={"document": {"children": [{"id": "l1", "unit_price": "100.00"}]}},
    )
    expected = 200 if role in VIEW_ROLES else 404
    assert response.status_code == expected, f"{role.value}: {response.text}"


def test_a_role_without_the_permission_gets_a_404_and_not_a_403(
    pg_client, world, every_role
):
    """The same answer as a company they are not in. A 403 would confirm the
    row exists to somebody who may not see it."""
    response = pg_client.get(
        "/catalog/items",
        headers=auth_headers(every_role[CompanyRole.tax_advisor], world["company"]),
    )
    assert response.status_code == 404


# ─── Items ──────────────────────────────────────────────────────────────────


def test_an_item_round_trips_with_its_rates(pg_client, world):
    unit = _unit(pg_client, world)
    category = _category(pg_client, world)
    created = _create_item(
        pg_client,
        world,
        sku="CAM-001",
        name_en="Arri Alexa 35",
        category_id=category["id"],
        unit_id=unit["id"],
        default_tax_class="standard",
        rates=[
            {"rate_type": "bill", "period_unit": "day", "amount": "1200.00"},
            {"rate_type": "cost", "period_unit": "day", "amount": "600.00"},
        ],
    )
    assert created["category_path"] == category["path"]
    assert created["unit_key"] == "day"
    assert {(r["rate_type"], r["amount"]) for r in created["rates"]} == {
        ("bill", "1200.00"),
        ("cost", "600.00"),
    }

    read = pg_client.get(
        f"/catalog/items/{created['id']}", headers=_headers(world)
    )
    assert read.status_code == 200
    assert read.json() == created


def test_money_crosses_the_wire_as_a_string(pg_client, world):
    """Rule 1. A JSON number would already have lost whatever it lost."""
    created = _create_item(
        pg_client,
        world,
        rates=[{"rate_type": "bill", "period_unit": "day", "amount": "1200.00"}],
    )
    raw = json.loads(
        pg_client.get(
            f"/catalog/items/{created['id']}", headers=_headers(world)
        ).content
    )
    assert raw["rates"][0]["amount"] == "1200.00"
    assert isinstance(raw["rates"][0]["amount"], str)


def test_a_rate_sent_as_a_json_number_is_refused(pg_client, world):
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={
            "name_de": "Float",
            "kind": "fee",
            "rates": [{"rate_type": "bill", "period_unit": "day", "amount": 1200.5}],
        },
    )
    assert response.status_code == 422


def test_an_sku_is_unique_within_the_company(pg_client, world):
    _create_item(pg_client, world, sku="CAM-001")
    again = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={"name_de": "Zweite Kamera", "kind": "equipment_rental", "sku": "CAM-001"},
    )
    assert again.status_code == 409
    assert "CAM-001" in again.json()["detail"]


def test_the_same_sku_in_another_company_is_fine(pg_db, world):
    """The index is `(company_id, sku)`. Two production companies both
    numbering their first camera CAM-001 is the normal case."""
    other = make_company(pg_db, "Andere Produktion GmbH")
    make_item(pg_db, world["company"], sku="CAM-001")
    make_item(pg_db, other, sku="CAM-001")
    assert pg_db.query(Item).filter(Item.sku == "CAM-001").count() == 2


def test_two_items_with_no_sku_are_both_allowed(pg_client, world):
    """`uq_items_company_sku` is partial — `WHERE sku IS NOT NULL` — because
    "no SKU" is not an identity two items can collide on."""
    _create_item(pg_client, world, name_de="Erstes")
    _create_item(pg_client, world, name_de="Zweites")
    rows = pg_client.get("/catalog/items", headers=_headers(world)).json()
    assert len([row for row in rows["items"] if row["sku"] is None]) == 2


def test_an_empty_sku_string_is_stored_as_no_sku(pg_client, world):
    """Otherwise two items with an empty box in the form would collide
    while two with no SKU do not."""
    first = _create_item(pg_client, world, name_de="Erstes", sku="")
    second = _create_item(pg_client, world, name_de="Zweites", sku="   ")
    assert first["sku"] is None and second["sku"] is None


def test_an_item_is_retired_and_never_deleted(pg_client, world):
    created = _create_item(pg_client, world, sku="CAM-001")
    retired = pg_client.post(
        f"/catalog/items/{created['id']}/deactivate", headers=_headers(world)
    )
    assert retired.status_code == 200
    assert retired.json()["is_active"] is False

    listed = pg_client.get("/catalog/items", headers=_headers(world)).json()
    assert listed["total"] == 0
    with_inactive = pg_client.get(
        "/catalog/items?include_inactive=true", headers=_headers(world)
    ).json()
    assert with_inactive["total"] == 1

    back = pg_client.post(
        f"/catalog/items/{created['id']}/reactivate", headers=_headers(world)
    )
    assert back.json()["is_active"] is True


def test_searching_finds_an_item_by_sku_or_by_either_name(pg_client, world):
    _create_item(
        pg_client, world, sku="CAM-001", name_de="Arri Alexa 35", name_en="Alexa 35"
    )
    _create_item(pg_client, world, sku="CREW-001", name_de="Oberbeleuchter", kind="labor")
    for needle, expected in (
        ("CAM", "CAM-001"),
        ("alexa", "CAM-001"),
        ("beleucht", "CREW-001"),
    ):
        found = pg_client.get(
            f"/catalog/items?search={needle}", headers=_headers(world)
        ).json()
        assert [row["sku"] for row in found["items"]] == [expected], needle


def test_the_list_pages(pg_client, world):
    for index in range(5):
        _create_item(pg_client, world, sku=f"X-{index}", name_de=f"Posten {index}")
    page = pg_client.get(
        "/catalog/items?limit=2&offset=2", headers=_headers(world)
    ).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert (page["limit"], page["offset"]) == (2, 2)


def test_filters_narrow_the_list(pg_client, world):
    category = _category(pg_client, world, kind="labor")
    _create_item(pg_client, world, sku="CAM-001")
    _create_item(
        pg_client,
        world,
        sku="CREW-001",
        name_de="Oberbeleuchter",
        kind="labor",
        category_id=category["id"],
    )
    by_kind = pg_client.get(
        "/catalog/items?kind=labor", headers=_headers(world)
    ).json()
    assert [row["sku"] for row in by_kind["items"]] == ["CREW-001"]
    by_category = pg_client.get(
        f"/catalog/items?category_id={category['id']}", headers=_headers(world)
    ).json()
    assert [row["sku"] for row in by_category["items"]] == ["CREW-001"]


def test_an_item_cannot_point_at_a_tax_class_this_company_does_not_have(
    pg_client, world
):
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={
            "name_de": "Posten",
            "kind": "fee",
            "default_tax_class": "super_reduced",
        },
    )
    assert response.status_code == 422
    assert "super_reduced" in response.json()["detail"]


def test_an_item_cannot_point_at_a_deactivated_tax_class(pg_client, world, pg_db):
    """`reduced_3` ships inactive in the AT pack, with its code, because a
    rate switched half-on is a state nothing on screen can explain."""
    inactive = (
        pg_db.query(TaxClass)
        .filter(
            TaxClass.company_id == world["company"].id,
            TaxClass.is_active.is_(False),
        )
        .first()
    )
    assert inactive is not None, "the AT pack is expected to ship one inactive class"
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={"name_de": "Posten", "kind": "fee", "default_tax_class": inactive.key},
    )
    assert response.status_code == 422


def test_an_item_cannot_be_filed_under_a_retired_category(pg_client, world):
    category = _category(pg_client, world, kind="storage")
    pg_client.post(
        f"/catalog/categories/{category['id']}/deactivate", headers=_headers(world)
    )
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={"name_de": "Posten", "kind": "storage", "category_id": category["id"]},
    )
    assert response.status_code == 422
    assert "retired" in response.json()["detail"]


def test_rebilled_cost_only_means_something_on_a_travel_item(pg_client, world):
    """SCOPE §4.1: the flag marks a cost passed through rather than a service
    sold. Set on an equipment item it is noise, and the API says so instead
    of storing it."""
    refused = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={"name_de": "Kamera", "kind": "equipment_rental", "rebilled_cost": True},
    )
    assert refused.status_code == 422
    assert "travel" in refused.json()["detail"]

    allowed = _create_item(
        pg_client, world, name_de="Taxi", kind="travel", rebilled_cost=True
    )
    assert allowed["rebilled_cost"] is True


def test_a_negative_rate_is_refused_and_named_as_not_a_discount(pg_client, world):
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={
            "name_de": "Rabattposten",
            "kind": "fee",
            "rates": [
                {"rate_type": "bill", "period_unit": "day", "amount": "-100.00"}
            ],
        },
    )
    assert response.status_code == 422


def test_replacing_the_rates_removes_the_ones_left_out(pg_client, world):
    """"This item has no cost rate" cannot be said any other way."""
    created = _create_item(
        pg_client,
        world,
        rates=[
            {"rate_type": "bill", "period_unit": "day", "amount": "1200.00"},
            {"rate_type": "cost", "period_unit": "day", "amount": "600.00"},
        ],
    )
    response = pg_client.put(
        f"/catalog/items/{created['id']}/rates",
        headers=_headers(world),
        json=[{"rate_type": "bill", "period_unit": "day", "amount": "1300.00"}],
    )
    assert response.status_code == 200
    assert [(r["rate_type"], r["amount"]) for r in response.json()] == [
        ("bill", "1300.00")
    ]


def test_one_rate_per_type_and_period(pg_client, world):
    response = pg_client.post(
        "/catalog/items",
        headers=_headers(world),
        json={
            "name_de": "Posten",
            "kind": "fee",
            "rates": [
                {"rate_type": "bill", "period_unit": "day", "amount": "100.00"},
                {"rate_type": "bill", "period_unit": "day", "amount": "200.00"},
            ],
        },
    )
    assert response.status_code == 422
    assert "two answers" in response.json()["detail"]


def test_an_items_rates_are_stored_as_decimals_in_the_database(pg_db, world):
    item = make_item(pg_db, world["company"], bill_rate_day="1200.00")
    rate = (
        pg_db.query(ItemRate)
        .filter(ItemRate.item_id == item.id, ItemRate.rate_type == "bill")
        .one()
    )
    assert isinstance(rate.amount, Decimal)
    assert rate.amount == Decimal("1200.00")


# ─── Categories ─────────────────────────────────────────────────────────────


def test_a_category_tree_may_be_three_levels_deep_and_no_more(pg_client, world):
    root = _category(pg_client, world)
    second = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Kamera", "kind": "equipment_rental", "parent_id": root["id"]},
    )
    assert second.status_code == 201, second.text
    third = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={
            "name": "Cinema",
            "kind": "equipment_rental",
            "parent_id": second.json()["id"],
        },
    )
    assert third.status_code == 201, third.text
    assert third.json()["depth"] == 3
    assert third.json()["path"].count("›") == 2

    fourth = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={
            "name": "Zu tief",
            "kind": "equipment_rental",
            "parent_id": third.json()["id"],
        },
    )
    assert fourth.status_code == 409
    assert "3 levels" in fourth.json()["detail"]


def test_a_category_cannot_be_its_own_parent(pg_client, world):
    category = _category(pg_client, world)
    response = pg_client.patch(
        f"/catalog/categories/{category['id']}",
        headers=_headers(world),
        json={"name": category["name"], "parent_id": category["id"]},
    )
    assert response.status_code == 409
    assert "its own parent" in response.json()["detail"]


def test_a_cycle_is_refused_and_the_refusal_shows_the_route(pg_client, world):
    """A → B → C, then "make C the parent of A". Git-silent in a tree built
    with raw UPDATEs; refused here, with the route named."""
    a = _category(pg_client, world)
    b = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Kamera", "kind": "equipment_rental", "parent_id": a["id"]},
    ).json()
    c = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Cinema", "kind": "equipment_rental", "parent_id": b["id"]},
    ).json()

    response = pg_client.patch(
        f"/catalog/categories/{a['id']}",
        headers=_headers(world),
        json={"name": a["name"], "parent_id": c["id"]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "loop" in detail
    assert "Cinema" in detail and a["name"] in detail


def test_moving_a_branch_counts_what_is_already_under_it(pg_client, world):
    """Checking only the moved category's own new depth would allow this:
    a two-level branch dropped under a level-2 category puts its leaves at
    level 4."""
    root = _category(pg_client, world)
    second = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Kamera", "kind": "equipment_rental", "parent_id": root["id"]},
    ).json()

    branch = pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Licht", "kind": "equipment_rental"},
    ).json()
    pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "HMI", "kind": "equipment_rental", "parent_id": branch["id"]},
    )

    response = pg_client.patch(
        f"/catalog/categories/{branch['id']}",
        headers=_headers(world),
        json={"name": "Licht", "parent_id": second["id"]},
    )
    assert response.status_code == 409
    assert "would make 4" in response.json()["detail"]


def test_a_category_with_live_children_cannot_be_retired(pg_client, world):
    root = _category(pg_client, world)
    pg_client.post(
        "/catalog/categories",
        headers=_headers(world),
        json={"name": "Kamera", "kind": "equipment_rental", "parent_id": root["id"]},
    )
    response = pg_client.post(
        f"/catalog/categories/{root['id']}/deactivate", headers=_headers(world)
    )
    assert response.status_code == 409
    assert "sub-categor" in response.json()["detail"]


def test_a_category_carries_its_item_count(pg_client, world):
    category = _category(pg_client, world)
    _create_item(pg_client, world, category_id=category["id"])
    rows = pg_client.get("/catalog/categories", headers=_headers(world)).json()
    counted = next(row for row in rows if row["id"] == category["id"])
    assert counted["item_count"] == 1


# ─── Rental factor tables ───────────────────────────────────────────────────


@pytest.fixture
def factor_group(pg_client, world):
    """The A1 prompt's own table, through the endpoints."""
    group = pg_client.post(
        "/catalog/rental-factor-groups",
        headers=_headers(world),
        json={"name": "Standard-Mietfaktoren"},
    ).json()
    response = pg_client.put(
        f"/catalog/rental-factor-groups/{group['id']}/factors",
        headers=_headers(world),
        json=[
            {"days": 1, "factor": "1.0"},
            {"days": 2, "factor": "1.5"},
            {"days": 3, "factor": "2.0"},
            {"days": 5, "factor": "2.8"},
            {"days": 7, "factor": "3.5"},
        ],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_factor_table_round_trips_in_order(factor_group):
    assert [(row["days"], row["factor"]) for row in factor_group["factors"]] == [
        (1, "1.0000"),
        (2, "1.5000"),
        (3, "2.0000"),
        (5, "2.8000"),
        (7, "3.5000"),
    ]


@pytest.mark.parametrize(
    "days, factor, matched, rule",
    [
        (5, "2.8000", 5, "exact"),
        (4, "2.0000", 3, "next_lower"),
        (6, "2.8000", 5, "next_lower"),
        (9, "3.5000", 7, "highest_listed"),
    ],
)
def test_the_lookup_endpoint_answers_the_prompts_cases(
    pg_client, world, factor_group, days, factor, matched, rule
):
    """Over HTTP, because this is the surface the screen reads — and the
    screen has to be able to show WHICH row produced the number, since the
    rule itself is a guess (SCOPE D71)."""
    response = pg_client.get(
        f"/catalog/rental-factor-groups/{factor_group['id']}/factor?days={days}",
        headers=_headers(world),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["factor"], body["matched_days"], body["rule"]) == (
        factor,
        matched,
        rule,
    )


def test_four_days_is_not_rounded_up_over_http_either(
    pg_client, world, factor_group
):
    body = pg_client.get(
        f"/catalog/rental-factor-groups/{factor_group['id']}/factor?days=4",
        headers=_headers(world),
    ).json()
    assert body["factor"] == "2.0000"
    assert body["factor"] != "2.8000"


def test_zero_days_is_refused_by_the_endpoint(pg_client, world, factor_group):
    response = pg_client.get(
        f"/catalog/rental-factor-groups/{factor_group['id']}/factor?days=0",
        headers=_headers(world),
    )
    assert response.status_code == 422


def test_a_factor_table_refuses_two_factors_for_one_day_count(
    pg_client, world, factor_group
):
    response = pg_client.put(
        f"/catalog/rental-factor-groups/{factor_group['id']}/factors",
        headers=_headers(world),
        json=[{"days": 5, "factor": "2.8"}, {"days": 5, "factor": "3.0"}],
    )
    assert response.status_code == 422
    assert "two different factors" in response.json()["detail"]


def test_replacing_a_factor_table_removes_the_rows_left_out(
    pg_client, world, factor_group
):
    response = pg_client.put(
        f"/catalog/rental-factor-groups/{factor_group['id']}/factors",
        headers=_headers(world),
        json=[{"days": 1, "factor": "1.0"}],
    )
    assert [row["days"] for row in response.json()["factors"]] == [1]


# ─── The CSV import ─────────────────────────────────────────────────────────

#: A German price list: semicolons, decimal commas, `ja`, the seeded German
#: category and unit names — and one bad row, because the acceptance the A1
#: prompt wrote for Mathias has one.
IMPORT_CSV = (
    "sku;name_de;name_en;kind;category;unit;bill_rate_day;cost_rate_day;tax_class;active\n"
    "CAM-001;Arri Alexa 35;Arri Alexa 35;equipment_rental;Equipment-Miete;Tag;1.200,00;600,00;standard;ja\n"
    "CAM-002;Cooke S4 Satz;Cooke S4 set;equipment_rental;Equipment-Miete;Tag;450,00;180,00;standard;ja\n"
    "CREW-001;Oberbeleuchter;Gaffer;labor;Personal;Tag;550,00;420,00;standard;ja\n"
    "BAD-001;Kamerawagen;Dolly;kamerawagen;;;;;;\n"
)


def _upload(pg_client, world, path, text=IMPORT_CSV, **data):
    return pg_client.post(
        path,
        headers=_headers(world),
        files={"file": ("artikel.csv", text.encode("utf-8"), "text/csv")},
        data=data or None,
    )


def test_the_preview_reads_a_german_price_list_and_writes_nothing(pg_client, world):
    response = _upload(pg_client, world, "/catalog/items/import/preview")
    assert response.status_code == 200, response.text
    plan = response.json()
    assert (plan["created"], plan["updated"], plan["errors"]) == (3, 0, 1)
    assert plan["delimiter"] == ";"
    alexa = next(row for row in plan["rows"] if row["sku"] == "CAM-001")
    assert alexa["bill_rate_day"] == "1200.00"
    assert alexa["cost_rate_day"] == "600.00"
    assert alexa["active"] is True
    # An error row carries its SKU and its name even though nothing else
    # about it parsed — otherwise the preview table says "line 5: that is
    # not an item kind" about one of two hundred items and nobody can tell
    # which.
    bad = next(row for row in plan["rows"] if row["sku"] == "BAD-001")
    assert bad["action"] == "error"
    assert bad["name_de"] == "Kamerawagen"
    assert bad["line_number"] == 5
    assert "kamerawagen" in bad["message"]

    assert pg_client.get("/catalog/items", headers=_headers(world)).json()["total"] == 0


def test_the_import_applies_exactly_what_the_preview_showed(pg_client, world, pg_db):
    """The property the digest exists to make true, checked against the rows.

    Not "the apply returned the same counts" — that would be two readings of
    the same plan agreeing with each other. This compares the preview's own
    per-row verdict with what is in the database afterwards: every `create`
    row has an item with that SKU and those rates, and every `error` row has
    none.
    """
    preview = _upload(pg_client, world, "/catalog/items/import/preview").json()

    applied = _upload(
        pg_client, world, "/catalog/items/import", digest=preview["digest"]
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 3
    assert applied.json()["digest"] == preview["digest"]

    for row in preview["rows"]:
        item = (
            pg_db.query(Item)
            .filter(
                Item.company_id == world["company"].id, Item.sku == row["sku"]
            )
            .first()
        )
        if row["action"] == "error":
            assert item is None, f"line {row['line_number']} was an error and landed"
            continue
        assert item is not None, f"line {row['line_number']} was a {row['action']} and did not land"
        assert item.name_de == row["name_de"]
        assert item.kind == row["kind"]
        rates = {entry.rate_type: entry.amount for entry in item.rates}
        assert rates["bill"] == Decimal(row["bill_rate_day"])
        assert rates["cost"] == Decimal(row["cost_rate_day"])

    # And nothing else came in with it.
    assert (
        pg_db.query(Item).filter(Item.company_id == world["company"].id).count() == 3
    )


def test_the_import_resolves_the_german_category_and_unit_names(
    pg_client, world, pg_db
):
    _upload(pg_client, world, "/catalog/items/import")
    item = (
        pg_db.query(Item)
        .filter(Item.company_id == world["company"].id, Item.sku == "CAM-001")
        .one()
    )
    category = pg_db.query(ItemCategory).filter(
        ItemCategory.id == item.category_id
    ).one()
    unit = pg_db.query(Unit).filter(Unit.id == item.unit_id).one()
    assert category.kind == "equipment_rental"
    assert unit.key == "day"


def test_a_stale_preview_is_refused_and_nothing_is_written(pg_client, world, pg_db):
    """Somebody in another tab creates CAM-002 between the preview and the
    apply. The plan silently turns from a create into an update, and the
    honest answer is "this is no longer what you looked at"."""
    preview = _upload(pg_client, world, "/catalog/items/import/preview").json()
    make_item(pg_db, world["company"], sku="CAM-002", name_de="Von Hand angelegt")

    response = _upload(
        pg_client, world, "/catalog/items/import", digest=preview["digest"]
    )
    assert response.status_code == 409, response.text
    assert preview["digest"] in response.json()["detail"]
    assert "preview" in response.json()["detail"]

    # Only the hand-made row exists: the refused apply wrote nothing.
    assert (
        pg_db.query(Item).filter(Item.company_id == world["company"].id).count() == 1
    )


def test_an_import_without_a_digest_is_allowed_for_a_script(pg_client, world):
    response = _upload(pg_client, world, "/catalog/items/import")
    assert response.status_code == 200
    assert response.json()["applied"] == 3


def test_running_the_same_file_twice_updates_and_does_not_duplicate(
    pg_client, world, pg_db
):
    _upload(pg_client, world, "/catalog/items/import")
    second = _upload(pg_client, world, "/catalog/items/import")
    assert second.json()["updated"] == 3
    assert second.json()["created"] == 0
    assert (
        pg_db.query(Item).filter(Item.company_id == world["company"].id).count() == 3
    )


def test_an_import_never_touches_an_item_the_file_does_not_mention(
    pg_client, world, pg_db
):
    """Never deletes, never deactivates, never reprices."""
    untouched = make_item(
        pg_db, world["company"], sku="OLD-001", name_de="Alter Posten"
    )
    _upload(pg_client, world, "/catalog/items/import")
    pg_db.refresh(untouched)
    assert untouched.is_active is True
    assert untouched.name_de == "Alter Posten"
    rates = {entry.rate_type: entry.amount for entry in untouched.rates}
    assert rates["bill"] == Decimal("1200.00")


def test_a_blank_column_leaves_the_field_alone_on_an_update(pg_client, world, pg_db):
    """A bridge from a spreadsheet: a column somebody did not fill in is not
    an instruction to clear the field."""
    _upload(pg_client, world, "/catalog/items/import")
    thinner = (
        "sku;name_de;kind;bill_rate_day\n"
        "CAM-001;Arri Alexa 35;equipment_rental;1.300,00\n"
    )
    assert _upload(
        pg_client, world, "/catalog/items/import", text=thinner
    ).status_code == 200

    item = (
        pg_db.query(Item)
        .filter(Item.company_id == world["company"].id, Item.sku == "CAM-001")
        .one()
    )
    rates = {entry.rate_type: entry.amount for entry in item.rates}
    assert rates["bill"] == Decimal("1300.00")
    # The cost rate the thinner file said nothing about is still there.
    assert rates["cost"] == Decimal("600.00")
    assert item.category_id is not None
    assert item.unit_id is not None


def test_an_empty_file_is_refused_with_a_sentence(pg_client, world):
    response = _upload(pg_client, world, "/catalog/items/import/preview", text="   ")
    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


def test_a_file_with_no_sku_column_is_refused_as_a_whole(pg_client, world):
    text = "name_de;kind\nArri Alexa 35;equipment_rental\n"
    response = _upload(pg_client, world, "/catalog/items/import/preview", text=text)
    assert response.status_code == 422
    assert "sku" in response.json()["detail"]


def test_the_import_is_an_edit_permission(pg_client, world, every_role):
    for role in (CompanyRole.staff, CompanyRole.accountant):
        response = pg_client.post(
            "/catalog/items/import/preview",
            headers=auth_headers(every_role[role], world["company"]),
            files={"file": ("a.csv", IMPORT_CSV.encode("utf-8"), "text/csv")},
        )
        assert response.status_code == 404, f"{role.value}: {response.text}"


# ─── The reference registry ─────────────────────────────────────────────────


def test_a_tax_class_an_item_uses_cannot_be_deactivated(pg_client, world, pg_db):
    """P1a-2's registry, through a source registered by `catalog_service`.

    The row is chosen so that only this guard protects it: `standard` has no
    rule pointing at it in this company beyond its own code, and the item is
    the only other referrer — so a missing registration makes the deactivate
    SUCCEED rather than fail for some other reason.
    """
    _create_item(pg_client, world, name_de="Arri Alexa 35", default_tax_class="standard")
    standard = (
        pg_db.query(TaxClass)
        .filter(
            TaxClass.company_id == world["company"].id, TaxClass.key == "standard"
        )
        .one()
    )

    response = pg_client.patch(
        f"/company/tax/classes/{standard.id}",
        headers=_headers(world),
        json={"is_active": False},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "Arri Alexa 35" in detail
    assert "item" in detail


def test_a_retired_item_is_not_a_reason_to_keep_a_class_switched_on(
    pg_client, world, pg_db
):
    """A retired item resolves nothing. Also the control case for the test
    above: it proves the refusal comes from the ITEM and not from something
    else in the company."""
    created = _create_item(
        pg_client, world, name_de="Arri Alexa 35", default_tax_class="zero"
    )
    pg_client.post(
        f"/catalog/items/{created['id']}/deactivate", headers=_headers(world)
    )
    zero = (
        pg_db.query(TaxClass)
        .filter(TaxClass.company_id == world["company"].id, TaxClass.key == "zero")
        .one()
    )
    response = pg_client.patch(
        f"/company/tax/classes/{zero.id}",
        headers=_headers(world),
        json={"is_active": False},
    )
    assert response.status_code == 200, response.text


# ─── Audit ──────────────────────────────────────────────────────────────────


def _actions(pg_db, company_id) -> list[str]:
    return [
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.company_id == company_id)
        .order_by(AuditEvent.created_at)
        .all()
    ]


def test_every_change_to_an_item_is_audited(pg_client, world, pg_db):
    created = _create_item(pg_client, world, sku="CAM-001", name_de="Arri Alexa 35")
    pg_client.patch(
        f"/catalog/items/{created['id']}",
        headers=_headers(world),
        json={"name_de": "Arri Alexa 35 Mini", "kind": "equipment_rental"},
    )
    pg_client.post(
        f"/catalog/items/{created['id']}/deactivate", headers=_headers(world)
    )
    pg_client.post(
        f"/catalog/items/{created['id']}/reactivate", headers=_headers(world)
    )
    actions = _actions(pg_db, world["company"].id)
    assert "item.created" in actions
    assert "item.updated" in actions
    assert "item.deactivated" in actions
    assert "item.reactivated" in actions


def test_an_audit_row_says_what_changed(pg_client, world, pg_db):
    created = _create_item(pg_client, world, sku="CAM-001", name_de="Arri Alexa 35")
    pg_client.patch(
        f"/catalog/items/{created['id']}",
        headers=_headers(world),
        json={"name_de": "Arri Alexa 35 Mini", "kind": "equipment_rental"},
    )
    event = (
        pg_db.query(AuditEvent)
        .filter(
            AuditEvent.company_id == world["company"].id,
            AuditEvent.action == "item.updated",
        )
        .one()
    )
    assert event.changes["name_de"] == ["Arri Alexa 35", "Arri Alexa 35 Mini"]


def test_a_price_change_is_audited_as_one(pg_client, world, pg_db):
    created = _create_item(
        pg_client,
        world,
        rates=[{"rate_type": "bill", "period_unit": "day", "amount": "1200.00"}],
    )
    pg_client.put(
        f"/catalog/items/{created['id']}/rates",
        headers=_headers(world),
        json=[{"rate_type": "bill", "period_unit": "day", "amount": "1300.00"}],
    )
    event = (
        pg_db.query(AuditEvent)
        .filter(
            AuditEvent.company_id == world["company"].id,
            AuditEvent.action == "item.rates_changed",
        )
        .one()
    )
    assert event.changes["bill/day"] == ["1200.00", "1300.00"]


def test_an_applied_import_is_one_audit_row_plus_one_per_item(
    pg_client, world, pg_db
):
    _upload(pg_client, world, "/catalog/items/import")
    actions = _actions(pg_db, world["company"].id)
    assert actions.count("catalog.import_applied") == 1
    assert actions.count("item.created") == 3
    event = (
        pg_db.query(AuditEvent)
        .filter(
            AuditEvent.company_id == world["company"].id,
            AuditEvent.action == "catalog.import_applied",
        )
        .one()
    )
    assert event.changes["created"] == [None, 3]
    assert event.changes["skipped_with_errors"] == [None, 1]


def test_nothing_is_audited_when_the_import_is_refused(pg_client, world, pg_db):
    preview = _upload(pg_client, world, "/catalog/items/import/preview").json()
    make_item(pg_db, world["company"], sku="CAM-002")
    _upload(pg_client, world, "/catalog/items/import", digest=preview["digest"])
    assert "catalog.import_applied" not in _actions(pg_db, world["company"].id)
    assert "item.created" not in _actions(pg_db, world["company"].id)


def test_a_calculation_preview_leaves_no_audit_row(pg_client, world, pg_db):
    """Arithmetic that left a trail would make the audit log unreadable by
    the time somebody had dragged a slider twenty times."""
    before = len(_actions(pg_db, world["company"].id))
    pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={"document": {"children": [{"id": "l1", "unit_price": "100.00"}]}},
    )
    assert len(_actions(pg_db, world["company"].id)) == before


# ─── The engine over HTTP ───────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", ["s1-quote", "s6-rounding"])
def test_the_preview_endpoint_reproduces_the_golden_fixtures(
    pg_client, world, scenario
):
    """The same documents and the same numbers as `test_calc_fixtures.py`,
    through HTTP and through Pydantic.

    This is the test that would catch a transport that rounded on the way
    out — serialising `Decimal("12517.20")` through a float would answer
    `12517.2` and every number in the engine's own suite would still pass.
    """
    data = json.loads((FIXTURES / f"{scenario}.json").read_text())
    expected = data["expect"]
    response = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={
            "document": data["document"],
            "vat_rates": expected["vat"]["rates"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["net"] == expected["net"]
    assert body["subtotal"] == expected["subtotal"]
    assert body["vat"]["vat"] == expected["vat"]["vat"]
    assert body["vat"]["gross"] == expected["vat"]["gross"]
    assert body["net_by_tax_ref"] == expected["net_by_tax_ref"]


def test_the_prompts_s1_numbers_are_what_the_endpoint_answers(pg_client, world):
    """Written out, because these three figures are the acceptance test
    Mathias was given and a fixture file could be edited to match a bug."""
    data = json.loads((FIXTURES / "s1-quote.json").read_text())
    body = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={"document": data["document"], "vat_rates": {"AT-20": "20"}},
    ).json()
    assert body["net"] == "12517.20"
    assert body["vat"]["vat"] == "2503.44"
    assert body["vat"]["gross"] == "15020.64"


def test_the_prompts_s6_numbers_are_what_the_endpoint_answers(pg_client, world):
    data = json.loads((FIXTURES / "s6-rounding.json").read_text())
    body = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={"document": data["document"], "vat_rates": {"AT-20": "20"}},
    ).json()
    assert body["net"] == "2502.29"
    assert body["vat"]["vat"] == "500.46"
    assert body["vat"]["gross"] == "3002.75"


def test_a_third_level_of_nesting_is_refused_with_the_engines_own_sentence(
    pg_client, world
):
    document = {
        "id": "doc",
        "children": [
            {
                "id": "g1",
                "children": [
                    {
                        "id": "g2",
                        "children": [
                            {"id": "g3", "children": [{"id": "l1", "unit_price": "10"}]}
                        ],
                    }
                ],
            }
        ],
    }
    response = pg_client.post(
        "/calc/preview", headers=_headers(world), json={"document": document}
    )
    assert response.status_code == 422
    # The engine's own sentence, which names the group and the level: "group
    # 'g3' would be at nesting level 3, and a document nests groups 2 deep at
    # most (SCOPE §5.4)." Asserted on the parts that identify the problem
    # rather than on the whole string, so rewording it is not a test failure.
    detail = response.json()["detail"]
    assert "g3" in detail
    assert "nesting level 3" in detail


def test_a_missing_vat_rate_is_a_422_naming_the_category(pg_client, world):
    response = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={
            "document": {
                "children": [{"id": "l1", "unit_price": "100", "tax_ref": "AT-13"}]
            },
            "vat_rates": {"AT-20": "20"},
        },
    )
    assert response.status_code == 422
    assert "AT-13" in response.json()["detail"]


def test_without_vat_rates_the_response_carries_nets_only(pg_client, world):
    """The honest answer for a planning screen that has not resolved tax
    yet — and the reason VAT is a separate step in the engine."""
    body = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={"document": {"children": [{"id": "l1", "unit_price": "100.00"}]}},
    ).json()
    assert body["net"] == "100.00"
    assert body["vat"] is None


def test_a_group_is_returned_as_a_group_and_a_line_as_a_line(pg_client, world):
    """The `type` tag, which is how a client walks the tree without guessing
    from which fields happen to be present."""
    body = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={
            "document": {
                "children": [
                    {"id": "l1", "unit_price": "100.00"},
                    {"id": "g1", "children": [{"id": "l2", "unit_price": "50.00"}]},
                ]
            }
        },
    ).json()
    assert [child["type"] for child in body["children"]] == ["line", "group"]
    assert body["children"][1]["children"][0]["type"] == "line"


def test_an_unknown_field_in_a_line_is_refused_rather_than_ignored(pg_client, world):
    response = pg_client.post(
        "/calc/preview",
        headers=_headers(world),
        json={
            "document": {
                "children": [{"id": "l1", "unit_price": "100.00", "rabatt": "10"}]
            }
        },
    )
    assert response.status_code == 422
