"""One IBAN once per company — the constraint, the message, and the race.

Mathias added the same IBAN to one company three times in the browser and
nothing stopped him (2026-10-02). Two rows for one account is not a cosmetic
problem: the settings screen shows the same bank account twice, the "default"
flag can sit on either of them, and whichever a finalized document happens to
snapshot (CLAUDE.md rule 4) is arbitrary.

**Three layers, and each one is tested as itself** (CLAUDE.md 17b):

  1. `normalise_iban` on every write path, so the comparison below is between
     comparable values;
  2. a read-then-409 in the router, which exists for the MESSAGE — it names
     the account that already holds the IBAN;
  3. `UNIQUE (company_id, iban)` in the database, which is the actual rule and
     the only layer that covers the window between (2)'s read and the insert.

Layer 3 is the one a test can most easily fail to exercise, because the
single-threaded path never reaches it — layer 2 answers first every time. So
`test_two_requests_adding_one_iban...` drives two real HTTP requests from two
threads through two sessions, released together on a barrier, and the mutation
that drops the constraint is the one that proves this file is not just
re-testing layer 2.

**Environment.** Real Postgres, through the `pg_client`/`pg_engine` fixtures
(see `conftest.py`): a MagicMock session returns what it was told for every
filter and for none, so it cannot tell a unique constraint from its absence.
"""

import os
import subprocess
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from apps.api.models.company import CompanyBankAccount, CompanyRole
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)

#: Documentation examples with correct check digits, not anybody's account.
VALID_AT = "AT61 1904 3002 3457 3201"
VALID_DE = "DE89 3704 0044 0532 0130 00"
#: The IBAN the server-test instance holds, label "Test". Used by the
#: migration test below, which has to pass on that row unchanged.
SERVER_TEST_DE = "DE70100110012526517299"

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]


@pytest.fixture
def world(pg_db):
    """One installation, two companies, an owner in each.

    Two companies rather than one, because half of what this file asserts is
    what is still ALLOWED: the same IBAN in a different company is a normal
    thing — two companies on one install can be paid into the same account —
    and a constraint on `iban` alone would forbid it while passing every
    single-company test here.
    """
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)

    company = make_company(pg_db, "YON Studio OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)

    other = make_company(pg_db, "Other GmbH")
    other_owner = make_user(pg_db, "other-owner@example.com")
    grant(pg_db, other, other_owner, CompanyRole.owner)

    pg_db.commit()
    return {
        "company": company,
        "owner": owner,
        "other": other,
        "other_owner": other_owner,
    }


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _other_headers(world):
    return auth_headers(world["other_owner"], world["other"])


def _add(client, headers, **body):
    return client.post("/company/bank-accounts", headers=headers, json=body)


# ─── Normalisation: the comparison has to be between comparable values ──────


def test_a_spaced_lower_case_variant_is_the_same_iban(pg_client, pg_db, world):
    """`at61 1904 …` and `AT611904…` are one account, and the refusal says so.

    This is the shape Mathias hit. Without normalisation the two strings are
    simply different and the unique constraint is satisfied by both, so this
    test is what the "remove normalisation" mutation has to fail.
    """
    first = _add(pg_client, _headers(world), iban=VALID_AT, label="Hauptkonto")
    assert first.status_code == 201, first.text

    again = _add(pg_client, _headers(world), iban="  at61 1904 3002 3457 3201 ")

    assert again.status_code == 409, again.text
    assert again.json()["detail"] == 'This IBAN is already saved as "Hauptkonto".'
    assert pg_db.query(CompanyBankAccount).count() == 1


def test_the_refusal_reads_sensibly_when_the_account_has_no_label(
    pg_client, world
):
    """A label is optional and is usually absent — both rows in the dev
    database have none — so the message cannot depend on having one. It says
    there is no label and gives the grouped IBAN, which is what the person is
    looking at on screen."""
    assert _add(pg_client, _headers(world), iban=VALID_AT).status_code == 201

    again = _add(pg_client, _headers(world), iban=VALID_AT)

    assert again.status_code == 409
    detail = again.json()["detail"]
    assert "no label" in detail
    assert "AT61 1904 3002 3457 3201" in detail


# ─── The same IBAN in another company is allowed ────────────────────────────


def test_the_same_iban_in_a_different_company_is_accepted(
    pg_client, pg_db, world
):
    """The constraint is on the PAIR. One on `iban` alone would pass every
    other test in this file and break a two-company installation."""
    assert _add(pg_client, _headers(world), iban=VALID_AT).status_code == 201

    theirs = _add(pg_client, _other_headers(world), iban=VALID_AT)

    assert theirs.status_code == 201, theirs.text
    assert pg_db.query(CompanyBankAccount).count() == 2


# ─── Editing is covered too ─────────────────────────────────────────────────


def test_editing_an_account_onto_an_existing_iban_is_refused(
    pg_client, pg_db, world
):
    first = _add(pg_client, _headers(world), iban=VALID_AT, label="Hauptkonto").json()
    second = _add(pg_client, _headers(world), iban=VALID_DE, label="Payroll").json()

    response = pg_client.patch(
        f"/company/bank-accounts/{second['id']}",
        headers=_headers(world),
        json={"iban": "at61 1904 3002 3457 3201"},
    )

    assert response.status_code == 409, response.text
    assert 'already saved as "Hauptkonto"' in response.json()["detail"]
    pg_db.expire_all()
    kept = pg_db.query(CompanyBankAccount).filter_by(id=uuid.UUID(second["id"])).one()
    assert kept.iban == "DE89370400440532013000", "the refused edit was written anyway"
    assert first["iban"] == "AT611904300234573201"


def test_saving_an_account_with_its_own_iban_unchanged_is_not_a_conflict(
    pg_client, world
):
    """`exclude_id` is what makes this work. Without it, renaming an account
    would be refused because the account itself already holds the IBAN — the
    classic off-by-one in a uniqueness check."""
    account = _add(pg_client, _headers(world), iban=VALID_AT, label="Old").json()

    response = pg_client.patch(
        f"/company/bank-accounts/{account['id']}",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "New"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["label"] == "New"


# ─── The race: the layer only the database can hold ─────────────────────────


def _client_with_a_session_per_request(pg_engine):
    """A TestClient whose `get_db` opens a NEW session per request.

    The shared `pg_client` cannot be used for the race: its override hands
    every request the one `pg_db` session, so two "concurrent" requests would
    serialise inside one transaction and see each other's uncommitted rows —
    a harness in which the race cannot happen, and therefore a harness that
    would pass with the unique constraint deleted (CLAUDE.md 17b).

    One session per request is also what production does (`database.get_db`),
    so this is the faithful shape rather than a special one.
    """
    from fastapi.testclient import TestClient

    from apps.api.database import get_db
    from apps.api.main import app

    Session = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)

    def _per_request_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _per_request_session
    return TestClient(app, raise_server_exceptions=False)


def _run_concurrently(pg_engine, headers, iban, *, threads, gate=None):
    """Fire `threads` POSTs of one IBAN at once; return their status codes.

    `gate`, when given, is called inside the router's duplicate READ — which
    is how the window between the read and the insert is made certain instead
    of merely likely. Nothing in the application changes: the real
    `find_bank_account_with_iban` runs and returns its real answer, and only
    the moment it returns is controlled.
    """
    from apps.api.main import app
    from apps.api.services import company_service as service

    real_read = service.find_bank_account_with_iban

    def _read(*args, **kwargs):
        answer = real_read(*args, **kwargs)
        if gate is not None:
            gate()
        return answer

    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def attempt():
        # A client per thread: one TestClient's portal is not meant to be
        # driven from two threads at once.
        client = _client_with_a_session_per_request(pg_engine)
        response = client.post(
            "/company/bank-accounts", headers=headers, json={"iban": iban}
        )
        with lock:
            results.append((response.status_code, response.text))

    with patch("apps.api.services.s3_service.ensure_bucket_exists"), patch(
        "apps.api.services.s3_service.get_s3_client", return_value=MagicMock()
    ), patch.object(service, "find_bank_account_with_iban", _read):
        # Daemon threads, so a request that wedges fails this test rather
        # than hanging the interpreter at exit — which is what the first
        # version of this did: pytest printed its failures and the process
        # never returned, because a non-daemon worker was still blocked.
        workers = [
            threading.Thread(target=attempt, daemon=True) for _ in range(threads)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60)
        app.dependency_overrides.clear()

    return results


def test_two_requests_adding_one_iban_give_one_201_and_one_409(
    pg_engine, pg_db, world
):
    """Exactly one wins, the loser gets the same 409, and neither gets a 500.

    **Why the read is gated.** Written the obvious way — release both threads
    on a barrier and let them run — this test passed with the unique
    constraint deleted from the model. The first request simply committed
    before the second one read, so the second was refused by the router's own
    check and the database was never asked. The test was measuring layer 2
    while claiming to measure layer 3, which is CLAUDE.md 17b with the
    harness being kinder than production by accident of scheduling.

    The barrier therefore sits INSIDE the duplicate read: both requests have
    finished reading (and both have seen nothing) before either inserts.
    That is the state two real requests reach whenever they arrive close
    enough together, and from there only `UNIQUE (company_id, iban)` can
    decide. The loser's `IntegrityError` is mapped back to the same 409 it
    would have got a millisecond earlier; a 500 is what this endpoint did
    before Part 2 and is what it does again if that mapping is removed.
    """
    barrier = threading.Barrier(2)
    results = _run_concurrently(
        pg_engine,
        _headers(world),
        VALID_AT,
        threads=2,
        gate=lambda: barrier.wait(timeout=30),
    )

    codes = sorted(code for code, _ in results)
    assert len(results) == 2, f"a thread did not finish: {results}"
    assert 500 not in codes, f"a duplicate IBAN produced a 500: {results}"
    assert codes == [201, 409], (
        "with both requests past the duplicate read, only the unique "
        f"constraint can decide; got {results}"
    )

    pg_db.expire_all()
    stored = pg_db.query(CompanyBankAccount).all()
    assert len(stored) == 1, f"{len(stored)} rows for one IBAN"
    assert stored[0].iban == "AT611904300234573201"


def test_eight_requests_adding_one_iban_leave_exactly_one_row(
    pg_engine, pg_db, world
):
    """The same invariant without controlling the timing at all.

    Ungated, so most of these are refused by the router's own read and the
    constraint may never be reached — which is why this is not the test the
    "drop the constraint" mutation is aimed at. It is here because it is the
    shape a real double-click produces, and because "exactly one row, and
    nothing answered 500" is worth asserting under genuine contention.
    """
    results = _run_concurrently(
        pg_engine, _headers(world), VALID_AT, threads=8
    )

    codes = [code for code, _ in results]
    assert len(results) == 8, f"a thread did not finish: {results}"
    assert 500 not in codes, f"a 500 under contention: {results}"
    assert codes.count(201) == 1, f"expected exactly one 201, got {codes}"
    assert set(codes) == {201, 409}, f"unexpected statuses: {results}"

    pg_db.expire_all()
    assert pg_db.query(CompanyBankAccount).count() == 1


# ─── The migration ──────────────────────────────────────────────────────────
#
# Driven as a subprocess, which is how production and CI run it. Calling
# `upgrade()` in-process would need alembic's env.py, which reads the URL from
# the already-imported settings singleton — so the in-process version would
# silently migrate whichever database this suite is pointed at.


def _scratch_database(base_url: str):
    """Create a database named after this test, and drop it afterwards."""
    from urllib.parse import urlsplit, urlunsplit

    name = f"filmbill_mig_{uuid.uuid4().hex[:12]}"

    def _with(db: str) -> str:
        return urlunsplit(urlsplit(base_url)._replace(path=f"/{db}"))

    admin = create_engine(_with("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    return name, _with(name), admin


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    return subprocess.run(
        ["alembic", *args],
        cwd=str(API_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def at_revision_0006(pg_engine):
    """A fresh database migrated to the revision BEFORE this one.

    `pg_engine` is taken only to inherit the "is there a Postgres" decision
    and its skip; the scratch database here is a different one, because this
    fixture migrates it and the suite's own scratch database must keep the
    schema the other tests created.
    """
    base = os.environ["DATABASE_URL"]
    name, url, admin = _scratch_database(base)
    result = _alembic(url, "upgrade", "0006_company_accounting_settings")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0006:\n{result.stderr}")

    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _insert_company(engine, legal_name: str) -> uuid.UUID:
    company_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(:id, :name, 'EUR', 'de', 1, 'Europe/Vienna', '[]', false)"
            ),
            {"id": company_id, "name": legal_name},
        )
    return company_id


def _insert_account(engine, company_id, iban: str, label=None, is_default=False):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO company_bank_accounts "
                "(id, company_id, label, iban, is_default) VALUES "
                "(:id, :company, :label, :iban, :is_default)"
            ),
            {
                "id": uuid.uuid4(),
                "company": company_id,
                "label": label,
                "iban": iban,
                "is_default": is_default,
            },
        )


def test_the_migration_normalises_what_is_already_stored(at_revision_0006):
    """A constraint over a column holding both forms of one IBAN is a
    constraint that silently does not hold. 0005's docstring said the column
    stored the IBAN "exactly as entered, spaces and all", so rows in that
    shape are possible and the migration has to deal with them."""
    engine, url = at_revision_0006
    company = _insert_company(engine, "Spacey GmbH")
    _insert_account(engine, company, "at61 1904 3002 3457 3201", label="Lower")
    _insert_account(engine, company, "DE89 3704 0044 0532 0130 00", label="Spaced")

    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    with engine.connect() as conn:
        stored = sorted(
            row[0]
            for row in conn.execute(text("SELECT iban FROM company_bank_accounts"))
        )
    assert stored == ["AT611904300234573201", "DE89370400440532013000"]


def test_the_migration_turns_two_spellings_of_one_iban_into_a_refusal(
    at_revision_0006,
):
    """Normalising FIRST is what makes this a duplicate at all.

    These two rows are not equal before the migration runs, so a migration
    that added the constraint without normalising would succeed here and
    leave the table holding one IBAN twice — the exact state the constraint
    is supposed to make impossible.
    """
    engine, url = at_revision_0006
    company = _insert_company(engine, "Doubled GmbH")
    _insert_account(engine, company, "AT611904300234573201", label="Compact")
    _insert_account(engine, company, "at61 1904 3002 3457 3201", label="Spaced")

    result = _alembic(url, "upgrade", "head")

    assert result.returncode != 0, "the migration accepted a duplicated IBAN"
    assert "AT611904300234573201" in result.stderr
    assert str(company) in result.stderr

    with engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM company_bank_accounts")
        ).scalar()
    assert remaining == 2, "the migration deleted or merged a row"


def test_the_migration_lists_every_duplicate_rather_than_the_first(
    at_revision_0006,
):
    """Postgres's own unique-index error names one pair and stops. Whoever has
    to clean this up needs the whole list in one run."""
    engine, url = at_revision_0006
    company = _insert_company(engine, "Doubled GmbH")
    for iban in (VALID_AT, VALID_DE):
        _insert_account(engine, company, iban.replace(" ", ""))
        _insert_account(engine, company, iban.lower())

    result = _alembic(url, "upgrade", "head")

    assert result.returncode != 0
    assert "AT611904300234573201" in result.stderr
    assert "DE89370400440532013000" in result.stderr


def test_the_migration_passes_on_the_server_test_instances_single_row(
    at_revision_0006,
):
    """The state the LAN test instance is actually in: one account, label
    "Test", already normalised. It must come through untouched."""
    engine, url = at_revision_0006
    company = _insert_company(engine, "FilmBill")
    _insert_account(engine, company, SERVER_TEST_DE, label="Test", is_default=True)

    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT iban, label, is_default FROM company_bank_accounts")
        ).one()
    assert row.iban == SERVER_TEST_DE
    assert row.label == "Test"
    assert row.is_default is True


def test_the_migration_keeps_the_same_iban_in_two_companies(at_revision_0006):
    """The allowed case, asserted against the real constraint rather than
    against the model's declaration of it."""
    engine, url = at_revision_0006
    first = _insert_company(engine, "One GmbH")
    second = _insert_company(engine, "Two GmbH")
    _insert_account(engine, first, "AT611904300234573201")
    _insert_account(engine, second, "AT611904300234573201")

    result = _alembic(url, "upgrade", "head")

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
