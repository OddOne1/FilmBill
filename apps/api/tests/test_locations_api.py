"""Locations as records (L1 part 1). Real PostgreSQL.

The world: company "YON Studio OG" with an owner. Locations are created
through the API unless a test needs one written directly.

The numbers the hospital tests use: AKH Wien, phone `+43 1 40400`,
emergency phone `+43 1 40400 1964`, coordinates `48.2207, 16.3477`.
"""

from datetime import date

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.location import Location
from apps.api.models.site_settings import SiteSettings
from apps.api.services import permissions

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_location,
    make_party,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "loc-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _user(pg_db, world, role):
    user = make_user(pg_db, f"loc-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


def _create(pg_client, world, user=None, **body):
    body.setdefault("name", "Studio Wien")
    return pg_client.post("/locations", headers=_h(world, user), json=body)


def _ok(pg_client, world, **body):
    made = _create(pg_client, world, **body)
    assert made.status_code == 201, made.text
    return made.json()["location"]


def _patch(pg_client, world, location_id, body, user=None):
    return pg_client.patch(f"/locations/{location_id}", headers=_h(world, user), json=body)


def _names(pg_client, world, user=None, **params):
    got = pg_client.get("/locations", headers=_h(world, user), params=params)
    assert got.status_code == 200, got.text
    return [row["name"] for row in got.json()["locations"]]


def _actions(pg_db, location_id):
    pg_db.expire_all()
    return [
        e.action
        for e in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_type == "locations", AuditEvent.entity_id == location_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ]


# ─── The record ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["shoot", "hotel", "hospital", "other"])
def test_every_kind_creates(pg_client, pg_db, world, kind):
    made = _ok(
        pg_client,
        world,
        name=f"A {kind}",
        kind=kind,
        street="Spitalgasse 23",
        zip="1090",
        city="Wien",
        country="at",
        phone="+43 1 40400",
    )
    assert (made["kind"], made["country"], made["city"], made["is_active"]) == (kind, "AT", "Wien", True)
    assert made["map_link"] == "https://www.google.com/maps/search/?api=1&query=Spitalgasse%2023%2C%201090%20Wien%2C%20AT"
    row = pg_db.get(Location, made["id"])
    assert (row.company_id, row.name_key) == (world["company"].id, f"a {kind}")
    assert _actions(pg_db, row.id) == ["location.created"]


def test_a_duplicate_name_is_refused_case_insensitively(pg_client, pg_db, world):
    _ok(pg_client, world, name="Studio Wien")
    again = _create(pg_client, world, name="  studio   WIEN ")
    assert again.status_code == 409
    assert again.json()["detail"] == (
        "A location called “Studio Wien” already exists. Give this one another name, or open the existing one."
    )
    assert pg_db.query(Location).count() == 1


def test_an_archived_location_does_not_block_its_name_but_blocks_its_own_restore(pg_client, pg_db, world):
    first = _ok(pg_client, world, name="Studio Wien")
    assert pg_client.post(f"/locations/{first['id']}/archive", headers=_h(world)).status_code == 200
    second = _ok(pg_client, world, name="Studio Wien")
    assert second["id"] != first["id"]
    restore = pg_client.post(f"/locations/{first['id']}/restore", headers=_h(world))
    assert restore.status_code == 409
    assert "already exists" in restore.json()["detail"]
    _patch(pg_client, world, second["id"], {"name": "Studio Wien 2"})
    restored = pg_client.post(f"/locations/{first['id']}/restore", headers=_h(world))
    assert restored.status_code == 200 and restored.json()["location"]["is_active"] is True
    assert _actions(pg_db, pg_db.get(Location, first["id"]).id) == [
        "location.created",
        "location.archived",
        "location.restored",
    ]


def test_a_location_without_a_name_is_refused(pg_client, world):
    made = _create(pg_client, world, name="   ")
    assert made.status_code == 422
    assert made.json()["detail"] == "This location has no name. Give it a name, for example “Studio Wien”."


def test_an_unknown_country_is_refused_with_a_sentence(pg_client, world):
    made = _create(pg_client, world, country="Austria")
    assert made.status_code == 422
    assert made.json()["detail"].startswith("“Austria” is not a country code.")


def test_phone_email_and_website_are_checked_lightly(pg_client, world):
    assert _create(pg_client, world, name="A", phone="call Anna").status_code == 422
    assert _create(pg_client, world, name="B", email="office-at-example").status_code == 422
    assert _create(pg_client, world, name="C", website="www.example .com").status_code == 422
    fine = _ok(pg_client, world, name="D", phone="+43 (1) 404-00/1", email="office@example.com", website="example.com")
    assert (fine["phone"], fine["email"], fine["website"]) == ("+43 (1) 404-00/1", "office@example.com", "example.com")


# ─── Coordinates ────────────────────────────────────────────────────────────


def test_a_pasted_pair_is_stored_to_six_decimals(pg_client, pg_db, world):
    made = _ok(pg_client, world, coordinates="48,2082; 16,3738")
    assert (made["latitude"], made["longitude"]) == ("48.208200", "16.373800")
    assert made["coordinates_display"] == "Latitude 48.2082, Longitude 16.3738"
    assert made["map_link"] == "https://www.google.com/maps/search/?api=1&query=48.2082%2C16.3738"
    row = pg_db.get(Location, made["id"])
    assert (str(row.latitude), str(row.longitude)) == ("48.208200", "16.373800")


def test_coordinates_are_both_or_neither(pg_client, pg_db, world):
    half = _create(pg_client, world, latitude="48.2082")
    assert half.status_code == 422
    assert half.json()["detail"] == (
        "These coordinates have only one number. Give both the latitude and the longitude, or neither."
    )
    one_number = _create(pg_client, world, coordinates="48.2082")
    assert one_number.status_code == 422
    assert "only one number" in one_number.json()["detail"]
    out_of_range = _create(pg_client, world, latitude="99", longitude="16")
    assert out_of_range.status_code == 422
    assert out_of_range.json()["detail"].startswith("The latitude 99 is outside -90 to 90.")
    twice = _create(pg_client, world, coordinates="48.2, 16.3", latitude="48.2", longitude="16.3")
    assert twice.status_code == 422
    assert pg_db.query(Location).count() == 0
    made = _ok(pg_client, world, latitude="48.2082", longitude="16.3738")
    cleared = _patch(pg_client, world, made["id"], {"coordinates": ""})
    assert cleared.status_code == 200
    assert (cleared.json()["location"]["latitude"], cleared.json()["location"]["longitude"]) == (None, None)


def test_the_database_refuses_half_a_pair_as_well(pg_db, world):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        make_location(pg_db, world["company"], latitude="48.2082")
    pg_db.rollback()


def test_the_parse_endpoint_answers_like_a_save(pg_client, world):
    ok = pg_client.post("/locations/coordinates", headers=_h(world), json={"text": "48.2082 16.3738"})
    assert ok.json() == {
        "latitude": "48.208200",
        "longitude": "16.373800",
        "ok": True,
        "display": "Latitude 48.2082, Longitude 16.3738",
        "refusal": None,
    }
    refused = pg_client.post("/locations/coordinates", headers=_h(world), json={"text": "48.2082"})
    assert refused.json()["ok"] is False
    assert refused.json()["refusal"].startswith("These coordinates have only one number.")


# ─── A hospital's verification ──────────────────────────────────────────────


def _hospital(pg_client, world):
    return _ok(
        pg_client,
        world,
        name="AKH Wien",
        kind="hospital",
        street="Währinger Gürtel 18-20",
        zip="1090",
        city="Wien",
        country="AT",
        phone="+43 1 40400",
        emergency_phone="+43 1 40400 1964",
        coordinates="48.2207, 16.3477",
    )


def _verify(pg_client, world, location_id, note="Called the switchboard"):
    return pg_client.post(f"/locations/{location_id}/verify", headers=_h(world), json={"note": note})


def test_verification_is_kept_after_a_notes_edit_and_cleared_by_a_phone_change(pg_client, pg_db, world):
    akh = _hospital(pg_client, world)
    assert akh["verified"] is False
    verified = _verify(pg_client, world, akh["id"])
    assert verified.status_code == 200, verified.text
    body = verified.json()["location"]
    assert (body["verified"], body["verified_on"], body["verified_by_name"], body["verification_note"]) == (
        True,
        date.today().isoformat(),
        world["owner"].name,
        "Called the switchboard",
    )

    notes = _patch(pg_client, world, akh["id"], {"notes": "Emergency entrance on Lazarettgasse", "access_notes": "Gate 7"})
    assert notes.status_code == 200
    assert (notes.json()["location"]["verified"], notes.json()["verification_cleared"], notes.json()["message"]) == (True, False, None)

    phone = _patch(pg_client, world, akh["id"], {"phone": "+43 1 40400 0"})
    assert phone.status_code == 200
    answer = phone.json()
    assert (answer["location"]["verified"], answer["location"]["verified_on"], answer["location"]["verified_by_name"]) == (False, None, None)
    assert answer["verification_cleared"] is True
    assert answer["message"] == (
        "This hospital is no longer marked verified, because its address or phone changed. "
        "Check the entry and mark it verified again."
    )
    # Sorted: the two rows of the phone change are written in one
    # transaction and share `created_at`, so their order is not defined.
    assert sorted(_actions(pg_db, pg_db.get(Location, akh["id"]).id)) == [
        "location.created",
        "location.updated",
        "location.updated",
        "location.verification_cleared",
        "location.verified",
    ]
    cleared = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "location.verification_cleared")
        .one()
    )
    assert cleared.reason == "The address, coordinates or phone changed."
    assert cleared.changes["verified_on"] == [date.today().isoformat(), None]


@pytest.mark.parametrize(
    "change",
    [
        {"street": "Währinger Gürtel 18"},
        {"zip": "1091"},
        {"city": "Vienna"},
        {"country": "DE"},
        {"coordinates": "48.2208, 16.3477"},
        {"emergency_phone": "144"},
    ],
    ids=["street", "zip", "city", "country", "coordinates", "emergency_phone"],
)
def test_any_address_coordinate_or_phone_change_clears_it(pg_client, world, change):
    akh = _hospital(pg_client, world)
    _verify(pg_client, world, akh["id"])
    answer = _patch(pg_client, world, akh["id"], change).json()
    assert (answer["verification_cleared"], answer["location"]["verified"]) == (True, False)


def test_saving_the_same_phone_again_keeps_it(pg_client, world):
    akh = _hospital(pg_client, world)
    _verify(pg_client, world, akh["id"])
    answer = _patch(pg_client, world, akh["id"], {"phone": "+43 1 40400", "coordinates": "48.2207; 16.3477"}).json()
    assert (answer["verification_cleared"], answer["location"]["verified"]) == (False, True)


def test_only_a_hospital_can_be_marked_verified(pg_client, world):
    studio = _ok(pg_client, world, name="Studio Wien")
    refused = _verify(pg_client, world, studio["id"])
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "“Studio Wien” is a shoot location, not a hospital. Only a hospital can be marked verified."
    )


# ─── Listing ────────────────────────────────────────────────────────────────


def test_search_kind_filter_and_archived(pg_client, world):
    _ok(pg_client, world, name="Studio Wien", city="Wien")
    _ok(pg_client, world, name="Hotel Test", kind="hotel", city="Mödling")
    gone = _ok(pg_client, world, name="Old barn", city="Tulln")
    pg_client.post(f"/locations/{gone['id']}/archive", headers=_h(world))
    assert _names(pg_client, world) == ["Hotel Test", "Studio Wien"]
    assert _names(pg_client, world, kind="hotel") == ["Hotel Test"]
    assert _names(pg_client, world, q="mödl") == ["Hotel Test"]
    assert _names(pg_client, world, include_archived=True) == ["Hotel Test", "Studio Wien", "Old barn"]


def test_the_on_site_contact_is_a_contact_of_the_company(pg_client, pg_db, world):
    berger = make_party(pg_db, world["company"], type="person", name="Anna Berger")
    other = make_company(pg_db, "Other Films GmbH")
    theirs = make_party(pg_db, other, name="Their contact")
    made = _ok(pg_client, world, contact_party_id=str(berger.id), contact_text="Hausmeister, 0664 123")
    assert (made["contact_party_name"], made["contact_text"]) == ("Anna Berger", "Hausmeister, 0664 123")
    assert _patch(pg_client, world, made["id"], {"contact_party_id": str(theirs.id)}).status_code == 404


# ─── Who may ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, sees_shoot, sees_hotel, can_edit",
    [
        (CompanyRole.owner, True, True, True),
        (CompanyRole.admin, True, True, True),
        (CompanyRole.producer, True, True, True),
        (CompanyRole.accountant, True, True, False),
        (CompanyRole.staff, True, False, False),
        (CompanyRole.tax_advisor, False, False, False),
    ],
)
def test_the_location_permission_matrix(pg_client, pg_db, world, role, sees_shoot, sees_hotel, can_edit):
    studio = make_location(pg_db, world["company"], name="Studio Wien", kind="shoot")
    hotel = make_location(pg_db, world["company"], name="Hotel Test", kind="hotel")
    user = _user(pg_db, world, role)
    listed = pg_client.get("/locations", headers=_h(world, user))
    assert listed.status_code == (200 if sees_shoot else 404)
    if sees_shoot:
        names = [row["name"] for row in listed.json()["locations"]]
        assert ("Studio Wien" in names, "Hotel Test" in names) == (True, sees_hotel)
        assert listed.json()["hotels_visible"] is sees_hotel
    assert pg_client.get(f"/locations/{studio.id}", headers=_h(world, user)).status_code == (200 if sees_shoot else 404)
    assert pg_client.get(f"/locations/{hotel.id}", headers=_h(world, user)).status_code == (200 if sees_hotel else 404)
    assert pg_client.get(f"/locations/{hotel.id}/usage", headers=_h(world, user)).status_code == (200 if sees_hotel else 404)
    made = _create(pg_client, world, user=user, name=f"New by {role.value}")
    assert made.status_code == (201 if can_edit else 404), made.text
    edit = _patch(pg_client, world, studio.id, {"notes": "x"}, user=user)
    assert edit.status_code == (200 if can_edit else 404)


def test_staff_list_shoot_locations_and_no_hotel(pg_client, pg_db, world):
    """The hotel row is the one ONLY the kind guard protects: it is active,
    of this company, and matches the search — staff hold `locations.view`."""
    make_location(pg_db, world["company"], name="Studio Wien", kind="shoot", city="Wien")
    make_location(pg_db, world["company"], name="Hotel Wien", kind="hotel", city="Wien")
    staff = _user(pg_db, world, CompanyRole.staff)
    assert _names(pg_client, world, staff, q="Wien") == ["Studio Wien"]
    assert _names(pg_client, world, staff, kind="hotel") == []
    # Control: the owner sees both with the same search.
    assert _names(pg_client, world, q="Wien") == ["Hotel Wien", "Studio Wien"]


def test_without_crew_access_a_hotel_cannot_be_created(pg_client, pg_db, world, monkeypatch):
    monkeypatch.setitem(
        permissions.PERMISSIONS, "parties.view", permissions.PERMISSIONS["parties.view"] - {CompanyRole.producer}
    )
    producer = _user(pg_db, world, CompanyRole.producer)
    assert _create(pg_client, world, user=producer, name="Hotel X", kind="hotel").status_code == 404
    made = _create(pg_client, world, user=producer, name="Studio X", kind="shoot")
    assert made.status_code == 201
    assert _patch(pg_client, world, made.json()["location"]["id"], {"kind": "hotel"}, user=producer).status_code == 404


def test_staff_do_not_see_the_on_site_contacts_name(pg_client, pg_db, world):
    berger = make_party(pg_db, world["company"], type="person", name="Anna Berger")
    studio = make_location(pg_db, world["company"], name="Studio Wien", contact_party_id=berger.id, contact_text="Hausmeister")
    staff = _user(pg_db, world, CompanyRole.staff)
    seen = pg_client.get(f"/locations/{studio.id}", headers=_h(world, staff)).json()
    assert (seen["contact_party_id"], seen["contact_party_name"], seen["contact_text"]) == (None, None, "Hausmeister")
    owner = pg_client.get(f"/locations/{studio.id}", headers=_h(world)).json()
    assert owner["contact_party_name"] == "Anna Berger"


def test_another_companys_location_is_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    theirs = make_location(pg_db, other, name="Their studio")
    make_location(pg_db, world["company"], name="Our studio")
    assert _names(pg_client, world) == ["Our studio"]
    for call in (
        lambda: pg_client.get(f"/locations/{theirs.id}", headers=_h(world)),
        lambda: _patch(pg_client, world, theirs.id, {"notes": "x"}),
        lambda: pg_client.post(f"/locations/{theirs.id}/archive", headers=_h(world)),
        lambda: pg_client.get(f"/locations/{theirs.id}/usage", headers=_h(world)),
    ):
        assert call().status_code == 404
    pg_db.expire_all()
    assert pg_db.get(Location, theirs.id).is_active is True


def test_there_is_no_delete_route_for_locations(pg_client, world):
    from apps.api.main import app

    deletes = [
        route.path for route in app.routes
        if "DELETE" in getattr(route, "methods", set()) and "location" in route.path
    ]
    assert deletes == []
    made = _ok(pg_client, world)
    assert pg_client.delete(f"/locations/{made['id']}", headers=_h(world)).status_code == 405
