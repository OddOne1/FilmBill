"""People's names in parts; organisations' second line (P1b-4 part 1).

Real PostgreSQL throughout. The things this file is ABOUT, each made real:

  * **the derived `name` cannot drift from the parts.** Checked through the
    endpoint, through crew quick add, through the CSV import, AND through a
    bare ORM write that goes round every router — the last one is what proves
    the derivation lives on the write itself (the mapper event) and not in a
    handler the next write path forgets to call.
  * **existing people are not split.** A person written before P1b-4 (no
    parts) keeps its stored name through unrelated edits, and is listed
    under Needs attention with the reason `name_not_split` until the parts
    are entered — read back from the list the web sends.
  * **sort and search** through `GET /parties`, the request every picker
    makes.
  * **both CSV column styles**, through the real preview and apply.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from apps.api.billing.resources.crew_csv import display_name
from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.party import Party, person_display_name
from apps.api.models.site_settings import SiteSettings
from apps.api.services import tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
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
    owner = make_user(pg_db, "names-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _h(world):
    return auth_headers(world["owner"], world["company"])


def _create(pg_client, world, **body):
    response = pg_client.post("/parties", headers=_h(world), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(pg_client, world, party_id, **body):
    response = pg_client.patch(f"/parties/{party_id}", headers=_h(world), json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _list(pg_client, world, **params):
    response = pg_client.get("/parties", headers=_h(world), params=params)
    assert response.status_code == 200, response.text
    return response.json()["parties"]


def _audit(pg_db, party_id, action):
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.entity_id == uuid.UUID(str(party_id)), AuditEvent.action == action)
        .order_by(AuditEvent.created_at.desc())
        .all()
    )


# ─── The migration ──────────────────────────────────────────────────────────


@pytest.fixture
def at_revision_0023(pg_engine):
    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0023_devices")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0023:\n{result.stderr}")
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


def test_the_migration_adds_the_parts_splits_nobody_and_comes_off_again(at_revision_0023):
    engine, url = at_revision_0023
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
                "INSERT INTO parties (id, company_id, type, name) VALUES "
                "(gen_random_uuid(), :c, 'person', 'Reisinger Philipp Maximilian'), "
                "(gen_random_uuid(), :c, 'organisation', 'ORF')"
            ),
            {"c": company_id},
        )
    assert "first_name" not in _columns(engine)

    up = _alembic(url, "upgrade", "0024_party_name_parts")
    assert up.returncode == 0, up.stderr
    assert {"first_name", "middle_name", "last_name", "name_addition"} <= _columns(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT name, first_name, middle_name, last_name, name_addition "
                "FROM parties ORDER BY name"
            )
        ).all()
    # Nobody split: the surname-first name is exactly as it was.
    assert rows == [
        ("ORF", None, None, None, None),
        ("Reisinger Philipp Maximilian", None, None, None, None),
    ]
    # The CHECKs are in the migrated schema itself, not only in the models.
    with pytest.raises(Exception, match="ck_parties_name_parts_person_only"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE parties SET first_name = 'X' WHERE name = 'ORF'"))
    with pytest.raises(Exception, match="ck_parties_name_addition_not_person"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE parties SET name_addition = 'X' "
                    "WHERE name = 'Reisinger Philipp Maximilian'"
                )
            )

    down = _alembic(url, "downgrade", "0023_devices")
    assert down.returncode == 0, down.stderr
    assert "first_name" not in _columns(engine)
    with engine.connect() as conn:
        assert sorted(conn.execute(text("SELECT name FROM parties")).scalars()) == [
            "ORF",
            "Reisinger Philipp Maximilian",
        ]
    again = _alembic(url, "upgrade", "0024_party_name_parts")
    assert again.returncode == 0, again.stderr


# ─── The derived name ───────────────────────────────────────────────────────


def test_a_person_with_parts_is_named_first_middle_last(pg_client, pg_db, world):
    body = _create(
        pg_client,
        world,
        type="person",
        first_name="  Philipp ",
        middle_name="Maximilian",
        last_name="Reisinger",
    )
    assert body["name"] == "Philipp Maximilian Reisinger"
    assert (body["first_name"], body["middle_name"], body["last_name"]) == (
        "Philipp",
        "Maximilian",
        "Reisinger",
    )
    assert body["name_not_split"] is False
    row = pg_db.get(Party, body["id"])
    pg_db.refresh(row)
    assert row.name == "Philipp Maximilian Reisinger"


def test_editing_a_first_name_rebuilds_the_name(pg_client, pg_db, world):
    made = _create(pg_client, world, type="person", first_name="Anna", last_name="Huber")
    edited = _patch(pg_client, world, made["id"], first_name="Johanna")
    assert edited["name"] == "Johanna Huber"
    dropped = _patch(pg_client, world, made["id"], middle_name="Maria")
    assert dropped["name"] == "Johanna Maria Huber"
    cleared = _patch(pg_client, world, made["id"], middle_name="")
    assert cleared["name"] == "Johanna Huber"
    assert cleared["middle_name"] is None
    row = pg_db.get(Party, made["id"])
    pg_db.refresh(row)
    assert row.name == "Johanna Huber"


def test_the_name_cannot_drift_even_on_a_write_that_skips_every_router(pg_db, world):
    """A script, a future service, a migration helper: anything that writes
    the ORM row directly still gets the derived name, because it lives on
    the write (mapper events), not in a handler."""
    person = Party(
        company_id=world["company"].id,
        type="person",
        name="stale",
        first_name="Sam",
        last_name="Licht",
        tags=[],
        einvoice_routing={},
    )
    pg_db.add(person)
    pg_db.commit()
    assert person.name == "Sam Licht"

    cases = [
        ("Sam", None, "Licht-Berger"),
        ("Samuel", "J.", "Licht"),
        (None, None, "Licht"),
        ("Sam", None, None),
        ("  Sam  ", "  ", "  Licht "),
    ]
    for first, middle, last in cases:
        person.first_name, person.middle_name, person.last_name = first, middle, last
        person.name = "somebody typed this"
        pg_db.commit()
        stored = pg_db.execute(
            text(
                "SELECT name, first_name, middle_name, last_name FROM parties "
                "WHERE id = :id"
            ),
            {"id": person.id},
        ).one()
        assert stored[0] == person_display_name(stored[1], stored[2], stored[3]), stored


def test_the_import_and_the_model_build_names_the_same_way():
    cases = [
        ("Anna", None, "Huber"),
        ("Anna", "Maria", "Huber"),
        (None, None, "Huber"),
        ("Anna", None, None),
        ("  Anna ", " ", "Huber  Berger"),
        (None, None, None),
    ]
    for case in cases:
        assert display_name(*case) == person_display_name(*case), case


def test_a_name_sent_beside_the_parts_must_agree_with_them(pg_client, world):
    made = _create(pg_client, world, type="person", first_name="Anna", last_name="Huber")
    response = pg_client.patch(
        f"/parties/{made['id']}", headers=_h(world), json={"name": "Somebody Else"}
    )
    assert response.status_code == 422
    assert "first, middle and last name" in response.json()["detail"]
    # The same name, sent back unchanged by a form, is fine.
    _patch(pg_client, world, made["id"], name="Anna Huber", position="DoP")


@pytest.mark.parametrize(
    "body, phrase",
    [
        ({"type": "organisation", "name": "ORF", "first_name": "X"}, "are for people"),
        ({"type": "person", "first_name": "Anna", "name_addition": "X"}, "organisations and departments"),
        ({"type": "person", "middle_name": "Maria"}, "middle name but no first or last"),
        ({"type": "person"}, "has no name"),
        ({"type": "organisation"}, "has no name"),
    ],
)
def test_names_that_cannot_be_stored_are_refused_with_a_sentence(pg_client, world, body, phrase):
    response = pg_client.post("/parties", headers=_h(world), json=body)
    assert response.status_code == 422, response.text
    assert phrase in response.json()["detail"]


def test_an_organisation_has_a_second_line(pg_client, pg_db, world):
    made = _create(
        pg_client, world, type="organisation", name="ORF", name_addition="Marketing department"
    )
    assert made["name"] == "ORF"
    assert made["name_addition"] == "Marketing department"
    department = _create(
        pg_client,
        world,
        type="department",
        name="Redaktion Kultur",
        name_addition="c/o Küniglberg",
        parent_id=made["id"],
    )
    parent = pg_client.get(f"/parties/{made['id']}", headers=_h(world)).json()
    assert [(c["name"], c["name_addition"]) for c in parent["children"]] == [
        ("Redaktion Kultur", "c/o Küniglberg")
    ]
    assert department["name_addition"] == "c/o Küniglberg"


# ─── Existing people are not split ──────────────────────────────────────────


def test_an_existing_person_keeps_its_name_and_needs_attention_until_split(pg_client, pg_db, world):
    old = make_party(pg_db, world["company"], type="person", name="Reisinger Philipp Maximilian")
    split = make_party(pg_db, world["company"], type="person", name="placeholder")
    split.first_name, split.last_name = "Anna", "Huber"
    pg_db.commit()

    # An unrelated edit leaves the stored name exactly as it was.
    body = _patch(pg_client, world, old.id, position="Producer")
    assert body["name"] == "Reisinger Philipp Maximilian"
    assert body["first_name"] is None and body["last_name"] is None
    assert body["name_not_split"] is True
    assert body["needs_attention_reasons"] == ["name_not_split"]

    attention = _list(pg_client, world, needs_attention="true")
    assert [p["name"] for p in attention] == ["Reisinger Philipp Maximilian"]
    assert attention[0]["needs_attention_reasons"] == ["name_not_split"]

    fixed = _patch(
        pg_client,
        world,
        old.id,
        first_name="Philipp",
        middle_name="Maximilian",
        last_name="Reisinger",
    )
    assert fixed["name"] == "Philipp Maximilian Reisinger"
    assert fixed["name_not_split"] is False
    assert fixed["needs_attention_reasons"] == []
    assert _list(pg_client, world, needs_attention="true") == []


def test_an_organisation_is_never_name_not_split(pg_client, pg_db, world):
    make_party(pg_db, world["company"], type="organisation", name="ORF")
    assert _list(pg_client, world, needs_attention="true") == []


# ─── Sort and search ────────────────────────────────────────────────────────


def test_lists_sort_people_by_last_name_and_everybody_else_by_name(pg_client, pg_db, world):
    _create(pg_client, world, type="person", first_name="Zoe", last_name="Adler")
    _create(pg_client, world, type="person", first_name="Anna", last_name="Zangl")
    _create(pg_client, world, type="person", first_name="Bert", last_name="Huber")
    _create(pg_client, world, type="person", first_name="Anna", last_name="Huber")
    _create(pg_client, world, type="organisation", name="Kino GmbH")
    # Not split: sorts by its stored name, "Moser …".
    make_party(pg_db, world["company"], type="person", name="Moser Karl")

    names = [p["name"] for p in _list(pg_client, world)]
    assert names == [
        "Zoe Adler",
        "Anna Huber",
        "Bert Huber",
        "Kino GmbH",
        "Moser Karl",
        "Anna Zangl",
    ]


@pytest.mark.parametrize("needle", ["Philipp", "maxim", "REISINGER"])
def test_search_finds_a_person_by_any_part(pg_client, world, needle):
    _create(
        pg_client,
        world,
        type="person",
        first_name="Philipp",
        middle_name="Maximilian",
        last_name="Reisinger",
    )
    _create(pg_client, world, type="person", first_name="Anna", last_name="Huber")
    assert [p["name"] for p in _list(pg_client, world, search=needle)] == [
        "Philipp Maximilian Reisinger"
    ]


def test_search_finds_an_organisation_by_its_second_line(pg_client, world):
    _create(pg_client, world, type="organisation", name="ORF", name_addition="Studio Vienna")
    _create(pg_client, world, type="organisation", name="ZDF")
    assert [p["name"] for p in _list(pg_client, world, search="studio vien")] == ["ORF"]


def test_search_and_needs_attention_never_reach_another_company(pg_client, pg_db, world):
    other = make_company(pg_db, "Other GmbH", address_country="AT")
    stranger = make_party(pg_db, other, type="person", name="Reisinger Fremd")
    stranger_org = make_party(pg_db, other, type="organisation", name="Fremd AG")
    stranger_org.name_addition = "Studio Vienna"
    pg_db.commit()
    assert _list(pg_client, world, search="reisinger") == []
    assert _list(pg_client, world, search="studio vienna") == []
    assert _list(pg_client, world, needs_attention="true") == []
    response = pg_client.get(f"/parties/{stranger.id}", headers=_h(world))
    assert response.status_code == 404


# ─── Audit ──────────────────────────────────────────────────────────────────


def test_audit_records_the_parts_and_the_second_line(pg_client, pg_db, world):
    person = _create(pg_client, world, type="person", first_name="Anna", last_name="Huber")
    created = _audit(pg_db, person["id"], "party.created")[0]
    assert created.changes["first_name"] == [None, "Anna"]
    assert created.changes["last_name"] == [None, "Huber"]

    _patch(pg_client, world, person["id"], first_name="Johanna")
    updated = _audit(pg_db, person["id"], "party.updated")[0]
    assert updated.changes["first_name"] == ["Anna", "Johanna"]
    # The derived name is in the same diff, already rebuilt.
    assert updated.changes["name"] == ["Anna Huber", "Johanna Huber"]

    org = _create(pg_client, world, type="organisation", name="ORF")
    _patch(pg_client, world, org["id"], name_addition="Studio Vienna")
    org_updated = _audit(pg_db, org["id"], "party.updated")[0]
    assert org_updated.changes["name_addition"] == [None, "Studio Vienna"]


# ─── Crew: quick add and both CSV styles ────────────────────────────────────


def _csv(header: str, *lines: str) -> bytes:
    return ("\r\n".join([header, *lines]) + "\r\n").encode("utf-8")


def _import(pg_client, world, payload: bytes):
    preview = pg_client.post(
        "/crew/import/preview",
        headers=_h(world),
        files={"file": ("crew.csv", payload, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    applied = pg_client.post(
        "/crew/import",
        headers=_h(world),
        files={"file": ("crew.csv", payload, "text/csv")},
        data={"digest": preview.json()["digest"]},
    )
    assert applied.status_code == 200, applied.text
    return preview.json(), applied.json()


def test_quick_add_takes_the_parts(pg_client, pg_db, world):
    response = pg_client.post(
        "/crew",
        headers=_h(world),
        json={"first_name": "Sam", "last_name": "Licht", "roles": ["Gaffer"]},
    )
    assert response.status_code == 201, response.text
    member = response.json()["member"]
    assert member["name"] == "Sam Licht"
    assert (member["first_name"], member["last_name"]) == ("Sam", "Licht")
    row = pg_db.get(Party, member["id"])
    assert (row.first_name, row.last_name, row.name) == ("Sam", "Licht", "Sam Licht")
    created = _audit(pg_db, member["id"], "party.created")[0]
    assert created.changes["first_name"][1] == "Sam"


def test_the_new_columns_import_into_the_parts(pg_client, pg_db, world):
    preview, applied = _import(
        pg_client,
        world,
        _csv(
            "first_name;middle_name;last_name;roles;email",
            "Philipp;Maximilian;Reisinger;DoP;pr@example.com",
            "Anna;;Huber;Gaffer;",
        ),
    )
    assert [(r["first_name"], r["middle_name"], r["last_name"]) for r in preview["rows"]] == [
        ("Philipp", "Maximilian", "Reisinger"),
        ("Anna", None, "Huber"),
    ]
    assert applied["created"] == 2
    rows = {
        p.name: p
        for p in pg_db.query(Party).filter(Party.company_id == world["company"].id).all()
    }
    reisinger = rows["Philipp Maximilian Reisinger"]
    assert (reisinger.first_name, reisinger.middle_name, reisinger.last_name) == (
        "Philipp",
        "Maximilian",
        "Reisinger",
    )
    assert rows["Anna Huber"].middle_name is None


def test_the_old_name_column_is_stored_as_given_and_flagged_not_split(pg_client, pg_db, world):
    _, applied = _import(
        pg_client, world, _csv("name;roles", "Reisinger Philipp Maximilian;DoP")
    )
    assert applied["created"] == 1
    person = pg_db.query(Party).filter(Party.company_id == world["company"].id).one()
    # Exactly as written, and in NO part: putting it in last_name (or
    # splitting it on the first space) would be a guess about name order.
    assert person.name == "Reisinger Philipp Maximilian"
    assert (person.first_name, person.middle_name, person.last_name) == (None, None, None)
    attention = _list(pg_client, world, needs_attention="true")
    assert [p["name"] for p in attention] == ["Reisinger Philipp Maximilian"]
    assert attention[0]["needs_attention_reasons"] == ["name_not_split"]


def test_when_a_line_has_both_styles_the_parts_win(pg_client, pg_db, world):
    _, applied = _import(
        pg_client, world, _csv("first_name;last_name;name", "Anna;Huber;Huber Anna")
    )
    assert applied["created"] == 1
    person = pg_db.query(Party).filter(Party.company_id == world["company"].id).one()
    assert (person.name, person.first_name, person.last_name) == ("Anna Huber", "Anna", "Huber")


def test_a_file_with_no_name_column_at_all_is_refused_with_the_columns_named(pg_client, world):
    response = pg_client.post(
        "/crew/import/preview",
        headers=_h(world),
        files={"file": ("crew.csv", _csv("roles;email", "DoP;a@example.com"), "text/csv")},
    )
    assert response.status_code == 422, response.text
    assert "first_name;middle_name;last_name" in response.json()["detail"]


def test_the_export_writes_the_parts_and_survives_a_round_trip(pg_client, pg_db, world):
    pg_client.post(
        "/crew",
        headers=_h(world),
        json={"first_name": "Anna", "middle_name": "Maria", "last_name": "Huber"},
    )
    pg_client.post("/crew", headers=_h(world), json={"name": "Moser Karl"})
    exported = pg_client.get("/crew/export.csv", headers=_h(world))
    assert exported.status_code == 200
    lines = exported.content.decode("utf-8-sig").splitlines()
    # R1: `day_rate` closes the header for an owner, who may see rates.
    assert lines[0] == "first_name;middle_name;last_name;name;roles;email;phone;notes;day_rate"
    assert "Anna;Maria;Huber;Anna Maria Huber;;;;;" in lines
    # The unsplit person keeps their whole name in the `name` column.
    assert ";;;Moser Karl;;;;;" in lines
