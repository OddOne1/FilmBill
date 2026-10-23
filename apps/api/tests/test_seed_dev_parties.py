"""The dev-only seed script, and above all its refusal.

**The refusal is the subject.** A seed script that could reach the LAN
server-test instance is a script that will eventually put "ORF (seed)" in
front of somebody real; one that could reach production is worse. So the
three gates are tested one at a time, each with the others satisfied, which
is the only way to know that each is load-bearing rather than shadowed by its
neighbours.

`check_environment` is a separate function for exactly this reason: the
refusal can be exercised without a database, and a test that could only
check it by pointing at production would be worse than none.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "seed_dev_parties.py"

#: The dev stack's own values: `FILMBILL_ENV` unset, `filmbill` on the
#: compose network host `postgres`.
DEV_URL = "postgresql://filmbill:filmbill@postgres:5432/filmbill"
#: What `docker-compose.server-test.yml` actually produces.
SERVER_TEST_URL = "postgresql://filmbill:x@192.168.50.100:5432/filmbill_v2"


@pytest.fixture(scope="module")
def script():
    """The script as a module, without running it."""
    spec = importlib.util.spec_from_file_location("seed_dev_parties", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dev_stacks_own_environment_is_accepted(script):
    """The guard on every refusal below: if this failed, they would all pass
    for the wrong reason."""
    script.check_environment(database_url=DEV_URL, filmbill_env=None)
    script.check_environment(database_url=DEV_URL, filmbill_env="dev")


def test_a_pytest_scratch_database_is_accepted(script):
    """So the suite can run it, which is how this file exists."""
    script.check_environment(
        database_url="postgresql://u:p@postgres:5432/filmbill_pytest_scratch_42",
        filmbill_env=None,
    )


@pytest.mark.parametrize("env", ["server-test", "production", "prod", "staging"])
def test_a_non_dev_filmbill_env_is_refused(script, env):
    """Gate one, with the URL deliberately a valid dev one — so what refuses
    is the environment name and nothing else."""
    with pytest.raises(script.NotTheDevStack) as caught:
        script.check_environment(database_url=DEV_URL, filmbill_env=env)
    assert env in str(caught.value)


def test_an_unrecognised_env_is_refused_rather_than_assumed_to_be_dev(script):
    """"I do not know what this is" has to mean no.

    The alternative — treating an unknown value as development — is the
    failure mode where somebody sets `FILMBILL_ENV=prod-eu` and the script
    cheerfully writes.
    """
    with pytest.raises(script.NotTheDevStack):
        script.check_environment(database_url=DEV_URL, filmbill_env="whatever")


def test_the_server_test_database_name_is_refused(script):
    """Gate two. `filmbill_v2` is the LAN instance's name."""
    with pytest.raises(script.NotTheDevStack) as caught:
        script.check_environment(
            database_url="postgresql://u:p@postgres:5432/filmbill_v2",
            filmbill_env=None,
        )
    message = str(caught.value)
    assert "filmbill_v2" in message
    assert "will not write to it" in message


def test_a_non_local_host_is_refused(script):
    """Gate three, with a database name that WOULD be acceptable — so what
    refuses is the host."""
    with pytest.raises(script.NotTheDevStack) as caught:
        script.check_environment(
            database_url="postgresql://u:p@192.168.50.100:5432/filmbill",
            filmbill_env=None,
        )
    assert "not local" in str(caught.value)


def test_the_real_server_test_url_is_refused_by_more_than_one_gate(script):
    """Defence in depth, stated rather than assumed: the LAN instance's own
    URL trips both the name gate and the host gate, so neither being removed
    by a future edit opens the door on its own."""
    with pytest.raises(script.NotTheDevStack):
        script.check_environment(
            database_url=SERVER_TEST_URL, filmbill_env="server-test"
        )
    # Each gate alone.
    with pytest.raises(script.NotTheDevStack):
        script.check_environment(database_url=SERVER_TEST_URL, filmbill_env=None)
    with pytest.raises(script.NotTheDevStack):
        script.check_environment(
            database_url="postgresql://u:p@postgres:5432/filmbill",
            filmbill_env="server-test",
        )


def test_a_missing_database_url_is_refused(script):
    """"Which database would this write to" with no answer is a no."""
    with pytest.raises(script.NotTheDevStack) as caught:
        script.check_environment(database_url="", filmbill_env=None)
    assert "no way to tell" in str(caught.value)


def test_there_is_no_override_flag(script):
    """Deliberately absent.

    An `--i-know-what-i-am-doing` flag is a flag somebody uses at 23:00 while
    pointed at the wrong database. Asserted on the source because the subject
    IS the absence of an interface — there is no behaviour to exercise.
    """
    source = SCRIPT.read_text()
    assert "argparse" not in source
    assert "--force" not in source
    assert "sys.argv" not in source


def test_the_script_exits_non_zero_when_it_refuses(tmp_path):
    """End to end, as a subprocess, because the exit code is what a person
    piping it into something else sees."""
    import os
    import subprocess

    env = os.environ.copy()
    env["DATABASE_URL"] = SERVER_TEST_URL
    env["FILMBILL_ENV"] = "server-test"
    result = subprocess.run(
        ["python", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2, result.stdout
    assert "refusing to run" in result.stderr
    assert "filmbill_v2" in result.stderr or "server-test" in result.stderr


def test_the_seed_list_covers_every_buyer_case_the_acceptance_walks_through(
    script,
):
    """An Austrian chain, a German, a Swiss, an American and a private
    person — so the walkthrough can see each buyer type and each VIES
    outcome without typing six forms."""
    types = [entry[0] for entry in script.PARTIES]
    countries = {
        entry[3].get("tax_country") for entry in script.PARTIES
    } - {None}

    assert types.count("organisation") >= 4
    assert "department" in types and "person" in types
    assert {"AT", "DE", "CH", "US"} <= countries
    # Every name says it is seed data, so nobody mistakes one for a real
    # customer on the LAN instance if a dump ever travels.
    assert all("(seed)" in entry[1] for entry in script.PARTIES)


def test_the_script_is_idempotent_on_name(pg_db, monkeypatch, script):
    """Running it twice adds nothing.

    Driven against the real scratch database through the script's own `main`,
    with `SessionLocal` pointed at the test session — so this covers the
    actual insert path rather than a re-implementation of it.
    """
    import os

    from apps.api.models.company import Company
    from apps.api.models.party import Party
    from apps.api.services.number_series import seed_number_series

    from .company_factories import make_company

    company = make_company(pg_db, "Seed Target OG", address_country="AT")
    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()

    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.delenv("FILMBILL_ENV", raising=False)

    import apps.api.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: pg_db)
    # The script closes the session it is given; the fixture needs it after.
    monkeypatch.setattr(pg_db, "close", lambda: None)

    assert script.main() == 0
    first = pg_db.query(Party).filter(Party.company_id == company.id).count()
    assert first == len(script.PARTIES)

    assert script.main() == 0
    assert (
        pg_db.query(Party).filter(Party.company_id == company.id).count() == first
    ), "running it twice added rows"


def test_the_seeded_chain_resolves_the_way_the_acceptance_expects(
    pg_db, monkeypatch, script
):
    """Acceptance item 3, as a test: the person inherits the organisation's
    address and keeps her own name."""
    import os

    from apps.api.models.party import Party
    from apps.api.services import party_service
    from apps.api.services.number_series import seed_number_series

    from .company_factories import make_company

    company = make_company(pg_db, "Seed Target OG", address_country="AT")
    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.delenv("FILMBILL_ENV", raising=False)

    import apps.api.database as database

    monkeypatch.setattr(database, "SessionLocal", lambda: pg_db)
    monkeypatch.setattr(pg_db, "close", lambda: None)
    assert script.main() == 0

    person = (
        pg_db.query(Party)
        .filter(Party.company_id == company.id, Party.name == "Maria Huber (seed)")
        .one()
    )
    resolved = party_service.resolve(pg_db, person)

    assert resolved.contact_name.value == "Maria Huber (seed)"
    assert resolved.contact_name.source == "person"
    assert resolved.billing_address.source == "organisation"
    assert resolved.billing_address.value.city == "Wien"
    assert resolved.vat_id.value == "ATU16360407"
    assert resolved.vat_id.source == "organisation"
