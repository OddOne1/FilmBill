"""Gear on the calendar: bookings on strips, dependencies, overbooking,
status, removal, the project summary (B2c-2 part 2). Real PostgreSQL.

The world: project "1000m" has a strip "Shoot" from Mon 6 to Fri 10 July
2026, 8 July is `off`; project "Doku" has a strip "Dreh" from 9 to 12 July.

Gear: **Camera A** (3 owned, 2.500 kg) with the alternative **Camera B**
(1 owned, "needs a different mount", "about +150 per day"); **Battery**
(6 owned, 0.400 kg); **Charger** (4 owned); **Filter** (5 owned);
**Light** (2 owned, no weight). Dependencies: Camera A needs 2 Battery
(always) and 1 Filter (ask); Battery needs 1 Charger (always) — the second
level, which booking never follows.
"""

from datetime import date
from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.gear import GearBooking, GearBookingDay
from apps.api.models.project import ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import permissions

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_device,
    make_device_alternative,
    make_device_association,
    make_gear_booking,
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
    owner = make_user(pg_db, "gear-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    film = make_project(pg_db, company, name="1000m", project_number="PRJ-1000", start_date=JUL(1))
    doku = make_project(pg_db, company, name="Doku", project_number="PRJ-2", start_date=JUL(1))
    shoot = make_project_block(pg_db, company, film, start_date=JUL(6), end_date=JUL(10), label="Shoot")
    days = {d.date: d for d in pg_db.query(ProjectDay).filter(ProjectDay.block_id == shoot.id)}
    days[JUL(8)].type = "off"
    dreh = make_project_block(pg_db, company, doku, start_date=JUL(9), end_date=JUL(12), label="Dreh")
    pg_db.commit()
    camera = make_device(pg_db, company, name="Camera A", quantity_owned=3)
    camera.weight_kg = Decimal("2.500")
    camera_b = make_device(pg_db, company, name="Camera B", quantity_owned=1)
    battery = make_device(pg_db, company, name="Battery", quantity_owned=6, category="Power")
    battery.weight_kg = Decimal("0.400")
    charger = make_device(pg_db, company, name="Charger", quantity_owned=4, category="Power")
    filt = make_device(pg_db, company, name="Filter", quantity_owned=5, category="Optics")
    light = make_device(pg_db, company, name="Light", quantity_owned=2, category="Light")
    pg_db.commit()
    alt = make_device_alternative(pg_db, company, camera, camera_b)
    alt.note, alt.price_hint = "needs a different mount", "about +150 per day"
    pg_db.commit()
    batteries = make_device_association(pg_db, company, camera, battery, quantity_per_parent=2)
    filters = make_device_association(pg_db, company, camera, filt, quantity_per_parent=1, booking_mode="ask")
    chargers = make_device_association(pg_db, company, battery, charger, quantity_per_parent=1)
    return {
        "company": company, "owner": owner, "film": film, "doku": doku, "shoot": shoot, "dreh": dreh,
        "camera": camera, "camera_b": camera_b, "battery": battery, "charger": charger, "filter": filt,
        "light": light, "batteries": batteries, "filters": filters, "chargers": chargers,
    }


def _h(world, user=None):
    return auth_headers(user or world["owner"], world["company"])


def _entry(device, quantity=1, dates=None, chosen=None, note=None):
    entry = {"device_id": str(device.id), "quantity": quantity}
    if dates is not None:
        entry["dates"] = [ISO(d) for d in dates]
    if chosen is not None:
        entry["chosen_dependencies"] = chosen
    if note is not None:
        entry["overbooking_note"] = note
    return entry


def _add(pg_client, world, *entries, block=None, project=None, user=None):
    block = block or world["shoot"]
    project = project or world["film"]
    return pg_client.post(
        f"/projects/{project.id}/blocks/{block.id}/gear", headers=_h(world, user), json={"bookings": list(entries)}
    )


def _ok(pg_client, world, *entries, **kwargs):
    made = _add(pg_client, world, *entries, **kwargs)
    assert made.status_code == 201, made.text
    return made.json()


def _by_device(body):
    return {b["device_name"]: b for b in body["bookings"]}


def _rows(pg_db, world, device):
    pg_db.expire_all()
    return pg_db.query(GearBooking).filter(GearBooking.device_id == world[device].id).all()


def _dates(pg_db, booking_id):
    pg_db.expire_all()
    return sorted(
        d.date
        for d in pg_db.query(GearBookingDay).filter(
            GearBookingDay.booking_id == booking_id, GearBookingDay.is_active.is_(True)
        )
    )


def _patch(pg_client, world, booking_id, body, project=None):
    project = project or world["film"]
    return pg_client.patch(f"/projects/{project.id}/gear/{booking_id}", headers=_h(world), json=body)


def _remove(pg_client, world, booking_id, body=None, project=None):
    project = project or world["film"]
    return pg_client.post(f"/projects/{project.id}/gear/{booking_id}/remove", headers=_h(world), json=body)


def _availability(pg_client, world, device, start=6, end=10):
    response = pg_client.get(
        f"/devices/{world[device].id}/availability", headers=_h(world), params={"from": ISO(start), "to": ISO(end)}
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── Creating: days and the dependency step ─────────────────────────────────


def test_a_new_booking_gets_every_strip_day_but_the_off_day_and_its_always_dependencies(pg_client, pg_db, world):
    body = _ok(pg_client, world, _entry(world["camera"], 2))
    rows = _by_device(body)
    camera, battery = rows["Camera A"], rows["Battery"]
    assert set(rows) == {"Camera A", "Battery"}
    # 6–10 July without the `off` day 8 July.
    assert camera["dates"] == [ISO(6), ISO(7), ISO(9), ISO(10)]
    assert _dates(pg_db, camera["id"]) == [JUL(6), JUL(7), JUL(9), JUL(10)]
    assert (camera["quantity"], camera["status"], camera["added_via_booking_id"]) == (2, "scheduled", None)
    # 2 cameras × 2 batteries per camera = 4, same days and status.
    assert (battery["quantity"], battery["status"], battery["dates"]) == (4, "scheduled", camera["dates"])
    assert (battery["added_via_booking_id"], battery["follows_parent"], battery["quantity_per_parent"]) == (camera["id"], True, 2)
    assert battery["added_via_device_name"] == "Camera A"
    # The `ask` Filter is a suggestion with its pre-calculated quantity, not a row.
    assert [(s["device_name"], s["quantity"], s["parent_device_name"]) for s in body["suggestions"]] == [("Filter", 2, "Camera A")]
    assert _rows(pg_db, world, "filter") == []
    assert body["warnings"] == []
    events = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"]).all()
    assert [e.action for e in events] == ["gear_booking.created", "gear_booking.created"]
    # One event for the camera, ONE listing the dependencies added with it.
    (listing,) = [e.changes for e in events if "added_via_booking_id" in e.changes]
    assert listing["count"][1] == 1
    assert [row["device_id"] for row in listing["bookings"][1]] == [str(world["battery"].id)]


def test_three_cameras_bring_six_batteries(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 3)))
    assert (rows["Camera A"]["quantity"], rows["Battery"]["quantity"]) == (3, 6)


def test_unticked_days_are_kept_and_given_to_the_dependencies(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 1, dates=[6, 7])))
    assert _dates(pg_db, rows["Camera A"]["id"]) == [JUL(6), JUL(7)]
    assert _dates(pg_db, rows["Battery"]["id"]) == [JUL(6), JUL(7)]


def test_a_day_outside_the_strip_is_refused(pg_client, pg_db, world):
    response = _add(pg_client, world, _entry(world["camera"], 1, dates=[6, 11]))
    assert response.status_code == 422
    assert "outside the strip “Shoot”" in response.json()["detail"]
    assert _rows(pg_db, world, "camera") == []


def test_an_ask_dependency_is_added_only_when_chosen(pg_client, pg_db, world):
    # Not chosen: suggested, never stored (also covered above).
    _ok(pg_client, world, _entry(world["light"]))
    assert _rows(pg_db, world, "filter") == []
    chosen = _ok(pg_client, world, _entry(world["camera"], 2, chosen=[{"association_id": str(world["filters"].id)}]))
    rows = _by_device(chosen)
    assert (rows["Filter"]["quantity"], rows["Filter"]["follows_parent"]) == (2, True)
    assert chosen["suggestions"] == []


def test_an_ask_dependency_with_its_own_quantity_does_not_follow(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 2, chosen=[{"association_id": str(world["filters"].id), "quantity": 1}])))
    assert (rows["Filter"]["quantity"], rows["Filter"]["follows_parent"]) == (1, False)


def test_an_always_link_cannot_be_sent_as_chosen(pg_client, pg_db, world):
    response = _add(pg_client, world, _entry(world["camera"], 1, chosen=[{"association_id": str(world["batteries"].id)}]))
    assert response.status_code == 422
    assert _rows(pg_db, world, "camera") == []


def test_booking_follows_one_level_only_even_through_a_chain_or_a_loop(pg_client, pg_db, world):
    """Camera A → Battery → Charger is three levels: booking the camera adds
    the batteries and never the chargers. A loop written past the setup
    guard (Battery → Camera A) does not bring a second camera either."""
    make_device_association(pg_db, world["company"], world["battery"], world["camera"], quantity_per_parent=1)
    body = _ok(pg_client, world, _entry(world["camera"], 2))
    assert sorted(b["device_name"] for b in body["bookings"]) == ["Battery", "Camera A"]
    assert _rows(pg_db, world, "charger") == []
    assert len(_rows(pg_db, world, "camera")) == 1
    # Booking the battery directly follows ITS one level: chargers (and
    # the looped camera), and nothing below them.
    direct = _by_device(_ok(pg_client, world, _entry(world["battery"], 1), block=world["dreh"], project=world["doku"]))
    assert set(direct) == {"Battery", "Charger", "Camera A"}
    assert direct["Camera A"]["added_via_booking_id"] == direct["Battery"]["id"]
    # The looped camera's own batteries are not added through it.
    assert len([r for r in _rows(pg_db, world, "battery") if r.block_id == world["dreh"].id]) == 1


def test_the_same_device_twice_on_a_strip_is_a_conflict(pg_client, pg_db, world):
    _ok(pg_client, world, _entry(world["light"]))
    again = _add(pg_client, world, _entry(world["light"]))
    assert again.status_code == 409
    assert "already on the strip “Shoot”" in again.json()["detail"]
    # A battery added directly AND through a camera is two routes: allowed.
    _ok(pg_client, world, _entry(world["battery"], 1))
    _ok(pg_client, world, _entry(world["camera"], 1))
    assert len([r for r in _rows(pg_db, world, "battery") if r.status != "cancelled"]) == 2


def test_the_preview_shows_what_would_be_added_and_stores_nothing(pg_client, pg_db, world):
    make_gear_booking(pg_db, world["company"], world["doku"], world["dreh"], world["camera"], quantity=1, status="booked", dates=[JUL(9)])
    response = pg_client.post(
        f"/projects/{world['film'].id}/blocks/{world['shoot'].id}/gear/preview",
        headers=_h(world),
        json={"bookings": [_entry(world["camera"], 2)]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    planned = {(p["device_name"], p["via"]): p for p in body["bookings"]}
    camera = planned[("Camera A", "direct")]
    # 3 owned, Doku holds 1 on 9 July: 2 free before, 0 after.
    assert (camera["owned"], camera["min_available"], camera["available_after"]) == (3, 2, 0)
    assert planned[("Battery", "always")]["quantity"] == 4
    assert [(s["device_name"], s["quantity"]) for s in body["suggestions"]] == [("Filter", 2)]
    assert len(_rows(pg_db, world, "camera")) == 1


# ─── Changing: quantity, days, followers, swap ──────────────────────────────


def test_a_new_parent_quantity_recalculates_only_the_dependents_that_follow(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 2, chosen=[{"association_id": str(world["filters"].id), "quantity": 1}])))
    camera, battery, filt = rows["Camera A"], rows["Battery"], rows["Filter"]
    changed = _patch(pg_client, world, camera["id"], {"quantity": 3})
    assert changed.status_code == 200, changed.text
    assert [(r["device_name"], r["quantity"]) for r in changed.json()["recalculated"]] == [("Battery", 6)]
    pg_db.expire_all()
    assert (pg_db.get(GearBooking, battery["id"]).quantity, pg_db.get(GearBooking, filt["id"]).quantity) == (6, 1)
    # The battery set by hand stops following, and the answer says so.
    by_hand = _patch(pg_client, world, battery["id"], {"quantity": 5}).json()
    assert by_hand["stopped_following"] is True
    assert by_hand["message"] == "Battery no longer follows Camera A: its quantity and days stay as you set them."
    _patch(pg_client, world, camera["id"], {"quantity": 2})
    pg_db.expire_all()
    assert (pg_db.get(GearBooking, battery["id"]).quantity, pg_db.get(GearBooking, battery["id"]).follows_parent) == (5, False)
    recalculated = [e for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"], AuditEvent.action == "gear_booking.updated")]
    assert any(e.changes.get("recalculated") for e in recalculated)


def test_days_are_set_and_audited(pg_client, pg_db, world):
    camera = _by_device(_ok(pg_client, world, _entry(world["camera"])))["Camera A"]
    response = _patch(pg_client, world, camera["id"], {"dates": [ISO(9), ISO(10)]})
    assert response.status_code == 200, response.text
    assert _dates(pg_db, camera["id"]) == [JUL(9), JUL(10)]
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"], AuditEvent.action == "gear_booking.days_set").one()
    assert event.changes["days"][1] == [ISO(9), ISO(10)]
    assert _patch(pg_client, world, camera["id"], {"dates": [ISO(12)]}).status_code == 422


# ─── Overbooking: warn, never block ─────────────────────────────────────────


def test_overbooking_warns_names_who_holds_it_offers_the_alternatives_and_saves(pg_client, pg_db, world):
    """Doku holds 2 Camera A (booked) on 9–12 July. Adding 2 more to "Shoot"
    (6, 7, 9, 10 July): on 9 and 10 July only 3 − 2 = 1 is free for 2
    wanted, a shortage of 1. The save succeeds."""
    make_gear_booking(pg_db, world["company"], world["doku"], world["dreh"], world["camera"], quantity=2, status="booked", dates=[JUL(9), JUL(10), JUL(11), JUL(12)])
    made = _add(pg_client, world, _entry(world["camera"], 2, note="renting in"))
    assert made.status_code == 201, made.text
    body = made.json()
    camera = _by_device(body)["Camera A"]
    warning = camera["warning"]
    assert (warning["owned"], warning["quantity"], warning["shortage"], warning["worst_day"]) == (3, 2, 1, ISO(9))
    assert warning["dates"] == [ISO(9), ISO(10)]
    assert [(h["project_name"], h["strip_name"], h["status"], h["quantity"], h["dates"]) for h in warning["held_by"]] == [
        ("Doku", "Dreh", "booked", 2, [ISO(9), ISO(10)])
    ]
    assert warning["message"] == (
        "Camera A is overbooked by 1 on 9 July 2026 – 10 July 2026: 3 owned, 2 wanted here. "
        "Doku (strip “Dreh”, booked 2) holds it there. Swap to an alternative, or book anyway with a note."
    )
    # Camera B, the alternative: 1 owned, nothing booked, not enough for 2.
    assert [(a["name"], a["owned"], a["min_available"], a["enough"], a["note"], a["price_hint"]) for a in warning["alternatives"]] == [
        ("Camera B", 1, 1, False, "needs a different mount", "about +150 per day")
    ]
    assert [w["booking_id"] for w in body["warnings"]] == [camera["id"]]
    stored = pg_db.get(GearBooking, camera["id"])
    assert (stored.status, stored.quantity, stored.overbooking_note) == ("scheduled", 2, "renting in")
    # The batteries are fine (6 owned, 4 wanted): no warning on them.
    assert _by_device(body)["Battery"]["warning"] is None


def test_swapping_to_an_alternative_keeps_days_and_status_and_is_audited(pg_client, pg_db, world):
    make_gear_booking(pg_db, world["company"], world["doku"], world["dreh"], world["camera"], quantity=3, status="booked", dates=[JUL(9)])
    camera = _by_device(_ok(pg_client, world, _entry(world["camera"], 1)))["Camera A"]
    assert camera["warning"]["shortage"] == 1
    swapped = _patch(pg_client, world, camera["id"], {"device_id": str(world["camera_b"].id)})
    assert swapped.status_code == 200, swapped.text
    booking = swapped.json()["booking"]
    assert (booking["device_name"], booking["quantity"], booking["status"], booking["dates"]) == ("Camera B", 1, "scheduled", camera["dates"])
    assert booking["warning"] is None
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"], AuditEvent.action == "gear_booking.updated").one()
    assert event.changes["device_id"] == [str(world["camera"].id), str(world["camera_b"].id)]


def test_book_anyway_records_the_note(pg_client, pg_db, world):
    make_gear_booking(pg_db, world["company"], world["doku"], world["dreh"], world["light"], quantity=2, status="booked", dates=[JUL(9)])
    light = _by_device(_ok(pg_client, world, _entry(world["light"])))["Light"]
    assert light["warning"]["shortage"] == 1
    response = _patch(pg_client, world, light["id"], {"overbooking_note": "Renting one in from Licht GmbH"})
    assert response.status_code == 200
    assert response.json()["booking"]["overbooking_note"] == "Renting one in from Licht GmbH"
    assert response.json()["warnings"][0]["shortage"] == 1


# ─── Status ─────────────────────────────────────────────────────────────────


def _status(pg_client, world, booking_id, status, note=None):
    return pg_client.post(f"/projects/{world['film'].id}/gear/{booking_id}/status", headers=_h(world), json={"status": status, "note": note})


def test_the_status_path(pg_client, pg_db, world):
    light = _by_device(_ok(pg_client, world, _entry(world["light"])))["Light"]
    booked = _status(pg_client, world, light["id"], "booked", "by phone")
    assert booked.status_code == 200, booked.text
    assert (booked.json()["status"], booked.json()["status_note"], booked.json()["booked_by_name"] is not None) == ("booked", "by phone", True)
    assert _status(pg_client, world, light["id"], "booked").status_code == 422
    no_note = _status(pg_client, world, light["id"], "scheduled")
    assert no_note.status_code == 422 and "needs a note" in no_note.json()["detail"]
    reopened = _status(pg_client, world, light["id"], "scheduled", "date moved")
    assert (reopened.status_code, reopened.json()["status"], reopened.json()["booked_on"]) == (200, "scheduled", None)
    assert _status(pg_client, world, light["id"], "cancelled").status_code == 422
    actions = [e.action for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == light["id"]).order_by(AuditEvent.created_at, AuditEvent.id)]
    assert actions == ["gear_booking.created", "gear_booking.status_changed", "gear_booking.status_changed"]


def test_mark_selected_as_booked_books_the_scheduled_and_lists_the_others(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 1), _entry(world["light"])))
    _status(pg_client, world, rows["Light"]["id"], "booked", "by mail")
    ids = [rows["Camera A"]["id"], rows["Battery"]["id"], rows["Light"]["id"]]
    response = pg_client.post(f"/projects/{world['film'].id}/gear/status", headers=_h(world), json={"booking_ids": ids, "status": "booked", "note": "by phone"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] == 2
    assert [(s["device_name"], s["reason"]) for s in body["skipped"]] == [("Light", "Light is already booked on this strip.")]
    event = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == world["film"].id, AuditEvent.action == "gear_booking.status_changed").one()
    assert (event.changes["changed"][1], event.changes["skipped"][1]) == (2, 1)


# ─── Removing ───────────────────────────────────────────────────────────────


def test_cancelling_a_booking_gives_the_availability_back(pg_client, pg_db, world):
    light = _by_device(_ok(pg_client, world, _entry(world["light"], 2)))["Light"]
    assert _availability(pg_client, world, "light")["min_available"] == 0
    removed = _remove(pg_client, world, light["id"], {"note": "not needed"})
    assert removed.status_code == 200, removed.text
    assert (removed.json()["removed"], removed.json()["booking"]["status"], removed.json()["booking"]["removal_note"]) == (True, "cancelled", "not needed")
    assert _availability(pg_client, world, "light")["min_available"] == 2
    # Never deleted: the row and its days are still there.
    assert pg_db.get(GearBooking, light["id"]) is not None and _dates(pg_db, light["id"]) == [JUL(6), JUL(7), JUL(9), JUL(10)]


def test_removing_a_parent_first_asks_about_its_dependents(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 2)))
    camera, battery = rows["Camera A"], rows["Battery"]
    asked = _remove(pg_client, world, camera["id"], {"note": "shoot cut"})
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["removed"] is False
    assert [(d["device_name"], d["quantity"]) for d in body["dependents"]] == [("Battery", 4)]
    assert body["question"] == "Camera A was booked with 4 × Battery. Cancel them too, or keep them on the strip as bookings of their own?"
    pg_db.expire_all()
    assert (pg_db.get(GearBooking, camera["id"]).status, pg_db.get(GearBooking, battery["id"]).status) == ("scheduled", "scheduled")
    assert _availability(pg_client, world, "battery")["min_available"] == 2
    cancelled = _remove(pg_client, world, camera["id"], {"note": "shoot cut", "dependents": "cancel"}).json()
    assert (cancelled["removed"], cancelled["dependents_choice"]) == (True, "cancel")
    pg_db.expire_all()
    assert (pg_db.get(GearBooking, camera["id"]).status, pg_db.get(GearBooking, battery["id"]).status) == ("cancelled", "cancelled")
    assert _availability(pg_client, world, "battery")["min_available"] == 6
    events = {e.action: e for e in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"], AuditEvent.action == "gear_booking.removed")}
    assert len(pg_db.query(AuditEvent).filter(AuditEvent.entity_id == camera["id"], AuditEvent.action == "gear_booking.removed").all()) == 2
    assert events


def test_keeping_the_dependents_leaves_them_booked_on_their_own(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 2)))
    kept = _remove(pg_client, world, rows["Camera A"]["id"], {"dependents": "keep"}).json()
    assert (kept["removed"], kept["dependents_choice"]) == (True, "keep")
    pg_db.expire_all()
    battery = pg_db.get(GearBooking, rows["Battery"]["id"])
    assert (battery.status, battery.follows_parent) == ("scheduled", False)
    removed = pg_db.query(AuditEvent).filter(AuditEvent.entity_id == rows["Camera A"]["id"], AuditEvent.action == "gear_booking.removed").one()
    assert removed.changes["dependents"][1] == "keep"


def test_a_dependent_can_be_removed_on_its_own_with_a_note(pg_client, pg_db, world):
    rows = _by_device(_ok(pg_client, world, _entry(world["camera"], 1)))
    response = _remove(pg_client, world, rows["Battery"]["id"], {"note": "own batteries"})
    assert response.json()["removed"] is True
    pg_db.expire_all()
    assert pg_db.get(GearBooking, rows["Battery"]["id"]).removal_note == "own batteries"
    assert pg_db.get(GearBooking, rows["Camera A"]["id"]).status == "scheduled"


# ─── Reading: the project summary, the device's bookings, the planner ──────


def test_the_project_gear_summary_counts_pieces_and_totals_the_weight(pg_client, pg_db, world):
    _ok(pg_client, world, _entry(world["camera"], 2), _entry(world["light"], 2))
    response = pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world))
    assert response.status_code == 200, response.text
    body = response.json()
    strip = body["strips"][0]
    assert strip["strip_name"] == "Shoot"
    # 2 cameras + 4 batteries + 2 lights, all scheduled.
    assert strip["counts"] == {"scheduled": 8, "booked": 0, "overbooked": 0}
    # 2 × 2.500 + 4 × 0.400 = 6.600 kg; the 2 lights have no weight.
    assert (strip["total_weight_kg"], strip["items_without_weight"]) == ("6.600", 2)
    assert (body["total_weight_kg"], body["items_without_weight"]) == ("6.600", 2)
    assert "price" not in response.text and "65000" not in response.text


def test_weight_with_every_device_weighed(pg_client, pg_db, world):
    world["charger"].weight_kg = Decimal("0.150")
    pg_db.commit()
    # 3 batteries bring 3 chargers: 3 × 0.400 + 3 × 0.150 = 1.650 kg.
    _ok(pg_client, world, _entry(world["battery"], 3))
    strip = pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world)).json()["strips"][0]
    assert (strip["total_weight_kg"], strip["items_without_weight"]) == ("1.650", 0)


def test_the_device_bookings_are_in_date_order(pg_client, pg_db, world):
    make_gear_booking(pg_db, world["company"], world["doku"], world["dreh"], world["light"], quantity=1, dates=[JUL(11)])
    _ok(pg_client, world, _entry(world["light"]))
    response = pg_client.get(f"/devices/{world['light'].id}/bookings", headers=_h(world))
    assert response.status_code == 200
    assert [(b["project_name"], b["strip_name"], b["dates"][0]) for b in response.json()["bookings"]] == [
        ("1000m", "Shoot", ISO(6)),
        ("Doku", "Dreh", ISO(11)),
    ]


def test_the_planner_lists_devices_by_category_and_search(pg_client, pg_db, world):
    _ok(pg_client, world, _entry(world["battery"], 2))
    params = {"from": ISO(6), "to": ISO(8), "category": "power"}
    body = pg_client.get("/gear/availability", headers=_h(world), params=params).json()
    assert [d["name"] for d in body["devices"]] == ["Battery", "Charger"]
    battery = body["devices"][0]
    # 6 owned, 2 scheduled on 6 and 7 July; 8 July is off for the booking.
    assert [(d["date"], d["scheduled"], d["available"]) for d in battery["days"]] == [(ISO(6), 2, 4), (ISO(7), 2, 4), (ISO(8), 0, 6)]
    assert battery["days"][0]["holders"][0]["project_name"] == "1000m"
    assert [d["name"] for d in pg_client.get("/gear/availability", headers=_h(world), params={"from": ISO(6), "to": ISO(8), "q": "cam"}).json()["devices"]] == ["Camera A", "Camera B"]
    too_long = pg_client.get("/gear/availability", headers=_h(world), params={"from": "2026-01-01", "to": "2026-12-31"})
    assert too_long.status_code == 422


# ─── Who may ────────────────────────────────────────────────────────────────


def _user(pg_db, world, role):
    user = make_user(pg_db, f"gear-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    return user


@pytest.mark.parametrize(
    "role, can_view, can_change",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, True, False),
        (CompanyRole.tax_advisor, False, False),
    ],
)
def test_the_gear_booking_permission_matrix(pg_client, pg_db, world, role, can_view, can_change):
    user = _user(pg_db, world, role)
    seen = pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world, user))
    assert seen.status_code == (200 if can_view else 404)
    planner = pg_client.get("/gear/availability", headers=_h(world, user), params={"from": ISO(6), "to": ISO(8)})
    assert planner.status_code == (200 if can_view else 404)
    made = _add(pg_client, world, _entry(world["light"]), user=user)
    assert made.status_code == (201 if can_change else 404), made.text


def test_staff_see_bookings_and_no_price(pg_client, pg_db, world):
    world["camera"].purchase_price = Decimal("65000.00")
    pg_db.commit()
    _ok(pg_client, world, _entry(world["camera"], 1))
    staff = _user(pg_db, world, CompanyRole.staff)
    body = pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world, staff))
    assert body.status_code == 200
    assert [b["device_name"] for b in body.json()["strips"][0]["bookings"]] == ["Camera A", "Battery"]
    assert "65000" not in body.text and "price" not in body.text


def test_without_devices_view_the_bookings_are_not_found(pg_client, pg_db, world, monkeypatch):
    monkeypatch.setitem(
        permissions.PERMISSIONS, "devices.view", permissions.PERMISSIONS["devices.view"] - {CompanyRole.producer}
    )
    producer = _user(pg_db, world, CompanyRole.producer)
    assert pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world, producer)).status_code == 404
    assert _add(pg_client, world, _entry(world["light"]), user=producer).status_code == 404
    # Control: the owner in the same world.
    assert pg_client.get(f"/projects/{world['film'].id}/gear", headers=_h(world)).status_code == 200


def test_another_companys_device_strip_and_booking_are_not_found(pg_client, pg_db, world):
    other = make_company(pg_db, "Other Films GmbH")
    their_project = make_project(pg_db, other, name="Theirs", project_number="X-1")
    their_block = make_project_block(pg_db, other, their_project, start_date=JUL(6), end_date=JUL(8))
    their_device = make_device(pg_db, other, name="Their camera")
    their_booking = make_gear_booking(pg_db, other, their_project, their_block, their_device, dates=[JUL(7)])
    film = world["film"]
    assert _add(pg_client, world, _entry(their_device)).status_code == 404
    assert _add(pg_client, world, _entry(world["light"]), block=their_block, project=their_project).status_code == 404
    assert _patch(pg_client, world, their_booking.id, {"quantity": 2}).status_code == 404
    own = _by_device(_ok(pg_client, world, _entry(world["light"])))["Light"]
    assert _patch(pg_client, world, own["id"], {"device_id": str(their_device.id)}).status_code == 404
    assert pg_client.post(f"/projects/{film.id}/gear/status", headers=_h(world), json={"booking_ids": [str(their_booking.id)], "status": "booked"}).status_code == 404
    assert pg_client.get(f"/devices/{their_device.id}/availability", headers=_h(world), params={"from": ISO(6), "to": ISO(8)}).status_code == 404
    # Their booking does not count against our light either.
    assert _availability(pg_client, world, "light")["days"][0]["holders"][0]["project_name"] == "1000m"


def test_there_is_no_delete_route_for_gear_bookings(pg_client, world):
    from apps.api.main import app

    deletes = [
        route.path for route in app.routes
        if "DELETE" in getattr(route, "methods", set()) and "gear" in route.path
    ]
    assert deletes == []
    light = _by_device(_ok(pg_client, world, _entry(world["light"])))["Light"]
    response = pg_client.delete(f"/projects/{world['film'].id}/gear/{light['id']}", headers=_h(world))
    assert response.status_code == 405
