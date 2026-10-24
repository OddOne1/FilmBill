"""The scheduler, the retry schedule, and change detection.

Against real Postgres throughout, because the three things that matter here
only exist there: `FOR UPDATE SKIP LOCKED`, the append-only triggers, and
the partial indexes the due-selection relies on.

**Two real connections** for the concurrency test. A mocked second worker
would prove the mock; two sessions against one database prove the lock. That
test is also the one the `SKIP LOCKED` mutation has to fail, and it is the
only row in the suite that only that guard protects — every other test here
passes with or without it.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.api.billing.parties.verification import (
    INVALID,
    NOT_CHECKABLE,
    NOT_CONFIGURED,
    UNAVAILABLE,
    VALID,
    VerificationResult,
)
from apps.api.models.party import PartyTaxId, PartyVatCheck
from apps.api.services import party_service, tax_id_checks, tax_service
from apps.api.services.number_series import seed_number_series
from apps.api.services.tax_id_policy import TaxIdCheckPolicy

from .company_factories import make_company, make_party, make_party_tax_id

pytestmark = pytest.mark.realdb


@pytest.fixture
def company(pg_db):
    row = make_company(pg_db, "Scheduler OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=row)
    seed_number_series(pg_db, company_id=row.id)
    pg_db.commit()
    return row


@pytest.fixture
def party(pg_db, company):
    row = make_party(pg_db, company, name="Berlin GmbH", tax_country="DE")
    pg_db.commit()
    return row


def a_tax_id(pg_db, company, party, *, value="DE123456789", due=None, **kwargs):
    """One identifier, primary only if the party has none yet.

    The first one claims primary and the rest do not — which is what the
    endpoint does, and what the partial unique index allows. The index
    caught this helper setting every identifier primary, which is the index
    doing exactly the job it exists for.
    """
    has_primary = (
        party_service.primary_tax_id(pg_db, party) is not None
        if kwargs.get("is_active", True)
        else True
    )
    row = make_party_tax_id(
        pg_db,
        company,
        party,
        value=value,
        country="DE",
        is_primary=kwargs.pop("is_primary", not has_primary),
        **kwargs,
    )
    row.next_check_at = due
    pg_db.commit()
    return row


def result(status=VALID, **kwargs):
    defaults = dict(
        provider="vies", value="DE123456789", kind="vat", country="DE",
        detail="a sentence",
    )
    defaults.update(kwargs)
    return VerificationResult(status=status, **defaults)


# ─── Due selection ─────────────────────────────────────────────────────────


def test_only_identifiers_that_are_due_are_selected(pg_db, company, party):
    now = datetime.now(timezone.utc)
    due = a_tax_id(pg_db, company, party, value="DE123456789", due=now - timedelta(hours=1))
    a_tax_id(pg_db, company, party, value="DE111111111", due=now + timedelta(days=1))
    a_tax_id(pg_db, company, party, value="DE222222222", due=None)

    selected = tax_id_checks.due_identifiers(pg_db, now=now)

    assert [row.id for row in selected] == [due.id]


def test_a_retired_identifier_is_never_selected(pg_db, company, party):
    """It keeps its checks as evidence for invoices already issued, but
    nobody is going to invoice on it again."""
    now = datetime.now(timezone.utc)
    a_tax_id(
        pg_db, company, party, value="DE123456789",
        due=now - timedelta(days=1), is_active=False,
    )

    assert tax_id_checks.due_identifiers(pg_db, now=now) == []


def test_the_oldest_due_is_taken_first(pg_db, company, party):
    """So a backlog drains in order rather than starving the identifiers
    that have waited longest."""
    now = datetime.now(timezone.utc)
    older = a_tax_id(
        pg_db, company, party, value="DE111111111", due=now - timedelta(days=5)
    )
    newer = a_tax_id(
        pg_db, company, party, value="DE222222222", due=now - timedelta(hours=1)
    )

    selected = tax_id_checks.due_identifiers(pg_db, now=now)

    assert [row.id for row in selected] == [older.id, newer.id]


def test_the_batch_is_bounded(pg_db, company, party):
    """An installation with 2000 customers spreads them over hours rather
    than opening 2000 connections to the Commission."""
    now = datetime.now(timezone.utc)
    for index in range(6):
        a_tax_id(
            pg_db, company, party, value=f"DE10000000{index}",
            due=now - timedelta(days=index + 1),
        )

    assert len(tax_id_checks.due_identifiers(pg_db, now=now, limit=3)) == 3


def test_two_workers_never_take_the_same_identifier(pg_db, pg_engine, company, party):
    """**The `SKIP LOCKED` test, with two real connections.**

    A second beat process exists during every deploy. Without `SKIP LOCKED`
    the second selection would either block on the first's rows or — with a
    plain `FOR UPDATE` and a short lock timeout — return them anyway, and
    every check would be made twice. Twice the load on a register that
    publishes concurrency limits and blocks addresses exceeding them.

    Two sessions against one database, because a mocked second worker would
    prove the mock. This is the only test in the file that fails when
    `SKIP LOCKED` is removed — every other one passes with or without it,
    which is exactly why it has to exist.

    **`lock_timeout` is load-bearing in the test itself.** Without
    `SKIP LOCKED` the second session BLOCKS on the first's rows rather than
    returning anything, so the test would hang instead of failing — and a
    hang is not a caught mutation (CLAUDE.md rule 12). A one-second lock
    timeout turns the block into an error, which is a real FAIL line.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    now = datetime.now(timezone.utc)
    first_id = a_tax_id(
        pg_db, company, party, value="DE111111111", due=now - timedelta(days=2)
    )
    second_id = a_tax_id(
        pg_db, company, party, value="DE222222222", due=now - timedelta(days=1)
    )

    # Worker one takes everything and holds the locks: its transaction stays
    # open for the duration of this test.
    taken_by_one = tax_id_checks.due_identifiers(pg_db, now=now)
    assert {row.id for row in taken_by_one} == {first_id.id, second_id.id}

    Second = sessionmaker(bind=pg_engine, expire_on_commit=False)
    other = Second()
    try:
        other.execute(text("SET LOCAL lock_timeout = '1s'"))
        taken_by_two = tax_id_checks.due_identifiers(other, now=now)
        # Nothing: worker one holds both rows, and worker two steps over
        # them rather than waiting or duplicating. If the selection ever
        # loses `SKIP LOCKED`, this raises a lock timeout instead.
        assert taken_by_two == []
    finally:
        other.rollback()
        other.close()

    # And once worker one is done, the rows are available again.
    pg_db.rollback()
    third = Second()
    try:
        assert len(tax_id_checks.due_identifiers(third, now=now)) == 2
    finally:
        third.rollback()
        third.close()


# ─── The initial series, then the interval ─────────────────────────────────


def test_a_new_identifier_is_scheduled_at_the_first_offset(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    policy = TaxIdCheckPolicy(initial_check_offsets=(0, 1, 7))

    tax_id_checks.schedule_initial(tax_id, policy=policy)

    assert tax_id.initial_checks_done == 0
    assert tax_id.next_check_at is not None
    # Offset 0 means "now", so it is due immediately.
    assert tax_id.next_check_at <= datetime.now(timezone.utc) + timedelta(seconds=5)


def test_a_kind_with_no_provider_is_never_scheduled(pg_db, company, party):
    """A US EIN would otherwise generate an attempt every quarter that can
    only ever return the same answer."""
    tax_id = make_party_tax_id(
        pg_db, company, party, kind="ein", value="123456789", country="US"
    )
    pg_db.commit()

    tax_id_checks.schedule_initial(tax_id, policy=TaxIdCheckPolicy())

    assert tax_id.next_check_at is None


def test_the_initial_series_runs_then_the_interval_applies(pg_db, company, party):
    """Three checks in the first week, then quarterly. The series is measured
    from the identifier's creation, so it does not stretch when a check runs
    late."""
    policy = TaxIdCheckPolicy(
        initial_check_offsets=(0, 1, 7), recheck_interval_days=90,
        max_check_age_days=180,
    )
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.schedule_initial(tax_id, policy=policy)

    for expected_done in (1, 2):
        tax_id_checks.record_check(
            pg_db, tax_id=tax_id, party=party, result=result(),
            trigger_kind="initial", policy=policy,
        )
        pg_db.commit()
        assert tax_id.initial_checks_done == expected_done
        # Still inside the week.
        assert tax_id.next_check_at < datetime.now(timezone.utc) + timedelta(days=8)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(),
        trigger_kind="initial", policy=policy,
    )
    pg_db.commit()

    assert tax_id.initial_checks_done == 3
    # Now on the quarterly schedule: at least 80 days out even with jitter.
    assert tax_id.next_check_at > datetime.now(timezone.utc) + timedelta(days=80)


def test_turning_the_interval_off_stops_scheduling_after_the_series(
    pg_db, company, party
):
    """A supported configuration: no outbound internet, so no point
    accumulating `unavailable` rows for ever."""
    policy = TaxIdCheckPolicy(
        initial_check_offsets=(0,), recheck_interval_days=None,
        max_check_age_days=None,
    )
    tax_id = a_tax_id(pg_db, company, party)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(), policy=policy
    )
    pg_db.commit()

    assert tax_id.next_check_at is None


def test_a_party_override_shortens_the_interval_for_that_party_only(
    pg_db, company, party
):
    policy = TaxIdCheckPolicy(
        initial_check_offsets=(0,), recheck_interval_days=365,
        max_check_age_days=730,
    )
    party.recheck_interval_days = 7
    pg_db.commit()
    tax_id = a_tax_id(pg_db, company, party)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(), policy=policy
    )
    pg_db.commit()

    assert tax_id.next_check_at < datetime.now(timezone.utc) + timedelta(days=20)


def test_not_checkable_settles_the_schedule_rather_than_retrying(
    pg_db, company, party
):
    """It is a fact about the world, not a failure. So the series is
    completed and nothing is scheduled — unlike `unavailable`, which is
    exactly a "try again"."""
    tax_id = make_party_tax_id(
        pg_db, company, party, kind="ein", value="123456789", country="US"
    )
    pg_db.commit()

    tax_id_checks.record_check(
        pg_db,
        tax_id=tax_id,
        party=party,
        result=result(status=NOT_CHECKABLE, provider="none", kind="ein"),
        policy=TaxIdCheckPolicy(),
    )
    pg_db.commit()

    assert tax_id.next_check_at is None
    assert tax_id.unavailable_streak == 0


# ─── `unavailable` is not an answer ────────────────────────────────────────


def test_an_unavailable_does_not_advance_the_initial_series(pg_db, company, party):
    """Otherwise a registry being down would walk an identifier through its
    whole initial series without ever having reached it, and then report it
    as fully checked."""
    policy = TaxIdCheckPolicy(initial_check_offsets=(0, 1, 7))
    tax_id = a_tax_id(pg_db, company, party)

    for _ in range(3):
        tax_id_checks.record_check(
            pg_db, tax_id=tax_id, party=party,
            result=result(status=UNAVAILABLE), policy=policy,
        )
        pg_db.commit()

    assert tax_id.initial_checks_done == 0
    assert tax_id.unavailable_streak == 3


def test_the_retry_schedule_is_six_hours_then_a_day_then_the_interval(
    pg_db, company, party
):
    """Two attempts close together, then back to the ordinary schedule. A
    registry down for six hours is usually down for a day, and hammering it
    is both rude and counter-productive against a published concurrency
    limit."""
    policy = TaxIdCheckPolicy(
        initial_check_offsets=(0,), recheck_interval_days=90,
        max_check_age_days=180,
    )
    tax_id = a_tax_id(pg_db, company, party)
    now = datetime.now(timezone.utc)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(status=UNAVAILABLE), policy=policy,
    )
    pg_db.commit()
    first = tax_id.next_check_at
    assert timedelta(hours=3) < first - now < timedelta(hours=9)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(status=UNAVAILABLE), policy=policy,
    )
    pg_db.commit()
    second = tax_id.next_check_at
    assert timedelta(hours=20) < second - now < timedelta(hours=28)

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(status=UNAVAILABLE), policy=policy,
    )
    pg_db.commit()
    # Out of retries: the ordinary interval, not another six hours for ever.
    assert tax_id.next_check_at > now + timedelta(days=80)


def test_an_unavailable_keeps_the_previous_verified_state(pg_db, company, party):
    """**The rule a government service's bad afternoon must not break.**

    A `valid` from yesterday followed by an `unavailable` today is still
    verified. Otherwise every customer would un-verify at once, and then
    re-verify an hour later, writing a change event each way.
    """
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    assert party_service.vat_is_verified(pg_db, party) is True

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=UNAVAILABLE)
    )
    pg_db.commit()

    assert party_service.vat_is_verified(pg_db, party) is True


def test_a_not_configured_also_keeps_the_previous_state(pg_db, company, party):
    """An operator who has not supplied a credential has said nothing about
    anybody's number."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(status=NOT_CONFIGURED, provider="abn"),
    )
    pg_db.commit()

    assert party_service.vat_is_verified(pg_db, party) is True


def test_an_invalid_does_un_verify(pg_db, company, party):
    """The contrast that makes the three tests above meaningful: a registry
    saying no is an answer, and it counts."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=INVALID)
    )
    pg_db.commit()

    assert party_service.vat_is_verified(pg_db, party) is False


# ─── Staleness ─────────────────────────────────────────────────────────────


def test_a_check_older_than_the_age_limit_stops_counting(pg_db, company, party):
    """A verification from 2019 is a historical fact, not a current one."""
    tax_id = a_tax_id(pg_db, company, party)
    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    assert party_service.vat_is_verified(pg_db, party) is True

    # Age the check. Written directly with SQL because the table is
    # append-only at the database level and that is the point of it — the
    # trigger is disabled for this one statement and put straight back.
    _age_check(pg_db, row, days=400)

    assert party_service.vat_is_verified(pg_db, party) is False
    # And no evidence is offered either, so the flag and the evidence cannot
    # disagree.
    assert party_service.tax_id_evidence(pg_db, party) is None


def test_an_age_limit_of_none_lets_a_check_count_for_ever(pg_db, company, party):
    from apps.api.services.tax_id_policy import save_policy

    save_policy(
        pg_db,
        company_id=company.id,
        raw={"recheck_interval_days": None, "max_check_age_days": None},
    )
    tax_id = a_tax_id(pg_db, company, party)
    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    _age_check(pg_db, row, days=4000)

    assert party_service.vat_is_verified(pg_db, party) is True


def _age_check(pg_db, row, *, days: int) -> None:
    """Backdate a check row, around the append-only trigger.

    The trigger is the reason a check is evidence, so it is disabled for one
    statement and restored immediately — and
    `test_the_append_only_triggers_survive_the_new_columns` in
    `test_party_tax_ids.py` is what proves it is back on.
    """
    from sqlalchemy import text

    when = datetime.now(timezone.utc) - timedelta(days=days)
    pg_db.execute(text("ALTER TABLE party_vat_checks DISABLE TRIGGER party_vat_checks_no_update_delete"))
    pg_db.execute(
        text("UPDATE party_vat_checks SET checked_at = :when WHERE id = :id"),
        {"when": when, "id": row.id},
    )
    pg_db.execute(text("ALTER TABLE party_vat_checks ENABLE TRIGGER party_vat_checks_no_update_delete"))
    pg_db.commit()


# ─── Change detection ──────────────────────────────────────────────────────


def test_the_first_check_of_an_identifier_is_never_a_change(pg_db, company, party):
    """"Status changed from nothing to valid" on every new customer would
    make the attention list useless on day one."""
    tax_id = a_tax_id(pg_db, company, party)

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    assert row.change_detected is None
    assert tax_id.needs_attention is False


def test_a_status_change_is_detected_and_flagged(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=INVALID)
    )
    pg_db.commit()

    assert row.change_detected[tax_id_checks.CHANGE_STATUS] == ["valid", "invalid"]
    assert tax_id.needs_attention is True
    assert "changed from “valid” to “invalid”" in tax_id.needs_attention_reason


def test_a_registered_name_change_is_detected(pg_db, company, party):
    """The company was renamed, or sold, or the number now belongs to
    somebody else."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(registered_name="Berlin GmbH"),
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(registered_name="Hamburg Media AG"),
    )
    pg_db.commit()

    assert row.change_detected[tax_id_checks.CHANGE_NAME] == [
        "Berlin GmbH",
        "Hamburg Media AG",
    ]
    assert "Hamburg Media AG" in tax_id.needs_attention_reason


def test_a_register_that_stops_disclosing_names_is_not_a_change(
    pg_db, company, party
):
    """That is a change in the register's policy, not in the company — and
    reporting it would fire on every customer of that member state on the
    same day."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(registered_name="Berlin GmbH"),
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(registered_name=None)
    )
    pg_db.commit()

    assert row.change_detected is None
    assert tax_id.needs_attention is False


def test_a_name_match_worsening_to_mismatch_is_detected(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(name_match="match")
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(name_match="mismatch")
    )
    pg_db.commit()

    assert tax_id_checks.CHANGE_NAME_MATCH in row.change_detected
    assert "no longer looks like" in tax_id.needs_attention_reason


def test_a_name_match_improving_is_not_a_change(pg_db, company, party):
    """An improvement needs nobody's attention."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(name_match="mismatch")
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(name_match="match")
    )
    pg_db.commit()

    assert row.change_detected is None


def test_losing_a_registration_is_detected(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(registration_active=True, gst_registered=True),
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party,
        result=result(registration_active=True, gst_registered=False),
    )
    pg_db.commit()

    assert tax_id_checks.CHANGE_REGISTRATION in row.change_detected
    assert "GST registration has ended" in tax_id.needs_attention_reason


def test_gaining_a_registration_is_not_a_change(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(gst_registered=False)
    )
    pg_db.commit()

    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(gst_registered=True)
    )
    pg_db.commit()

    assert row.change_detected is None


def test_an_identical_check_detects_nothing(pg_db, company, party):
    """The commonest case by far — a quarterly re-check that confirms what
    the last one said — must be silent."""
    tax_id = a_tax_id(pg_db, company, party)
    for _ in range(2):
        row = tax_id_checks.record_check(
            pg_db, tax_id=tax_id, party=party,
            result=result(registered_name="Berlin GmbH", name_match="match"),
        )
        pg_db.commit()

    assert row.change_detected is None
    assert tax_id.needs_attention is False


def test_an_unavailable_between_two_valids_is_not_two_changes(pg_db, company, party):
    """`valid` → `unavailable` → `valid` is one unreachable registry, not
    two changes. Comparing completed checks only is what makes that true."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=UNAVAILABLE)
    )
    pg_db.commit()
    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    assert row.change_detected is None
    assert tax_id.needs_attention is False


def test_a_later_check_does_not_clear_the_flag(pg_db, company, party):
    """Only an explicit acknowledgement does. A change that resolved itself
    still happened, and somebody should be able to see that it did."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=INVALID)
    )
    pg_db.commit()
    assert tax_id.needs_attention is True

    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()

    assert tax_id.needs_attention is True


def test_acknowledging_clears_the_flag(pg_db, company, party):
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=VALID)
    )
    pg_db.commit()
    row = tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=INVALID)
    )
    pg_db.commit()

    tax_id_checks.acknowledge(
        pg_db, tax_id=tax_id, party=party, check=row, note="Rang them, sorted."
    )
    pg_db.commit()

    assert tax_id.needs_attention is False
    assert tax_id.needs_attention_reason is None


def test_the_attention_list_is_per_company(pg_db, company, party):
    """Rule 6: another company's flagged identifiers are not in this one's
    list."""
    other = make_company(pg_db, "Other OG", address_country="AT")
    other_party = make_party(pg_db, other, name="Theirs")
    other_id = make_party_tax_id(
        pg_db, other, other_party, value="DE999999999", country="DE"
    )
    other_id.needs_attention = True
    tax_id = a_tax_id(pg_db, company, party)
    tax_id.needs_attention = True
    pg_db.commit()

    ours = tax_id_checks.needs_attention(pg_db, company_id=company.id)

    assert [row.id for row in ours] == [tax_id.id]


# ─── The evidence carried to documents ─────────────────────────────────────


def test_the_evidence_names_the_check_that_was_relied_on(pg_db, company, party):
    """P2 snapshots this onto a finalized document (D64): an invoice claiming
    a reverse charge should be able to say which lookup, on which day, with
    which consultation reference, it rested on."""
    tax_id = a_tax_id(pg_db, company, party)
    row = tax_id_checks.record_check(
        pg_db,
        tax_id=tax_id,
        party=party,
        result=result(
            status=VALID, qualified=True, consultation_ref="WAPIAAAA1"
        ),
    )
    pg_db.commit()

    evidence = party_service.tax_id_evidence(pg_db, party)

    assert evidence["check_id"] == str(row.id)
    assert evidence["tax_id_id"] == str(tax_id.id)
    assert evidence["value"] == "DE123456789"
    assert evidence["kind"] == "vat"
    assert evidence["qualified"] is True
    assert evidence["consultation_ref"] == "WAPIAAAA1"
    assert evidence["provider"] == "vies"
    # And the resolver carries it, so a document builder never has to ask
    # the service a second question.
    from apps.api.billing.parties import tax_inputs

    assert tax_inputs(party_service.resolve(pg_db, party))[
        "tax_id_evidence"
    ]["check_id"] == str(row.id)


def test_there_is_no_evidence_when_the_party_is_not_verified(pg_db, company, party):
    """The flag and the evidence are computed from the same two reads, so
    they cannot disagree."""
    tax_id = a_tax_id(pg_db, company, party)
    tax_id_checks.record_check(
        pg_db, tax_id=tax_id, party=party, result=result(status=INVALID)
    )
    pg_db.commit()

    assert party_service.vat_is_verified(pg_db, party) is False
    assert party_service.tax_id_evidence(pg_db, party) is None
