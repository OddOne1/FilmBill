"""Crew: people in the parties list with a third flag (B2b part 1).

Real PostgreSQL throughout, because three of the things checked here are
things only it can answer: the array filter on `tags`, the partial index the
flag ships with, and the migration that adds it.

The themes, each the thing a test is ABOUT made real:

  * **No number for crew alone.** The number comes from
    `allocate_party_number` through the real number series; the test reads
    the allocation table, and proves the same path DOES number the person
    the moment they become a supplier.
  * **The pickers do not leak, in either direction.** Asserted through the
    exact requests the web pickers send (`/parties?role=customer`,
    `role=supplier`) and the crew list's own request.
  * **An import only creates.** The existing party's row, tags and contact
    points are read back from the database after an apply that ticked it.
  * **Preview equals apply.** The same file, the preview's digest, and the
    created people compared with the preview's lines.
"""

import os

import pytest
from sqlalchemy import create_engine, text

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.number_series import NumberAllocation
from apps.api.models.party import Party, PartyContactPoint
from apps.api.models.site_settings import SiteSettings
from apps.api.services import tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_item,
    make_party,
    make_superadmin,
    make_user,
)
from .test_projects_api import _alembic, _scratch_database


@pytest.fixture
def world(pg_db):
    from apps.api.services.number_series import seed_number_series

    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "crew-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world):
    return auth_headers(world["owner"], world["company"])


def _add(pg_client, world, **body):
    payload = {"name": "Anna Huber", "roles": ["DoP"]}
    payload.update(body)
    response = pg_client.post("/crew", headers=_h(world), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _csv(*lines: str, bom: bool = False, sep: str = ";") -> bytes:
    header = sep.join(["name", "roles", "email", "phone", "notes"])
    text_ = "\r\n".join([header, *lines]) + "\r\n"
    return ("﻿" if bom else "").encode("utf-8") + text_.encode("utf-8")


def _preview(pg_client, world, payload: bytes):
    response = pg_client.post(
        "/crew/import/preview",
        headers=_h(world),
        files={"file": ("crew.csv", payload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _apply(pg_client, world, payload: bytes, digest: str, include=()):
    data = {"digest": digest}
    if include:
        data["include_lines"] = ",".join(str(line) for line in include)
    return pg_client.post(
        "/crew/import",
        headers=_h(world),
        files={"file": ("crew.csv", payload, "text/csv")},
        data=data,
    )


def _names(body) -> list[str]:
    return [member["name"] for member in body["members"]]


# ─── The flag and its migration ─────────────────────────────────────────────


def test_a_party_is_not_crew_until_somebody_says_so(pg_client, pg_db, world):
    made = pg_client.post(
        "/parties", headers=_h(world), json={"type": "person", "name": "Pat"}
    )
    assert made.status_code == 201, made.text
    assert made.json()["is_crew"] is False
    assert pg_db.get(Party, made.json()["id"]).is_crew is False


@pytest.fixture
def at_revision_0021(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0021_project_blocks")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0021:\n{result.stderr}")
    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _columns(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'parties'"
                )
            )
        }


def test_the_migration_adds_the_flag_false_for_everybody_and_takes_it_away_again(
    at_revision_0021,
):
    engine, url = at_revision_0021
    with engine.begin() as conn:
        company_id = conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(gen_random_uuid(), 'Mig GmbH', 'EUR', 'de', 1, "
                "'Europe/Vienna', '[]', false) RETURNING id"
            )
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO parties (id, company_id, type, name, is_customer) "
                "VALUES (gen_random_uuid(), :c, 'organisation', 'ORF', true)"
            ),
            {"c": company_id},
        )
    assert "is_crew" not in _columns(engine)

    up = _alembic(url, "upgrade", "0022_party_crew_flag")
    assert up.returncode == 0, up.stderr
    assert "is_crew" in _columns(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name, is_crew FROM parties")).all() == [
            ("ORF", False)
        ]

    down = _alembic(url, "downgrade", "0021_project_blocks")
    assert down.returncode == 0, down.stderr
    assert "is_crew" not in _columns(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM parties")).scalars().all() == ["ORF"]

    again = _alembic(url, "upgrade", "0022_party_crew_flag")
    assert again.returncode == 0, again.stderr


# ─── No number for crew alone ───────────────────────────────────────────────


def test_a_crew_only_person_gets_no_number_and_takes_none_from_a_series(
    pg_client, pg_db, world
):
    member = _add(pg_client, world, name="Anna Huber")
    assert member["member"]["party_number"] is None
    assert pg_db.get(Party, member["member"]["id"]).party_number is None
    assert (
        pg_db.query(NumberAllocation)
        .filter(NumberAllocation.entity_id == member["member"]["id"])
        .count()
        == 0
    )

    # The positive half: the very next customer gets the FIRST customer
    # number, so the crew member consumed nothing from the series.
    customer = pg_client.post(
        "/parties",
        headers=_h(world),
        json={"type": "organisation", "name": "ORF", "is_customer": True},
    ).json()
    assert customer["party_number"] is not None
    assert customer["party_number"].endswith("1"), customer["party_number"]
    assert (
        pg_db.query(NumberAllocation)
        .filter(NumberAllocation.entity_id == customer["id"])
        .count()
        == 1
    )


def test_making_a_crew_member_a_supplier_numbers_them_then(pg_client, pg_db, world):
    member = _add(pg_client, world, name="Sam Licht")["member"]
    response = pg_client.patch(
        f"/parties/{member['id']}", headers=_h(world), json={"is_supplier": True}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_crew"] is True and body["is_supplier"] is True
    assert body["party_number"] is not None
    # Still one record.
    assert pg_db.query(Party).filter(Party.name == "Sam Licht").count() == 1


# ─── The pickers do not leak ────────────────────────────────────────────────


def test_crew_never_appears_in_a_customer_or_supplier_picker_and_back(
    pg_client, pg_db, world
):
    company = world["company"]
    crew_only = _add(pg_client, world, name="Crew Only")["member"]
    customer = make_party(pg_db, company, name="Customer Only", is_customer=True)
    supplier = make_party(pg_db, company, name="Supplier Only", is_supplier=True)
    both = make_party(
        pg_db, company, type="person", name="Crew And Supplier", is_supplier=True, is_crew=True
    )

    customers = pg_client.get("/parties?role=customer&limit=200", headers=_h(world)).json()
    suppliers = pg_client.get("/parties?role=supplier&limit=200", headers=_h(world)).json()
    crew = pg_client.get("/crew", headers=_h(world)).json()

    customer_names = {row["name"] for row in customers["parties"]}
    supplier_names = {row["name"] for row in suppliers["parties"]}
    assert customer_names == {customer.name}
    assert supplier_names == {supplier.name, both.name}
    assert set(_names(crew)) == {crew_only["name"], both.name}
    # And the parties list's own crew tab agrees with the crew list.
    tab = pg_client.get("/parties?role=crew", headers=_h(world)).json()
    assert {row["name"] for row in tab["parties"]} == set(_names(crew))


# ─── Quick add ──────────────────────────────────────────────────────────────


def test_quick_add_needs_only_a_name_and_records_where_it_came_from(
    pg_client, pg_db, world
):
    bare = _add(pg_client, world, name="  Lea   Ton ", roles=[])["member"]
    assert bare["name"] == "Lea Ton"
    assert bare["roles"] == [] and bare["email"] is None and bare["phone"] is None

    full = _add(
        pg_client,
        world,
        name="Anna Huber",
        roles=["DoP", " Camera operator ", "dop"],
        email="anna@example.com",
        phone="+43 660 1234567",
        notes="Prefers Alexa",
    )["member"]
    assert full["roles"] == ["DoP", "Camera operator"]
    assert full["email"] == "anna@example.com"
    assert full["phone"] == "+43 660 1234567"

    party = pg_db.get(Party, full["id"])
    assert party.type == "person" and party.is_crew is True
    assert party.is_customer is False and party.is_supplier is False
    assert party.notes == "Prefers Alexa"
    kinds = {
        (row.kind, row.value, row.is_primary)
        for row in pg_db.query(PartyContactPoint).filter(PartyContactPoint.party_id == party.id)
    }
    assert kinds == {("email", "anna@example.com", True), ("phone", "+43 660 1234567", True)}

    event = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == party.id, AuditEvent.action == "party.created")
        .one()
    )
    assert event.changes["source"] == [None, "crew_quick_add"]
    assert event.changes["is_crew"] == [None, True]


def test_quick_add_refuses_a_phone_number_in_the_email_field_with_a_sentence(
    pg_client, world
):
    response = pg_client.post(
        "/crew", headers=_h(world), json={"name": "X", "email": "0660 123"}
    )
    assert response.status_code == 422
    assert "is not an email address" in response.json()["detail"]


def test_quick_add_warns_about_somebody_who_looks_already_on_file(pg_client, pg_db, world):
    existing = make_party(
        pg_db, world["company"], type="person", name="Anna Huber", email="anna@example.com"
    )
    response = _add(pg_client, world, name="anna  huber", email="ANNA@example.com")
    reasons = {(entry["party_id"], entry["reason"]) for entry in response["duplicates"]}
    assert (str(existing.id), "email") in reasons
    # Created anyway: a warning, never a refusal.
    assert pg_db.query(Party).filter(Party.is_crew.is_(True)).count() == 1


def test_making_an_existing_party_crew_is_its_own_audit_event(pg_client, pg_db, world):
    party = make_party(pg_db, world["company"], type="person", name="Pat", is_supplier=True)
    response = pg_client.patch(
        f"/parties/{party.id}", headers=_h(world), json={"is_crew": True, "tags": ["Gaffer"]}
    )
    assert response.status_code == 200, response.text
    actions = {
        event.action: event.changes
        for event in pg_db.query(AuditEvent).filter(AuditEvent.entity_id == party.id)
    }
    assert actions["party.crew_flag_set"] == {"is_crew": [False, True]}
    assert "is_crew" not in actions["party.updated"]
    assert actions["party.updated"]["tags"] == [[], ["Gaffer"]]


# ─── The list ───────────────────────────────────────────────────────────────


def test_search_by_name_role_and_email_filter_by_role_and_hide_the_archived(
    pg_client, pg_db, world
):
    _add(pg_client, world, name="Anna Huber", roles=["DoP"], email="anna@example.com")
    _add(pg_client, world, name="Ben Ohr", roles=["Sound"], email="ben@ton.at")
    gaffer = _add(pg_client, world, name="Cleo Licht", roles=["Gaffer", "Best boy"])["member"]
    pg_db.get(Party, gaffer["id"]).is_active = False
    pg_db.commit()

    def names(query: str) -> list[str]:
        response = pg_client.get(f"/crew?{query}", headers=_h(world))
        assert response.status_code == 200, response.text
        return _names(response.json())

    assert names("") == ["Anna Huber", "Ben Ohr"]
    assert names("include_inactive=true") == ["Anna Huber", "Ben Ohr", "Cleo Licht"]
    assert names("search=huber") == ["Anna Huber"]
    assert names("search=sound") == ["Ben Ohr"]
    assert names("search=ton.at") == ["Ben Ohr"]
    assert names("role=dop") == ["Anna Huber"]
    assert names("role=gaffer") == []
    assert names("role=gaffer&include_inactive=true") == ["Cleo Licht"]
    assert names("sort=-name") == ["Ben Ohr", "Anna Huber"]
    assert names("sort=-role") == ["Ben Ohr", "Anna Huber"]


def test_role_suggestions_are_the_roles_in_use_and_the_catalogs_labor_items(
    pg_client, pg_db, world
):
    _add(pg_client, world, name="A", roles=["DoP", "Sound"])
    _add(pg_client, world, name="B", roles=["Sound"])
    make_item(pg_db, world["company"], sku="L-1", name_de="Gaffer", kind="labor")
    make_item(pg_db, world["company"], sku="L-2", name_de="Sound", kind="labor")
    make_item(pg_db, world["company"], sku="E-1", name_de="Alexa 35", kind="equipment_rental")
    body = pg_client.get("/crew/roles", headers=_h(world)).json()
    assert body["used"] == ["Sound", "DoP"]
    # "Sound" is already in use, so the catalog adds only what is new.
    assert body["catalog"] == ["Gaffer"]


# ─── The import ─────────────────────────────────────────────────────────────


def test_the_import_creates_exactly_what_the_preview_showed(pg_client, pg_db, world):
    payload = _csv(
        "Anna Huber;DoP|Camera operator;anna@example.com;+43 1;",
        "Ben Ohr;Sound;;;Own kit",
        ";Gaffer;;;",
        "Cleo Licht;Gaffer;not-an-email;;",
        "Dora Fokus;Focus puller;;;",
        "Anna H.;DoP;ANNA@example.com;;",
    )
    preview = _preview(pg_client, world, payload)
    assert (preview["created"], preview["duplicates"], preview["errors"]) == (3, 1, 2)
    planned = [row["name"] for row in preview["rows"] if row["action"] == "create"]
    assert planned == ["Anna Huber", "Ben Ohr", "Dora Fokus"]
    assert [row["line_number"] for row in preview["rows"] if row["action"] == "error"] == [4, 5]

    applied = _apply(pg_client, world, payload, preview["digest"])
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["created"] == 3 and body["errors"] == 2 and body["skipped"] == 1

    created = (
        pg_db.query(Party).filter(Party.is_crew.is_(True)).order_by(Party.name).all()
    )
    assert [party.name for party in created] == planned
    anna = created[0]
    assert anna.tags == ["DoP", "Camera operator"] and anna.party_number is None
    sources = {
        event.changes["source"][1]
        for event in pg_db.query(AuditEvent).filter(AuditEvent.action == "party.created")
    }
    assert sources == {"crew_import"}


def test_a_bom_and_a_comma_separator_read_the_same(pg_client, world):
    semicolon = _preview(pg_client, world, _csv("Anna Huber;DoP|Sound;;;"))
    comma = _preview(pg_client, world, _csv("Anna Huber,DoP|Sound,,,", bom=True, sep=","))
    assert comma["delimiter"] == ","
    assert [(r["name"], r["roles"]) for r in comma["rows"]] == [
        (r["name"], r["roles"]) for r in semicolon["rows"]
    ] == [("Anna Huber", ["DoP", "Sound"])]


def test_duplicates_by_email_and_by_name_are_held_back_unless_ticked(
    pg_client, pg_db, world
):
    company = world["company"]
    by_email = make_party(
        pg_db, company, type="person", name="Anna H.", email="Anna@Example.com", is_customer=True
    )
    by_name = make_party(pg_db, company, type="person", name="Ben  Ohr", is_active=False)
    payload = _csv(
        "Anna Huber;DoP;anna@example.com;;",
        "ben ohr;Sound;;;",
        "Cleo Licht;Gaffer;;;",
        "Cleo  Licht;Gaffer;;;",
    )
    preview = _preview(pg_client, world, payload)
    rows = {row["line_number"]: row for row in preview["rows"]}
    assert rows[2]["action"] == "duplicate" and rows[2]["duplicate_reason"] == "email"
    assert rows[2]["duplicate_of_party_id"] == str(by_email.id)
    assert rows[3]["action"] == "duplicate" and rows[3]["duplicate_reason"] == "name"
    assert rows[3]["duplicate_of_party_id"] == str(by_name.id)
    assert rows[4]["action"] == "create"
    assert rows[5]["action"] == "duplicate" and rows[5]["duplicate_of_line"] == 4

    applied = _apply(pg_client, world, payload, preview["digest"], include=[3]).json()
    actions = {row["line_number"]: row["action"] for row in applied["rows"]}
    assert actions == {2: "skipped", 3: "created", 4: "created", 5: "skipped"}
    crew = sorted(p.name for p in pg_db.query(Party).filter(Party.is_crew.is_(True)))
    assert crew == ["Cleo Licht", "ben ohr"]


def test_an_import_never_updates_an_existing_party_even_when_its_line_is_ticked(
    pg_client, pg_db, world
):
    existing = make_party(
        pg_db,
        world["company"],
        type="person",
        name="Anna Huber",
        email="anna@example.com",
        is_supplier=True,
        tags=["Editor"],
    )
    payload = _csv("Anna Huber;DoP;anna@example.com;+43 999;New note")
    preview = _preview(pg_client, world, payload)
    assert preview["rows"][0]["action"] == "duplicate"
    applied = _apply(pg_client, world, payload, preview["digest"], include=[2])
    assert applied.status_code == 200, applied.text

    pg_db.expire_all()
    row = pg_db.get(Party, existing.id)
    assert (row.name, row.tags, row.is_crew, row.notes) == ("Anna Huber", ["Editor"], False, None)
    assert {
        (c.kind, c.value)
        for c in pg_db.query(PartyContactPoint).filter(PartyContactPoint.party_id == existing.id)
    } == {("email", "anna@example.com")}
    # Ticked, so a SECOND record was made — never a merge into the first.
    assert pg_db.query(Party).filter(Party.name == "Anna Huber").count() == 2
    assert not (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == existing.id, AuditEvent.action != "party.created")
        .count()
    )


def test_a_file_that_moved_since_the_preview_writes_nothing(pg_client, pg_db, world):
    payload = _csv("Anna Huber;DoP;;;")
    preview = _preview(pg_client, world, payload)
    # Somebody adds her in another tab between preview and apply.
    _add(pg_client, world, name="Anna Huber")
    response = _apply(pg_client, world, payload, preview["digest"])
    assert response.status_code == 409
    assert "no longer matches the preview" in response.json()["detail"]
    assert pg_db.query(Party).filter(Party.is_crew.is_(True)).count() == 1


def test_a_file_without_a_name_column_is_refused_with_the_columns_named(pg_client, world):
    response = pg_client.post(
        "/crew/import/preview",
        headers=_h(world),
        # P1b-4: `vorname` is now read as `first_name`, so the file without a
        # name column is one with only roles and email.
        files={"file": ("crew.csv", b"rolle;email\nDoP;a@b.at\n", "text/csv")},
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_the_export_is_the_import_format_and_comes_back_as_duplicates(pg_client, world):
    _add(pg_client, world, name="Anna Huber", roles=["DoP", "Sound"], email="anna@example.com", phone="+43 1", notes="Ja; gern")
    _add(pg_client, world, name="Ben Ohr", roles=[])
    response = pg_client.get("/crew/export.csv", headers=_h(world))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    content = response.content
    assert content.startswith("﻿".encode("utf-8"))
    lines = content.decode("utf-8-sig").splitlines()
    # P1b-4: the three name parts first, then the display name. These two
    # were added through the older single `name` field, so their parts are
    # empty and the name column carries the whole name. R1: an owner may see
    # rates, so `day_rate` closes the row (empty: no catalog role here).
    assert lines[0] == "first_name;middle_name;last_name;name;roles;email;phone;notes;day_rate"
    assert lines[1] == ';;;Anna Huber;DoP|Sound;anna@example.com;+43 1;"Ja; gern";'
    assert lines[2] == ";;;Ben Ohr;;;;;"

    again = _preview(pg_client, world, content)
    assert [row["action"] for row in again["rows"]] == ["duplicate", "duplicate"]
    assert again["rows"][0]["notes"] == "Ja; gern"


# ─── Permissions ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, can_read, can_add",
    [
        (CompanyRole.owner, True, True),
        (CompanyRole.admin, True, True),
        (CompanyRole.producer, True, True),
        (CompanyRole.accountant, True, False),
        (CompanyRole.staff, False, False),
        (CompanyRole.tax_advisor, False, False),
    ],
    ids=lambda value: value.value if isinstance(value, CompanyRole) else str(value),
)
def test_the_crew_list_follows_the_party_permissions(
    pg_client, pg_db, world, role, can_read, can_add
):
    user = make_user(pg_db, f"crew-member-{role.value}@example.com")
    grant(pg_db, world["company"], user, role)
    pg_db.commit()
    headers = auth_headers(user, world["company"])
    for path in ("/crew", "/crew/roles", "/crew/export.csv"):
        assert pg_client.get(path, headers=headers).status_code == (200 if can_read else 404), path
    added = pg_client.post("/crew", headers=headers, json={"name": f"By {role.value}"})
    assert added.status_code == (201 if can_add else 404), added.text
    preview = pg_client.post(
        "/crew/import/preview",
        headers=headers,
        files={"file": ("crew.csv", _csv("X;;;;"), "text/csv")},
    )
    assert preview.status_code == (200 if can_add else 404)
    assert pg_db.query(Party).filter(Party.is_crew.is_(True)).count() == (1 if can_add else 0)


def test_there_is_no_delete_on_the_crew_surface(client):
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    crew_operations = sorted(
        f"{method.upper()} {path}"
        for path, methods in schema["paths"].items()
        for method in methods
        if path.startswith("/crew")
    )
    assert crew_operations == [
        "GET /crew",
        "GET /crew/export.csv",
        "GET /crew/roles",
        "POST /crew",
        "POST /crew/import",
        "POST /crew/import/preview",
    ]
