"""Crew on the calendar: assignments to strips, days, status, clashes,
availability and unavailability (B2c-1 part 2). Real PostgreSQL throughout.

The world: project "Spot Sommer" has a strip "Shoot" from Mon 6 to Fri 10
July 2026 (6 July is a travel day, 8 July is `off`); project "Doku Herbst"
has a strip "Dreh" from 9 to 12 July. Labor items DoP (typical 600/day) and
Gaffer (typical 400/day). Crew: Anna (DoP, own 650), Ben (Gaffer), Cleo
(DoP, typical). Pat is a contact who is not crew.

Every assertion reads back the database or the API's own answer, and the
clash cases name the dates so a reader can check them by hand.
"""

from datetime import date, time
from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.crew import CrewAssignment, CrewAssignmentDay, CrewUnavailability
from apps.api.models.project import ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import permissions

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_crew_assignment,
    make_crew_role,
    make_crew_unavailability,
    make_item,
    make_party,
    make_project,
    make_project_block,
    make_superadmin,
    make_user,
)

JUL = lambda day: date(2026, 7, day)  # noqa: E731


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "assign-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    dop = make_item(pg_db, company, sku="L-DOP", name_de="DoP", kind="labor", bill_rate_day="900.00", cost_rate_day="600.00")
    gaffer = make_item(pg_db, company, sku="L-GAF", name_de="Gaffer", kind="labor", bill_rate_day="600.00", cost_rate_day="400.00")
    spot = make_project(pg_db, company, name="Spot Sommer", project_number="PRJ-1", start_date=JUL(1))
    doku = make_project(pg_db, company, name="Doku Herbst", project_number="PRJ-2", start_date=JUL(1))
    shoot = make_project_block(pg_db, company, spot, start_date=JUL(6), end_date=JUL(10), type="shoot", label="Shoot")
    shoot.default_call_time = time(8, 0)
    days = {d.date: d for d in pg_db.query(ProjectDay).filter(ProjectDay.block_id == shoot.id)}
    days[JUL(6)].type = "travel"
    days[JUL(8)].type = "off"
    days[JUL(9)].call_time = time(7, 0)
    dreh = make_project_block(pg_db, company, doku, start_date=JUL(9), end_date=JUL(12), type="shoot", label="Dreh")
    pg_db.commit()
    anna = make_party(pg_db, company, type="person", name="Anna Huber", is_crew=True)
    ben = make_party(pg_db, company, type="person", name="Ben Ohr", is_crew=True)
    cleo = make_party(pg_db, company, type="person", name="Cleo Lang", is_crew=True)
    pat = make_party(pg_db, company, type="person", name="Pat Client", is_crew=False)
    make_crew_role(pg_db, company, anna, dop, own_day_rate="650.00")
    make_crew_role(pg_db, company, ben, gaffer)
    make_crew_role(pg_db, company, cleo, dop)
    return {
        "company": company, "owner": owner, "dop": dop, "gaffer": gaffer,
        "spot": spot, "doku": doku, "shoot": shoot, "dreh": dreh,
        "anna": anna, "ben": ben, "cleo": cleo, "pat": pat,
    }


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _add(pg_client, world, block, project, *people, user=None):
    body = {"people": [dict(p) for p in people]}
    return pg_client.post(f"/projects/{project.id}/blocks/{block.id}/crew", headers=_h(world, user), json=body)


def _person(party, role=None, dates=None):
    entry = {"party_id": str(party.id)}
    if role is not None:
        entry["role_item_id"] = str(role.id)
    if dates is not None:
        entry["dates"] = [d.isoformat() for d in dates]
    return entry


def _ok_add(pg_client, world, block, project, *people):
    made = _add(pg_client, world, block, project, *people)
    assert made.status_code == 201, made.text
    return made.json()


def _status(pg_client, world, project, assignment_id, status, note=None, user=None):
    return pg_client.post(
        f"/projects/{project.id}/crew/{assignment_id}/status",
        headers=_h(world, user),
        json={"status": status, "note": note},
    )


def _dates(pg_db, assignment_id):
    pg_db.expire_all()
    return sorted(
        d.date
        for d in pg_db.query(CrewAssignmentDay).filter(
            CrewAssignmentDay.assignment_id == assignment_id, CrewAssignmentDay.is_active.is_(True)
        )
    )


# ─── Creating, days, call and wrap ──────────────────────────────────────────


def test_a_new_assignment_gets_every_strip_day_but_the_off_day_and_copies_the_rate(pg_client, pg_db, world):
    body = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"], world["dop"]), _person(world["ben"]))
    anna, ben = body["assignments"]
    assert (anna["party_name"], anna["status"], anna["role_name"], anna["day_rate"]) == ("Anna Huber", "scheduled", "DoP", "650.00")
    assert (ben["role_name"], ben["day_rate"]) == (None, None)
    # 6–10 July without the `off` day 8 July; 6 July (travel) stays in.
    assert [d["date"] for d in anna["days"]] == ["2026-07-06", "2026-07-07", "2026-07-09", "2026-07-10"]
    assert [d["day_type"] for d in anna["days"]] == ["travel", "shoot", "shoot", "shoot"]
    assert _dates(pg_db, anna["id"]) == [JUL(6), JUL(7), JUL(9), JUL(10)]
    stored = pg_db.get(CrewAssignment, anna["id"])
    assert (stored.status, stored.day_rate) == ("scheduled", Decimal("650.00"))
    assert [e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == stored.id)] == ["crew_assignment.created"]
    assert body["clashes"] == []


def test_unticking_days_and_call_and_wrap_overrides(pg_client, pg_db, world):
    made = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))["assignments"][0]
    changed = pg_client.patch(
        f"/projects/{world['spot'].id}/crew/{made['id']}",
        headers=_h(world),
        json={"days": [{"date": "2026-07-07", "call_time": "06:30"}, {"date": "2026-07-09"}, {"date": "2026-07-10", "wrap_time": "20:00"}]},
    )
    assert changed.status_code == 200, changed.text
    days = {d["date"]: d for d in changed.json()["days"]}
    assert list(days) == ["2026-07-07", "2026-07-09", "2026-07-10"]
    # Own call; the day's own call (07:00); the strip's default (08:00).
    assert (days["2026-07-07"]["call_time"], days["2026-07-07"]["effective_call_time"]) == ("06:30:00", "06:30:00")
    assert (days["2026-07-09"]["call_time"], days["2026-07-09"]["effective_call_time"]) == (None, "07:00:00")
    assert days["2026-07-10"]["effective_call_time"] == "08:00:00"
    assert days["2026-07-10"]["wrap_time"] == "20:00:00"
    assert _dates(pg_db, made["id"]) == [JUL(7), JUL(9), JUL(10)]
    # The unticked travel day is kept as an inactive row, not deleted.
    assert pg_db.query(CrewAssignmentDay).filter(CrewAssignmentDay.assignment_id == made["id"], CrewAssignmentDay.date == JUL(6)).one().is_active is False
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == made["id"], AuditEvent.action == "crew_assignment.days_set").one()
    assert [d["date"] for d in event.changes["days"][1]] == ["2026-07-07", "2026-07-09", "2026-07-10"]

    # Ticking 6 July again reactivates the same row.
    again = pg_client.patch(
        f"/projects/{world['spot'].id}/crew/{made['id']}",
        headers=_h(world),
        json={"days": [{"date": "2026-07-06"}, {"date": "2026-07-07", "call_time": "06:30"}]},
    )
    assert again.status_code == 200
    assert pg_db.query(CrewAssignmentDay).filter(CrewAssignmentDay.assignment_id == made["id"], CrewAssignmentDay.date == JUL(6)).count() == 1


def test_a_day_outside_the_strip_is_refused(pg_client, pg_db, world):
    refused = _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"], dates=[JUL(7), JUL(20)]))
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "20 July 2026 is outside the strip “Shoot” (6 July 2026 – 10 July 2026). Choose days inside the strip, or change the strip first."
    )
    assert pg_db.query(CrewAssignment).count() == 0
    made = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))["assignments"][0]
    patched = pg_client.patch(
        f"/projects/{world['spot'].id}/crew/{made['id']}", headers=_h(world), json={"days": [{"date": "2026-07-05"}]}
    )
    assert patched.status_code == 422
    assert _dates(pg_db, made["id"]) == [JUL(6), JUL(7), JUL(9), JUL(10)]


def test_a_party_that_is_not_crew_is_refused_and_nobody_is_added(pg_client, pg_db, world):
    refused = _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]), _person(world["pat"]))
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Pat Client is not marked as crew. Mark them as crew, then add them to the strip."
    assert pg_db.query(CrewAssignment).count() == 0


def test_the_same_person_twice_on_a_strip_is_refused(pg_client, pg_db, world):
    _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))
    again = _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))
    assert again.status_code == 409
    assert "Anna Huber is already on the strip “Shoot”." in again.json()["detail"]


# ─── Status ─────────────────────────────────────────────────────────────────


def test_the_status_path_and_the_refused_changes(pg_client, pg_db, world):
    spot = world["spot"]
    a = _ok_add(pg_client, world, world["shoot"], spot, _person(world["anna"]), _person(world["ben"]))["assignments"]
    anna, ben = a[0]["id"], a[1]["id"]

    booked = _status(pg_client, world, spot, anna, "booked", "by phone")
    assert booked.status_code == 200, booked.text
    assert (booked.json()["status"], booked.json()["status_note"]) == ("booked", "by phone")
    assert booked.json()["booked_by_name"] and booked.json()["booked_on"]

    # Reopen needs a note.
    no_note = _status(pg_client, world, spot, anna, "scheduled")
    assert no_note.status_code == 422 and "needs a note saying why" in no_note.json()["detail"]
    reopened = _status(pg_client, world, spot, anna, "scheduled", "dates moved")
    assert reopened.status_code == 200 and reopened.json()["booked_on"] is None

    assert _status(pg_client, world, spot, ben, "declined", "busy").json()["status"] == "declined"
    refused = _status(pg_client, world, spot, ben, "booked")
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("Ben Ohr is declined on this strip and cannot be marked booked.")
    # scheduled → cancelled is "remove", not a status change.
    assert _status(pg_client, world, spot, anna, "cancelled").status_code == 422
    assert _status(pg_client, world, spot, anna, "booked", "email").status_code == 200
    assert _status(pg_client, world, spot, anna, "cancelled").json()["status"] == "cancelled"

    pg_db.expire_all()
    assert (pg_db.get(CrewAssignment, anna).status, pg_db.get(CrewAssignment, ben).status) == ("cancelled", "declined")
    actions = [e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == anna).order_by(AuditEvent.created_at, AuditEvent.id)]
    assert actions == ["crew_assignment.created"] + ["crew_assignment.status_changed"] * 4


def test_remove_is_cancelled_and_frees_the_person_to_be_added_again(pg_client, pg_db, world):
    made = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))["assignments"][0]
    removed = pg_client.post(f"/projects/{world['spot'].id}/crew/{made['id']}/remove", headers=_h(world))
    assert removed.json()["status"] == "cancelled"
    pg_db.expire_all()
    assert pg_db.get(CrewAssignment, made["id"]).status == "cancelled"  # kept
    listed = pg_client.get(f"/projects/{world['spot'].id}/crew", headers=_h(world)).json()
    assert listed["strips"][0]["assignments"] == []
    assert _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"])).status_code == 201


def test_bulk_booking_books_the_scheduled_and_leaves_the_declined(pg_client, pg_db, world):
    spot = world["spot"]
    a = _ok_add(pg_client, world, world["shoot"], spot, _person(world["anna"]), _person(world["ben"]), _person(world["cleo"]))["assignments"]
    _status(pg_client, world, spot, a[2]["id"], "declined", "no time")
    result = pg_client.post(
        f"/projects/{spot.id}/crew/status",
        headers=_h(world),
        json={"assignment_ids": [x["id"] for x in a], "status": "booked", "note": "confirmed by email"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed"] == 2
    assert [(s["party_name"], s["reason"].split(" and")[0]) for s in body["skipped"]] == [("Cleo Lang", "Cleo Lang is declined on this strip")]
    pg_db.expire_all()
    assert [pg_db.get(CrewAssignment, x["id"]).status for x in a] == ["booked", "booked", "declined"]
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == spot.id, AuditEvent.action == "crew_assignment.status_changed").one()
    assert event.changes["changed"] == [None, 2] and event.changes["skipped"] == [None, 1]
    counts = pg_client.get(f"/projects/{spot.id}/crew", headers=_h(world)).json()["strips"][0]["counts"]
    assert counts == {"scheduled": 0, "booked": 2, "declined": 1, "with_clashes": 0}


# ─── Clashes ────────────────────────────────────────────────────────────────


def test_a_clash_with_a_scheduled_then_booked_assignment_in_another_project(pg_client, pg_db, world):
    spot, doku = world["spot"], world["doku"]
    first = _ok_add(pg_client, world, world["shoot"], spot, _person(world["anna"]))["assignments"][0]
    assert first["clashes"] == []  # never with itself

    # Anna on Dreh 9–12 July overlaps her Shoot days 9 and 10 July.
    second = _ok_add(pg_client, world, world["dreh"], doku, _person(world["anna"]))
    clash = second["clashes"][0]
    assert (clash["kind"], clash["project_name"], clash["strip_name"], clash["status"], clash["dates"]) == (
        "assignment", "Spot Sommer", "Shoot", "scheduled", ["2026-07-09", "2026-07-10"],
    )
    assert clash["message"] == (
        "Anna Huber is already scheduled on Spot Sommer, strip “Shoot”, on 9 July 2026 – 10 July 2026. "
        "Check with them, or untick those days."
    )
    # The save went through anyway: clashes warn, never block.
    assert pg_db.query(CrewAssignment).filter(CrewAssignment.party_id == world["anna"].id).count() == 2

    _status(pg_client, world, spot, first["id"], "booked", "by phone")
    crew = pg_client.get(f"/projects/{doku.id}/crew", headers=_h(world)).json()
    assert crew["strips"][0]["assignments"][0]["clashes"][0]["status"] == "booked"
    assert crew["strips"][0]["counts"]["with_clashes"] == 1
    # And the first one sees the second, from the other side.
    other_side = pg_client.get(f"/projects/{spot.id}/crew", headers=_h(world)).json()["strips"][0]["assignments"][0]
    assert [(c["project_name"], c["dates"]) for c in other_side["clashes"]] == [("Doku Herbst", ["2026-07-09", "2026-07-10"])]


def test_declined_and_cancelled_assignments_do_not_clash(pg_client, pg_db, world):
    spot, doku = world["spot"], world["doku"]
    anna = _ok_add(pg_client, world, world["shoot"], spot, _person(world["anna"]))["assignments"][0]
    ben = _ok_add(pg_client, world, world["shoot"], spot, _person(world["ben"]))["assignments"][0]
    _status(pg_client, world, spot, anna["id"], "booked", "phone")
    _status(pg_client, world, spot, anna["id"], "cancelled")
    _status(pg_client, world, spot, ben["id"], "declined")
    made = _ok_add(pg_client, world, world["dreh"], doku, _person(world["anna"]), _person(world["ben"]))
    assert made["clashes"] == []


def test_an_unavailability_period_overlapping_the_strip_is_a_clash(pg_client, pg_db, world):
    # Cleo is away 9–11 July; the Shoot has her on 6, 7, 9, 10 July → 9 and 10.
    made = pg_client.post(
        f"/parties/{world['cleo'].id}/unavailability",
        headers=_h(world),
        json={"start_date": "2026-07-09", "end_date": "2026-07-11", "reason": "Vacation"},
    )
    assert made.status_code == 201, made.text
    body = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["cleo"]))
    clash = body["clashes"][0]
    assert (clash["kind"], clash["dates"], clash["reason"]) == ("unavailable", ["2026-07-09", "2026-07-10"], "Vacation")
    assert clash["message"] == (
        "Cleo Lang is not available on 9 July 2026 – 10 July 2026 (Vacation). Check with them before booking, or untick those days."
    )
    # Unticking those two days makes the warning go away.
    patched = pg_client.patch(
        f"/projects/{world['spot'].id}/crew/{body['assignments'][0]['id']}",
        headers=_h(world),
        json={"days": [{"date": "2026-07-06"}, {"date": "2026-07-07"}]},
    )
    assert patched.json()["clashes"] == []


# ─── Availability ───────────────────────────────────────────────────────────


def _availability(pg_client, world, user=None, **params):
    response = pg_client.get("/crew/availability", headers=_h(world, user), params={"from": "2026-07-06", "to": "2026-07-12", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_availability_grid_for_three_people_with_role_and_rate_filters(pg_client, pg_db, world):
    spot, doku = world["spot"], world["doku"]
    a = _ok_add(pg_client, world, world["shoot"], spot, _person(world["anna"], world["dop"]))["assignments"][0]
    _status(pg_client, world, spot, a["id"], "booked", "phone")
    _ok_add(pg_client, world, world["dreh"], doku, _person(world["ben"], dates=[JUL(11), JUL(12)]))
    make_crew_unavailability(pg_db, world["company"], world["cleo"], JUL(6), JUL(7))

    grid = _availability(pg_client, world)
    people = {p["name"]: p for p in grid["people"]}
    # The names were never split, so they sort by the whole name.
    assert list(people) == ["Anna Huber", "Ben Ohr", "Cleo Lang"]
    states = {name: [d["state"] for d in p["days"]] for name, p in people.items()}
    # 6 … 12 July.
    assert states["Anna Huber"] == ["booked", "booked", "free", "booked", "booked", "free", "free"]
    assert states["Ben Ohr"] == ["free", "free", "free", "free", "free", "scheduled", "scheduled"]
    assert states["Cleo Lang"] == ["unavailable", "unavailable", "free", "free", "free", "free", "free"]
    assert [p["free_days"] for p in (people["Anna Huber"], people["Ben Ohr"], people["Cleo Lang"])] == [3, 5, 5]
    entry = people["Anna Huber"]["days"][0]["entries"][0]
    assert (entry["project_name"], entry["strip_name"], entry["status"]) == ("Spot Sommer", "Shoot", "booked")

    # Role DoP: Anna and Cleo. Max rate 620 on the EFFECTIVE rate: Cleo (600
    # typical) but not Anna (650 own).
    dop = _availability(pg_client, world, role_item_id=str(world["dop"].id))
    assert sorted(p["name"] for p in dop["people"]) == ["Anna Huber", "Cleo Lang"]
    cheap = _availability(pg_client, world, role_item_id=str(world["dop"].id), max_rate="620")
    assert [(p["name"], p["rate"]) for p in cheap["people"]] == [("Cleo Lang", "600.00")]
    # Only people on Doku Herbst; only people free all week.
    assert [p["name"] for p in _availability(pg_client, world, project_id=str(doku.id))["people"]] == ["Ben Ohr"]
    assert _availability(pg_client, world, available_only="true")["people"] == []


def test_the_range_is_checked(pg_client, world):
    long = pg_client.get("/crew/availability", headers=_h(world), params={"from": "2026-01-01", "to": "2026-12-31"})
    assert long.status_code == 422 and "at most 92" in long.json()["detail"]
    backwards = pg_client.get("/crew/availability", headers=_h(world), params={"from": "2026-07-10", "to": "2026-07-01"})
    assert backwards.status_code == 422


# ─── Unavailability ─────────────────────────────────────────────────────────


def test_unavailability_add_change_remove_and_audit(pg_client, pg_db, world):
    cleo = world["cleo"]
    made = pg_client.post(
        f"/parties/{cleo.id}/unavailability",
        headers=_h(world),
        json={"start_date": "2026-07-09", "end_date": "2026-07-11", "reason": "Vacation"},
    ).json()
    period = made["periods"][0]
    changed = pg_client.patch(
        f"/parties/{cleo.id}/unavailability/{period['id']}", headers=_h(world), json={"end_date": "2026-07-12", "reason": "Other job"}
    )
    assert changed.json()["periods"][0]["end_date"] == "2026-07-12"
    backwards = pg_client.patch(f"/parties/{cleo.id}/unavailability/{period['id']}", headers=_h(world), json={"end_date": "2026-07-01"})
    assert backwards.status_code == 422
    removed = pg_client.post(f"/parties/{cleo.id}/unavailability/{period['id']}/remove", headers=_h(world))
    assert removed.json()["periods"] == []
    pg_db.expire_all()
    assert pg_db.get(CrewUnavailability, period["id"]).is_active is False
    actions = [e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == period["id"]).order_by(AuditEvent.created_at, AuditEvent.id)]
    assert actions == ["crew_unavailability.created", "crew_unavailability.updated", "crew_unavailability.removed"]


# ─── Who sees what ──────────────────────────────────────────────────────────


@pytest.fixture
def producer_without_cost(monkeypatch):
    monkeypatch.setitem(
        permissions.PERMISSIONS,
        "budgets.view",
        permissions.PERMISSIONS["budgets.view"] - {CompanyRole.producer},
    )


def _user(pg_db, world, role):
    user = make_user(pg_db, f"assign-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


def test_without_budgets_view_no_assignment_response_carries_a_rate(pg_client, pg_db, world, producer_without_cost):
    producer = _user(pg_db, world, CompanyRole.producer)
    made = _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"], world["dop"]), user=producer)
    assert made.status_code == 201
    row = made.json()["assignments"][0]
    assert (row["rates_visible"], row["day_rate"]) == (False, None)
    # The rate was still copied: it is there for somebody who may see it.
    assert pg_db.get(CrewAssignment, row["id"]).day_rate == Decimal("650.00")
    crew = pg_client.get(f"/projects/{world['spot'].id}/crew", headers=_h(world, producer)).json()
    assert crew["rates_visible"] is False and crew["strips"][0]["assignments"][0]["day_rate"] is None
    assert "650" not in pg_client.get(f"/projects/{world['spot'].id}/crew", headers=_h(world, producer)).text
    grid = _availability(pg_client, world, user=producer)
    assert grid["rates_visible"] is False and all(p["rate"] is None for p in grid["people"])
    assert "650" not in pg_client.get("/crew/availability", headers=_h(world, producer), params={"from": "2026-07-06", "to": "2026-07-12"}).text
    # Writing a rate, or filtering by one: a 404.
    assert pg_client.patch(f"/projects/{world['spot'].id}/crew/{row['id']}", headers=_h(world, producer), json={"day_rate": "1.00"}).status_code == 404
    assert pg_client.get("/crew/availability", headers=_h(world, producer), params={"from": "2026-07-06", "to": "2026-07-12", "max_rate": "700"}).status_code == 404
    # The owner, in the same world, sees it (control).
    assert pg_client.get(f"/projects/{world['spot'].id}/crew", headers=_h(world)).json()["strips"][0]["assignments"][0]["day_rate"] == "650.00"


def test_staff_see_no_crew_on_the_calendar_and_no_rates(pg_client, pg_db, world):
    """D111 is open: until it is decided, staff (projects.view, no
    parties.view) get a 404 from every crew endpoint — no names, no rates —
    while the calendar itself stays open to them."""
    staff = _user(pg_db, world, CompanyRole.staff)
    a = _ok_add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]))["assignments"][0]
    spot = world["spot"]
    for method, path, body in [
        ("GET", f"/projects/{spot.id}/crew", None),
        ("POST", f"/projects/{spot.id}/blocks/{world['shoot'].id}/crew", {"people": [_person(world["ben"])]}),
        ("PATCH", f"/projects/{spot.id}/crew/{a['id']}", {"days": []}),
        ("POST", f"/projects/{spot.id}/crew/{a['id']}/status", {"status": "booked"}),
        ("POST", f"/projects/{spot.id}/crew/status", {"assignment_ids": [a["id"]], "status": "booked"}),
        ("POST", f"/projects/{spot.id}/crew/{a['id']}/remove", None),
        ("GET", "/crew/availability?from=2026-07-06&to=2026-07-12", None),
        ("GET", f"/parties/{world['anna'].id}/unavailability", None),
        ("POST", f"/parties/{world['anna'].id}/unavailability", {"start_date": "2026-07-01", "end_date": "2026-07-02"}),
    ]:
        response = pg_client.request(method, path, headers=_h(world, staff), json=body)
        assert response.status_code == 404, (method, path, response.status_code)
        assert "Anna" not in response.text
    # The calendar itself stays readable for staff.
    assert pg_client.get(f"/projects/{spot.id}/calendar", headers=_h(world, staff)).status_code == 200
    pg_db.expire_all()
    assert pg_db.get(CrewAssignment, a["id"]).status == "scheduled"


@pytest.mark.parametrize(
    "role, can_view, can_change, can_unavailability",
    [
        (CompanyRole.owner, True, True, True),
        (CompanyRole.admin, True, True, True),
        (CompanyRole.producer, True, True, True),
        (CompanyRole.accountant, True, False, False),
        (CompanyRole.staff, False, False, False),
        (CompanyRole.tax_advisor, False, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_assignment_permission_matrix(pg_client, pg_db, world, role, can_view, can_change, can_unavailability):
    user = _user(pg_db, world, role)
    read = pg_client.get(f"/projects/{world['spot'].id}/crew", headers=_h(world, user))
    assert read.status_code == (200 if can_view else 404)
    made = _add(pg_client, world, world["shoot"], world["spot"], _person(world["anna"]), user=user)
    assert made.status_code == (201 if can_change else 404), made.text
    period = pg_client.post(
        f"/parties/{world['ben'].id}/unavailability", headers=_h(world, user), json={"start_date": "2026-07-01", "end_date": "2026-07-02"}
    )
    assert period.status_code == (201 if can_unavailability else 404)


def test_another_companys_strip_person_and_assignment_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    their_project = make_project(pg_db, other, name="Theirs", project_number="X-1")
    their_block = make_project_block(pg_db, other, their_project, start_date=JUL(6), end_date=JUL(8))
    pg_db.commit()
    theirs = make_party(pg_db, other, type="person", name="Their gaffer", is_crew=True)
    their_assignment = make_crew_assignment(pg_db, other, their_project, their_block, theirs, dates=[JUL(7)])
    their_period = make_crew_unavailability(pg_db, other, theirs, JUL(6), JUL(7))
    spot, shoot = world["spot"], world["shoot"]
    # Their person on our strip; our person on their strip.
    assert _add(pg_client, world, shoot, spot, _person(theirs)).status_code == 404
    assert _add(pg_client, world, their_block, their_project, _person(world["anna"])).status_code == 404
    # Their assignment through our project.
    assert pg_client.post(f"/projects/{spot.id}/crew/{their_assignment.id}/status", headers=_h(world), json={"status": "booked"}).status_code == 404
    assert pg_client.post(f"/projects/{spot.id}/crew/status", headers=_h(world), json={"assignment_ids": [str(their_assignment.id)], "status": "booked"}).status_code == 404
    assert pg_client.post(f"/parties/{theirs.id}/unavailability/{their_period.id}/remove", headers=_h(world)).status_code == 404
    pg_db.expire_all()
    assert pg_db.get(CrewAssignment, their_assignment.id).status == "scheduled"
    assert pg_db.get(CrewUnavailability, their_period.id).is_active is True
    # Their crew is not in our grid, and their booking is not our clash.
    _ok_add(pg_client, world, shoot, spot, _person(world["anna"]))
    grid = _availability(pg_client, world)
    assert [p["name"] for p in grid["people"]] == ["Anna Huber", "Ben Ohr", "Cleo Lang"]
    assert grid["people"][0]["days"][1]["entries"] == [
        {"kind": "assignment", "status": "scheduled", "assignment_id": grid["people"][0]["days"][1]["entries"][0]["assignment_id"],
         "project_id": str(spot.id), "project_name": "Spot Sommer", "block_id": str(shoot.id), "strip_name": "Shoot",
         "unavailability_id": None, "reason": None}
    ]


def test_there_is_no_delete_route_for_crew_on_the_calendar(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    paths = [p for p in schema["paths"] if "/crew" in p or "/unavailability" in p]
    assert any("/unavailability" in p for p in paths) and any(p.endswith("/crew/status") for p in paths)
    assert not [p for p in paths if "delete" in schema["paths"][p]]
