"""A sole proprietor (Einzelunternehmen): one record, person and business (P1b-4 part 2).

Real PostgreSQL for everything that goes through an endpoint; the resolver
rule is also asserted as a table on plain data, because it is a pure function
and the table is where its cases are easiest to read.

What each test is ABOUT, made real:

  * **`invoice_name`**: read from `GET /parties/{id}/resolved`, the shape P2
    will snapshot — with and without a business name, and `owner_name`
    beside it only when the business name wins.
  * **`business_status = auto` follows the flag** and a hand-set value still
    wins — asserted on the endpoint's `buyer_type` AND on the engine's
    answer, because the reason a buyer is a business matters only through
    the tax it carries.
  * **the reverse charge**: a German sole proprietor with a VERIFIED VAT ID
    gets `EU-AE` from `POST /parties/{id}/tax-preview`. Built with P1b's own
    fixtures — `world` and `_mark_verified` from `test_parties_api.py`, which
    points the check at the identifier row (trap 18) — so the evidence is the
    same kind the organisation test relies on.
"""

import uuid

import pytest

from apps.api.billing.parties import (
    LEVEL_PERSON,
    PartyLevel,
    recipient_buyer_type,
    resolve_recipient,
)
from apps.api.models.audit import AuditEvent
from apps.api.models.party import Party, PartyTaxId

from .company_factories import make_party
from .test_parties_api import _headers, _mark_verified, world  # noqa: F401  (fixture)


def _create(pg_client, world, **body):
    payload = {"type": "person", "first_name": "Sam", "last_name": "Licht"}
    payload.update(body)
    response = pg_client.post("/parties", headers=_headers(world), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _patch(pg_client, world, party_id, **body):
    response = pg_client.patch(
        f"/parties/{party_id}", headers=_headers(world), json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def _resolved(pg_client, world, party_id):
    response = pg_client.get(f"/parties/{party_id}/resolved", headers=_headers(world))
    assert response.status_code == 200, response.text
    return response.json()


def _add_tax_id(pg_client, world, party_id, value="DE123456789"):
    response = pg_client.post(
        f"/parties/{party_id}/tax-ids",
        headers=_headers(world),
        json={"kind": "vat", "value": value},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _preview(pg_client, world, party_id):
    response = pg_client.post(
        f"/parties/{party_id}/tax-preview",
        headers=_headers(world),
        json={"supply_kind": "services"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ─── The resolver, as a table ───────────────────────────────────────────────


def _person(**kwargs):
    return PartyLevel(level=LEVEL_PERSON, id="p", name="Sam Licht", **kwargs)


@pytest.mark.parametrize(
    "level, invoice, owner",
    [
        (_person(), "Sam Licht", None),
        (_person(is_sole_proprietor=True), "Sam Licht", None),
        (_person(is_sole_proprietor=True, business_name="Studio Licht"), "Studio Licht", "Sam Licht"),
        # A business name on somebody NOT flagged changes nothing.
        (_person(business_name="Studio Licht"), "Sam Licht", None),
        (_person(is_sole_proprietor=True, business_name="   "), "Sam Licht", None),
    ],
)
def test_the_invoice_name_rule_as_a_table(level, invoice, owner):
    resolved = resolve_recipient([level])
    assert resolved.invoice_name.value == invoice
    assert resolved.owner_name.value == owner
    # The contact name is always the person's own.
    assert resolved.contact_name.value == "Sam Licht"


@pytest.mark.parametrize(
    "level, expected",
    [
        (_person(), ("private", "person")),
        (_person(is_sole_proprietor=True), ("business", "sole_proprietor")),
        (_person(is_sole_proprietor=True, business_status="private"), ("private", "explicit")),
        (_person(is_sole_proprietor=True, business_status="business"), ("business", "explicit_without_tax_id")),
        (_person(vat_id="ATU99999999"), ("business", "person_with_vat_id")),
    ],
)
def test_auto_follows_the_flag_and_a_hand_set_value_wins(level, expected):
    assert recipient_buyer_type(resolve_recipient([level])) == expected


# ─── Through the endpoints ──────────────────────────────────────────────────


def test_a_sole_proprietor_without_a_business_name_is_invoiced_under_their_own_name(
    pg_client, world
):
    person = _create(pg_client, world, is_sole_proprietor=True, legal_form="Einzelunternehmen")
    assert person["is_sole_proprietor"] is True
    resolved = _resolved(pg_client, world, person["id"])
    assert resolved["invoice_name"] == {"value": "Sam Licht", "source": "person"}
    assert resolved["owner_name"] == {"value": None, "source": None}
    assert resolved["is_sole_proprietor"] is True
    assert resolved["buyer_type"] == "business"
    assert resolved["buyer_type_reason"] == "sole_proprietor"


def test_a_business_name_wins_and_the_person_becomes_the_owner(pg_client, world):
    person = _create(pg_client, world, is_sole_proprietor=True)
    _patch(pg_client, world, person["id"], business_name="Studio Licht")
    resolved = _resolved(pg_client, world, person["id"])
    assert resolved["invoice_name"] == {"value": "Studio Licht", "source": "person"}
    assert resolved["owner_name"] == {"value": "Sam Licht", "source": "person"}
    # The person's own name is untouched: still how the contact reads.
    assert resolved["contact_name"]["value"] == "Sam Licht"


def test_a_person_who_is_not_a_sole_proprietor_is_unchanged(pg_client, world):
    person = _create(pg_client, world)
    resolved = _resolved(pg_client, world, person["id"])
    assert resolved["invoice_name"]["value"] == "Sam Licht"
    assert resolved["owner_name"]["value"] is None
    assert (resolved["buyer_type"], resolved["buyer_type_reason"]) == ("private", "person")


def test_unticking_the_flag_makes_them_private_again_and_hand_set_values_win(pg_client, world):
    person = _create(pg_client, world, is_sole_proprietor=True)
    assert _patch(pg_client, world, person["id"], business_status="private")["buyer_type"] == "private"
    back = _patch(pg_client, world, person["id"], business_status="auto")
    assert (back["buyer_type"], back["buyer_type_reason"]) == ("business", "sole_proprietor")
    off = _patch(pg_client, world, person["id"], is_sole_proprietor=False)
    assert (off["buyer_type"], off["buyer_type_reason"]) == ("private", "person")


@pytest.mark.parametrize("party_type", ["organisation", "department"])
def test_the_flag_on_anything_but_a_person_is_refused(pg_client, pg_db, world, party_type):
    org = make_party(pg_db, world["company"], type="organisation", name="ORF")
    body = {"type": party_type, "name": "X", "is_sole_proprietor": True}
    if party_type == "department":
        body["parent_id"] = str(org.id)
    response = pg_client.post("/parties", headers=_headers(world), json=body)
    assert response.status_code == 422, response.text
    assert "Only a person can be a sole proprietor" in response.json()["detail"]
    patched = pg_client.patch(
        f"/parties/{org.id}", headers=_headers(world), json={"is_sole_proprietor": True}
    )
    assert patched.status_code == 422
    pg_db.refresh(org)
    assert org.is_sole_proprietor is False


def test_a_business_name_without_the_flag_is_refused(pg_client, world):
    response = pg_client.post(
        "/parties",
        headers=_headers(world),
        json={"type": "person", "first_name": "Sam", "business_name": "Studio Licht"},
    )
    assert response.status_code == 422
    assert "Also works as a business" in response.json()["detail"]


def test_the_database_itself_refuses_the_flag_on_an_organisation(pg_db, world):
    org = make_party(pg_db, world["company"], type="organisation", name="ORF")
    org.is_sole_proprietor = True
    with pytest.raises(Exception, match="ck_parties_sole_proprietor_person_only"):
        pg_db.commit()
    pg_db.rollback()


def test_a_verified_german_sole_proprietor_gets_the_reverse_charge(pg_client, pg_db, world):
    person = _create(pg_client, world, is_sole_proprietor=True, tax_country="DE")
    tax_id = _add_tax_id(pg_client, world, person["id"])
    assert tax_id["is_primary"] is True
    _mark_verified(pg_db, world, person, status="valid", value="DE123456789")

    body = _preview(pg_client, world, person["id"])
    assert body["code"] == "EU-AE"
    assert body["treatment"] == "eu_reverse_charge"
    # Still one row: the person and the business are the same record.
    assert pg_db.query(Party).filter(Party.company_id == world["company"].id).count() == 1


def test_crew_supplier_and_customer_at_once_with_tax_ids_on_one_row(pg_client, pg_db, world):
    person = _create(
        pg_client,
        world,
        is_sole_proprietor=True,
        business_name="Studio Licht",
        is_crew=True,
        is_supplier=True,
        is_customer=True,
    )
    _add_tax_id(pg_client, world, person["id"], value="ATU16360407")
    response = pg_client.post(
        f"/parties/{person['id']}/tax-ids",
        headers=_headers(world),
        json={"kind": "tax_number", "value": "12 345/6789", "country": "AT"},
    )
    assert response.status_code == 201, response.text
    body = pg_client.get(f"/parties/{person['id']}", headers=_headers(world)).json()
    assert (body["is_crew"], body["is_supplier"], body["is_customer"]) == (True, True, True)
    assert sorted(t["kind"] for t in body["tax_ids"]) == ["tax_number", "vat"]
    assert pg_db.query(PartyTaxId).filter(PartyTaxId.party_id == uuid.UUID(person["id"])).count() == 2
    assert pg_db.query(Party).filter(Party.company_id == world["company"].id).count() == 1


def test_the_flag_and_the_business_name_are_in_the_update_diff(pg_client, pg_db, world):
    person = _create(pg_client, world)
    _patch(pg_client, world, person["id"], is_sole_proprietor=True, business_name="Studio Licht")
    event = (
        pg_db.query(AuditEvent)
        .filter(
            AuditEvent.entity_id == uuid.UUID(person["id"]),
            AuditEvent.action == "party.updated",
        )
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    assert event.changes["is_sole_proprietor"] == [False, True]
    assert event.changes["business_name"] == [None, "Studio Licht"]


# ─── The migration ──────────────────────────────────────────────────────────


def test_the_migration_adds_the_flag_false_for_everybody_and_comes_off_again(pg_engine):
    import os

    from sqlalchemy import create_engine, text

    from .test_projects_api import _alembic, _scratch_database

    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    engine = None
    try:
        assert _alembic(url, "upgrade", "0024_party_name_parts").returncode == 0
        engine = create_engine(url)
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
                    "(gen_random_uuid(), :c, 'person', 'Sam Licht'), "
                    "(gen_random_uuid(), :c, 'organisation', 'ORF')"
                ),
                {"c": company_id},
            )
        up = _alembic(url, "upgrade", "0025_party_sole_proprietor")
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            assert sorted(
                conn.execute(
                    text("SELECT name, is_sole_proprietor, business_name FROM parties")
                ).all()
            ) == [("ORF", False, None), ("Sam Licht", False, None)]
        with pytest.raises(Exception, match="ck_parties_sole_proprietor_person_only"):
            with engine.begin() as conn:
                conn.execute(text("UPDATE parties SET is_sole_proprietor = true WHERE name = 'ORF'"))
        down = _alembic(url, "downgrade", "0024_party_name_parts")
        assert down.returncode == 0, down.stderr
        again = _alembic(url, "upgrade", "0025_party_sole_proprietor")
        assert again.returncode == 0, again.stderr
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
