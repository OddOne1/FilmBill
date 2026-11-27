"""The database layer gives the pure rule's numbers for the same data (B2c-2
part 1). Real PostgreSQL.

The three-device example, every number written out:

* **Camera A**, 3 owned. Project 1000m, strip "Shoot" (6–8 July): 2 booked.
  Project "Doku", strip "Dreh" (7–9 July): 2 scheduled.
  → 6 July: 2 booked, 1 available · 7 and 8 July: 2 booked + 2 scheduled,
  −1 (short by 1) · 9 July: 2 scheduled, 1 available. Lowest day: 7 July.
* **Battery**, 6 owned. "Shoot": 4 scheduled; "Dreh": 2 booked but
  CANCELLED. → 6–8 July: 4 scheduled, 2 available; 9 July: 6. Lowest: 6 July.
* **Light**, 2 owned. "Shoot": 2 booked on 6 and 7 July, 8 July UNTICKED;
  and 1 booked on a strip that was REMOVED. → 6 and 7 July: 0 available,
  not overbooked; 8 and 9 July: 2. Lowest: 6 July.
"""

from datetime import date

import pytest

from apps.api.billing.planning import Holding, period
from apps.api.models.company import CompanyRole
from apps.api.services import gear_booking_service as service

from .company_factories import (
    grant,
    make_company,
    make_device,
    make_gear_booking,
    make_project,
    make_project_block,
    make_user,
)

JUL = lambda day: date(2026, 7, day)  # noqa: E731
RANGE = [JUL(6), JUL(7), JUL(8), JUL(9)]


@pytest.fixture
def world(pg_db):
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "gear-avail@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    p1 = make_project(pg_db, company, name="1000m", project_number="PRJ-1", start_date=JUL(1))
    p2 = make_project(pg_db, company, name="Doku", project_number="PRJ-2", start_date=JUL(1))
    shoot = make_project_block(pg_db, company, p1, start_date=JUL(6), end_date=JUL(8), label="Shoot")
    dreh = make_project_block(pg_db, company, p2, start_date=JUL(7), end_date=JUL(9), label="Dreh")
    gone = make_project_block(pg_db, company, p2, start_date=JUL(6), end_date=JUL(9), label="Gone")
    gone.is_active = False
    pg_db.commit()
    camera = make_device(pg_db, company, name="Camera A", quantity_owned=3)
    battery = make_device(pg_db, company, name="Battery", quantity_owned=6, category="Power")
    light = make_device(pg_db, company, name="Light", quantity_owned=2, category="Light")
    rows = {
        "cam_shoot": make_gear_booking(pg_db, company, p1, shoot, camera, quantity=2, status="booked", dates=[JUL(6), JUL(7), JUL(8)]),
        "cam_dreh": make_gear_booking(pg_db, company, p2, dreh, camera, quantity=2, status="scheduled", dates=[JUL(7), JUL(8), JUL(9)]),
        "bat_shoot": make_gear_booking(pg_db, company, p1, shoot, battery, quantity=4, dates=[JUL(6), JUL(7), JUL(8)]),
        "bat_dreh": make_gear_booking(pg_db, company, p2, dreh, battery, quantity=2, status="cancelled", dates=[JUL(7), JUL(8), JUL(9)]),
        "light_shoot": make_gear_booking(pg_db, company, p1, shoot, light, quantity=2, status="booked", dates=[JUL(6), JUL(7)], inactive_dates=[JUL(8)]),
        "light_gone": make_gear_booking(pg_db, company, p2, gone, light, quantity=1, status="booked", dates=RANGE),
    }
    return {"company": company, "camera": camera, "battery": battery, "light": light, **rows}


def _numbers(result):
    return {
        d.date: (d.scheduled, d.booked, d.available, d.overbooked, d.shortage) for d in result.days
    }


def test_the_three_device_example_gives_the_numbers_written_out(pg_db, world):
    got = service.availability(pg_db, world["company"].id, [world["camera"], world["battery"], world["light"]], RANGE)
    camera, battery, light = got[world["camera"].id], got[world["battery"].id], got[world["light"].id]
    assert _numbers(camera) == {
        JUL(6): (0, 2, 1, False, 0),
        JUL(7): (2, 2, -1, True, 1),
        JUL(8): (2, 2, -1, True, 1),
        JUL(9): (2, 0, 1, False, 0),
    }
    assert (camera.min_available, camera.worst_day, camera.shortage) == (-1, JUL(7), 1)
    assert _numbers(battery) == {
        JUL(6): (4, 0, 2, False, 0),
        JUL(7): (4, 0, 2, False, 0),
        JUL(8): (4, 0, 2, False, 0),
        JUL(9): (0, 0, 6, False, 0),
    }
    assert (battery.min_available, battery.worst_day) == (2, JUL(6))
    assert _numbers(light) == {
        JUL(6): (0, 2, 0, False, 0),
        JUL(7): (0, 2, 0, False, 0),
        JUL(8): (0, 0, 2, False, 0),
        JUL(9): (0, 0, 2, False, 0),
    }
    assert (light.min_available, light.worst_day, light.overbooked) == (0, JUL(6), False)


def test_the_database_layer_agrees_with_the_pure_rule_on_the_same_plain_data(pg_db, world):
    """The same bookings typed as plain data, through the pure module only."""
    plain = {
        "camera": (3, [("c1", 2, "booked", [6, 7, 8]), ("c2", 2, "scheduled", [7, 8, 9])]),
        "battery": (6, [("b1", 4, "scheduled", [6, 7, 8]), ("b2", 2, "cancelled", [7, 8, 9])]),
        "light": (2, [("l1", 2, "booked", [6, 7])]),
    }
    got = service.availability(pg_db, world["company"].id, [world["camera"], world["battery"], world["light"]], RANGE)
    for key, (owned, bookings) in plain.items():
        by_day: dict = {}
        for booking_id, quantity, status, days in bookings:
            for d in days:
                by_day.setdefault(JUL(d), []).append(Holding(booking_id, quantity, status))
        expected = period(owned, by_day, RANGE)
        assert _numbers(got[world[key].id]) == _numbers(expected), key
        assert got[world[key].id].min_available == expected.min_available, key


def test_editing_a_booking_does_not_count_it_against_itself(pg_db, world):
    """Camera A's "Dreh" booking, being edited: only the "Shoot" booking
    counts, so 1 is free on 7–9 July for it (3 owned − 2 booked)."""
    got = service.availability(
        pg_db, world["company"].id, [world["camera"]], [JUL(7), JUL(8), JUL(9)], exclude=world["cam_dreh"].id
    )[world["camera"].id]
    assert (got.min_available, got.worst_day, got.overbooked) == (1, JUL(7), False)


def test_the_holders_name_project_and_strip(pg_db, world):
    loaded = service.load(pg_db, world["company"].id, [world["camera"].id], JUL(6), JUL(9))
    names = sorted((h.project_name, h.strip_name, h.status, h.quantity) for h in loaded.holders.values())
    assert names == [("1000m", "Shoot", "booked", 2), ("Doku", "Dreh", "scheduled", 2)]


def test_another_company_sees_none_of_these_bookings(pg_db, world):
    other = make_company(pg_db, "Other GmbH", address_country="AT")
    got = service.availability(pg_db, other.id, [world["camera"]], RANGE)[world["camera"].id]
    assert [d.available for d in got.days] == [3, 3, 3, 3]
