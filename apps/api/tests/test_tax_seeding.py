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

import pytest
from sqlalchemy import create_engine, text


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
