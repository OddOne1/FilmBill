"""Team members as crew: a login linked to a contact (R2 part 1). Real
PostgreSQL throughout.

The world, in YON Studio OG: Olga (owner), Adam (admin), Paula (producer),
Sam (staff), Theo (tax advisor) and Max (staff). Contacts: Anna Berger, a
person whose primary email is Sam's login email in other letters; "Berger
Film GmbH", an organisation; Max Crew, a crew person already linked to Max
with the role Gaffer and an own day rate of 650.00. Labor items DoP and
Gaffer. A project "Spot" with a future strip "Shoot" and a past strip
"Prep".

Every assertion reads the database or the API's own answer.
"""

import os
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from apps.api.models.activity import Notification
from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.crew import CrewAssignment, PartyCrewRole
from apps.api.models.party import Party, PartyContactPoint
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_crew_assignment,
    make_crew_role,
    make_item,
    make_party,
    make_project,
    make_project_block,
    make_superadmin,
    make_user,
)
from .test_projects_api import _alembic, _scratch_database

TODAY = date.today()
FUTURE = TODAY + timedelta(days=10)
PAST = TODAY - timedelta(days=20)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    other = make_company(pg_db, "Other GmbH", address_country="AT")
    people = {}
    for key, email, role in (
        ("olga", "olga@yon.example", CompanyRole.owner),
        ("adam", "adam@yon.example", CompanyRole.admin),
        ("paula", "paula@yon.example", CompanyRole.producer),
        ("acc", "acc@yon.example", CompanyRole.accountant),
        ("sam", "sam@yon.example", CompanyRole.staff),
        ("theo", "theo@tax.example", CompanyRole.tax_advisor),
        ("max", "max@yon.example", CompanyRole.staff),
        ("nora", "nora@yon.example", CompanyRole.staff),
    ):
        user = make_user(pg_db, email)
        user.first_name = key.capitalize()
        user.last_name = "Tester"
        grant(pg_db, company, user, role)
        people[key] = user
    # Rex had access and lost it; Uma is in the other company only.
    rex = make_user(pg_db, "rex@yon.example")
    grant(pg_db, company, rex, CompanyRole.staff, revoked_at=datetime.now(timezone.utc) - timedelta(days=1))
    uma = make_user(pg_db, "uma@other.example")
    grant(pg_db, other, uma, CompanyRole.owner)
    pg_db.commit()

    dop = make_item(pg_db, company, sku="L-DOP", name_de="DoP", kind="labor", bill_rate_day="900.00", cost_rate_day="600.00")
    gaffer = make_item(pg_db, company, sku="L-GAF", name_de="Gaffer", kind="labor", bill_rate_day="600.00", cost_rate_day="400.00")
    camera = make_item(pg_db, company, sku="E-CAM", name_de="Kamera", kind="equipment", bill_rate_day="300.00", cost_rate_day="0")

    anna = make_party(pg_db, company, type="person", name="Anna Berger", email="SAM@Yon.Example", tags=["Licht"])
    org = make_party(pg_db, company, type="organisation", name="Berger Film GmbH", email="org@yon.example")
    max_contact = make_party(pg_db, company, type="person", name="Max Crew", is_crew=True)
    max_contact.user_id = people["max"].id
    pg_db.commit()
    max_role = make_crew_role(pg_db, company, max_contact, gaffer, own_day_rate="650.00")

    spot = make_project(pg_db, company, name="Spot", project_number="PRJ-1", start_date=PAST)
    shoot = make_project_block(pg_db, company, spot, start_date=FUTURE, end_date=FUTURE + timedelta(days=2), label="Shoot")
    shoot.location = "Studio Wien"
    shoot.default_call_time = time(8, 0)
    prep = make_project_block(pg_db, company, spot, start_date=PAST, end_date=PAST + timedelta(days=1), label="Prep")
    pg_db.commit()
    return {
        "company": company, "other": other, **people, "rex": rex, "uma": uma,
        "dop": dop, "gaffer": gaffer, "camera": camera,
        "anna": anna, "org": org, "max_contact": max_contact, "max_role": max_role,
        "spot": spot, "shoot": shoot, "prep": prep,
    }


def _h(world, who="olga"):
    return auth_headers(world[who], world["company"])


def _team(pg_client, world, who="olga"):
    response = pg_client.get("/team", headers=_h(world, who))
    assert response.status_code == 200, response.text
    return {m["email"]: m for m in response.json()["members"]}


def _crew(pg_client, world, user, who="olga", **body):
    return pg_client.post(f"/team/{world[user].id}/crew", headers=_h(world, who), json=body)


def _events(pg_db, entity_id, prefix=""):
    return [
        e.action
        for e in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == entity_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
        if e.action.startswith(prefix)
    ]


def _walk(value, path=""):
    """Every (key path, value) in a JSON body."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield f"{path}.{key}", inner
            yield from _walk(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _walk(inner, f"{path}[{index}]")


# ─── The link ───────────────────────────────────────────────────────────────


def test_the_email_suggestion_is_offered_and_linking_needs_the_confirmed_id(pg_client, pg_db, world):
    sam = _team(pg_client, world)["sam@yon.example"]
    # One person contact with Sam's email, in other letters: suggested,
    # and nothing linked yet.
    assert sam["crew_state"] == "needs_link"
    assert sam["suggestion"]["id"] == str(world["anna"].id)
    assert sam["suggestion"]["name"] == "Anna Berger"
    pg_db.expire_all()
    assert pg_db.get(Party, world["anna"].id).user_id is None

    # `link` without the id is refused; nothing follows the suggestion.
    refused = _crew(pg_client, world, "sam", mode="link")
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Choose the contact to connect, or create a new crew contact instead."
    pg_db.expire_all()
    assert pg_db.get(Party, world["anna"].id).user_id is None

    linked = _crew(pg_client, world, "sam", mode="link", party_id=str(world["anna"].id))
    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert (body["crew_state"], body["contact"]["name"], body["suggestion"]) == ("crew", "Anna Berger", None)
    pg_db.expire_all()
    anna = pg_db.get(Party, world["anna"].id)
    assert (anna.user_id, anna.is_crew) == (world["sam"].id, True)
    # Linking merged nothing: the tags and the email stay as they were.
    assert anna.tags == ["Licht"]
    assert [c.value for c in pg_db.query(PartyContactPoint).filter(PartyContactPoint.party_id == anna.id)] == ["SAM@Yon.Example"]
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == anna.id, AuditEvent.action == "team.crew_linked").one()
    assert event.user_id == world["olga"].id


def test_suggestions_with_none_one_and_two_matches(pg_client, pg_db, world):
    # None: Nora's email is on no contact → no suggestion, "not crew".
    nora = _team(pg_client, world)["nora@yon.example"]
    assert (nora["crew_state"], nora["suggestion"], nora["matches"]) == ("not_crew", None, [])
    # One: Sam (see above). Two: a second person with Sam's email → no
    # suggestion, both offered to pick from, still "needs link".
    make_party(pg_db, world["company"], type="person", name="Anna B.", email="sam@yon.example")
    sam = _team(pg_client, world)["sam@yon.example"]
    assert sam["crew_state"] == "needs_link"
    assert sam["suggestion"] is None
    assert sorted(m["name"] for m in sam["matches"]) == ["Anna B.", "Anna Berger"]
    # An organisation with an email is never a match; an archived person
    # is not either.
    make_party(pg_db, world["company"], type="organisation", name="Nora GmbH", email="nora@yon.example")
    make_party(pg_db, world["company"], type="person", name="Old Nora", email="nora@yon.example", is_active=False)
    assert _team(pg_client, world)["nora@yon.example"]["matches"] == []


def test_linking_an_organisation_is_refused(pg_client, pg_db, world):
    refused = _crew(pg_client, world, "sam", mode="link", party_id=str(world["org"].id))
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "Berger Film GmbH is an organisation, not a person. A login can only be connected to a "
        "person contact; choose the person, or create a new crew contact."
    )
    pg_db.expire_all()
    assert (pg_db.get(Party, world["org"].id).user_id, pg_db.get(Party, world["org"].id).is_crew) == (None, False)


def test_a_user_without_an_active_membership_here_is_refused(pg_client, pg_db, world):
    # Rex's membership was revoked: refused with a sentence.
    refused = _crew(pg_client, world, "rex", mode="link", party_id=str(world["anna"].id))
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "Test rex has no active login in your company. Only a team member with access can be "
        "connected to a contact; give them access first."
    )
    # Uma is in another company only: she does not exist here.
    assert _crew(pg_client, world, "uma", mode="create").status_code == 404
    pg_db.expire_all()
    assert pg_db.get(Party, world["anna"].id).user_id is None
    assert pg_db.query(Party).filter(Party.user_id.in_([world["rex"].id, world["uma"].id])).count() == 0


def test_a_contact_linked_twice_is_refused_and_the_database_refuses_it_too(pg_client, pg_db, world):
    refused = _crew(pg_client, world, "sam", mode="link", party_id=str(world["max_contact"].id))
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Max Crew is already connected to another login. Unlink it there first, or choose another contact."
    )
    # A login with a contact already cannot get a second one.
    second = _crew(pg_client, world, "max", mode="link", party_id=str(world["anna"].id))
    assert second.status_code == 409
    assert second.json()["detail"].startswith("Max Tester is already connected to the contact Max Crew.")
    pg_db.expire_all()
    assert pg_db.get(Party, world["anna"].id).user_id is None
    # The partial unique index, without the service in front of it.
    world["anna"].user_id = world["max"].id
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()


def test_create_builds_a_person_contact_with_the_email_and_the_team_tag(pg_client, pg_db, world):
    made = _crew(pg_client, world, "nora", mode="create", role_item_ids=[str(world["dop"].id)])
    assert made.status_code == 200, made.text
    body = made.json()
    assert (body["crew_state"], body["contact"]["name"], body["contact"]["email"]) == ("crew", "Nora Tester", "nora@yon.example")
    # The admin chose the role: confirmed.
    assert [(r["name"], r["confirmed"], r["set_by"]) for r in body["roles"]] == [("DoP", True, "admin")]
    pg_db.expire_all()
    party = pg_db.query(Party).filter(Party.user_id == world["nora"].id).one()
    assert (party.type, party.first_name, party.last_name, party.is_crew, party.tags) == (
        "person", "Nora", "Tester", True, ["Team"]
    )
    point = pg_db.query(PartyContactPoint).filter(PartyContactPoint.party_id == party.id).one()
    assert (point.kind, point.value, point.is_primary) == ("email", "nora@yon.example", True)
    assert sorted(_events(pg_db, party.id)) == ["party.created", "team.crew_created"]


def test_stop_working_as_crew_lists_future_bookings_and_cancels_nothing(pg_client, pg_db, world):
    w = world
    future = make_crew_assignment(pg_db, w["company"], w["spot"], w["shoot"], w["max_contact"], status="booked", dates=[FUTURE, FUTURE + timedelta(days=1)], role=w["gaffer"])
    past = make_crew_assignment(pg_db, w["company"], w["spot"], w["prep"], w["max_contact"], dates=[PAST])
    stopped = pg_client.post(f"/team/{w['max'].id}/crew/stop", headers=_h(w))
    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert [(b["project_name"], b["strip_name"], b["status"], b["first_date"], b["last_date"]) for b in body["future_bookings"]] == [
        ("Spot", "Shoot", "booked", FUTURE.isoformat(), (FUTURE + timedelta(days=1)).isoformat())
    ]
    assert (body["member"]["crew_state"], body["member"]["contact"]["name"]) == ("not_crew", "Max Crew")
    pg_db.expire_all()
    contact = pg_db.get(Party, w["max_contact"].id)
    assert (contact.is_crew, contact.user_id) == (False, w["max"].id)
    assert (pg_db.get(CrewAssignment, future.id).status, pg_db.get(CrewAssignment, past.id).status) == ("booked", "scheduled")
    assert pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == contact.id, PartyCrewRole.is_active.is_(True)).count() == 1
    assert _events(pg_db, contact.id, "team.") == ["team.crew_stopped"]


def test_unlink_leaves_the_contact(pg_client, pg_db, world):
    unlinked = pg_client.post(f"/team/{world['max'].id}/unlink", headers=_h(world))
    assert unlinked.status_code == 200, unlinked.text
    assert (unlinked.json()["contact"], unlinked.json()["crew_state"]) == (None, "not_crew")
    pg_db.expire_all()
    contact = pg_db.get(Party, world["max_contact"].id)
    assert (contact.user_id, contact.is_crew, contact.is_active, contact.name) == (None, True, True, "Max Crew")
    assert pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == contact.id, PartyCrewRole.is_active.is_(True)).count() == 1
    assert _events(pg_db, contact.id, "team.") == ["team.unlinked"]


def test_a_revoked_login_keeps_its_link_and_shows_no_active_login(pg_client, pg_db, world):
    from apps.api.models.company import CompanyMembership

    record = pg_db.query(CompanyMembership).filter(CompanyMembership.user_id == world["max"].id).one()
    record.revoked_at = datetime.now(timezone.utc)
    pg_db.commit()
    row = _team(pg_client, world)["max@yon.example"]
    assert (row["has_active_login"], row["role"], row["crew_state"], row["contact"]["name"]) == (False, None, "crew", "Max Crew")
    pg_db.expire_all()
    assert pg_db.get(Party, world["max_contact"].id).user_id == world["max"].id
    # And the member's own routes do not apply any more.
    assert pg_client.get("/me/crew", headers=_h(world, "max")).status_code == 404


# ─── Self-service ───────────────────────────────────────────────────────────


def test_switching_on_with_no_match_creates_one_contact(pg_client, pg_db, world):
    on = pg_client.put("/me/crew", headers=_h(world, "nora"), json={"is_crew": True})
    assert on.status_code == 200, on.text
    profile = on.json()["profile"]
    assert (profile["is_crew"], profile["crew_state"], profile["contact_name"], profile["message"]) == (True, "crew", "Nora Tester", None)
    again = pg_client.put("/me/crew", headers=_h(world, "nora"), json={"is_crew": True})
    assert again.status_code == 200
    pg_db.expire_all()
    party = pg_db.query(Party).filter(Party.user_id == world["nora"].id).one()
    assert party.tags == ["Team"]
    created = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == party.id, AuditEvent.action == "team.crew_created").one()
    assert (created.user_id, created.changes["self_service"][1]) == (world["nora"].id, True)


def test_switching_on_with_a_match_creates_nothing_and_tells_the_admins(pg_client, pg_db, world):
    parties_before = pg_db.query(Party).count()
    on = pg_client.put("/me/crew", headers=_h(world, "sam"), json={"is_crew": True})
    assert on.status_code == 409
    assert on.json()["detail"] == "A contact with your email already exists. An admin has to connect it to your login."
    pg_db.expire_all()
    assert pg_db.query(Party).count() == parties_before
    # The match was NOT linked: an admin has to confirm it.
    assert pg_db.get(Party, world["anna"].id).user_id is None
    assert pg_db.query(Party).filter(Party.user_id == world["sam"].id).count() == 0
    notes = pg_db.query(Notification).filter(Notification.title == "Sam Tester wants to work as crew").all()
    assert sorted(n.user_id for n in notes) == sorted([world["olga"].id, world["adam"].id])
    assert {n.link for n in notes} == {"/settings/company/members"}
    # A second click while those are unread notifies nobody again.
    assert pg_client.put("/me/crew", headers=_h(world, "sam"), json={"is_crew": True}).status_code == 409
    assert pg_db.query(Notification).filter(Notification.title == "Sam Tester wants to work as crew").count() == 2
    profile = pg_client.get("/me/crew", headers=_h(world, "sam")).json()
    assert (profile["is_crew"], profile["crew_state"], profile["message"]) == (
        False, "needs_link", "A contact with your email already exists. An admin has to connect it to your login."
    )


def test_a_member_writing_roles_gets_self_reported_ones_and_an_admin_confirms(pg_client, pg_db, world):
    w = world
    assert pg_client.put("/me/crew", headers=_h(w, "nora"), json={"is_crew": True}).status_code == 200
    wrote = pg_client.put("/me/crew/roles", headers=_h(w, "nora"), json={"item_ids": [str(w["dop"].id), str(w["gaffer"].id)]})
    assert wrote.status_code == 200, wrote.text
    assert [(r["name"], r["confirmed"], r["set_by"]) for r in wrote.json()["roles"]] == [
        ("DoP", False, "self"), ("Gaffer", False, "self")
    ]
    pg_db.expire_all()
    party = pg_db.query(Party).filter(Party.user_id == w["nora"].id).one()
    rows = pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == party.id, PartyCrewRole.is_active.is_(True)).all()
    assert {(r.confirmed, r.set_by, r.own_day_rate) for r in rows} == {(False, "self", None)}
    assert _events(pg_db, rows[0].id) == ["crew_role.self_set"]

    dop_row = next(r for r in rows if r.item_id == w["dop"].id)
    confirmed = pg_client.post(f"/team/{w['nora'].id}/crew/roles/{dop_row.id}/confirm", headers=_h(w, "adam"))
    assert confirmed.status_code == 200, confirmed.text
    assert [(r["name"], r["confirmed"]) for r in confirmed.json()["roles"]] == [("DoP", True), ("Gaffer", False)]
    assert sorted(_events(pg_db, dop_row.id)) == ["crew_role.confirmed", "crew_role.self_set"]

    # The member saves again without Gaffer: DoP stays confirmed, Gaffer is
    # retired (kept as a row), nothing is deleted.
    again = pg_client.put("/me/crew/roles", headers=_h(w, "nora"), json={"item_ids": [str(w["dop"].id)]})
    assert [(r["name"], r["confirmed"]) for r in again.json()["roles"]] == [("DoP", True)]
    pg_db.expire_all()
    assert pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == party.id).count() == 2
    # A non-labor item is refused with a sentence.
    refused = pg_client.put("/me/crew/roles", headers=_h(w, "nora"), json={"item_ids": [str(w["camera"].id)]})
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("“Kamera” is not a labor item in the catalog.")


def test_roles_need_crew_first(pg_client, pg_db, world):
    refused = pg_client.put("/me/crew/roles", headers=_h(world, "nora"), json={"item_ids": [str(world["dop"].id)]})
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "You are not working as crew yet. Switch on “I also work as crew” first, then choose your roles."
    )


def test_the_admin_dialog_saving_roles_confirms_them(pg_client, pg_db, world):
    w = world
    pg_client.put("/me/crew", headers=_h(w, "nora"), json={"is_crew": True})
    pg_client.put("/me/crew/roles", headers=_h(w, "nora"), json={"item_ids": [str(w["dop"].id)]})
    contact_id = _team(pg_client, w)["nora@yon.example"]["contact"]["id"]
    saved = _crew(pg_client, w, "nora", mode="link", party_id=contact_id, role_item_ids=[str(w["dop"].id)])
    assert saved.status_code == 200, saved.text
    assert [(r["name"], r["confirmed"], r["set_by"]) for r in saved.json()["roles"]] == [("DoP", True, "self")]


def test_no_rate_in_any_me_response(pg_client, pg_db, world):
    w = world
    # Max holds Gaffer at an own rate of 650.00, and has a booking.
    make_crew_assignment(pg_db, w["company"], w["spot"], w["shoot"], w["max_contact"], status="booked", dates=[FUTURE], role=w["gaffer"])
    pg_db.query(CrewAssignment).update({"day_rate": 650})
    pg_db.commit()
    bodies = {
        "GET /me/crew": pg_client.get("/me/crew", headers=_h(w, "max")),
        "PUT /me/crew/roles": pg_client.put("/me/crew/roles", headers=_h(w, "max"), json={"item_ids": [str(w["gaffer"].id)]}),
        "GET /me/schedule": pg_client.get("/me/schedule", headers=_h(w, "max")),
        "PUT /me/crew": pg_client.put("/me/crew", headers=_h(w, "max"), json={"is_crew": True}),
    }
    for name, response in bodies.items():
        assert response.status_code == 200, (name, response.text)
        found = [(path, value) for path, value in _walk(response.json()) if "rate" in path.lower() or value in ("650.00", "650", 650)]
        assert found == [], f"{name} carries a rate: {found}"
    # The positive half: the rate is there, for somebody who may see it.
    roles = pg_client.get(f"/parties/{w['max_contact'].id}/crew-roles", headers=_h(w, "olga")).json()["roles"]
    assert roles[0]["own_day_rate"] == "650.00"
    assert bodies["GET /me/crew"].json()["roles"][0]["name"] == "Gaffer"


def test_a_me_request_cannot_carry_a_rate_or_a_party(pg_client, pg_db, world):
    w = world
    with_rate = pg_client.put("/me/crew/roles", headers=_h(w, "max"), json={"item_ids": [], "own_day_rate": "900.00"})
    assert with_rate.status_code == 422
    pg_db.expire_all()
    assert str(pg_db.get(PartyCrewRole, w["max_role"].id).own_day_rate) == "650.00"


def test_a_self_service_route_never_touches_another_members_contact(pg_client, pg_db, world):
    """The victim is Max's linked contact: the row only the "the contact
    comes from the session" guard protects. Nora is crew herself, so her
    request is otherwise a valid one."""
    w = world
    pg_client.put("/me/crew", headers=_h(w, "nora"), json={"is_crew": True})
    attempt = pg_client.put(
        "/me/crew/roles",
        headers=_h(w, "nora"),
        json={"item_ids": [str(w["dop"].id)], "party_id": str(w["max_contact"].id)},
    )
    pg_db.expire_all()
    victim_roles = sorted(
        (r.item_id, r.is_active, r.confirmed)
        for r in pg_db.query(PartyCrewRole).filter(PartyCrewRole.party_id == w["max_contact"].id)
    )
    assert victim_roles == [(w["gaffer"].id, True, True)]
    assert attempt.status_code == 422
    # And switching off is the caller's own contact too.
    pg_client.put("/me/crew", headers=_h(w, "nora"), json={"is_crew": False, "party_id": str(w["max_contact"].id)})
    pg_db.expire_all()
    assert pg_db.get(Party, w["max_contact"].id).is_crew is True


def test_switching_off_keeps_the_link_and_the_bookings(pg_client, pg_db, world):
    w = world
    booking = make_crew_assignment(pg_db, w["company"], w["spot"], w["shoot"], w["max_contact"], dates=[FUTURE])
    off = pg_client.put("/me/crew", headers=_h(w, "max"), json={"is_crew": False})
    assert off.status_code == 200, off.text
    assert [b["strip_name"] for b in off.json()["future_bookings"]] == ["Shoot"]
    pg_db.expire_all()
    contact = pg_db.get(Party, w["max_contact"].id)
    assert (contact.is_crew, contact.user_id, pg_db.get(CrewAssignment, booking.id).status) == (False, w["max"].id, "scheduled")
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == contact.id, AuditEvent.action == "team.crew_stopped").one()
    assert (event.user_id, event.changes["self_service"][1]) == (w["max"].id, True)


# ─── Who may ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "who, team, me",
    [
        ("olga", 200, 200),
        ("adam", 200, 200),
        ("paula", 404, 200),
        ("acc", 404, 200),
        ("sam", 404, 200),
        ("theo", 404, 404),
    ],
)
def test_the_permission_matrix(pg_client, pg_db, world, who, team, me):
    w = world
    assert pg_client.get("/team", headers=_h(w, who)).status_code == team
    assert _crew(pg_client, w, "nora", who=who, mode="create").status_code == team
    assert pg_client.post(f"/team/{w['max'].id}/unlink", headers=_h(w, who)).status_code == team
    assert pg_client.get("/me/crew", headers=_h(w, who)).status_code == me
    assert pg_client.get("/me/schedule", headers=_h(w, who)).status_code == me
    pg_db.expire_all()
    linked = pg_db.get(Party, w["max_contact"].id).user_id
    assert linked == (None if team == 200 else w["max"].id)


def test_there_is_no_route_that_deletes_a_contact_or_a_link():
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    deletes = sorted(
        path
        for path, operations in schema["paths"].items()
        if "delete" in operations and (path.startswith("/team") or path.startswith("/me/crew") or path.startswith("/me/schedule"))
    )
    # "Stops working as crew" is a POST (`…/crew/stop`); the stop test
    # proves the contact, the link and the bookings stay. The positive half:
    # the walk did see the team routes.
    assert deletes == []
    assert "/team/{user_id}/crew/stop" in schema["paths"]


# ─── The migration ──────────────────────────────────────────────────────────


@pytest.fixture
def at_revision_0031(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0031_gear_bookings")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0031:\n{result.stderr}")
    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_migration_keeps_every_existing_role_confirmed_and_goes_back(at_revision_0031):
    engine, url = at_revision_0031
    with engine.begin() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, default_language, "
                "fiscal_year_start_month, timezone, require_2fa_roles, tax_advisor_reports) VALUES "
                "(gen_random_uuid(), 'Mig GmbH', 'EUR', 'de', 1, 'Europe/Vienna', '[]', false) RETURNING id"
            )
        ).scalar()
        party_id = conn.execute(
            text("INSERT INTO parties (id, company_id, type, name, is_crew) VALUES (gen_random_uuid(), :c, 'person', 'Anna', true) RETURNING id"),
            {"c": company_id},
        ).scalar()
        item_id = conn.execute(
            text(
                "INSERT INTO items (id, company_id, sku, name_de, kind) VALUES "
                "(gen_random_uuid(), :c, 'L-DOP', 'DoP', 'labor') RETURNING id"
            ),
            {"c": company_id},
        ).scalar()
        conn.execute(
            text("INSERT INTO party_crew_roles (id, company_id, party_id, item_id) VALUES (gen_random_uuid(), :c, :p, :i)"),
            {"c": company_id, "p": party_id, "i": item_id},
        )

    up = _alembic(url, "upgrade", "0032_team_members_as_crew")
    assert up.returncode == 0, up.stderr
    with engine.connect() as conn:
        assert conn.execute(text("SELECT confirmed, set_by FROM party_crew_roles")).all() == [(True, "admin")]
        assert conn.execute(text("SELECT name, user_id FROM parties")).all() == [("Anna", None)]
        assert "team" in conn.execute(text("SELECT unnest(enum_range(NULL::notificationtype))::text")).scalars().all()

    down = _alembic(url, "downgrade", "0031_gear_bookings")
    assert down.returncode == 0, down.stderr
    with engine.connect() as conn:
        columns = conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'parties'")
        ).scalars().all()
        assert "user_id" not in columns
        assert conn.execute(text("SELECT unnest(enum_range(NULL::notificationtype))::text")).scalars().all() == ["account"]

    again = _alembic(url, "upgrade", "0032_team_members_as_crew")
    assert again.returncode == 0, again.stderr


# ─── The "Team" marker on contacts, the crew list and the picker (part 2) ──


def test_a_linked_contact_is_marked_team_everywhere_and_the_crew_list_filters(pg_client, pg_db, world):
    w = world
    freelancer = make_party(pg_db, w["company"], type="person", name="Fred Lance", is_crew=True)
    h = _h(w)
    detail = pg_client.get(f"/parties/{w['max_contact'].id}", headers=h).json()
    assert (detail["is_team_member"], detail["team_user_id"], detail["team_login_email"]) == (True, str(w["max"].id), "max@yon.example")
    rows = {p["name"]: p["is_team_member"] for p in pg_client.get("/parties?type=person", headers=h).json()["parties"]}
    assert (rows["Max Crew"], rows["Fred Lance"], rows["Anna Berger"]) == (True, False, False)

    def crew(kind=None):
        query = f"?kind={kind}" if kind else ""
        return {m["name"]: m["is_team_member"] for m in pg_client.get(f"/crew{query}", headers=h).json()["members"]}

    assert crew() == {"Fred Lance": False, "Max Crew": True}
    assert crew("team") == {"Max Crew": True}
    assert crew("freelance") == {"Fred Lance": False}
    people = pg_client.get(f"/crew/availability?from={FUTURE}&to={FUTURE}", headers=h).json()["people"]
    assert {p["name"]: p["is_team_member"] for p in people} == {"Fred Lance": False, "Max Crew": True}
    # Rates and roles: the same as for anybody — Max's own rate is there.
    assert pg_client.get("/crew?kind=team", headers=h).json()["members"][0]["crew_roles"][0]["own_day_rate"] == "650.00"
    assert freelancer.user_id is None
