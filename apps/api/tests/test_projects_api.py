"""Projects and production days, against a real PostgreSQL.

Five themes:

  * **the number is allocated inside the create transaction** (rule 5), which
    is checked by making the create fail AFTER the allocation and asserting
    the counter did not move. That is the only version of this test that
    distinguishes "allocated in the transaction" from "allocated and
    committed separately", and it is the mutation that proves it.
  * **a day is unique per (date, type) among ACTIVE days**, so removing one
    and adding it again works — which a plain unique constraint would make
    impossible for ever.
  * **the range endpoint reports what it did**: created, already there,
    weekends skipped. Rule 17c applied to a bulk action: a screen that
    silently produced eight days when somebody asked for ten is a screen
    nobody can trust.
  * **who may do what**, over the whole role ladder rather than a sample.
  * **a party used by a live project cannot be archived**, with the project
    named — P1b's archive registry, through a source registered by
    `services/project_service.py`.

The migration's own up/down and its number-series backfill are in
`test_projects_migration` at the bottom, against a scratch database, because
they have to run alembic for real.
"""

import uuid
from datetime import date

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.middleware.company import Membership
from apps.api.models.company import CompanyRole
from apps.api.models.number_series import NumberAllocation, NumberSeries
from apps.api.models.project import Project, ProjectDay
from apps.api.models.site_settings import SiteSettings
from apps.api.services import (
    number_series,
    party_service,
    project_service,
    tax_service,
)
from apps.api.services.permissions import PERMISSIONS, permissions_for

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_party,
    make_superadmin,
    make_user,
)

#: Who may read a project and who may change one. Written out rather than
#: read from `PERMISSIONS`, because a test that read the same literal it is
#: checking would pass for any matrix at all.
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
    """An Austrian company with its number series seeded and one owner."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "projects-world-owner@example.com")
    record = grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    number_series.seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {
        "company": company,
        "owner": owner,
        # The REAL `Membership` the middleware would build, not a stand-in:
        # the service-level tests below call `project_service` directly and a
        # double carrying only `company_id` would pass for a service that had
        # started reading the role (rule 17b).
        "membership": Membership(user=owner, company=company, record=record),
        "db": pg_db,
    }


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _create(pg_client, world, **body):
    body.setdefault("name", "Spot Sommer 2026")
    body.setdefault("start_date", "2026-06-01")
    response = pg_client.post("/projects", headers=_headers(world), json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ─── The surface ────────────────────────────────────────────────────────────


def test_there_is_no_delete_route_anywhere_under_projects(client):
    """Asserted on the document the app serves, not on a handler.

    A handler that refused DELETE would still be a route somebody could make
    work; the guarantee is that there is nothing to call. A project is
    retired by its status and a day by `POST .../remove`.
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    deletes = [
        f"DELETE {path}"
        for path, operations in schema["paths"].items()
        if path.startswith("/projects") and "delete" in operations
    ]
    assert not deletes, (
        f"projects has a DELETE route: {deletes}. A project is retired with "
        f"status `archived` and a day with POST .../remove — both keep the "
        f"row, and anything that counts days needs the row to still be there."
    )


# ─── The number ─────────────────────────────────────────────────────────────


def test_a_new_project_gets_a_number_from_the_project_series(pg_client, world):
    first = _create(pg_client, world)
    second = _create(pg_client, world, name="Imagefilm")

    assert first["project_number"] == "PRJ-2026-001"
    assert second["project_number"] == "PRJ-2026-002"


def test_the_number_period_comes_from_the_projects_own_start_date(
    pg_client, world
):
    """A project starting in 2027, planned in 2026, is numbered in 2027.

    The series resets yearly and the period is taken from the project's start
    date rather than from today, because `PRJ-2026-004` on a 2027 production
    is the kind of thing somebody has to explain to an accountant.
    """
    project = _create(pg_client, world, start_date="2027-01-15")

    assert project["project_number"] == "PRJ-2027-001"


def test_an_allocation_that_is_rolled_back_consumes_no_number(pg_db, world):
    """Rule 5, as a behaviour: the allocation is in the CALLER's transaction.

    The service is called directly and the transaction is then rolled back,
    which is the only version of this test that distinguishes "allocated in
    the caller's transaction" from "allocated and committed separately". If
    `allocate_number` committed on its own — the mutation this test exists to
    catch — the counter would have moved with no project to show for it, and
    the next project would be `PRJ-2026-002` with no `PRJ-2026-001` anywhere.
    That gap is exactly what gap-free numbering promises not to leave.
    """
    project = project_service.create_project(
        pg_db,
        world["membership"],
        name="Never saved",
        start_date=date(2026, 6, 1),
    )
    assert project.project_number == "PRJ-2026-001"

    pg_db.rollback()

    series = (
        pg_db.query(NumberSeries)
        .filter(
            NumberSeries.company_id == world["company"].id,
            NumberSeries.document_type == "project",
        )
        .one()
    )
    assert series.next_value == 1, (
        "the counter moved although the project was rolled back — the number "
        "was allocated outside the caller's transaction"
    )
    assert pg_db.query(Project).count() == 0
    assert (
        pg_db.query(NumberAllocation)
        .filter(NumberAllocation.series_id == series.id)
        .count()
        == 0
    ), "the allocation row survived a rolled-back create"


def test_a_company_with_its_project_series_switched_off_is_told_so(
    pg_db, world
):
    """A 409 with a sentence naming the screen, not a 500.

    Somebody can switch a series off in Settings → Company → Number series,
    and a project that cannot be numbered has to say why rather than fall
    over.
    """
    pg_db.query(NumberSeries).filter(
        NumberSeries.company_id == world["company"].id,
        NumberSeries.document_type == "project",
    ).update({"is_active": False})
    pg_db.commit()

    with pytest.raises(project_service.ProjectError):
        project_service.create_project(
            pg_db,
            world["membership"],
            name="Unnumberable",
            start_date=date(2026, 6, 1),
        )


# ─── Creating, reading, editing ─────────────────────────────────────────────


def test_a_client_from_another_company_is_a_404_and_not_a_422(
    pg_client, pg_db, world
):
    """Rule 6: the answer must not confirm that the id exists somewhere.

    A 422 naming the field would say "that party is real, just not yours",
    which is a fact about another company's customer list.
    """
    other = make_company(pg_db, "Someone else GmbH")
    theirs = make_party(pg_db, other, name="Their customer")
    pg_db.commit()

    response = pg_client.post(
        "/projects",
        headers=_headers(world),
        json={
            "name": "Spot",
            "start_date": "2026-06-01",
            "client_party_id": str(theirs.id),
        },
    )

    assert response.status_code == 404, response.text
    assert pg_db.query(Project).count() == 0


def test_a_client_in_the_same_company_is_resolved_into_the_response(
    pg_client, pg_db, world
):
    client_party = make_party(pg_db, world["company"], name="ORF")
    pg_db.commit()

    project = _create(pg_client, world, client_party_id=str(client_party.id))

    assert project["client_party_id"] == str(client_party.id)
    assert project["client_name"] == "ORF"


def test_an_end_date_before_the_start_is_refused(pg_client, world):
    response = pg_client.post(
        "/projects",
        headers=_headers(world),
        json={
            "name": "Backwards",
            "start_date": "2026-06-10",
            "end_date": "2026-06-01",
        },
    )

    assert response.status_code == 409, response.text
    assert "before the start date" in response.json()["detail"]


def test_a_patch_that_would_invert_the_dates_is_refused(pg_client, world):
    project = _create(pg_client, world, end_date="2026-06-30")

    response = pg_client.patch(
        f"/projects/{project['id']}",
        headers=_headers(world),
        json={"start_date": "2026-07-15"},
    )

    assert response.status_code == 409, response.text


def test_the_status_endpoint_writes_its_own_audit_action(pg_client, pg_db, world):
    """"When did this go active" has to be answerable without diffing every
    `project.updated` event."""
    project = _create(pg_client, world)

    response = pg_client.post(
        f"/projects/{project['id']}/status",
        headers=_headers(world),
        json={"status": "active", "reason": "Shoot confirmed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    actions = [
        row.action
        for row in pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == uuid.UUID(project["id"]))
        .all()
    ]
    assert "project.status_changed" in actions
    event = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "project.status_changed")
        .one()
    )
    assert event.reason == "Shoot confirmed"
    assert event.changes["status"] == ["planning", "active"]


def test_the_list_filters_by_status_and_by_client(pg_client, pg_db, world):
    client_party = make_party(pg_db, world["company"], name="ORF")
    pg_db.commit()
    planning = _create(pg_client, world, name="Pitch")
    with_client = _create(
        pg_client, world, name="Spot", client_party_id=str(client_party.id)
    )
    pg_client.post(
        f"/projects/{with_client['id']}/status",
        headers=_headers(world),
        json={"status": "active"},
    )

    active = pg_client.get(
        "/projects", headers=_headers(world), params={"status": "active"}
    ).json()
    assert [row["id"] for row in active["projects"]] == [with_client["id"]]

    by_client = pg_client.get(
        "/projects",
        headers=_headers(world),
        params={"client_party_id": str(client_party.id)},
    ).json()
    assert [row["id"] for row in by_client["projects"]] == [with_client["id"]]

    everything = pg_client.get("/projects", headers=_headers(world)).json()
    assert {row["id"] for row in everything["projects"]} == {
        planning["id"],
        with_client["id"],
    }


def test_an_archived_project_is_out_of_the_list_until_asked_for(
    pg_client, world
):
    project = _create(pg_client, world)
    pg_client.post(
        f"/projects/{project['id']}/status",
        headers=_headers(world),
        json={"status": "archived"},
    )

    default = pg_client.get("/projects", headers=_headers(world)).json()
    assert default["projects"] == []

    asked = pg_client.get(
        "/projects", headers=_headers(world), params={"include_archived": True}
    ).json()
    assert [row["id"] for row in asked["projects"]] == [project["id"]]


# ─── Days ───────────────────────────────────────────────────────────────────


def test_two_active_days_of_the_same_type_on_one_date_are_refused(
    pg_client, world
):
    project = _create(pg_client, world)
    body = {"date": "2026-06-02", "type": "shoot"}

    first = pg_client.post(
        f"/projects/{project['id']}/days", headers=_headers(world), json=body
    )
    second = pg_client.post(
        f"/projects/{project['id']}/days", headers=_headers(world), json=body
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert "already has a shoot day" in second.json()["detail"]


def test_a_prep_day_and_a_shoot_day_can_share_a_date(pg_client, world):
    """The unique key is `(date, type)` and not `date`: a travel day that is
    also a prep day is a normal thing to plan."""
    project = _create(pg_client, world)

    for day_type in ("shoot", "prep", "travel"):
        response = pg_client.post(
            f"/projects/{project['id']}/days",
            headers=_headers(world),
            json={"date": "2026-06-02", "type": day_type},
        )
        assert response.status_code == 201, response.text


def test_a_removed_day_can_be_added_again(pg_client, pg_db, world):
    """What the PARTIAL unique index buys.

    A plain unique constraint on `(project_id, date, type)` would make a
    removed day block its own re-creation for ever — and a shoot day that
    moves out of the plan and back in again is an ordinary week in
    production.
    """
    project = _create(pg_client, world)
    created = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-06-02", "type": "shoot"},
    ).json()

    removed = pg_client.post(
        f"/projects/{project['id']}/days/{created['id']}/remove",
        headers=_headers(world),
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["is_active"] is False

    again = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-06-02", "type": "shoot"},
    )
    assert again.status_code == 201, again.text

    # The removed row is still there, and still readable.
    rows = (
        pg_db.query(ProjectDay)
        .filter(ProjectDay.project_id == uuid.UUID(project["id"]))
        .all()
    )
    assert len(rows) == 2
    assert sorted(row.is_active for row in rows) == [False, True]


def test_the_day_range_skips_weekends_and_says_so(pg_client, world):
    project = _create(pg_client, world)

    response = pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        # Monday 1 June 2026 to Sunday 7 June 2026.
        json={
            "from": "2026-06-01",
            "to": "2026-06-07",
            "type": "shoot",
            "skip_weekends": True,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created"]) == 5
    assert body["skipped"] == ["2026-06-06", "2026-06-07"]
    assert "2 weekend day(s) skipped" in body["detail"]


def test_the_day_range_reports_the_days_that_were_already_there(
    pg_client, world
):
    """Reported, not refused. Adding a block over three days that are already
    in the plan is a normal act, and refusing the whole range because of them
    would make somebody enter the gaps by hand."""
    project = _create(pg_client, world)
    pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-06-02", "type": "shoot"},
    )

    response = pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        json={"from": "2026-06-01", "to": "2026-06-03", "type": "shoot"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created"]) == 2
    assert body["existing"] == ["2026-06-02"]
    assert "1 already there" in body["detail"]


def test_the_range_writes_one_audit_event_with_counts(pg_client, pg_db, world):
    """One event, not one per day. Fifteen rows in the log for one action is
    how a log stops being read."""
    project = _create(pg_client, world)

    pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        json={"from": "2026-06-01", "to": "2026-06-05", "type": "shoot"},
    )

    events = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "project_day.created")
        .all()
    )
    assert len(events) == 1
    assert events[0].changes["created"] == [None, 5]
    assert events[0].changes["type"] == [None, "shoot"]


def test_the_range_refuses_to_run_backwards(pg_client, world):
    project = _create(pg_client, world)

    response = pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        json={"from": "2026-06-10", "to": "2026-06-01", "type": "shoot"},
    )

    assert response.status_code == 422, response.text
    assert "before it starts" in response.json()["detail"]


def test_moving_a_day_onto_an_occupied_date_is_refused(pg_client, world):
    project = _create(pg_client, world)
    first = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-06-01", "type": "shoot"},
    ).json()
    pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-06-02", "type": "shoot"},
    )

    response = pg_client.patch(
        f"/projects/{project['id']}/days/{first['id']}",
        headers=_headers(world),
        json={"date": "2026-06-02"},
    )

    assert response.status_code == 409, response.text


def test_a_day_keeps_its_call_time_when_it_moves(pg_client, world):
    """Which is why a move is a PATCH and not delete-and-re-add."""
    project = _create(pg_client, world)
    day = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={
            "date": "2026-06-01",
            "type": "shoot",
            "call_time": "06:30:00",
            "location": "Studio A",
        },
    ).json()

    moved = pg_client.patch(
        f"/projects/{project['id']}/days/{day['id']}",
        headers=_headers(world),
        json={"date": "2026-06-08"},
    ).json()

    assert moved["date"] == "2026-06-08"
    assert moved["call_time"] == "06:30:00"
    assert moved["location"] == "Studio A"


def test_the_day_list_counts_the_active_days_by_type(pg_client, world):
    """The count the budget's `days:<type>` globals are built on, served to
    the screen from the same rows."""
    project = _create(pg_client, world)
    pg_client.post(
        f"/projects/{project['id']}/days/range",
        headers=_headers(world),
        json={"from": "2026-06-01", "to": "2026-06-03", "type": "shoot"},
    )
    prep = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        json={"date": "2026-05-29", "type": "prep"},
    ).json()

    body = pg_client.get(
        f"/projects/{project['id']}/days", headers=_headers(world)
    ).json()
    assert body["counts"] == {"shoot": 3, "prep": 1}

    pg_client.post(
        f"/projects/{project['id']}/days/{prep['id']}/remove",
        headers=_headers(world),
    )
    after = pg_client.get(
        f"/projects/{project['id']}/days", headers=_headers(world)
    ).json()
    assert after["counts"] == {"shoot": 3}
    assert len(after["days"]) == 3

    with_removed = pg_client.get(
        f"/projects/{project['id']}/days",
        headers=_headers(world),
        params={"include_removed": True},
    ).json()
    assert len(with_removed["days"]) == 4


# ─── The archive guard ──────────────────────────────────────────────────────


def test_a_party_used_by_a_live_project_cannot_be_archived(
    pg_client, pg_db, world
):
    """P1b's archive registry, with a project as a source.

    The refusal names the project, because "the party is in use" is not
    something anybody can act on.
    """
    client_party = make_party(pg_db, world["company"], name="ORF")
    pg_db.commit()
    project = _create(pg_client, world, client_party_id=str(client_party.id))

    response = pg_client.post(
        f"/parties/{client_party.id}/archive", headers=_headers(world)
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert project["project_number"] in detail
    assert "Spot Sommer 2026" in detail


def test_archiving_the_project_releases_its_client(pg_client, pg_db, world):
    client_party = make_party(pg_db, world["company"], name="ORF")
    pg_db.commit()
    project = _create(pg_client, world, client_party_id=str(client_party.id))
    pg_client.post(
        f"/projects/{project['id']}/status",
        headers=_headers(world),
        json={"status": "archived"},
    )

    response = pg_client.post(
        f"/parties/{client_party.id}/archive", headers=_headers(world)
    )

    assert response.status_code == 200, response.text


def test_the_project_archive_source_is_actually_registered():
    """The half of the guard a test of the endpoint cannot see.

    An endpoint test passes identically whether the guard refuses because a
    project was found or because nothing was ever registered to look. This
    asserts the registration itself — rule 17b's "give every component that
    registers something a test asserting it ran".
    """
    from apps.api.services import project_service

    assert party_service.registered_archive_blockers() >= 1
    assert project_service._projects_using_party in party_service._ARCHIVE_BLOCKERS


# ─── Who may do what ────────────────────────────────────────────────────────


def test_the_permission_keys_exist_with_the_documented_defaults():
    assert "projects.view" in PERMISSIONS
    assert "projects.edit" in PERMISSIONS
    assert set(PERMISSIONS["projects.view"]) == set(VIEW_ROLES)
    assert set(PERMISSIONS["projects.edit"]) == set(EDIT_ROLES)


@pytest.fixture
def every_role(pg_db, world):
    """One user per company role, all in the same company."""
    users = {}
    for role in ALL_ROLES:
        user = make_user(pg_db, f"projects-{role.value}@example.com")
        grant(pg_db, world["company"], user, role)
        users[role] = user
    pg_db.commit()
    return users


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_reading_a_project_follows_the_view_matrix(
    pg_client, world, every_role, role
):
    project = _create(pg_client, world)
    headers = auth_headers(every_role[role], world["company"])

    response = pg_client.get(f"/projects/{project['id']}", headers=headers)

    # 404 and not 403 for a role that lacks the permission: `require()`
    # answers the same 404 as having no membership at all, so that no
    # endpoint can leak "this exists and you may not have it" by being
    # written slightly differently from its neighbour.
    expected = 200 if role in VIEW_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )
    assert ("projects.view" in permissions_for(role)) == (role in VIEW_ROLES)


@pytest.mark.parametrize("role", ALL_ROLES, ids=lambda r: r.value)
def test_adding_a_day_follows_the_edit_matrix(
    pg_client, world, every_role, role
):
    project = _create(pg_client, world)
    headers = auth_headers(every_role[role], world["company"])

    response = pg_client.post(
        f"/projects/{project['id']}/days",
        headers=headers,
        json={"date": "2026-06-02", "type": "shoot"},
    )

    expected = 201 if role in EDIT_ROLES else 404
    assert response.status_code == expected, (
        f"{role.value} got {response.status_code}: {response.text}"
    )


def test_a_project_of_another_company_is_a_404(pg_client, pg_db, world):
    """Rule 6, at the row level, with a project that really exists."""
    other = make_company(pg_db, "Someone else GmbH")
    their_project = Project(
        company_id=other.id,
        project_number="PRJ-2026-001",
        name="Their production",
        status="active",
        start_date=date(2026, 6, 1),
    )
    pg_db.add(their_project)
    pg_db.commit()

    for method, url in (
        ("get", f"/projects/{their_project.id}"),
        ("patch", f"/projects/{their_project.id}"),
        ("get", f"/projects/{their_project.id}/days"),
    ):
        response = getattr(pg_client, method)(
            url, headers=_headers(world), **({} if method == "get" else {"json": {}})
        )
        assert response.status_code == 404, f"{method} {url}: {response.text}"


# ─── The migration ──────────────────────────────────────────────────────────


def _scratch_database(base_url: str):
    from urllib.parse import urlsplit, urlunsplit

    from sqlalchemy import create_engine, text

    name = f"filmbill_a2_{uuid.uuid4().hex[:12]}"

    def _with(db: str) -> str:
        return urlunsplit(urlsplit(base_url)._replace(path=f"/{db}"))

    admin = create_engine(_with("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    return name, _with(name), admin


def _alembic(url: str, *args: str):
    import os
    import subprocess
    from pathlib import Path

    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return subprocess.run(
        ["alembic", *args],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def at_revision_0014(pg_engine):
    """A scratch database at the revision before projects existed."""
    import os

    from sqlalchemy import create_engine, text

    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0014_catalog")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0014:\n{result.stderr}")

    db_engine = create_engine(url)
    try:
        yield db_engine, url
    finally:
        db_engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_migration_goes_up_and_down_and_backfills_the_series(
    at_revision_0014,
):
    """Up, down, and up again — and an existing company gets the series.

    The backfill matters because Mathias's dev database and the server-test
    instance both have companies that predate A2, and a company that cannot
    number its first project is a company that has to be configured by hand
    on the day it creates one.
    """
    from sqlalchemy import text

    db_engine, url = at_revision_0014

    with db_engine.begin() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(gen_random_uuid(), 'Predates A2', 'EUR', 'de', 1, "
                "'Europe/Vienna', '[]', false) RETURNING id"
            )
        ).scalar()

    up = _alembic(url, "upgrade", "head")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    with db_engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert {
            "projects",
            "project_days",
            "budgets",
            "budget_sections",
            "budget_lines",
            "budget_globals",
            "budget_adjustments",
        } <= tables

        series = conn.execute(
            text(
                "SELECT pattern, reset, padding, next_value, is_active "
                "FROM number_series "
                "WHERE company_id = :id AND document_type = 'project'"
            ),
            {"id": company_id},
        ).one()
        assert series.pattern == "PRJ-{YYYY}-{SEQ}"
        assert series.reset == "yearly"
        assert series.padding == 3
        assert series.next_value == 1
        assert series.is_active is True

        # The partial unique index, which is what lets a removed day be added
        # again. A plain unique constraint would pass a "there is an index"
        # check and fail the behaviour.
        definition = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_project_days_active'"
            )
        ).scalar()
        assert definition is not None
        assert "UNIQUE" in definition
        assert "WHERE" in definition and "is_active" in definition

    down = _alembic(url, "downgrade", "0014_catalog")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"

    with db_engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        assert "projects" not in tables
        assert "budget_lines" not in tables
        left_over = conn.execute(
            text(
                "SELECT count(*) FROM number_series WHERE document_type = 'project'"
            )
        ).scalar()
        assert left_over == 0, (
            "the downgrade left the project series behind — a re-upgrade "
            "would then skip the backfill and the company would have a "
            "series pointing at a table that had been dropped and recreated"
        )

    again = _alembic(url, "upgrade", "head")
    assert again.returncode == 0, f"{again.stdout}\n{again.stderr}"
