"""Seeding a company's tax configuration: the migration's backfill.

Driven as `alembic` subprocesses against scratch databases, which is how
production and CI run it. The migration carries its own copy of the seed data
(see its docstring), so these tests check the EFFECT — what a company ends up
holding — rather than comparing two literals, which would pass while neither
of them was ever executed.

Three shapes of existing company, because the migration treats them
differently and each of the three exists in the world:

  * `address_country = 'AT'` — Mathias's dev database once he fills the form
    in, and the shape every Austrian install will have;
  * `address_country = NULL` — the dev database and the LAN server-test
    instance **today**: `POST /setup/create-superadmin` creates the first
    company itself (D42) and asks nothing, so it has no country. These are
    SKIPPED, and that is the decision: seeding them from the generic pack
    would mean the "AT" they eventually get never arrives, because seeding
    never overwrites;
  * a country with no pack of its own — the generic pack, whose standard rate
    is 0 % and inactive, so nothing can propose an invented rate.
"""

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from apps.api.models.company import CompanyRole
from apps.api.models.number_series import NumberSeries
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxClass, TaxCode, TaxRule
from apps.api.services import tax_service
from apps.api.services.company_service import create_company

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)


# ─── Scratch databases and alembic ──────────────────────────────────────────
#
# The same two helpers as `test_number_series.py`. Copied rather than shared:
# a conftest fixture that creates and drops databases is a fixture every test
# in the suite pays for at collection time, and these two files are the only
# callers.


def _scratch_database(base_url: str):
    from urllib.parse import urlsplit, urlunsplit

    name = f"filmbill_tax_{uuid.uuid4().hex[:12]}"

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


#: The revision immediately before the tax tables.
BEFORE_TAX = "0009_audit_events"


@pytest.fixture
def at_revision_0009(pg_engine):
    """A fresh database at the revision before tax existed."""
    import os

    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", BEFORE_TAX)
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to {BEFORE_TAX}:\n{result.stderr}")

    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _insert_company(conn, *, name: str, country):
    company_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO companies (id, legal_name, address_country, "
            "default_currency, default_language, fiscal_year_start_month, "
            "timezone, require_2fa_roles, tax_advisor_reports) VALUES "
            "(:id, :name, :country, 'EUR', 'de', 1, 'Europe/Vienna', "
            "'[]', false)"
        ),
        {"id": company_id, "name": name, "country": country},
    )
    return company_id


def _codes(conn, company_id) -> dict:
    rows = conn.execute(
        text(
            "SELECT code, rate, category, is_active, origin_pack, "
            "notes_reviewed, legal_notes FROM tax_codes WHERE company_id = :c"
        ),
        {"c": company_id},
    ).fetchall()
    return {
        row[0]: {
            "rate": str(row[1]),
            "category": row[2],
            "is_active": row[3],
            "origin_pack": row[4],
            "notes_reviewed": row[5],
            "legal_notes": row[6],
        }
        for row in rows
    }


def _classes(conn, company_id) -> dict:
    rows = conn.execute(
        text(
            "SELECT c.key, t.code, c.position FROM tax_classes c "
            "JOIN tax_codes t ON t.id = c.code_id WHERE c.company_id = :c"
        ),
        {"c": company_id},
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _rules(conn, company_id) -> list:
    rows = conn.execute(
        text(
            "SELECT position, name, conditions, outcome FROM tax_rules "
            "WHERE company_id = :c ORDER BY position"
        ),
        {"c": company_id},
    ).fetchall()
    return [
        {
            "position": row[0],
            "name": row[1],
            "conditions": row[2],
            "outcome": row[3],
        }
        for row in rows
    ]


# ─── The backfill ───────────────────────────────────────────────────────────


def test_the_migration_backfills_an_austrian_company(at_revision_0009):
    """The rates, the classes and the eight rules, written out.

    Expected values spelled out rather than derived from the migration's own
    tuples: a test that imports the thing it checks agrees with it by
    construction.
    """
    engine, url = at_revision_0009
    with engine.begin() as conn:
        company_id = _insert_company(conn, name="YON Studio OG", country="AT")

    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    with engine.connect() as conn:
        codes = _codes(conn, company_id)
        classes = _classes(conn, company_id)
        rules = _rules(conn, company_id)

    assert codes["AT-20"]["rate"] == "20.0000"
    assert codes["AT-20"]["category"] == "S"
    assert codes["AT-13"]["rate"] == "13.0000"
    assert codes["AT-10"]["rate"] == "10.0000"
    assert codes["AT-0"]["rate"] == "0.0000"
    assert codes["AT-0"]["category"] == "Z"
    assert codes["AT-20"]["origin_pack"] == "at"

    # The generic treatment codes come with it: the rules point at them, and a
    # company with the Austrian rates but no `EU-AE` could not reverse-charge.
    for code in ("EU-AE", "EU-K", "EU-G", "EU-O", "EU-E", "EU-Z", "EU-KU"):
        assert code in codes, f"{code} missing; the rules point at it"
        assert codes[code]["rate"] == "0.0000"
        assert codes[code]["origin_pack"] == "eu_generic"

    # The generic standard code is inactive even for Austria, where AT-20 is
    # the real one. Harmless, and it keeps every company's pack the same shape.
    assert codes["EU-STD"]["is_active"] is False

    # SCOPE §6.3's classes, and `reduced` is the 10 % one — see the pack
    # header and §17's question for the Steuerberater.
    assert classes == {
        "standard": "AT-20",
        "reduced": "AT-10",
        "reduced_2": "AT-13",
        "zero": "AT-0",
    }

    assert [rule["position"] for rule in rules] == [10, 20, 30, 40, 50, 60, 70, 80]
    assert rules[0]["conditions"] == {"seller_is_kleinunternehmer": True}
    assert rules[0]["outcome"]["treatment"] == "small_business_exemption"
    assert rules[1]["outcome"]["code"] == {"class": "@item"}
    assert rules[2]["outcome"]["treatment"] == "eu_reverse_charge"
    assert rules[3]["outcome"]["treatment"] == "intra_community_supply"
    assert rules[4]["outcome"]["warning"] == "buyer_vat_id_not_verified"
    assert rules[6]["outcome"]["treatment"] == "not_taxable_in_country"
    assert rules[7]["outcome"]["treatment"] == "export"


def test_every_shipped_legal_note_arrives_unreviewed(at_revision_0009):
    """The flag that keeps a developer's sentence off an invoice as advice.

    Checked on the rows rather than on the YAML, because the row is what the
    document template will read.
    """
    engine, url = at_revision_0009
    with engine.begin() as conn:
        company_id = _insert_company(conn, name="Notes OG", country="AT")

    assert _alembic(url, "upgrade", "head").returncode == 0

    with engine.connect() as conn:
        codes = _codes(conn, company_id)

    with_notes = {
        code: data for code, data in codes.items() if data["legal_notes"]
    }
    assert with_notes, "no code shipped a legal note at all"
    for code, data in codes.items():
        assert data["notes_reviewed"] is False, f"{code} ships pre-reviewed"
    for code, data in with_notes.items():
        assert set(data["legal_notes"]) == {"en", "de"}, (
            f"{code} has a note in {sorted(data['legal_notes'])} — both "
            "languages or neither, or one of the two documents prints nothing"
        )


def test_the_migration_backfills_a_german_company(at_revision_0009):
    engine, url = at_revision_0009
    with engine.begin() as conn:
        company_id = _insert_company(conn, name="Berlin GmbH", country="DE")

    assert _alembic(url, "upgrade", "head").returncode == 0

    with engine.connect() as conn:
        codes = _codes(conn, company_id)
        classes = _classes(conn, company_id)

    assert codes["DE-19"]["rate"] == "19.0000"
    assert codes["DE-7"]["rate"] == "7.0000"
    assert classes == {"standard": "DE-19", "reduced": "DE-7", "zero": "DE-0"}
    # Germany has two positive rates, not three, so there is no `reduced_2` —
    # the pack decides the class list, not the schema.
    assert "reduced_2" not in classes
    assert "AT-20" not in codes


def test_a_company_in_a_country_with_no_pack_gets_the_generic_one(
    at_revision_0009,
):
    """And its standard rate is 0 % and inactive, so nothing can be proposed.

    This is the case that matters most: FilmBill must not invent a French
    rate. An inactive code at 0 % is a configuration the user has to finish,
    and the engine refuses to propose from it (`blocking`).
    """
    engine, url = at_revision_0009
    with engine.begin() as conn:
        company_id = _insert_company(conn, name="Paris SARL", country="FR")

    assert _alembic(url, "upgrade", "head").returncode == 0

    with engine.connect() as conn:
        codes = _codes(conn, company_id)
        classes = _classes(conn, company_id)

    assert codes["EU-STD"]["rate"] == "0.0000"
    assert codes["EU-STD"]["is_active"] is False
    assert classes["standard"] == "EU-STD"
    assert not any(code.startswith(("AT-", "DE-")) for code in codes), (
        f"a French company was given somebody else's rates: {sorted(codes)}"
    )


def test_a_company_without_a_country_is_skipped(at_revision_0009):
    """The dev database and the LAN server-test instance, today.

    Skipped rather than seeded generically, because seeding never overwrites:
    a company handed the generic pack now would never receive the Austrian one
    when its country is finally filled in. The service seeds it at that
    moment instead.
    """
    engine, url = at_revision_0009
    with engine.begin() as conn:
        country_less = _insert_company(conn, name="FilmBill", country=None)
        austrian = _insert_company(conn, name="Has One OG", country="AT")

    assert _alembic(url, "upgrade", "head").returncode == 0

    with engine.connect() as conn:
        assert _codes(conn, country_less) == {}
        assert _classes(conn, country_less) == {}
        assert _rules(conn, country_less) == []
        # The same migration DID seed the company beside it, so this is not a
        # test of a migration that did nothing at all.
        assert _codes(conn, austrian), "nothing was seeded for anybody"


def test_a_second_upgrade_seeds_each_code_exactly_once(at_revision_0009):
    """A re-run adds nothing twice.

    Named for what it proves. An earlier version of this test was called
    "does not overwrite what a company already has" and edited `AT-20` to
    21 % first — which proved nothing, because a downgrade drops the table
    and the edit with it. **This migration cannot be re-run against a
    database that already has these tables at all**: `create_table` would
    refuse. Its `ON CONFLICT DO NOTHING` clauses are therefore a floor under
    a half-seeded database and not the idempotence that matters.

    The idempotence that matters is the SERVICE's — it really does run twice,
    once at company creation and again when a country is set — and it is
    asserted there, on an edited row:
    `test_seeding_never_overwrites_an_edited_code`.
    """
    engine, url = at_revision_0009
    with engine.begin() as conn:
        company_id = _insert_company(conn, name="Twice OG", country="AT")

    assert _alembic(url, "upgrade", "head").returncode == 0
    assert _alembic(url, "downgrade", BEFORE_TAX).returncode == 0
    assert _alembic(url, "upgrade", "head").returncode == 0

    with engine.connect() as conn:
        duplicates = conn.execute(
            text(
                "SELECT code, count(*) FROM tax_codes WHERE company_id = :c "
                "GROUP BY code HAVING count(*) > 1"
            ),
            {"c": company_id},
        ).fetchall()
        rules = _rules(conn, company_id)
        codes = _codes(conn, company_id)

    assert duplicates == [], f"seeded twice: {duplicates}"
    assert len(rules) == 8, f"{len(rules)} rules after two upgrades"
    assert codes["AT-20"]["rate"] == "20.0000"


def test_the_tables_survive_a_downgrade_and_a_second_upgrade(at_revision_0009):
    """Rule 9's other half: a migration that cannot be undone cannot be
    tried on a copy of production."""
    engine, url = at_revision_0009
    with engine.begin() as conn:
        _insert_company(conn, name="Round Trip OG", country="AT")

    assert _alembic(url, "upgrade", "head").returncode == 0
    assert _alembic(url, "downgrade", BEFORE_TAX).returncode == 0

    with engine.connect() as conn:
        present = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('tax_codes','tax_classes','tax_rules')"
            )
        ).fetchall()
    assert present == [], f"downgrade left {present} behind"

    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


# ─── The service, which is what runs twice ──────────────────────────────────
#
# The migration cannot be re-run (see above). The SERVICE can and does: once
# at company creation, and again the moment a country is set on a company that
# had none. So the no-overwrite promise is asserted here, on edited rows.

def _codes_of(db, company) -> dict:
    return {
        row.code: row
        for row in db.query(TaxCode).filter(TaxCode.company_id == company.id).all()
    }


def _class_codes_of(db, company) -> dict:
    rows = (
        db.query(TaxClass, TaxCode)
        .join(TaxCode, TaxCode.id == TaxClass.code_id)
        .filter(TaxClass.company_id == company.id)
        .all()
    )
    return {tax_class.key: code.code for tax_class, code in rows}


def test_creating_a_company_with_a_country_seeds_its_pack(pg_db):
    """Through `create_company`, which is what `POST /companies` calls."""
    owner = make_user(pg_db, "seed-owner@example.com")
    company = create_company(
        pg_db, owner=owner, legal_name="Wien Film OG", address_country="AT"
    )
    pg_db.commit()

    codes = _codes_of(pg_db, company)
    assert codes["AT-20"].rate == Decimal("20.0000")
    assert codes["AT-20"].origin_pack == "at"
    assert codes["EU-AE"].origin_pack == "eu_generic"
    assert codes["EU-STD"].is_active is False
    assert _class_codes_of(pg_db, company) == {
        "standard": "AT-20",
        "reduced": "AT-10",
        "reduced_2": "AT-13",
        "zero": "AT-0",
    }
    assert (
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).count() == 8
    )
    # And the number series are still seeded beside it: two seeders in one
    # create, and the second must not have replaced the first.
    assert (
        pg_db.query(NumberSeries)
        .filter(NumberSeries.company_id == company.id)
        .count()
        == 8
    )


def test_creating_a_company_without_a_country_seeds_no_tax_configuration(pg_db):
    """The state of the dev database and the LAN server-test instance."""
    owner = make_user(pg_db, "no-country@example.com")
    company = create_company(pg_db, owner=owner, legal_name="FilmBill")
    pg_db.commit()

    assert _codes_of(pg_db, company) == {}
    assert _class_codes_of(pg_db, company) == {}
    assert (
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).count() == 0
    )


def test_a_company_in_a_country_with_no_pack_gets_an_inactive_standard_rate(pg_db):
    owner = make_user(pg_db, "paris@example.com")
    company = create_company(
        pg_db, owner=owner, legal_name="Paris SARL", address_country="FR"
    )
    pg_db.commit()

    codes = _codes_of(pg_db, company)
    assert codes["EU-STD"].is_active is False
    assert codes["EU-STD"].rate == Decimal("0.0000")
    assert _class_codes_of(pg_db, company)["standard"] == "EU-STD"
    assert not any(code.startswith(("AT-", "DE-")) for code in codes)


def test_seeding_twice_adds_nothing_the_second_time(pg_db):
    company = make_company(pg_db, "Twice OG", address_country="AT")

    first = tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    second = tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    assert first.codes > 0 and first.classes > 0 and first.rules == 8
    assert (second.codes, second.classes, second.rules) == (0, 0, 0)
    assert not second.seeded_anything
    codes = _codes_of(pg_db, company)
    assert len(codes) == len(set(codes))
    assert (
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).count() == 8
    )


def test_seeding_never_overwrites_an_edited_code(pg_db):
    """The promise, on an edited row.

    A company whose advisor said 21 % must not find 20 % back because a
    second seeding ran. Every rate, note and rule FilmBill ships is a
    starting point somebody else's advisor may contradict (SCOPE D19).
    """
    company = make_company(pg_db, "Edited OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    edited = _codes_of(pg_db, company)["AT-20"]
    edited.rate = Decimal("21")
    edited.name = "21 % (our advisor says so)"
    edited.notes_reviewed = True
    pg_db.commit()

    result = tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    assert result.codes == 0
    after = _codes_of(pg_db, company)["AT-20"]
    assert after.rate == Decimal("21.0000")
    assert after.name == "21 % (our advisor says so)"
    assert after.notes_reviewed is True


def test_seeding_does_not_re_add_a_rule_the_company_deleted(pg_db):
    """Rules are seeded only when there are none.

    Anything cleverer eventually re-adds a rule somebody deleted on purpose —
    and a reverse-charge rule reappearing in a company that removed it is a
    wrong invoice, not an inconvenience.
    """
    company = make_company(pg_db, "Pruned OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    reverse_charge = (
        pg_db.query(TaxRule)
        .filter(TaxRule.company_id == company.id, TaxRule.position == 30)
        .one()
    )
    pg_db.delete(reverse_charge)
    pg_db.commit()

    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    positions = [
        row.position
        for row in pg_db.query(TaxRule).filter(TaxRule.company_id == company.id)
    ]
    assert sorted(positions) == [10, 20, 40, 50, 60, 70, 80]


def test_seeding_fills_a_gap_in_the_codes_without_touching_the_rest(pg_db):
    """Codes ARE compared by key, unlike rules.

    A company that is missing `EU-AE` cannot reverse-charge at all, and the
    code is referenced by a shipped rule rather than chosen by the user — so
    adding a missing one back is a repair, not a surprise.
    """
    company = make_company(pg_db, "Gap OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    codes = _codes_of(pg_db, company)
    codes["AT-20"].rate = Decimal("21")
    pg_db.delete(codes["EU-AE"])
    pg_db.commit()

    result = tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()

    assert result.codes == 1
    after = _codes_of(pg_db, company)
    assert "EU-AE" in after
    assert after["AT-20"].rate == Decimal("21.0000"), "the edit was overwritten"


def test_has_tax_configuration_answers_on_codes(pg_db):
    company = make_company(pg_db, "Probe OG", address_country="AT")
    assert tax_service.has_tax_configuration(pg_db, company_id=company.id) is False

    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    assert tax_service.has_tax_configuration(pg_db, company_id=company.id) is True


def test_an_explicit_pack_name_overrides_the_country(pg_db):
    """The seam a future "re-seed from pack X" action uses. No UI in P1a."""
    company = make_company(pg_db, "Override OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=company, pack_name="de")
    pg_db.commit()

    codes = _codes_of(pg_db, company)
    assert "DE-19" in codes
    assert "AT-20" not in codes


# ─── Through the API: the country arriving for the first time ───────────────


@pytest.fixture
def country_less_company(pg_db):
    """A company in exactly the state `POST /setup/create-superadmin` leaves
    one in (D42): a name, an owner, and no country."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "first-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def test_setting_the_country_for_the_first_time_seeds_the_pack(
    pg_client, pg_db, country_less_company
):
    """End to end, because this is the ONLY path by which the installation's
    own first company is ever seeded."""
    company = country_less_company["company"]
    headers = auth_headers(country_less_company["owner"], company)
    assert _codes_of(pg_db, company) == {}

    response = pg_client.patch(
        "/company", headers=headers, json={"address_country": "at"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["address_country"] == "AT"
    pg_db.expire_all()
    codes = _codes_of(pg_db, company)
    assert codes["AT-20"].rate == Decimal("20.0000")
    assert _class_codes_of(pg_db, company)["standard"] == "AT-20"
    assert (
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).count() == 8
    )


def test_a_later_country_change_does_not_add_a_second_countrys_rates(
    pg_client, pg_db, country_less_company
):
    """AT → DE is not a re-seed.

    Quietly adding Germany's rates to a configured Austrian company is not
    something a settings save should do, and seeding never overwrites what is
    there — so the result would be a company holding both. Re-pointing a
    company at another pack is a deliberate act and has no UI in P1a.
    """
    company = country_less_company["company"]
    headers = auth_headers(country_less_company["owner"], company)

    assert (
        pg_client.patch(
            "/company", headers=headers, json={"address_country": "AT"}
        ).status_code
        == 200
    )
    assert (
        pg_client.patch(
            "/company", headers=headers, json={"address_country": "DE"}
        ).status_code
        == 200
    )

    pg_db.expire_all()
    codes = _codes_of(pg_db, company)
    assert "AT-20" in codes
    assert "DE-19" not in codes, (
        "the company now holds two countries' rates, and which one an item "
        "gets would depend on its class pointing at the right one"
    )


def test_a_patch_that_does_not_mention_the_country_seeds_nothing(
    pg_client, pg_db, country_less_company
):
    """The guard on the transition check: every other settings save must not
    trip it."""
    company = country_less_company["company"]
    headers = auth_headers(country_less_company["owner"], company)

    response = pg_client.patch(
        "/company", headers=headers, json={"trading_name": "FilmBill"}
    )

    assert response.status_code == 200, response.text
    pg_db.expire_all()
    assert _codes_of(pg_db, company) == {}


def test_a_failed_save_leaves_no_tax_configuration_behind(
    pg_client, pg_db, country_less_company
):
    """One transaction. The seeding does not commit on its own.

    Driven through a request the API refuses: a three-letter country fails
    Pydantic validation, so nothing is written — and the point is that the
    seeder cannot have written anything either, because it is inside the
    same transaction as the field it reacts to.
    """
    company = country_less_company["company"]
    headers = auth_headers(country_less_company["owner"], company)

    response = pg_client.patch(
        "/company", headers=headers, json={"address_country": "AUT"}
    )

    assert response.status_code == 422, response.text
    pg_db.expire_all()
    assert _codes_of(pg_db, company) == {}
    assert pg_db.query(TaxCode).count() == 0
