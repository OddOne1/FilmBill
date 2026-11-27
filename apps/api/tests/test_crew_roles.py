"""Crew roles and rates (R1 part 1). Real PostgreSQL throughout.

What this file is about, each made real:

  * **The effective rate** is own-else-typical — read from a real
    `item_rates` row and a real `party_crew_roles` row, in the response AND in
    the list's SQL filter, and the two are compared on the same people.
  * **Rates are cost data.** A member with `parties.view` but without
    `budgets.view` gets no number from any endpoint. No role in today's
    matrix is in that position (every holder of `parties.view` also holds
    `budgets.view`), so the test removes the producer from the REAL
    `PERMISSIONS["budgets.view"]` set — the thing `has_permission` reads —
    rather than patching a helper that aliases it.
  * **Tags are never converted by themselves**: only the one-click call does
    it, and it is read back from the party row.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.crew import PartyCrewRole
from apps.api.models.party import Party
from apps.api.models.site_settings import SiteSettings
from apps.api.services import permissions

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_crew_role,
    make_item,
    make_party,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "roles-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    dop = make_item(pg_db, company, sku="L-DOP", name_de="DoP", name_en="Director of photography", kind="labor", bill_rate_day="900.00", cost_rate_day="600.00")
    gaffer = make_item(pg_db, company, sku="L-GAF", name_de="Gaffer", name_en=None, kind="labor", bill_rate_day="600.00", cost_rate_day="400.00")
    runner = make_item(pg_db, company, sku="L-RUN", name_de="Runner", name_en=None, kind="labor", bill_rate_day="250.00", cost_rate_day=None)
    camera = make_item(pg_db, company, sku="CAM", name_de="Alexa 35", kind="equipment_rental")
    return {"company": company, "owner": owner, "dop": dop, "gaffer": gaffer, "runner": runner, "camera": camera}


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _person(pg_db, world, name, *, crew=True, tags=()):
    return make_party(pg_db, world["company"], type="person", name=name, is_crew=crew, tags=list(tags))


def _add(pg_client, world, party, item, rate=None, user=None):
    body = {"item_id": str(item.id)}
    if rate is not None:
        body["own_day_rate"] = rate
    return pg_client.post(f"/parties/{party.id}/crew-roles", headers=_h(world, user), json=body)


def _user(pg_db, world, role):
    user = make_user(pg_db, f"roles-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


@pytest.fixture
def producer_without_cost(monkeypatch):
    """The producer keeps parties.view/edit and loses budgets.view."""
    monkeypatch.setitem(
        permissions.PERMISSIONS,
        "budgets.view",
        permissions.PERMISSIONS["budgets.view"] - {CompanyRole.producer},
    )


# ─── The effective rate ─────────────────────────────────────────────────────


def test_the_effective_rate_in_the_four_cases(pg_client, pg_db, world):
    anna = _person(pg_db, world, "Anna Huber")
    # 1. own rate above typical: own wins, +50 above typical.
    made = _add(pg_client, world, anna, world["dop"], "650.00")
    assert made.status_code == 201, made.text
    # 2. typical only.
    assert _add(pg_client, world, anna, world["gaffer"]).status_code == 201
    # 3. none: the item has no cost rate and the person has no own rate.
    assert _add(pg_client, world, anna, world["runner"]).status_code == 201
    body = pg_client.get(f"/parties/{anna.id}/crew-roles", headers=_h(world)).json()
    assert body["rates_visible"] is True
    rows = {r["name"]: r for r in body["roles"]}
    assert (rows["DoP"]["own_day_rate"], rows["DoP"]["typical_day_rate"], rows["DoP"]["effective_day_rate"], rows["DoP"]["rate_source"], rows["DoP"]["difference_to_typical"]) == ("650.00", "600.00", "650.00", "own", "50.00")
    assert (rows["Gaffer"]["own_day_rate"], rows["Gaffer"]["effective_day_rate"], rows["Gaffer"]["rate_source"], rows["Gaffer"]["difference_to_typical"]) == (None, "400.00", "typical", None)
    assert (rows["Runner"]["typical_day_rate"], rows["Runner"]["effective_day_rate"], rows["Runner"]["rate_source"]) == (None, None, "none")

    # 4. own rate BELOW typical: own still wins, and the difference is negative.
    ben = _person(pg_db, world, "Ben Ohr")
    assert _add(pg_client, world, ben, world["dop"], "550,00".replace(",", ".")).status_code == 201
    row = pg_client.get(f"/parties/{ben.id}/crew-roles", headers=_h(world)).json()["roles"][0]
    assert (row["effective_day_rate"], row["rate_source"], row["difference_to_typical"]) == ("550.00", "own", "-50.00")


def test_several_roles_each_with_its_own_rate_and_the_rate_change_is_audited(pg_client, pg_db, world):
    anna = _person(pg_db, world, "Anna Huber")
    _add(pg_client, world, anna, world["dop"], "700.00")
    _add(pg_client, world, anna, world["gaffer"], "380.00")
    stored = {
        r.item_id: r.own_day_rate
        for r in pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == anna.id)
    }
    assert stored == {world["dop"].id: Decimal("700.00"), world["gaffer"].id: Decimal("380.00")}

    role = next(r for r in pg_client.get(f"/parties/{anna.id}/crew-roles", headers=_h(world)).json()["roles"] if r["name"] == "Gaffer")
    changed = pg_client.patch(f"/parties/{anna.id}/crew-roles/{role['id']}", headers=_h(world), json={"own_day_rate": "420.00"})
    assert changed.status_code == 200, changed.text
    cleared = pg_client.patch(f"/parties/{anna.id}/crew-roles/{role['id']}", headers=_h(world), json={"own_day_rate": None})
    gaffer = next(r for r in cleared.json()["roles"] if r["name"] == "Gaffer")
    assert (gaffer["own_day_rate"], gaffer["effective_day_rate"], gaffer["rate_source"]) == (None, "400.00", "typical")

    events = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == role["id"])
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .all()
    )
    assert [e.action for e in events] == ["party_crew_role.added", "party_crew_role.updated", "party_crew_role.updated"]
    assert events[1].changes["own_day_rate"] == ["380.00", "420.00"]

    removed = pg_client.post(f"/parties/{anna.id}/crew-roles/{role['id']}/remove", headers=_h(world))
    assert [r["name"] for r in removed.json()["roles"]] == ["DoP"]
    pg_db.expire_all()
    assert pg_db.get(PartyCrewRole, role["id"]).is_active is False  # kept
    # Removed, so it can be added again.
    assert _add(pg_client, world, anna, world["gaffer"]).status_code == 201


def test_a_role_twice_a_non_labor_item_and_a_negative_rate_are_refused(pg_client, pg_db, world):
    anna = _person(pg_db, world, "Anna Huber")
    assert _add(pg_client, world, anna, world["dop"]).status_code == 201
    again = _add(pg_client, world, anna, world["dop"])
    assert again.status_code == 409
    assert again.json()["detail"] == "Anna Huber already has the role “DoP”. Change its rate in the list instead."
    camera = _add(pg_client, world, anna, world["camera"])
    assert camera.status_code == 422
    assert camera.json()["detail"].startswith("“Alexa 35” is not a labor item in the catalog.")
    negative = _add(pg_client, world, anna, world["gaffer"], "-1.00")
    assert negative.status_code == 422
    assert "cannot be negative" in negative.json()["detail"]


def test_a_role_is_refused_on_a_party_that_is_not_crew(pg_client, pg_db, world):
    pat = _person(pg_db, world, "Pat Client", crew=False)
    refused = _add(pg_client, world, pat, world["dop"])
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Pat Client is not marked as crew. Mark them as crew first, then add the role."
    assert pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == pat.id).count() == 0


# ─── Tags ───────────────────────────────────────────────────────────────────


def test_a_matching_tag_is_offered_and_converted_only_on_request(pg_client, pg_db, world):
    anna = _person(pg_db, world, "Anna Huber", tags=["gaffer", "Drone pilot"])
    body = pg_client.get(f"/parties/{anna.id}/crew-roles", headers=_h(world)).json()
    # Offered (case-insensitive), never applied: no role yet, the tags intact.
    assert [(s["tag"], s["item_name"]) for s in body["tag_suggestions"]] == [("gaffer", "Gaffer")]
    assert body["roles"] == []
    pg_db.expire_all()
    assert pg_db.get(Party, anna.id).tags == ["gaffer", "Drone pilot"]

    converted = pg_client.post(f"/parties/{anna.id}/crew-roles/from-tag", headers=_h(world), json={"tag": "gaffer"})
    assert converted.status_code == 200, converted.text
    assert [r["name"] for r in converted.json()["roles"]] == ["Gaffer"]
    assert converted.json()["tag_suggestions"] == []
    pg_db.expire_all()
    assert pg_db.get(Party, anna.id).tags == ["Drone pilot"]
    event = pg_db.query(AuditEvent).filter(AuditEvent.action == "party_crew_role.added").one()
    assert event.changes["from_tag"] == [None, "gaffer"]

    unknown = pg_client.post(f"/parties/{anna.id}/crew-roles/from-tag", headers=_h(world), json={"tag": "Drone pilot"})
    assert unknown.status_code == 422
    assert "no tag “Drone pilot” that matches a labor item" in unknown.json()["detail"]


# ─── The list: role filter, rate range, sort ────────────────────────────────


@pytest.fixture
def three_people(pg_db, world):
    """DoP typical 600. Anna own 650, Ben no own (600), Cleo own 520."""
    company = world["company"]
    anna = _person(pg_db, world, "Anna Huber")
    ben = _person(pg_db, world, "Ben Ohr")
    cleo = _person(pg_db, world, "Cleo Lang")
    make_crew_role(pg_db, company, anna, world["dop"], own_day_rate="650.00")
    make_crew_role(pg_db, company, ben, world["dop"])
    make_crew_role(pg_db, company, cleo, world["dop"], own_day_rate="520.00")
    make_crew_role(pg_db, company, cleo, world["gaffer"], own_day_rate="450.00")
    return anna, ben, cleo


def _list(pg_client, world, user=None, **params):
    response = pg_client.get("/crew", headers=_h(world, user), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_rate_range_uses_own_else_typical(pg_client, world, three_people):
    dop = str(world["dop"].id)
    # Effective DoP rates: Anna 650 (own), Ben 600 (typical), Cleo 520 (own).
    # Range 560–640: only Ben. A filter on the typical rate would take Anna
    # and Cleo as well (600 each).
    body = _list(pg_client, world, role_item_id=dop, min_rate="560", max_rate="640")
    assert [m["name"] for m in body["members"]] == ["Ben Ohr"]
    assert (body["members"][0]["rate_low"], body["members"][0]["rate_source"]) == ("600.00", "typical")
    # Max 600: Ben (600) and Cleo (520); not Anna (650 own, above).
    assert [m["name"] for m in _list(pg_client, world, role_item_id=dop, max_rate="600")["members"]] == ["Ben Ohr", "Cleo Lang"]
    # Without a role: any role in range. Cleo's gaffer 450 is in 400–460.
    assert [m["name"] for m in _list(pg_client, world, min_rate="400", max_rate="460")["members"]] == ["Cleo Lang"]


def test_the_sql_filter_and_the_response_agree_on_every_person(pg_client, world, three_people):
    dop = str(world["dop"].id)
    everybody = _list(pg_client, world, role_item_id=dop)["members"]
    for member in everybody:
        rate = member["rate_low"]
        hits = _list(pg_client, world, role_item_id=dop, min_rate=rate, max_rate=rate)["members"]
        assert member["name"] in [m["name"] for m in hits], (member["name"], rate)


def test_sorting_by_rate_and_the_low_high_column(pg_client, world, three_people):
    dop = str(world["dop"].id)
    ordered = _list(pg_client, world, role_item_id=dop, sort="rate")["members"]
    assert [(m["name"], m["rate_low"], m["rate_source"]) for m in ordered] == [
        ("Cleo Lang", "520.00", "own"),
        ("Ben Ohr", "600.00", "typical"),
        ("Anna Huber", "650.00", "own"),
    ]
    reverse = _list(pg_client, world, role_item_id=dop, sort="-rate")["members"]
    assert [m["name"] for m in reverse] == ["Anna Huber", "Ben Ohr", "Cleo Lang"]
    # No role filter: the lowest to the highest over all of a person's roles.
    cleo = next(m for m in _list(pg_client, world)["members"] if m["name"] == "Cleo Lang")
    assert (cleo["rate_low"], cleo["rate_high"], cleo["rate_source"]) == ("450.00", "520.00", None)
    assert [r["name"] for r in cleo["crew_roles"]] == ["DoP", "Gaffer"]


def test_the_role_filter_finds_only_holders_of_that_labor_item(pg_client, world, three_people):
    gaffer = str(world["gaffer"].id)
    assert [m["name"] for m in _list(pg_client, world, role_item_id=gaffer)["members"]] == ["Cleo Lang"]


# ─── Quick add and the import ───────────────────────────────────────────────


def test_quick_add_matches_roles_to_labor_items_and_keeps_the_rest_as_tags(pg_client, pg_db, world):
    made = pg_client.post(
        "/crew",
        headers=_h(world),
        json={"first_name": "Dora", "last_name": "Licht", "roles": ["gaffer", "Drone pilot", "DoP"], "day_rate": "420.00"},
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["unknown_roles"] == ["Drone pilot"]
    assert any("no such role in the catalog" in note for note in body["notes"])
    member = body["member"]
    assert member["roles"] == ["Drone pilot"]
    # The rate goes on the FIRST matched role (Gaffer), not on DoP.
    assert [(r["name"], r["own_day_rate"]) for r in member["crew_roles"]] == [("Gaffer", "420.00"), ("DoP", None)]
    pg_db.expire_all()
    assert pg_db.get(Party, member["id"]).tags == ["Drone pilot"]


def _csv(*lines: str) -> bytes:
    header = "first_name;last_name;roles;email;day_rate"
    return ("\r\n".join([header, *lines]) + "\r\n").encode("utf-8")


def _preview(pg_client, world, payload, user=None):
    response = pg_client.post(
        "/crew/import/preview",
        headers=_h(world, user),
        files={"file": ("crew.csv", payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_import_matches_roles_parses_the_rate_and_refuses_the_ambiguous_dot(pg_client, pg_db, world):
    payload = _csv(
        "Anna;Huber;dop|Steadicam;anna@example.com;650,50",
        "Ben;Ohr;Gaffer;;1.500",
        "Cleo;Lang;Steadicam;;300",
        "Dan;Ton;Gaffer;;1.234,00",
    )
    preview = _preview(pg_client, world, payload)
    rows = preview["rows"]
    assert (rows[0]["action"], rows[0]["matched_roles"], rows[0]["unknown_roles"], rows[0]["day_rate"]) == ("create", ["DoP"], ["Steadicam"], "650.50")
    assert "“Steadicam”: no such role in the catalog." in rows[0]["role_notes"][0]
    assert rows[1]["action"] == "error" and "ambiguous" in rows[1]["message"]
    assert rows[2]["day_rate"] == "300.00" and any("day rate is not used" in n for n in rows[2]["role_notes"])
    assert rows[3]["day_rate"] == "1234.00"

    applied = pg_client.post(
        "/crew/import",
        headers=_h(world),
        files={"file": ("crew.csv", payload, "text/csv")},
        data={"digest": preview["digest"]},
    )
    assert applied.status_code == 200, applied.text
    pg_db.expire_all()
    anna = pg_db.query(Party).filter(Party.name == "Anna Huber").one()
    assert anna.tags == ["Steadicam"]
    role = pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == anna.id).one()
    assert (role.item_id, role.own_day_rate) == (world["dop"].id, Decimal("650.50"))
    # No catalog item was created for the unknown role.
    from apps.api.models.catalog import Item

    assert pg_db.query(Item).filter(Item.name_de == "Steadicam").count() == 0


def test_the_export_writes_roles_then_tags_and_the_first_own_rate(pg_client, pg_db, world):
    anna = _person(pg_db, world, "Anna Huber", tags=["Drone pilot"])
    make_crew_role(pg_db, world["company"], anna, world["dop"], own_day_rate="650.50")
    lines = pg_client.get("/crew/export.csv", headers=_h(world)).content.decode("utf-8-sig").splitlines()
    assert lines[0].endswith(";day_rate")
    assert lines[1] == ";;;Anna Huber;DoP|Drone pilot;;;;650,50"
    # And it reads back as the same rate.
    preview = _preview(pg_client, world, lines[0].encode() + b"\r\n" + lines[1].encode() + b"\r\n")
    assert (preview["rows"][0]["matched_roles"], preview["rows"][0]["day_rate"]) == (["DoP"], "650.50")


# ─── Who sees rates ─────────────────────────────────────────────────────────


def _no_rate_anywhere(value):
    """Every rate field in a JSON tree is null, and nothing is called a rate
    with a value — a POSITIVE check over the shape, not a missing key."""
    rate_keys = {"own_day_rate", "typical_day_rate", "effective_day_rate", "difference_to_typical", "rate_low", "rate_high", "rate_source", "day_rate"}
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in rate_keys and child is not None:
                    found.append((key, child))
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def test_without_budgets_view_no_endpoint_carries_a_rate(pg_client, pg_db, world, three_people, producer_without_cost):
    producer = _user(pg_db, world, CompanyRole.producer)
    anna = three_people[0]
    # The same requests as the owner makes, who DOES see rates (control).
    owner_list = _list(pg_client, world)
    assert owner_list["members"][0]["rates_visible"] is True
    assert _no_rate_anywhere(owner_list) != []

    listed = _list(pg_client, world, user=producer)
    assert listed["members"] and all(m["rates_visible"] is False for m in listed["members"])
    assert [r["name"] for r in listed["members"][0]["crew_roles"]] == ["DoP"]  # roles, no number
    assert _no_rate_anywhere(listed) == []

    detail = pg_client.get(f"/parties/{anna.id}/crew-roles", headers=_h(world, producer)).json()
    assert detail["rates_visible"] is False and [r["name"] for r in detail["roles"]] == ["DoP"]
    assert _no_rate_anywhere(detail) == []

    export = pg_client.get("/crew/export.csv", headers=_h(world, producer)).content.decode("utf-8-sig").splitlines()
    assert export[0] == "first_name;middle_name;last_name;name;roles;email;phone;notes"
    assert "650" not in "\n".join(export)

    preview = _preview(pg_client, world, _csv("Eva;Neu;DoP;;650,00"), user=producer)
    assert preview["rows"][0]["matched_roles"] == ["DoP"]
    assert "day_rate" in preview["ignored_columns"]
    assert _no_rate_anywhere(preview) == []

    # The rate filter, the rate sort and writing a rate are refused like a
    # missing permission.
    assert pg_client.get("/crew", headers=_h(world, producer), params={"max_rate": "600"}).status_code == 404
    assert pg_client.get("/crew", headers=_h(world, producer), params={"sort": "rate"}).status_code == 404
    assert _add(pg_client, world, three_people[1], world["gaffer"], "100.00", user=producer).status_code == 404
    # Adding a role without a number still works.
    assert _add(pg_client, world, three_people[1], world["gaffer"], user=producer).status_code == 201


def test_staff_see_no_crew_names_and_no_rates_at_all(pg_client, pg_db, world, three_people):
    """D111: staff hold neither `parties.view` nor `budgets.view`, so every
    crew and role endpoint is a 404 for them — no names, no rates."""
    staff = _user(pg_db, world, CompanyRole.staff)
    anna = three_people[0]
    role = pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == anna.id).first()
    for method, path, body in [
        ("GET", "/crew", None),
        ("GET", "/crew/export.csv", None),
        ("GET", f"/parties/{anna.id}/crew-roles", None),
        ("POST", f"/parties/{anna.id}/crew-roles", {"item_id": str(world["gaffer"].id)}),
        ("PATCH", f"/parties/{anna.id}/crew-roles/{role.id}", {"notes": "x"}),
        ("POST", f"/parties/{anna.id}/crew-roles/{role.id}/remove", None),
        ("POST", f"/parties/{anna.id}/crew-roles/from-tag", {"tag": "DoP"}),
    ]:
        response = pg_client.request(method, path, headers=_h(world, staff), json=body)
        assert response.status_code == 404, (method, path, response.status_code)
        assert "Anna" not in response.text and "650" not in response.text


@pytest.mark.parametrize(
    "role, can_view, can_edit",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, False, False),
        (CompanyRole.tax_advisor, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_roles_permission_matrix(pg_client, pg_db, world, role, can_view, can_edit):
    user = _user(pg_db, world, role)
    anna = _person(pg_db, world, "Anna Huber")
    read = pg_client.get(f"/parties/{anna.id}/crew-roles", headers=_h(world, user))
    assert read.status_code == (200 if can_view else 404)
    made = _add(pg_client, world, anna, world["dop"], user=user)
    assert made.status_code == (201 if can_edit else 404), made.text


def test_another_companys_person_item_and_role_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    theirs = make_party(pg_db, other, type="person", name="Their gaffer", is_crew=True)
    their_item = make_item(pg_db, other, sku="X", name_de="Gaffer", kind="labor")
    their_role = make_crew_role(pg_db, other, theirs, their_item, own_day_rate="999.00")
    ours = _person(pg_db, world, "Anna Huber")
    assert pg_client.get(f"/parties/{theirs.id}/crew-roles", headers=_h(world)).status_code == 404
    # Their labor item on our person.
    assert _add(pg_client, world, ours, their_item).status_code == 404
    # Their role, addressed through OUR person, and through theirs.
    assert pg_client.patch(f"/parties/{ours.id}/crew-roles/{their_role.id}", headers=_h(world), json={"own_day_rate": "1.00"}).status_code == 404
    assert pg_client.post(f"/parties/{theirs.id}/crew-roles/{their_role.id}/remove", headers=_h(world)).status_code == 404
    pg_db.expire_all()
    row = pg_db.get(PartyCrewRole, their_role.id)
    assert (row.is_active, row.own_day_rate) == (True, Decimal("999.00"))
    # Their item does not match a role in OUR import either.
    preview = _preview(pg_client, world, _csv("Eva;Neu;Gaffer;;"))
    assert preview["rows"][0]["matched_roles"] == ["Gaffer"]  # ours, not theirs
    assert preview["rows"][0]["unknown_roles"] == []


def test_there_is_no_delete_route_for_crew_roles(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    paths = [path for path in schema["paths"] if "/crew-roles" in path]
    assert len(paths) == 4, paths
    assert not [path for path in paths if "delete" in schema["paths"][path]]
