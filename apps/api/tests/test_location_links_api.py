"""Where a location attaches: strips, days, crew rows (L1 part 2, L1b).
Real PostgreSQL. L1b moved hotels to the project (stays), so the crew row
shows the person's stays with a Route; `test_project_locations_api.py` has
the project's list, guests and confirmations.

The world: project "1000m" has a strip "Shoot" Mon 6 – Fri 10 July 2026
with the old free text "Studio, Hof 2". Locations: **Studio Wien** (shooting,
Hauptstraße 1, 2340 Mödling, AT, no coordinates), **Barn Tulln** (shooting,
48.3297, 16.0542), **AKH Wien** (hospital), **Hotel Test** (hotel, 48.1986,
16.3417) and **Hotel Zwei** (hotel, Wien). **Anna Kern** (crew) is on the
strip on 7, 8 and 9 July.
"""

from datetime import date

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.crew import CrewAssignment
from apps.api.models.project import ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import permissions

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

STUDIO_LINK = "https://www.google.com/maps/search/?api=1&query=Hauptstra%C3%9Fe%201%2C%202340%20M%C3%B6dling%2C%20AT"
BARN_LINK = "https://www.google.com/maps/search/?api=1&query=48.3297%2C16.0542"


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "links-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    film = make_project(pg_db, company, name="1000m", project_number="PRJ-1000", start_date=JUL(1))
    shoot = make_project_block(pg_db, company, film, start_date=JUL(6), end_date=JUL(10), label="Shoot")
    shoot.location = "Studio, Hof 2"
    pg_db.commit()
    studio = make_location(pg_db, company, name="Studio Wien", kind="shoot", city="Mödling",
                           street="Hauptstraße 1", zip="2340", country="AT")
    barn = make_location(pg_db, company, name="Barn Tulln", kind="shoot", city="Tulln",
                         latitude="48.3297", longitude="16.0542")
    akh = make_location(pg_db, company, name="AKH Wien", kind="hospital")
    hotel = make_location(pg_db, company, name="Hotel Test", kind="hotel", latitude="48.1986", longitude="16.3417")
    hotel_two = make_location(pg_db, company, name="Hotel Zwei", kind="hotel", city="Wien")
    anna = make_party(pg_db, company, type="person", name="Anna Kern", is_crew=True)
    assignment = make_crew_assignment(pg_db, company, film, shoot, anna, dates=[JUL(7), JUL(8), JUL(9)])
    pg_db.commit()
    return {
        "company": company, "owner": owner, "film": film, "shoot": shoot, "studio": studio, "barn": barn,
        "akh": akh, "hotel": hotel, "hotel_two": hotel_two, "anna": anna, "assignment": assignment,
    }


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _user(pg_db, world, role):
    user = make_user(pg_db, f"links-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


def _base(world):
    return f"/projects/{world['film'].id}/blocks/{world['shoot'].id}"


def _set_strip(pg_client, world, location, user=None):
    return pg_client.patch(
        _base(world), headers=_h(world, user), json={"location_id": str(location.id) if location else None}
    )


def _calendar_strip(pg_client, world, user=None):
    got = pg_client.get(f"/projects/{world['film'].id}/calendar", headers=_h(world, user))
    assert got.status_code == 200, got.text
    return next(s for s in got.json()["strips"] if s["id"] == str(world["shoot"].id))


def _effective(strip):
    return {
        d["date"]: (
            d["effective_location_ref"]["name"] if d["effective_location_ref"] else None,
            d["effective_location_source"],
        )
        for d in strip["days"]
    }


def _shown(ref):
    """(name, is_active) of a location as a response carries it; None when
    the response carries none."""
    return (ref["name"], ref["is_active"]) if ref else None


def _day(pg_db, world, day):
    pg_db.expire_all()
    return pg_db.query(ProjectDay).filter(ProjectDay.block_id == world["shoot"].id, ProjectDay.date == JUL(day)).one()


def _bulk(pg_client, world, days, location, user=None):
    return pg_client.post(
        f"{_base(world)}/days/location",
        headers=_h(world, user),
        json={"dates": [ISO(d) for d in days], "location_id": str(location.id) if location else None},
    )


def _crew(pg_client, world, user=None):
    got = pg_client.get(f"/projects/{world['film'].id}/crew", headers=_h(world, user))
    assert got.status_code == 200, got.text
    return got.json()["strips"][0]["assignments"][0]


def _set_hotel(pg_client, world, location, user=None):
    """L1b: Anna gets a stay at `location` (default dates)."""
    return _stay(pg_client, world, location, user=user)


def _add_hotel(pg_client, world, location, user=None):
    """L1b: the location joins the project's list."""
    return pg_client.post(
        f"/projects/{world['film'].id}/locations", headers=_h(world, user), json={"location_id": str(location.id)}
    )


# ─── The strip and its days ─────────────────────────────────────────────────


def test_set_and_clear_the_strip_location(pg_client, pg_db, world):
    made = _set_strip(pg_client, world, world["studio"])
    assert made.status_code == 200, made.text
    strip = made.json()["strip"]
    assert (strip["location_id"], strip["location_ref"]["name"], strip["location_ref"]["map_link"]) == (
        str(world["studio"].id), "Studio Wien", STUDIO_LINK,
    )
    assert set(_effective(strip).values()) == {("Studio Wien", "strip")}
    assert strip["days"][0]["effective_location_ref"]["map_link"] == STUDIO_LINK
    # The old free text is untouched.
    assert strip["location"] == "Studio, Hof 2"
    cleared = _set_strip(pg_client, world, None).json()["strip"]
    assert (cleared["location_id"], cleared["location_ref"]) == (None, None)
    assert set(_effective(cleared).values()) == {(None, None)}
    events = [
        e.changes.get("location_id")
        for e in pg_db.query(AuditEvent).filter(AuditEvent.action == "project_block.updated").order_by(AuditEvent.created_at, AuditEvent.id)
    ]
    assert events == [[None, str(world["studio"].id)], [str(world["studio"].id), None]]


def test_a_day_that_differs_overrides_the_strip(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    day = _day(pg_db, world, 8)
    own = pg_client.patch(f"/projects/{world['film'].id}/days/{day.id}", headers=_h(world), json={"location_id": str(world["barn"].id)})
    assert own.status_code == 200, own.text
    assert own.json()["location_id"] == str(world["barn"].id)
    strip = _calendar_strip(pg_client, world)
    assert _effective(strip) == {
        ISO(6): ("Studio Wien", "strip"),
        ISO(7): ("Studio Wien", "strip"),
        ISO(8): ("Barn Tulln", "day"),
        ISO(9): ("Studio Wien", "strip"),
        ISO(10): ("Studio Wien", "strip"),
    }
    eighth = next(d for d in strip["days"] if d["date"] == ISO(8))
    assert (eighth["location_ref"]["name"], eighth["effective_location_ref"]["map_link"], eighth["has_own_data"]) == (
        "Barn Tulln", BARN_LINK, True,
    )
    back = pg_client.patch(f"/projects/{world['film'].id}/days/{day.id}", headers=_h(world), json={"location_id": None})
    assert back.status_code == 200
    assert _effective(_calendar_strip(pg_client, world))[ISO(8)] == ("Studio Wien", "strip")


def test_set_for_selected_days_and_back(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    made = _bulk(pg_client, world, [9, 7], world["barn"])
    assert made.status_code == 200, made.text
    assert (made.json()["detail"], made.json()["changed"]) == ("2 days set to Barn Tulln.", 2)
    assert [k for k, v in _effective(made.json()["strip"]).items() if v == ("Barn Tulln", "day")] == [ISO(7), ISO(9)]
    again = _bulk(pg_client, world, [7, 8], world["barn"]).json()
    assert again["changed"] == 1
    back = _bulk(pg_client, world, [7, 8, 9], None).json()
    assert (back["detail"], back["changed"]) == ("3 days back to the strip's location.", 3)
    assert set(_effective(back["strip"]).values()) == {("Studio Wien", "strip")}
    assert pg_db.query(AuditEvent).filter(AuditEvent.action == "project_day.updated").count() == 6


def test_days_outside_the_strip_are_refused(pg_client, world):
    refused = _bulk(pg_client, world, [5, 6], world["barn"])
    assert refused.status_code == 422
    assert refused.json()["detail"] == "5 July 2026 is not a day of this strip. Pick days inside the strip."


def test_moving_and_resizing_the_strip_do_not_touch_locations(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    _bulk(pg_client, world, [8], world["barn"])
    moved = pg_client.post(f"{_base(world)}/move", headers=_h(world), json={"start_date": ISO(13)}).json()["strip"]
    assert moved["location_ref"]["name"] == "Studio Wien"
    assert _effective(moved)[ISO(15)] == ("Barn Tulln", "day")
    grown = pg_client.post(f"{_base(world)}/resize", headers=_h(world), json={"end_date": ISO(18)}).json()["strip"]
    assert _effective(grown)[ISO(18)] == ("Studio Wien", "strip")
    assert _effective(grown)[ISO(15)] == ("Barn Tulln", "day")


@pytest.mark.parametrize(
    "which, sentence",
    [
        ("akh", "“AKH Wien” is not marked as a shooting location. Tick “Shooting location” on it first, or pick another location."),
        ("hotel", "“Hotel Test” is not marked as a shooting location. Tick “Shooting location” on it first, or pick another location."),
    ],
)
def test_a_kind_that_does_not_fit_is_refused_on_strip_and_day(pg_client, pg_db, world, which, sentence):
    refused = _set_strip(pg_client, world, world[which])
    assert (refused.status_code, refused.json()["detail"]) == (422, sentence)
    day = _day(pg_db, world, 7)
    on_day = pg_client.patch(f"/projects/{world['film'].id}/days/{day.id}", headers=_h(world), json={"location_id": str(world[which].id)})
    assert (on_day.status_code, on_day.json()["detail"]) == (422, sentence)
    assert _bulk(pg_client, world, [7], world[which]).status_code == 422
    assert _day(pg_db, world, 7).location_id is None


def test_an_archived_location_still_shows_and_cannot_be_newly_picked(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    _bulk(pg_client, world, [8], world["barn"])
    pg_client.post(f"/locations/{world['studio'].id}/archive", headers=_h(world))
    pg_client.post(f"/locations/{world['barn'].id}/archive", headers=_h(world))
    strip = _calendar_strip(pg_client, world)
    assert _shown(strip["location_ref"]) == ("Studio Wien", False)
    eighth = next(d for d in strip["days"] if d["date"] == ISO(8))
    assert _shown(eighth["effective_location_ref"]) == ("Barn Tulln", False)
    # Saving the strip with the location it already has is fine.
    assert _set_strip(pg_client, world, world["studio"]).status_code == 200
    new_pick = _bulk(pg_client, world, [9], world["studio"])
    assert (new_pick.status_code, new_pick.json()["detail"]) == (
        422, "“Studio Wien” is archived. Restore it under the project's Locations, or pick another location.",
    )


# ─── Hotel stays on the crew row (L1b) ──────────────────────────────────────


def _stay(pg_client, world, hotel, check_in=None, check_out=None, user=None):
    body = {"party_id": str(world["anna"].id)}
    if check_in:
        body["check_in"] = ISO(check_in)
    if check_out:
        body["check_out"] = ISO(check_out)
    return pg_client.post(
        f"/projects/{world['film'].id}/locations/{hotel.id}/stays", headers=_h(world, user), json=body
    )


def test_the_crew_row_shows_the_stay_and_the_route_from_it(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    assert _stay(pg_client, world, world["hotel"]).status_code == 200
    row = _crew(pg_client, world)
    assert row["hotel_visible"] is True
    stays = row["hotel_stays"]
    # Default dates: Anna's first day (7 July) to the day after her last (10).
    assert [(s["hotel"]["name"], s["check_in"], s["check_out"]) for s in stays] == [("Hotel Test", ISO(7), ISO(10))]
    # From the hotel's coordinates to where 7 July happens: Studio Wien.
    assert stays[0]["route_link"] == (
        "https://www.google.com/maps/dir/?api=1&origin=48.1986%2C16.3417"
        "&destination=Hauptstra%C3%9Fe%201%2C%202340%20M%C3%B6dling%2C%20AT"
    )
    _bulk(pg_client, world, [7], world["barn"])
    assert _crew(pg_client, world)["hotel_stays"][0]["route_link"] == (
        "https://www.google.com/maps/dir/?api=1&origin=48.1986%2C16.3417&destination=48.3297%2C16.0542"
    )


def test_two_hotels_the_route_starts_inside_each_stay(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    _bulk(pg_client, world, [9], world["barn"])
    _stay(pg_client, world, world["hotel"], 7, 9)
    _stay(pg_client, world, world["hotel_two"], 9, 10)
    stays = _crew(pg_client, world)["hotel_stays"]
    assert [(s["hotel"]["name"], s["route_link"].split("destination=")[1]) for s in stays] == [
        ("Hotel Test", "Hauptstra%C3%9Fe%201%2C%202340%20M%C3%B6dling%2C%20AT"),
        ("Hotel Zwei", "48.3297%2C16.0542"),
    ]


def test_no_route_without_a_location(pg_client, world):
    _stay(pg_client, world, world["hotel"])
    stay = _crew(pg_client, world)["hotel_stays"][0]
    assert (stay["hotel"]["name"], stay["route_link"]) == ("Hotel Test", None)


def test_an_archived_hotel_stays_on_the_crew_row(pg_client, world):
    _stay(pg_client, world, world["hotel"])
    pg_client.post(f"/locations/{world['hotel'].id}/archive", headers=_h(world))
    assert _shown(_crew(pg_client, world)["hotel_stays"][0]["hotel"]) == ("Hotel Test", False)


# ─── Where it is used ───────────────────────────────────────────────────────


def test_usage_lists_every_place_and_archiving_keeps_them(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    _bulk(pg_client, world, [8], world["studio"])
    _stay(pg_client, world, world["hotel"])
    studio = pg_client.get(f"/locations/{world['studio'].id}/usage", headers=_h(world)).json()
    assert [(u["kind"], u["project_name"], u["strip_name"], u["date"]) for u in studio["uses"]] == [
        ("project", "1000m", None, None),
        ("strip", "1000m", "Shoot", None),
        ("day", "1000m", "Shoot", ISO(8)),
    ]
    hotel = pg_client.get(f"/locations/{world['hotel'].id}/usage", headers=_h(world)).json()
    assert [(u["kind"], u["party_name"]) for u in hotel["uses"]] == [("project", None), ("hotel_stay", "Anna Kern")]
    listed = {row["name"]: row["in_use"] for row in pg_client.get("/locations", headers=_h(world)).json()["locations"]}
    assert (listed["Studio Wien"], listed["Hotel Test"], listed["Barn Tulln"]) == (3, 2, 0)
    pg_client.post(f"/locations/{world['studio'].id}/archive", headers=_h(world))
    after = pg_client.get(f"/locations/{world['studio'].id}/usage", headers=_h(world)).json()
    assert after["count"] == 3
    pg_db.expire_all()
    assert world["shoot"].location_id == world["studio"].id


def test_a_location_used_on_the_calendar_joins_the_projects_list(pg_client, world):
    _set_strip(pg_client, world, world["studio"])
    _bulk(pg_client, world, [8], world["barn"])
    listed = pg_client.get(f"/projects/{world['film'].id}/locations", headers=_h(world)).json()["locations"]
    assert [(e["location"]["name"], e["strip_names"], e["day_dates"]) for e in listed] == [
        ("Barn Tulln", [], [ISO(8)]),
        ("Studio Wien", ["Shoot"], []),
    ]


# ─── Who may ────────────────────────────────────────────────────────────────


def test_staff_see_the_strip_location_and_no_hotels(pg_client, pg_db, world):
    _set_strip(pg_client, world, world["studio"])
    _add_hotel(pg_client, world, world["hotel"])
    staff = _user(pg_db, world, CompanyRole.staff)
    _set_hotel(pg_client, world, world["hotel"])
    strip = _calendar_strip(pg_client, world, staff)
    assert strip["location_ref"]["name"] == "Studio Wien"
    assert "Hotel Test" not in pg_client.get(f"/projects/{world['film'].id}/calendar", headers=_h(world, staff)).text
    listed = pg_client.get(f"/projects/{world['film'].id}/locations", headers=_h(world, staff)).json()
    assert ([e["location"]["name"] for e in listed["locations"]], listed["hotels_visible"]) == (["Studio Wien"], False)
    assert "Anna Kern" not in str(listed)
    # Control: the owner sees the hotel and its guest on the same project.
    owner = pg_client.get(f"/projects/{world['film'].id}/locations", headers=_h(world)).json()
    assert [(e["location"]["name"], [g["party_name"] for g in e["stays"]]) for e in owner["locations"]] == [
        ("Hotel Test", ["Anna Kern"]),
        ("Studio Wien", []),
    ]
    assert _set_strip(pg_client, world, world["barn"], staff).status_code == 404
    assert _add_hotel(pg_client, world, world["hotel"], staff).status_code == 404
    assert _set_hotel(pg_client, world, world["hotel"], staff).status_code == 404


@pytest.mark.parametrize(
    "role, can_set",
    [
        (CompanyRole.owner, True),
        (CompanyRole.admin, True),
        (CompanyRole.producer, True),
        (CompanyRole.accountant, False),
        (CompanyRole.staff, False),
        (CompanyRole.tax_advisor, False),
    ],
)
def test_the_attach_permission_matrix(pg_client, pg_db, world, role, can_set):
    user = _user(pg_db, world, role)
    expected = 200 if can_set else 404
    assert _set_strip(pg_client, world, world["studio"], user).status_code == expected
    assert _bulk(pg_client, world, [7], world["barn"], user).status_code == expected
    assert _add_hotel(pg_client, world, world["hotel"], user).status_code == expected
    assert _set_hotel(pg_client, world, world["hotel"], user).status_code == expected


def test_without_locations_view_nothing_can_be_attached(pg_client, pg_db, world, monkeypatch):
    """The producer keeps `projects.edit` and `parties.view` — the crew row is
    reachable — and loses `locations.view`, so seeing hotels is false too."""
    monkeypatch.setitem(
        permissions.PERMISSIONS, "locations.view", permissions.PERMISSIONS["locations.view"] - {CompanyRole.producer}
    )
    producer = _user(pg_db, world, CompanyRole.producer)
    assert _set_strip(pg_client, world, world["studio"], producer).status_code == 404
    assert _bulk(pg_client, world, [7], world["barn"], producer).status_code == 404
    assert _add_hotel(pg_client, world, world["hotel"], producer).status_code == 404
    assert _set_hotel(pg_client, world, world["hotel"], producer).status_code == 404
    # The crew row itself is still theirs to read: without hotels.
    row = _crew(pg_client, world, producer)
    assert (row["party_name"], row["hotel_visible"], row["hotel_stays"]) == ("Anna Kern", False, [])
    # Control: the owner, same world.
    assert _set_hotel(pg_client, world, world["hotel"]).status_code == 200


def test_another_companys_location_cannot_be_attached(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    their_studio = make_location(pg_db, other, name="Their studio")
    their_hotel = make_location(pg_db, other, name="Their hotel", kind="hotel")
    assert _set_strip(pg_client, world, their_studio).status_code == 404
    assert _bulk(pg_client, world, [7], their_studio).status_code == 404
    day = _day(pg_db, world, 7)
    assert pg_client.patch(f"/projects/{world['film'].id}/days/{day.id}", headers=_h(world), json={"location_id": str(their_studio.id)}).status_code == 404
    assert _add_hotel(pg_client, world, their_hotel).status_code == 404
    assert _set_hotel(pg_client, world, their_hotel).status_code == 404
    pg_db.expire_all()
    assert (world["shoot"].location_id, _day(pg_db, world, 7).location_id) == (None, None)


def test_another_companys_strip_is_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    their_project = make_project(pg_db, other, name="Theirs", project_number="X-1")
    their_block = make_project_block(pg_db, other, their_project, start_date=JUL(6), end_date=JUL(8))
    pg_db.commit()
    url = f"/projects/{world['film'].id}/blocks/{their_block.id}"
    assert pg_client.patch(url, headers=_h(world), json={"location_id": str(world["studio"].id)}).status_code == 404
    pg_db.expire_all()
    assert their_block.location_id is None
