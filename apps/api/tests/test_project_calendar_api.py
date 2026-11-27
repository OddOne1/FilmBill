"""The calendar and strip routes (B2a part 2), against a real PostgreSQL.

Every route is driven through HTTP, and every assertion that matters reads
the DAYS back — the rows the budget counts — rather than the strip's own
fields, because a strip whose dates moved while its days stayed put would
pass any test that only looked at the strip.

Themes: the calendar's shape and counts; create (weekends off/same); edit
and a main-type change; move carrying overrides and details; resize and its
confirm rule; marking days (the middle travel day); duplicate; remove; the
old `/days` endpoints still counted; audit (one event per action); rule 6
over every route; the staff walk.
"""

import uuid
from datetime import date

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.project import ProjectBlock, ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import catalog_service, number_series, tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_project,
    make_superadmin,
    make_user,
)

EDIT_ROLES = (CompanyRole.owner, CompanyRole.admin, CompanyRole.producer)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "calendar-world-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    catalog_service.seed_catalog(pg_db, company=company)
    number_series.seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner, "db": pg_db}


def _h(world):
    return auth_headers(world["owner"], world["company"])


@pytest.fixture
def project(pg_client, world):
    response = pg_client.post(
        "/projects", headers=_h(world), json={"name": "1000m", "start_date": "2027-02-01"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create(pg_client, world, project, **body):
    body.setdefault("type", "shoot")
    response = pg_client.post(
        f"/projects/{project['id']}/blocks", headers=_h(world), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def _calendar(pg_client, world, project, **params):
    response = pg_client.get(
        f"/projects/{project['id']}/calendar", headers=_h(world), params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def _days(pg_db, block_id) -> list[tuple[str, str]]:
    """`[(iso date, type), …]` of a strip's ACTIVE rows, read from the table."""
    pg_db.expire_all()
    return [
        (row.date.isoformat(), row.type)
        for row in pg_db.query(ProjectDay)
        .filter(ProjectDay.block_id == uuid.UUID(str(block_id)), ProjectDay.is_active.is_(True))
        .order_by(ProjectDay.date)
    ]


def _url(project, strip, tail=""):
    return f"/projects/{project['id']}/blocks/{strip['strip']['id']}{tail}"


# ─── The calendar ───────────────────────────────────────────────────────────


def test_the_calendar_returns_strips_with_their_days_and_distinct_counts(
    pg_client, world, project
):
    _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-28", label="Main")
    _create(pg_client, world, project, start_date="2027-02-25", end_date="2027-02-26", label="BTS")

    view = _calendar(pg_client, world, project)

    assert [strip["label"] for strip in view["strips"]] == ["Main", "BTS"]
    assert len(view["strips"][0]["days"]) == 7
    assert view["strips"][0]["counts"] == {"shoot": 7}
    # 22–28 Feb, with 25 and 26 in both strips: SEVEN shoot dates, not nine.
    assert view["counts"] == {"shoot": 7}


def test_the_range_filter_returns_only_strips_that_touch_it_but_counts_all(
    pg_client, world, project
):
    _create(pg_client, world, project, start_date="2027-02-01", end_date="2027-02-03")
    _create(pg_client, world, project, start_date="2027-03-01", end_date="2027-03-02")

    view = _calendar(pg_client, world, project, **{"from": "2027-03-01", "to": "2027-03-31"})

    assert [strip["start_date"] for strip in view["strips"]] == ["2027-03-01"]
    assert view["counts"] == {"shoot": 5}


def test_a_backwards_range_is_refused(pg_client, world, project):
    response = pg_client.get(
        f"/projects/{project['id']}/calendar",
        headers=_h(world),
        params={"from": "2027-03-02", "to": "2027-03-01"},
    )
    assert response.status_code == 422
    assert "before it starts" in response.json()["detail"]


def test_a_day_shows_the_strips_default_until_it_has_its_own(pg_client, world, project):
    strip = _create(
        pg_client,
        world,
        project,
        start_date="2027-02-22",
        end_date="2027-02-23",
        location="Kaprun",
        default_call_time="07:00:00",
    )
    second = strip["strip"]["days"][1]
    patched = pg_client.patch(
        f"/projects/{project['id']}/days/{second['id']}",
        headers=_h(world),
        json={"location": "Zell am See"},
    )
    assert patched.status_code == 200, patched.text

    days = _calendar(pg_client, world, project)["strips"][0]["days"]
    assert days[0]["location"] is None
    assert days[0]["effective_location"] == "Kaprun"
    assert days[0]["effective_call_time"] == "07:00:00"
    assert days[1]["effective_location"] == "Zell am See"
    assert days[1]["has_own_data"] is True


# ─── Create ─────────────────────────────────────────────────────────────────


def test_weekends_off_creates_off_days_and_same_creates_main_type_days(
    pg_client, pg_db, world, project
):
    # Thu 25 Feb – Mon 1 Mar 2027: Sat 27 and Sun 28 are the weekend.
    off = _create(
        pg_client, world, project, start_date="2027-02-25", end_date="2027-03-01", weekends="off"
    )
    same = _create(
        pg_client, world, project, start_date="2027-02-25", end_date="2027-03-01", weekends="same"
    )

    assert _days(pg_db, off["strip"]["id"]) == [
        ("2027-02-25", "shoot"),
        ("2027-02-26", "shoot"),
        ("2027-02-27", "off"),
        ("2027-02-28", "off"),
        ("2027-03-01", "shoot"),
    ]
    assert [t for _, t in _days(pg_db, same["strip"]["id"])] == ["shoot"] * 5
    assert "2 of them weekend days off" in off["detail"]


@pytest.mark.parametrize(
    "body, phrase",
    [
        ({"start_date": "2027-03-05", "end_date": "2027-03-01"}, "before it starts"),
        ({"start_date": "2027-01-01", "end_date": "2029-01-02"}, "Split it into shorter"),
    ],
)
def test_a_strip_with_impossible_dates_is_refused(pg_client, world, project, body, phrase):
    response = pg_client.post(
        f"/projects/{project['id']}/blocks", headers=_h(world), json={"type": "shoot", **body}
    )
    assert response.status_code == 422, response.text
    assert phrase in response.json()["detail"]


def test_a_strip_of_an_unknown_type_is_refused(pg_client, world, project):
    response = pg_client.post(
        f"/projects/{project['id']}/blocks",
        headers=_h(world),
        json={"type": "party", "start_date": "2027-03-01", "end_date": "2027-03-01"},
    )
    assert response.status_code == 422


# ─── Edit ───────────────────────────────────────────────────────────────────


def test_a_new_main_type_carries_main_days_and_leaves_overrides(
    pg_client, pg_db, world, project
):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-24")
    pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-22", "type": "travel"}]},
    )

    response = pg_client.patch(
        _url(project, strip), headers=_h(world), json={"type": "prep", "label": "  Recce  "}
    )

    assert response.status_code == 200, response.text
    assert response.json()["strip"]["label"] == "Recce"
    assert _days(pg_db, strip["strip"]["id"]) == [
        ("2027-02-22", "travel"),
        ("2027-02-23", "prep"),
        ("2027-02-24", "prep"),
    ]


# ─── Move ───────────────────────────────────────────────────────────────────


def test_a_move_carries_overrides_and_per_day_details(pg_client, pg_db, world, project):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-28")
    pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-22", "type": "travel"}, {"date": "2027-02-28", "type": "travel"}]},
    )
    middle = strip["strip"]["days"][3]
    pg_client.patch(
        f"/projects/{project['id']}/days/{middle['id']}",
        headers=_h(world),
        json={"notes": "Glacier, early call", "call_time": "05:30:00"},
    )

    # A week later. One day later would let a buggy move collide with
    # itself (and pass); a week shifts every day onto a fresh date.
    response = pg_client.post(
        _url(project, strip, "/move"), headers=_h(world), json={"start_date": "2027-03-01"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["strip"]["start_date"] == "2027-03-01"
    assert response.json()["strip"]["end_date"] == "2027-03-07"
    assert _days(pg_db, strip["strip"]["id"]) == [
        ("2027-03-01", "travel"),
        ("2027-03-02", "shoot"),
        ("2027-03-03", "shoot"),
        ("2027-03-04", "shoot"),
        ("2027-03-05", "shoot"),
        ("2027-03-06", "shoot"),
        ("2027-03-07", "travel"),
    ]
    moved = pg_db.get(ProjectDay, uuid.UUID(middle["id"]))
    assert moved.date == date(2027, 3, 4)
    assert moved.notes == "Glacier, early call"


def test_a_one_day_move_does_not_trip_the_per_strip_index(pg_client, pg_db, world, project):
    """Forward by one: every day lands where its neighbour still is, unless
    the days are moved last-first."""
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")
    for start in ("2027-02-23", "2027-02-22"):
        response = pg_client.post(
            _url(project, strip, "/move"), headers=_h(world), json={"start_date": start}
        )
        assert response.status_code == 200, response.text
    assert [d for d, _ in _days(pg_db, strip["strip"]["id"])][0] == "2027-02-22"


# ─── Resize ─────────────────────────────────────────────────────────────────


def test_growing_adds_main_type_days_and_shrinking_takes_bare_days_out(
    pg_client, pg_db, world, project
):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-24")

    grown = pg_client.post(
        _url(project, strip, "/resize"), headers=_h(world), json={"end_date": "2027-02-26"}
    ).json()
    assert grown["applied"] is True and grown["added"] == 2
    assert len(_days(pg_db, strip["strip"]["id"])) == 5

    shrunk = pg_client.post(
        _url(project, strip, "/resize"), headers=_h(world), json={"start_date": "2027-02-24"}
    ).json()
    assert shrunk["applied"] is True and shrunk["removed"] == 2
    assert [d for d, _ in _days(pg_db, strip["strip"]["id"])] == [
        "2027-02-24",
        "2027-02-25",
        "2027-02-26",
    ]
    assert shrunk["counts"] == {"shoot": 3}


def test_shrinking_over_a_day_with_notes_needs_confirm_and_changes_nothing(
    pg_client, pg_db, world, project
):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")
    last = strip["strip"]["days"][-1]
    pg_client.patch(
        f"/projects/{project['id']}/days/{last['id']}",
        headers=_h(world),
        json={"notes": "Drone permit"},
    )

    asked = pg_client.post(
        _url(project, strip, "/resize"), headers=_h(world), json={"end_date": "2027-02-24"}
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["applied"] is False
    assert body["needs_confirm"] is True
    assert [day["date"] for day in body["days_with_data"]] == ["2027-02-26"]
    assert "26 February 2027" in body["detail"]
    # Nothing changed: five days, the strip still ends on the 26th.
    assert len(_days(pg_db, strip["strip"]["id"])) == 5
    assert pg_db.get(ProjectBlock, uuid.UUID(strip["strip"]["id"])).end_date == date(2027, 2, 26)

    confirmed = pg_client.post(
        _url(project, strip, "/resize"),
        headers=_h(world),
        json={"end_date": "2027-02-24", "confirm": True},
    ).json()
    assert confirmed["applied"] is True
    assert confirmed["removed"] == 2
    assert len(_days(pg_db, strip["strip"]["id"])) == 3
    # Taken out, not deleted: the noted day is still a row.
    kept = pg_db.get(ProjectDay, uuid.UUID(last["id"]))
    assert kept.is_active is False and kept.notes == "Drone permit"


# ─── Marking days ───────────────────────────────────────────────────────────


def test_marking_a_middle_day_as_travel_moves_the_counts(pg_client, pg_db, world, project):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")

    marked = pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-24", "type": "travel"}]},
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["counts"] == {"shoot": 4, "travel": 1}
    middle = [day for day in marked.json()["strip"]["days"] if day["date"] == "2027-02-24"][0]
    assert middle["is_override"] is True

    back = pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-24", "type": None}]},
    )
    assert back.json()["counts"] == {"shoot": 5}
    assert [t for _, t in _days(pg_db, strip["strip"]["id"])] == ["shoot"] * 5


@pytest.mark.parametrize(
    "days, phrase",
    [
        ([{"date": "2027-03-09", "type": "travel"}], "outside this strip"),
        (
            [{"date": "2027-02-23", "type": "travel"}, {"date": "2027-02-23", "type": "off"}],
            "in the list twice",
        ),
    ],
)
def test_marking_refuses_dates_outside_the_strip_and_twice_listed_dates(
    pg_client, world, project, days, phrase
):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")
    response = pg_client.put(_url(project, strip, "/days"), headers=_h(world), json={"days": days})
    assert response.status_code == 422, response.text
    assert phrase in response.json()["detail"]


def test_marking_writes_one_audit_event_with_counts(pg_client, pg_db, world, project):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")
    pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-22", "type": "travel"}, {"date": "2027-02-26", "type": "travel"}]},
    )
    events = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "project_block.days_set")
        .all()
    )
    assert len(events) == 1
    assert events[0].entity_id == uuid.UUID(strip["strip"]["id"])
    assert "changed" in str(events[0].changes)


# ─── Duplicate and remove ───────────────────────────────────────────────────


def test_duplicate_copies_types_and_details_to_the_new_start(pg_client, pg_db, world, project):
    strip = _create(
        pg_client, world, project, start_date="2027-02-22", end_date="2027-02-24", label="Main"
    )
    pg_client.put(
        _url(project, strip, "/days"),
        headers=_h(world),
        json={"days": [{"date": "2027-02-24", "type": "travel"}]},
    )

    copy = pg_client.post(
        _url(project, strip, "/duplicate"), headers=_h(world), json={"start_date": "2027-03-08"}
    )

    assert copy.status_code == 201, copy.text
    assert copy.json()["strip"]["id"] != strip["strip"]["id"]
    assert copy.json()["strip"]["label"] == "Main"
    assert _days(pg_db, copy.json()["strip"]["id"]) == [
        ("2027-03-08", "shoot"),
        ("2027-03-09", "shoot"),
        ("2027-03-10", "travel"),
    ]


def test_remove_takes_the_strip_and_its_days_out_and_says_how_many(
    pg_client, pg_db, world, project
):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-26")

    removed = pg_client.post(_url(project, strip, "/remove"), headers=_h(world))

    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == 5
    assert "5 days" in removed.json()["detail"]
    assert removed.json()["counts"] == {}
    assert _days(pg_db, strip["strip"]["id"]) == []
    # Still rows, just out of the plan.
    assert pg_db.query(ProjectDay).filter(
        ProjectDay.block_id == uuid.UUID(strip["strip"]["id"])
    ).count() == 5
    again = pg_client.post(_url(project, strip, "/remove"), headers=_h(world))
    assert again.status_code == 404


def test_every_strip_action_writes_its_own_audit_action(pg_client, pg_db, world, project):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-24")
    pg_client.patch(_url(project, strip), headers=_h(world), json={"label": "Main"})
    pg_client.post(_url(project, strip, "/move"), headers=_h(world), json={"start_date": "2027-03-01"})
    pg_client.post(_url(project, strip, "/resize"), headers=_h(world), json={"end_date": "2027-03-05"})
    pg_client.post(_url(project, strip, "/remove"), headers=_h(world))

    actions = [
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == uuid.UUID(strip["strip"]["id"]))
        .order_by(AuditEvent.created_at)
    ]
    assert actions == [
        "project_block.created",
        "project_block.updated",
        "project_block.moved",
        "project_block.resized",
        "project_block.removed",
    ]
    # No per-day event storm: none of these wrote a project_day.* event.
    assert (
        pg_db.query(AuditEvent).filter(AuditEvent.action.like("project_day.%")).count() == 0
    )


# ─── The old endpoints ──────────────────────────────────────────────────────


def test_a_day_made_through_the_old_endpoint_is_a_one_day_strip_and_is_counted(
    pg_client, world, project
):
    _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-23")
    loose = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_h(world),
        json={"date": "2027-02-23", "type": "shoot"},
    )
    assert loose.status_code == 201, loose.text
    pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_h(world),
        json={"from": "2027-03-01", "to": "2027-03-02", "type": "prep"},
    )

    view = _calendar(pg_client, world, project)
    kinds = [(strip["kind"], strip["start_date"], strip["type"]) for strip in view["strips"]]
    assert ("day", "2027-02-23", "shoot") in kinds
    assert ("day", "2027-03-01", "prep") in kinds
    # 22, 23 shoot (23 in the strip AND loose: once). 1, 2 prep.
    assert view["counts"] == {"shoot": 2, "prep": 2}

    budget = pg_client.post(
        f"/projects/{project['id']}/budgets", headers=_h(world), json={"name": "Estimate"}
    ).json()
    made = pg_client.post(
        f"/budgets/{budget['id']}/globals",
        headers=_h(world),
        json={"key": "prep_days", "label": "Prep", "source": "days:prep"},
    )
    assert made.json()["value"] == "2.0000"


def test_a_strip_days_date_cannot_be_moved_through_the_old_endpoint(pg_client, world, project):
    strip = _create(pg_client, world, project, start_date="2027-02-22", end_date="2027-02-23")
    day = strip["strip"]["days"][0]
    response = pg_client.patch(
        f"/projects/{project['id']}/days/{day['id']}",
        headers=_h(world),
        json={"date": "2027-03-09"},
    )
    assert response.status_code == 409
    assert "Move or resize the strip" in response.json()["detail"]


# ─── Rule 6, permissions, the staff walk ────────────────────────────────────


def _every_calendar_request(project_id, block_id):
    """Every operation on the calendar surface. Counted below against the
    OpenAPI document, so a new route cannot stay out of this walk."""
    base = f"/projects/{project_id}"
    return [
        ("get", f"{base}/calendar", None),
        ("post", f"{base}/blocks", {"type": "shoot", "start_date": "2027-03-01", "end_date": "2027-03-01"}),
        ("patch", f"{base}/blocks/{block_id}", {"label": "X"}),
        ("post", f"{base}/blocks/{block_id}/move", {"start_date": "2027-04-01"}),
        ("post", f"{base}/blocks/{block_id}/resize", {"end_date": "2027-04-09"}),
        ("put", f"{base}/blocks/{block_id}/days", {"days": [{"date": "2027-03-01", "type": "off"}]}),
        ("post", f"{base}/blocks/{block_id}/duplicate", {"start_date": "2027-05-01"}),
        ("post", f"{base}/blocks/{block_id}/remove", None),
    ]


def test_the_calendar_surface_in_the_walks_is_the_whole_surface(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    operations = sorted(
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method in methods
        # B2c-1's `.../blocks/{block_id}/crew` is crew, not the calendar:
        # it needs parties.view as well, and test_crew_assignments.py walks
        # it for staff and for every role. B2c-2's `.../gear` and
        # `.../gear/preview` are gear, walked by test_gear_bookings.py's
        # permission matrix (devices.view as well).
        if ("/blocks" in path and not path.endswith(("/crew", "/gear", "/gear/preview")))
        or path.endswith("/calendar")
    )
    assert len(operations) == len(_every_calendar_request("p", "b")), operations
    assert not [op for op in operations if op.startswith("DELETE")]


def test_another_companys_project_and_strip_are_a_404_on_every_route(
    pg_client, pg_db, world, project
):
    other = make_company(pg_db, "Someone else GmbH")
    their_project = make_project(pg_db, other)
    pg_db.flush()
    their_block = ProjectBlock(
        company_id=other.id,
        project_id=their_project.id,
        type="shoot",
        start_date=date(2027, 3, 1),
        end_date=date(2027, 3, 2),
        is_active=True,
    )
    pg_db.add(their_block)
    pg_db.commit()

    # Their project, their strip.
    for method, url, body in _every_calendar_request(their_project.id, their_block.id):
        response = getattr(pg_client, method)(
            url, headers=_h(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 404, f"{method} {url}: {response.text}"
    # Our project, THEIR strip id: still 404, not a write into their strip.
    for method, url, body in _every_calendar_request(project["id"], their_block.id)[2:]:
        response = getattr(pg_client, method)(
            url, headers=_h(world), **({} if body is None else {"json": body})
        )
        assert response.status_code == 404, f"{method} {url}: {response.text}"
    pg_db.expire_all()
    untouched = pg_db.get(ProjectBlock, their_block.id)
    assert untouched.is_active is True and untouched.label is None


@pytest.fixture
def every_role(pg_db, world):
    users = {}
    for role in CompanyRole:
        user = make_user(pg_db, f"calendar-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


def test_staff_see_the_calendar_and_can_change_nothing(
    pg_client, pg_db, world, project, every_role
):
    strip = _create(pg_client, world, project, start_date="2027-03-01", end_date="2027-03-03")
    headers = auth_headers(every_role[CompanyRole.staff], world["company"])

    requests = _every_calendar_request(project["id"], strip["strip"]["id"])
    for method, url, body in requests:
        response = getattr(pg_client, method)(
            url, headers=headers, **({} if body is None else {"json": body})
        )
        expected = 200 if method == "get" else 404
        assert response.status_code == expected, f"staff {method} {url}: {response.text}"
    seen = pg_client.get(f"/projects/{project['id']}/calendar", headers=headers).json()
    assert seen["counts"] == {"shoot": 3}
    assert _days(pg_db, strip["strip"]["id"]) == [
        ("2027-03-01", "shoot"),
        ("2027-03-02", "shoot"),
        ("2027-03-03", "shoot"),
    ]


@pytest.mark.parametrize("role", tuple(CompanyRole), ids=lambda r: r.value)
def test_creating_a_strip_follows_the_edit_matrix(pg_client, world, project, every_role, role):
    response = pg_client.post(
        f"/projects/{project['id']}/blocks",
        headers=auth_headers(every_role[role], world["company"]),
        json={"type": "shoot", "start_date": "2027-03-01", "end_date": "2027-03-01"},
    )
    expected = 201 if role in EDIT_ROLES else 404
    assert response.status_code == expected, f"{role.value}: {response.text}"
