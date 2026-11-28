"""A project's locations (L1b): its list, hotel guests with their own dates,
booking confirmations, and hospitals found on OpenStreetMap. Real
PostgreSQL; the OpenStreetMap side is a real HTTP server on this machine
that answers like the Overpass API and records what it was asked (rule 17b:
the client really ran).

The world: project "1000m" with a strip "Shoot" Mon 6 – Fri 10 July 2026.
**Anna Kern** (crew) is on it 7–9 July; **Ben Ohr** is a contact who is NOT
on the crew. Locations: **Studio Wien** (shooting, 48.2082, 16.3738),
**Halle Linz** (shooting, 48.3064, 14.2858 — 155 km away), **Hotel Test**
(hotel), **Hotel Sacher** (hotel AND shooting), **Gasthaus Krone**
(restaurant).
"""

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from apps.api.config import settings
from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.location import HotelStay, Location, LocationAttachment
from apps.api.models.site_settings import SiteSettings
from apps.api.services import osm_hospitals, permissions

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_crew_assignment,
    make_location,
    make_party,
    make_project,
    make_project_block,
    make_superadmin,
    make_user,
)

JUL = lambda day: date(2026, 7, day)  # noqa: E731
ISO = lambda day: f"2026-07-{day:02d}"  # noqa: E731


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "pl-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    film = make_project(pg_db, company, name="1000m", project_number="PRJ-1000", start_date=JUL(1))
    shoot = make_project_block(pg_db, company, film, start_date=JUL(6), end_date=JUL(10), label="Shoot")
    pg_db.commit()
    anna = make_party(pg_db, company, type="person", name="Anna Kern", is_crew=True)
    ben = make_party(pg_db, company, type="person", name="Ben Ohr", is_crew=True)
    make_crew_assignment(pg_db, company, film, shoot, anna, dates=[JUL(7), JUL(8), JUL(9)])
    w = {
        "company": company, "owner": owner, "film": film, "shoot": shoot, "anna": anna, "ben": ben,
        "studio": make_location(pg_db, company, name="Studio Wien", kind="shoot", latitude="48.2082", longitude="16.3738"),
        "linz": make_location(pg_db, company, name="Halle Linz", kind="shoot", city="Linz", latitude="48.3064", longitude="14.2858"),
        "hotel": make_location(pg_db, company, name="Hotel Test", kind="hotel"),
        "sacher": make_location(pg_db, company, name="Hotel Sacher", uses=["shoot", "hotel"]),
        "krone": make_location(pg_db, company, name="Gasthaus Krone", kind="restaurant"),
    }
    osm_hospitals._cache.clear()
    return w


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _user(pg_db, world, role):
    user = make_user(pg_db, f"pl-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


def _url(world, tail=""):
    return f"/projects/{world['film'].id}{tail}"


def _add(pg_client, world, location, user=None):
    return pg_client.post(_url(world, "/locations"), headers=_h(world, user), json={"location_id": str(location.id)})


def _listed(pg_client, world, user=None):
    got = pg_client.get(_url(world, "/locations"), headers=_h(world, user))
    assert got.status_code == 200, got.text
    return got.json()


def _entry(body, name):
    return next(e for e in body["locations"] if e["location"]["name"] == name)


def _stay(pg_client, world, hotel, party=None, check_in=None, check_out=None, user=None):
    body = {"party_id": str((party or world["anna"]).id)}
    if check_in:
        body["check_in"] = check_in
    if check_out:
        body["check_out"] = check_out
    return pg_client.post(_url(world, f"/locations/{hotel.id}/stays"), headers=_h(world, user), json=body)


# ─── The list ───────────────────────────────────────────────────────────────


def test_add_list_and_remove_locations_of_the_project(pg_client, pg_db, world):
    for name in ("krone", "studio"):
        assert _add(pg_client, world, world[name]).status_code == 200
    body = _listed(pg_client, world)
    assert [(e["location"]["name"], e["location"]["uses"]) for e in body["locations"]] == [
        ("Gasthaus Krone", ["restaurant"]),
        ("Studio Wien", ["shoot"]),
    ]
    assert _add(pg_client, world, world["krone"]).status_code == 200  # twice: still once
    removed = pg_client.post(_url(world, f"/locations/{world['krone'].id}/remove"), headers=_h(world))
    assert [e["location"]["name"] for e in removed.json()["locations"]] == ["Studio Wien"]
    # The location itself is untouched — it is the company's, for other projects.
    pg_db.expire_all()
    assert pg_db.get(Location, world["krone"].id).is_active is True
    assert sorted(
        e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.action.like("project_location.%"))
    ) == ["project_location.added", "project_location.added", "project_location.removed"]


def test_a_location_still_in_use_cannot_leave_the_project(pg_client, pg_db, world):
    pg_client.patch(_url(world, f"/blocks/{world['shoot'].id}"), headers=_h(world), json={"location_id": str(world["studio"].id)})
    refused = pg_client.post(_url(world, f"/locations/{world['studio'].id}/remove"), headers=_h(world))
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "“Studio Wien” is still used by 1 strip of this project. Change those first, then remove it from the project.",
    )


def test_a_location_from_another_project_is_reused_not_retyped(pg_client, pg_db, world):
    other = make_project(pg_db, world["company"], name="Doku", project_number="PRJ-2", start_date=JUL(1))
    pg_client.post(f"/projects/{other.id}/locations", headers=_h(world), json={"location_id": str(world["studio"].id)})
    _add(pg_client, world, world["studio"])
    assert pg_db.query(Location).filter(Location.name == "Studio Wien").count() == 1
    usage = pg_client.get(f"/locations/{world['studio'].id}/usage", headers=_h(world)).json()
    assert sorted(u["project_name"] for u in usage["uses"] if u["kind"] == "project") == ["1000m", "Doku"]


def test_create_straight_into_the_project(pg_client, world):
    made = pg_client.post(
        "/locations", headers=_h(world),
        json={"name": "Café Central", "uses": ["restaurant"], "project_id": str(world["film"].id)},
    )
    assert made.status_code == 201, made.text
    assert [e["location"]["name"] for e in _listed(pg_client, world)["locations"]] == ["Café Central"]


# ─── Hotel guests ───────────────────────────────────────────────────────────


def test_a_guests_default_dates_are_their_crew_days(pg_client, pg_db, world):
    made = _stay(pg_client, world, world["hotel"])
    assert made.status_code == 200, made.text
    guests = _entry(made.json(), "Hotel Test")["stays"]
    # First day 7 July, last 9 July: check-in 7, check-out the morning of 10.
    assert [(g["party_name"], g["check_in"], g["check_out"], g["nights"]) for g in guests] == [
        ("Anna Kern", ISO(7), ISO(10), 3)
    ]


def test_dates_are_adjusted_per_person(pg_client, pg_db, world):
    stay = _entry(_stay(pg_client, world, world["hotel"]).json(), "Hotel Test")["stays"][0]
    moved = pg_client.patch(_url(world, f"/stays/{stay['id']}"), headers=_h(world), json={"check_in": ISO(6), "notes": "Arrives late"})
    guest = _entry(moved.json(), "Hotel Test")["stays"][0]
    assert (guest["check_in"], guest["check_out"], guest["nights"], guest["notes"]) == (ISO(6), ISO(10), 4, "Arrives late")
    wrong = pg_client.patch(_url(world, f"/stays/{stay['id']}"), headers=_h(world), json={"check_out": ISO(6)})
    assert (wrong.status_code, wrong.json()["detail"]) == (
        422, "This stay checks out on or before its check-in. Set the check-out to a later day than the check-in.",
    )
    gone = pg_client.post(_url(world, f"/stays/{stay['id']}/remove"), headers=_h(world))
    assert _entry(gone.json(), "Hotel Test")["stays"] == []
    pg_db.expire_all()
    assert pg_db.query(HotelStay).one().is_active is False


def test_two_hotels_for_one_person_and_a_hotel_that_is_also_a_set(pg_client, world):
    _stay(pg_client, world, world["hotel"], check_in=ISO(7), check_out=ISO(8))
    body = _stay(pg_client, world, world["sacher"], check_in=ISO(8), check_out=ISO(10)).json()
    assert [(e["location"]["name"], [(g["check_in"], g["check_out"]) for g in e["stays"]]) for e in body["locations"]] == [
        ("Hotel Sacher", [(ISO(8), ISO(10))]),
        ("Hotel Test", [(ISO(7), ISO(8))]),
    ]


def test_only_crew_of_this_production_and_only_hotels(pg_client, world):
    not_crew = _stay(pg_client, world, world["hotel"], party=world["ben"])
    assert (not_crew.status_code, not_crew.json()["detail"]) == (
        422, "Ben Ohr is not on the crew of this project. Add them to a strip first, then give them a hotel.",
    )
    not_hotel = _stay(pg_client, world, world["krone"])
    assert (not_hotel.status_code, not_hotel.json()["detail"]) == (
        422, "“Gasthaus Krone” is not marked as a hotel. Tick “Hotel” on it first, or pick a hotel.",
    )


# ─── Booking confirmations ──────────────────────────────────────────────────


@pytest.fixture
def stored(monkeypatch):
    """The storage call itself, recorded — `s3_service.put_object` is what
    every upload goes through."""
    calls = {}

    def put_object(key, body, content_type=None, cache_control=None):
        calls[key] = (body, content_type)

    monkeypatch.setattr("apps.api.services.s3_service.put_object", put_object)
    return calls


def test_a_pdf_confirmation_is_stored_and_can_be_downloaded(pg_client, pg_db, world, stored):
    pdf = b"%PDF-1.4\nBooking 4711 for Anna Kern\n%%EOF"
    made = pg_client.post(
        _url(world, f"/locations/{world['hotel'].id}/attachments"), headers=_h(world),
        files={"file": ("booking.pdf", pdf, "application/pdf")}, data={"title": "Booking.com 4711"},
    )
    assert made.status_code == 200, made.text
    attachment = _entry(made.json(), "Hotel Test")["attachments"][0]
    assert (attachment["kind"], attachment["title"], attachment["file_name"], attachment["content_type"], attachment["size_bytes"]) == (
        "file", "Booking.com 4711", "booking.pdf", "application/pdf", len(pdf),
    )
    row = pg_db.query(LocationAttachment).one()
    assert stored[row.s3_key] == (pdf, "application/pdf")
    assert row.s3_key.startswith(f"companies/{world['company'].id}/projects/{world['film'].id}/locations/{world['hotel'].id}/")
    link = pg_client.get(_url(world, f"/attachments/{row.id}/download"), headers=_h(world))
    assert link.status_code == 200
    assert row.s3_key in link.json()["url"] and "booking.pdf" in link.json()["url"]


def test_an_email_and_a_pasted_text(pg_client, world, stored):
    eml = b"From: reservations@hotel-test.at\r\nSubject: Ihre Buchung\r\n\r\nZimmer 12, 7.-10.7."
    made = pg_client.post(
        _url(world, f"/locations/{world['hotel'].id}/attachments"), headers=_h(world),
        files={"file": ("Buchung.eml", eml, "message/rfc822")},
    )
    assert _entry(made.json(), "Hotel Test")["attachments"][0]["content_type"] == "message/rfc822"
    text = pg_client.post(
        _url(world, f"/locations/{world['hotel'].id}/attachments/text"), headers=_h(world),
        json={"title": "SMS from the hotel", "text": "Zimmer 12 bestätigt, Check-in ab 14 Uhr."},
    ).json()
    pasted = _entry(text, "Hotel Test")["attachments"][1]
    assert (pasted["kind"], pasted["title"], pasted["body_text"]) == (
        "text", "SMS from the hotel", "Zimmer 12 bestätigt, Check-in ab 14 Uhr."
    )


def test_a_file_that_is_not_a_confirmation_is_refused(pg_client, world, stored):
    refused = pg_client.post(
        _url(world, f"/locations/{world['hotel'].id}/attachments"), headers=_h(world),
        files={"file": ("tool.exe", b"MZ\x90\x00binary", "application/octet-stream")},
    )
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "“tool.exe” is not a PDF, an email (.eml or .msg), a picture or a text file. Save the confirmation as one "
        "of those, or paste its text instead.",
    )
    assert stored == {}


def test_a_confirmation_is_removed_not_deleted(pg_client, pg_db, world):
    pg_client.post(_url(world, f"/locations/{world['krone'].id}/attachments/text"), headers=_h(world), json={"text": "Tisch für 12"})
    row = pg_db.query(LocationAttachment).one()
    gone = pg_client.post(_url(world, f"/attachments/{row.id}/remove"), headers=_h(world))
    assert _entry(gone.json(), "Gasthaus Krone")["attachments"] == []
    pg_db.expire_all()
    assert pg_db.get(LocationAttachment, row.id).is_active is False


# ─── Who may ────────────────────────────────────────────────────────────────


def test_staff_see_the_places_and_no_guests_or_hotel_confirmations(pg_client, pg_db, world, stored):
    _stay(pg_client, world, world["sacher"])
    _add(pg_client, world, world["hotel"])
    pg_client.post(_url(world, f"/locations/{world['sacher'].id}/attachments/text"), headers=_h(world), json={"text": "Zimmer 5"})
    pg_client.post(_url(world, f"/locations/{world['krone'].id}/attachments/text"), headers=_h(world), json={"text": "Tisch für 12"})
    staff = _user(pg_db, world, CompanyRole.staff)
    body = _listed(pg_client, world, staff)
    # Hotel Sacher is also a set: visible as one, without its guests or its
    # confirmation. Hotel Test is only a hotel: absent.
    assert [(e["location"]["name"], e["location"]["uses"], e["stays"], len(e["attachments"])) for e in body["locations"]] == [
        ("Gasthaus Krone", ["restaurant"], [], 1),
        ("Hotel Sacher", ["shoot"], [], 0),
    ]
    assert body["hotels_visible"] is False and "Anna Kern" not in json.dumps(body)
    sacher_note = pg_db.query(LocationAttachment).filter(LocationAttachment.location_id == world["sacher"].id).one()
    assert pg_client.get(_url(world, f"/attachments/{sacher_note.id}/download"), headers=_h(world, staff)).status_code == 404
    # Control: the owner sees both.
    owner = _listed(pg_client, world)
    assert [e["location"]["name"] for e in owner["locations"]] == ["Gasthaus Krone", "Hotel Sacher", "Hotel Test"]
    assert [g["party_name"] for g in _entry(owner, "Hotel Sacher")["stays"]] == ["Anna Kern"]


@pytest.mark.parametrize(
    "role, sees, changes",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, True, False),
        (CompanyRole.tax_advisor, False, False),
    ],
)
def test_the_project_locations_permission_matrix(pg_client, pg_db, world, role, sees, changes):
    user = _user(pg_db, world, role)
    assert pg_client.get(_url(world, "/locations"), headers=_h(world, user)).status_code == (200 if sees else 404)
    assert _add(pg_client, world, world["studio"], user).status_code == (200 if changes else 404)
    assert _stay(pg_client, world, world["hotel"], user=user).status_code == (200 if changes else 404)


def test_without_crew_access_no_guest_can_be_added(pg_client, pg_db, world, monkeypatch):
    monkeypatch.setitem(
        permissions.PERMISSIONS, "parties.view", permissions.PERMISSIONS["parties.view"] - {CompanyRole.producer}
    )
    producer = _user(pg_db, world, CompanyRole.producer)
    assert _stay(pg_client, world, world["sacher"], user=producer).status_code == 404
    assert _add(pg_client, world, world["krone"], producer).status_code == 200
    # Control: the owner.
    assert _stay(pg_client, world, world["sacher"]).status_code == 200


def test_another_companys_rows_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    theirs = make_location(pg_db, other, name="Their hotel", kind="hotel")
    their_person = make_party(pg_db, other, type="person", name="Their crew", is_crew=True)
    assert _add(pg_client, world, theirs).status_code == 404
    assert _stay(pg_client, world, theirs).status_code == 404
    assert _stay(pg_client, world, world["hotel"], party=their_person).status_code == 404
    assert pg_db.query(HotelStay).count() == 0


# ─── Hospitals from OpenStreetMap ───────────────────────────────────────────


class _Overpass(BaseHTTPRequestHandler):
    """Answers like Overpass: hospitals near Vienna or near Linz, by the
    coordinate in the query. Records every query."""

    asked: list[str] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        query = parse_qs(self.rfile.read(length).decode())["data"][0]
        type(self).asked.append(query)
        if ",48.208200,16.373800)" in query:
            elements = [
                {"type": "way", "id": 1, "center": {"lat": 48.2209, "lon": 16.3477},
                 "tags": {"amenity": "hospital", "name": "AKH Wien", "emergency": "yes", "phone": "+43 1 40400",
                          "addr:street": "Währinger Gürtel", "addr:housenumber": "18-20", "addr:postcode": "1090", "addr:city": "Wien"}},
                {"type": "node", "id": 2, "lat": 48.2050, "lon": 16.3700,
                 "tags": {"amenity": "hospital", "name": "Privatklinik Nähe"}},
                {"type": "node", "id": 3, "lat": 48.2000, "lon": 16.3800, "tags": {"amenity": "hospital"}},
            ]
        elif ",48.306400,14.285800)" in query:
            elements = [
                {"type": "way", "id": 9, "center": {"lat": 48.3060, "lon": 14.3000},
                 "tags": {"amenity": "hospital", "name": "Kepler Universitätsklinikum", "emergency": "yes"}},
            ]
        else:
            elements = []
        payload = json.dumps({"elements": elements}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture
def overpass(monkeypatch):
    _Overpass.asked = []
    server = HTTPServer(("127.0.0.1", 0), _Overpass)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "overpass_url", f"http://127.0.0.1:{server.server_address[1]}/api/interpreter")
    yield _Overpass
    server.shutdown()


def test_hospitals_are_suggested_per_group_of_shooting_locations(pg_client, pg_db, world, overpass):
    _add(pg_client, world, world["studio"])
    _add(pg_client, world, world["linz"])
    _add(pg_client, world, world["sacher"])  # a set without coordinates
    got = pg_client.get(_url(world, "/hospital-suggestions"), headers=_h(world))
    assert got.status_code == 200, got.text
    body = got.json()
    # Vienna and Linz are 155 km apart: two groups, a hospital each.
    assert [g["shooting_locations"] for g in body["groups"]] == [["Halle Linz"], ["Studio Wien"]]
    linz, vienna = body["groups"]
    assert [(o["name"], o["emergency"]) for o in linz["offers"]] == [("Kepler Universitätsklinikum", True)]
    # The confirmed emergency department first, though the clinic is closer;
    # the unnamed hospital is left out.
    assert [(o["name"], o["emergency"], o["distance_km"]) for o in vienna["offers"]] == [
        ("AKH Wien", True, 2.4),
        ("Privatklinik Nähe", None, 0.5),
    ]
    assert body["missing_coordinates"] == ["Hotel Sacher"]
    assert len(overpass.asked) == 2
    assert all('nwr["amenity"="hospital"](around:30000,' in q for q in overpass.asked)


def test_a_suggestion_is_added_once_and_reused(pg_client, pg_db, world, overpass):
    _add(pg_client, world, world["studio"])
    offer = pg_client.get(_url(world, "/hospital-suggestions"), headers=_h(world)).json()["groups"][0]["offers"][0]
    body = {k: offer[k] for k in ("osm_ref", "name", "latitude", "longitude", "phone", "street", "zip", "city", "country", "website")}
    added = pg_client.post(_url(world, "/hospital-suggestions/add"), headers=_h(world), json=body)
    assert added.status_code == 200, added.text
    akh = _entry(added.json(), "AKH Wien")["location"]
    assert (akh["uses"], akh["phone"], akh["street"], akh["zip"], akh["latitude"], akh["from_openstreetmap"]) == (
        ["hospital"], "+43 1 40400", "Währinger Gürtel 18-20", "1090", "48.220900", True,
    )
    again = pg_client.post(_url(world, "/hospital-suggestions/add"), headers=_h(world), json=body)
    assert again.status_code == 200
    assert pg_db.query(Location).filter(Location.osm_ref == "way/1").count() == 1
    shown = pg_client.get(_url(world, "/hospital-suggestions"), headers=_h(world)).json()["groups"][0]["offers"][0]
    assert (shown["existing_location_id"], shown["in_project"]) == (akh["id"], True)


def test_when_openstreetmap_does_not_answer_the_tab_says_so(pg_client, world, monkeypatch):
    monkeypatch.setattr(settings, "overpass_url", "http://127.0.0.1:9/nothing-listens-here")
    _add(pg_client, world, world["studio"])
    body = pg_client.get(_url(world, "/hospital-suggestions"), headers=_h(world)).json()
    assert (body["groups"], body["error"]) == (
        [],
        "The hospital search on OpenStreetMap did not answer just now. Try again in a minute, or add the hospital by hand.",
    )
