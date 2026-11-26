"""People (and departments) created inside an organisation (P1b-4 part 5).

Real PostgreSQL. The thing the transactional test is ABOUT is what got
COMMITTED, so it counts through a separate connection on the engine rather
than through the test's own session: `pg_client` shares that session with
the handler, so rows a refused request had flushed would still be visible
there, while production's `get_db` closes the session and discards them.
Counting through the session would make "a failing row creates nothing"
fail for the harness's reasons, and a check that passes only after a
`rollback()` the app never does would prove nothing either (rule 17b).
"""

import uuid

import pytest
from sqlalchemy import text

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.party import Party, PartyContactPoint

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_party,
    make_party_address,
    make_user,
)
from .test_parties_api import _headers, world  # noqa: F401  (fixture)


def _post(pg_client, world, **body):
    return pg_client.post("/parties", headers=_headers(world), json=body)


def _committed(pg_engine, sql: str, **params):
    with pg_engine.connect() as conn:
        return conn.execute(text(sql), params).all()


@pytest.fixture
def orf(pg_db, world):
    org = make_party(
        pg_db,
        world["company"],
        name="ORF",
        tax_country="AT",
        vat_id="ATU16360407",
        is_customer=True,
    )
    make_party_address(pg_db, world["company"], org, city="Wien", country="AT")
    return org


# ─── Add person / Add department ────────────────────────────────────────────


def test_add_person_creates_the_child_with_its_email_and_phone(pg_client, pg_db, world, orf):
    response = _post(
        pg_client,
        world,
        type="person",
        parent_id=str(orf.id),
        title="Mag.",
        first_name="Maria",
        last_name="Huber",
        position="Redaktion Kultur",
        email="maria.huber@orf.at",
        phone="+43 1 87878",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["name"], body["parent_id"], body["title"]) == ("Maria Huber", str(orf.id), "Mag.")
    points = {
        (p.kind, p.value, p.is_primary)
        for p in pg_db.query(PartyContactPoint).filter(PartyContactPoint.party_id == uuid.UUID(body["id"]))
    }
    assert points == {("email", "maria.huber@orf.at", True), ("phone", "+43 1 87878", True)}
    # It appears in the organisation's Hierarchy list.
    parent = pg_client.get(f"/parties/{orf.id}", headers=_headers(world)).json()
    assert [c["name"] for c in parent["children"]] == ["Maria Huber"]


def test_add_another_creates_several_in_a_row(pg_client, world, orf):
    for first, last in (("Maria", "Huber"), ("Sam", "Licht"), ("Anna", "Adler")):
        response = _post(
            pg_client, world, type="person", parent_id=str(orf.id), first_name=first, last_name=last
        )
        assert response.status_code == 201, response.text
    parent = pg_client.get(f"/parties/{orf.id}", headers=_headers(world)).json()
    # Sorted by last name, as every list is.
    assert [c["name"] for c in parent["children"]] == ["Anna Adler", "Maria Huber", "Sam Licht"]


def test_add_department_with_its_additional_line(pg_client, world, orf):
    response = _post(
        pg_client,
        world,
        type="department",
        parent_id=str(orf.id),
        name="Unterhaltung",
        name_addition="Küniglberg",
    )
    assert response.status_code == 201, response.text
    assert response.json()["name_addition"] == "Küniglberg"


def test_a_person_added_inside_can_be_crew_with_roles(pg_client, world, orf):
    response = _post(
        pg_client,
        world,
        type="person",
        parent_id=str(orf.id),
        first_name="Sam",
        last_name="Licht",
        is_crew=True,
        tags=["Gaffer"],
    )
    assert response.status_code == 201, response.text
    assert (response.json()["is_crew"], response.json()["tags"]) == (True, ["Gaffer"])
    crew = pg_client.get("/crew", headers=_headers(world)).json()
    assert [m["name"] for m in crew["members"]] == ["Sam Licht"]


def test_the_new_child_inherits_through_the_resolver(pg_client, world, orf):
    person = _post(
        pg_client, world, type="person", parent_id=str(orf.id), first_name="Maria", last_name="Huber"
    ).json()
    resolved = pg_client.get(f"/parties/{person['id']}/resolved", headers=_headers(world)).json()
    assert resolved["tax_country"] == {"value": "AT", "source": "organisation"}
    assert resolved["vat_id"] == {"value": "ATU16360407", "source": "organisation"}
    assert resolved["billing_address"]["source"] == "organisation"
    assert (resolved["buyer_type"], resolved["buyer_type_reason"]) == ("business", "organisation")
    assert resolved["invoice_name"]["value"] == "ORF"
    assert resolved["contact_name"]["value"] == "Maria Huber"


# ─── "Add people now": with the organisation, in one transaction ───────────


def test_people_are_created_with_the_organisation_and_one_event_counts_them(pg_client, pg_db, world):
    response = _post(
        pg_client,
        world,
        type="organisation",
        name="Kino GmbH",
        people=[
            {"first_name": "Anna", "last_name": "Adler", "email": "anna@kino.at", "position": "CEO"},
            {"first_name": "Ben", "last_name": "Ohr", "phone": "+43 1 2"},
        ],
    )
    assert response.status_code == 201, response.text
    org = response.json()
    assert sorted(c["name"] for c in org["children"]) == ["Anna Adler", "Ben Ohr"]
    rows = (
        pg_db.query(Party)
        .filter(Party.parent_id == uuid.UUID(org["id"]))
        .order_by(Party.last_name)
        .all()
    )
    assert [(p.type, p.first_name, p.position) for p in rows] == [
        ("person", "Anna", "CEO"),
        ("person", "Ben", None),
    ]
    events = pg_db.query(AuditEvent).filter(AuditEvent.action == "party.created").all()
    assert len(events) == 1
    assert events[0].entity_id == uuid.UUID(org["id"])
    assert events[0].changes["people_created"] == [None, 2]
    assert events[0].changes["people"] == [None, ["Anna Adler", "Ben Ohr"]]


@pytest.mark.parametrize(
    "bad_row, phrase",
    [
        ({"first_name": "Ben", "email": "not-an-email"}, "Person 2 has 'not-an-email'"),
        ({"middle_name": "M."}, "Person 2: This person has a middle name"),
        ({}, "Person 2: This person has no name"),
    ],
)
def test_a_failure_in_one_row_creates_nothing_at_all(
    pg_client, pg_db, pg_engine, world, bad_row, phrase
):
    response = _post(
        pg_client,
        world,
        type="organisation",
        name="Doomed GmbH",
        is_customer=True,
        people=[{"first_name": "Anna", "last_name": "Adler"}, bad_row],
    )
    assert response.status_code == 422, response.text
    assert phrase in response.json()["detail"]
    # What was COMMITTED, read through another connection (see the module
    # docstring): no organisation, no first person, no contact point, no
    # event, no customer number taken.
    assert _committed(pg_engine, "SELECT name FROM parties WHERE company_id = :c", c=world["company"].id) == []
    assert _committed(pg_engine, "SELECT id FROM party_contact_points") == []
    assert _committed(pg_engine, "SELECT id FROM audit_events WHERE action = 'party.created'") == []
    assert _committed(pg_engine, "SELECT id FROM number_allocations") == []


def test_people_only_come_with_an_organisation(pg_client, world, orf):
    response = _post(
        pg_client,
        world,
        type="department",
        name="Unterhaltung",
        parent_id=str(orf.id),
        people=[{"first_name": "Anna"}],
    )
    assert response.status_code == 422
    assert "Hierarchy tab" in response.json()["detail"]


# ─── Moving a person in and out ─────────────────────────────────────────────


def test_a_person_moves_under_an_organisation_and_back_out(pg_client, world, orf):
    person = _post(pg_client, world, type="person", first_name="Sam", last_name="Licht").json()
    moved = pg_client.patch(
        f"/parties/{person['id']}", headers=_headers(world), json={"parent_id": str(orf.id)}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["parent_id"] == str(orf.id)
    out = pg_client.patch(
        f"/parties/{person['id']}", headers=_headers(world), json={"parent_id": None}
    )
    assert out.status_code == 200, out.text
    assert out.json()["parent_id"] is None


def test_an_organisation_under_an_organisation_is_refused_and_says_what_to_do(pg_client, world, orf):
    other = _post(pg_client, world, type="organisation", name="Kino GmbH").json()
    response = pg_client.patch(
        f"/parties/{other['id']}", headers=_headers(world), json={"parent_id": str(orf.id)}
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    # Names the subject, says what is wrong and what to do (B1.2 / 17f).
    assert detail.startswith("This organisation")
    assert "add it there as a department instead" in detail
    assert "party" not in detail.lower()


# ─── Permissions and isolation ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, allowed",
    [
        (CompanyRole.owner, True),
        (CompanyRole.admin, True),
        (CompanyRole.producer, True),
        (CompanyRole.accountant, False),
        (CompanyRole.staff, False),
        (CompanyRole.tax_advisor, False),
    ],
)
def test_only_an_editor_creates_people_inside(pg_client, pg_db, pg_engine, world, orf, role, allowed):
    member = make_user(pg_db, f"children-{role.value}@example.com")
    grant(pg_db, world["company"], member, role)
    pg_db.commit()
    response = pg_client.post(
        "/parties",
        headers=auth_headers(member, world["company"]),
        json={"type": "organisation", "name": "Neu GmbH", "people": [{"first_name": "Anna"}]},
    )
    assert (response.status_code == 201) is allowed, response.text
    committed = _committed(pg_engine, "SELECT name FROM parties WHERE name IN ('Neu GmbH', 'Anna')")
    assert (len(committed) == 2) is allowed


def test_another_companys_organisation_cannot_be_the_parent(pg_client, pg_db, pg_engine, world):
    theirs = make_party(pg_db, make_company(pg_db, "Theirs OG", address_country="AT"), name="Theirs")
    response = _post(
        pg_client, world, type="person", parent_id=str(theirs.id), first_name="Anna", last_name="Adler"
    )
    assert response.status_code == 404
    assert _committed(pg_engine, "SELECT name FROM parties WHERE name = 'Anna Adler'") == []
