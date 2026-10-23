"""Number series: the engine, the counter, and the two ways it can go wrong.

CLAUDE.md rule 5 is one sentence — "gap-free numbering assigned only inside
the finalize transaction" — and the whole of this file is what it costs.

**Two failure modes, and they need different tests.**

  * A **duplicate** is a legal problem: §11 UStG wants an identifier issued
    once. It is held by `unique (series_id, number)` in the database, and the
    tests that reach it have to actually overlap.
  * A **gap** is an audit problem: a missing number is a document somebody
    has to account for. It is held by the counter only ever moving under
    `SELECT … FOR UPDATE`, and by `allocate_number` never committing — so a
    caller that rolls back consumes nothing.

**The riskiest rule here is back-dating**, allowed with a warning by Mathias's
decision of 2026-10-20 (I recommended refusing). On a `yearly` series, a date
in a closed period draws from THAT period's counter — the highest value it
already issued, plus one — and the current period's counter is untouched. Get
that wrong in the obvious way (take the number from `next_value`) and you get
both failure modes at once: a hole in this year's run and a 2025-shaped number
sitting in the middle of 2026's. `test_a_back_dated_allocation...` and
`test_mixed_concurrency...` are the two that catch it, and the mutation that
computes the earlier period from `next_value` is the one that proves they do.

**Environment**: real Postgres throughout (the `pg_*` fixtures, which now FAIL
rather than skip when `CI` is set). A MagicMock session has no row lock, no
unique constraint and no `MAX()`, so every assertion below would pass against
an engine with all three deleted.
"""

import threading
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from apps.api.models.company import CompanyRole
from apps.api.models.number_series import NumberAllocation, NumberSeries
from apps.api.models.site_settings import SiteSettings
from apps.api.services import number_series as engine

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_number_series,
    make_superadmin,
    make_user,
)


@pytest.fixture
def world(pg_db):
    """A Vienna company with one invoice series, and a second company.

    `Europe/Vienna` on purpose: the timezone test needs a zone that is not
    UTC, and the company this software was written for is in it.
    """
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)

    company = make_company(pg_db, "YON Studio OG", timezone="Europe/Vienna")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)

    other = make_company(pg_db, "Other GmbH", timezone="Europe/Vienna")
    other_owner = make_user(pg_db, "other@example.com")
    grant(pg_db, other, other_owner, CompanyRole.owner)

    series = make_number_series(pg_db, company, document_type="invoice")
    pg_db.commit()
    return {
        "company": company,
        "owner": owner,
        "other": other,
        "other_owner": other_owner,
        "series": series,
    }


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _allocate(pg_db, world, document_date, **kwargs):
    return engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=document_date,
        **kwargs,
    )


# ─── Formatting, as a unit. No database needed and none used ────────────────


@pytest.mark.parametrize(
    "pattern,value,padding,expected",
    [
        ("AR-{YYYY}-{SEQ}", 1, 4, "AR-2026-0001"),
        ("AR-{YY}-{SEQ}", 1, 4, "AR-26-0001"),
        ("AR-{YYYY}{MM}-{SEQ}", 7, 3, "AR-202603-007"),
        ("{SEQ}", 42, 6, "000042"),
        ("RE{SEQ}/{YYYY}", 9, 2, "RE09/2026"),
    ],
)
def test_the_tokens_substitute(pattern, value, padding, expected):
    assert (
        engine.format_number(
            pattern, value=value, document_date=date(2026, 3, 14), padding=padding
        )
        == expected
    )


def test_padding_is_a_minimum_and_the_number_grows():
    """10000 with padding 4 prints 10000.

    Never truncated and never wrapped: a truncated number is a DIFFERENT
    document's number (`0000` for 10000 collides with the next 0000) and a
    wrapped one is a duplicate. This is the one place in the whole engine
    where the obvious implementation — a fixed-width format — would silently
    issue the same identifier twice.
    """
    assert engine.format_number(
        "AR-{YYYY}-{SEQ}", value=10000, document_date=date(2026, 1, 1), padding=4
    ) == "AR-2026-10000"
    assert engine.format_number(
        "AR-{YYYY}-{SEQ}", value=123456, document_date=date(2026, 1, 1), padding=4
    ) == "AR-2026-123456"


# ─── Pattern validation ─────────────────────────────────────────────────────


def test_a_pattern_without_seq_is_refused():
    with pytest.raises(engine.InvalidPattern) as caught:
        engine.validate_pattern("AR-{YYYY}", "yearly")
    assert "{SEQ}" in str(caught.value)


def test_a_yearly_series_without_a_year_token_is_refused():
    """The rule with a reason: without a year token the counter goes back to 1
    every January and last year's numbers are issued again."""
    with pytest.raises(engine.InvalidPattern) as caught:
        engine.validate_pattern("AR-{SEQ}", "yearly")
    message = str(caught.value)
    assert "{YYYY}" in message and "{YY}" in message
    assert "January" in message


def test_a_continuous_series_without_a_year_token_is_fine():
    """`AR-{SEQ}` is a legitimate choice under "Continue without restart" —
    Mathias's decision of 2026-10-20 — and refusing it would make the two
    modes differ in a way nothing explains."""
    assert engine.validate_pattern("AR-{SEQ}", "never") == "AR-{SEQ}"


def test_a_continuous_series_may_still_print_the_year():
    """A year token under `never` prints the year; it does not restart the
    count. `AR-2027-0412` continuing from 2026 is the shape Mathias asked
    for."""
    assert engine.validate_pattern("AR-{YYYY}-{SEQ}", "never")


@pytest.mark.parametrize("pattern", ["AR-{YYY}-{SEQ}", "AR-{YEAR}-{SEQ}", "{seq}"])
def test_an_unknown_token_is_refused_rather_than_printed(pattern):
    """`{YYY}` is a typo, and a typo printed literally becomes part of every
    invoice number until somebody notices — by which time the numbers are
    out."""
    with pytest.raises(engine.InvalidPattern):
        engine.validate_pattern(pattern, "never")


# ─── Allocation: the ordinary path ──────────────────────────────────────────


def test_the_first_number_is_one(pg_db, world):
    plan = _allocate(pg_db, world, date(2026, 3, 14))
    assert plan.number == "AR-2026-0001"
    assert plan.value == 1
    assert plan.period == "2026"
    assert plan.warnings == []


def test_numbers_run_consecutively(pg_db, world):
    numbers = [_allocate(pg_db, world, date(2026, 3, 14 + n)).number for n in range(5)]
    assert numbers == [f"AR-2026-{n:04d}" for n in range(1, 6)]


def test_allocate_does_not_commit_and_a_rollback_consumes_nothing(pg_db, world):
    """The property that makes "gap-free" survive a failed finalize.

    A number taken by a transaction that then rolls back must not exist and
    must not have moved the counter. Asserted by rolling back and then
    allocating again from a fresh read — the second allocation has to be 1,
    not 2.
    """
    first = _allocate(pg_db, world, date(2026, 3, 14))
    assert first.number == "AR-2026-0001"

    pg_db.rollback()

    assert pg_db.query(NumberAllocation).count() == 0, (
        "allocate_number committed on its own; a rolled-back finalize would "
        "consume a number"
    )
    series = pg_db.query(NumberSeries).filter_by(id=world["series"].id).one()
    assert series.next_value == 1, "the counter moved despite the rollback"

    again = _allocate(pg_db, world, date(2026, 3, 15))
    assert again.number == "AR-2026-0001", "the rolled-back number was not reusable"


def test_an_unknown_document_type_is_a_named_error_not_a_crash(pg_db, world):
    with pytest.raises(engine.NumberSeriesMissing) as caught:
        engine.allocate_number(
            pg_db,
            company_id=world["company"].id,
            document_type="there_is_no_such_thing",
            document_date=date(2026, 3, 14),
        )
    assert "Number series" in str(caught.value)


def test_an_inactive_series_does_not_allocate(pg_db, world):
    world["series"].is_active = False
    pg_db.commit()

    with pytest.raises(engine.NumberSeriesMissing):
        _allocate(pg_db, world, date(2026, 3, 14))


# ─── The turn of the year ───────────────────────────────────────────────────


def test_a_yearly_series_restarts_at_one_in_january(pg_db, world):
    _allocate(pg_db, world, date(2026, 11, 2))
    _allocate(pg_db, world, date(2026, 12, 30))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2027, 1, 2))

    assert plan.number == "AR-2027-0001"
    assert plan.value == 1
    assert plan.starts_new_period is True


def test_a_continuous_series_keeps_counting_across_the_year(pg_db, world):
    world["series"].reset = "never"
    pg_db.commit()
    _allocate(pg_db, world, date(2026, 11, 2))
    _allocate(pg_db, world, date(2026, 12, 30))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2027, 1, 2))

    assert plan.number == "AR-2027-0003", (
        "a continuous series restarted at the turn of the year"
    )
    assert plan.value == 3
    assert plan.starts_new_period is False


def test_the_period_comes_from_the_companys_timezone_not_utc(pg_db, world):
    """31 December 23:30 UTC is already 1 January in Vienna.

    On a yearly series those are numbers from two different sequences, so
    reading the UTC date would put a January document into December's run —
    and December's run may already be closed and handed to the accountant.
    """
    moment = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)

    derived = engine.document_date_in_company_timezone(world["company"], moment)

    assert derived == date(2027, 1, 1), (
        f"Europe/Vienna read 2026-12-31 23:30 UTC as {derived}"
    )
    assert engine.period_key(derived) == "2027"


def test_the_same_instant_in_a_utc_company_stays_in_december(pg_db):
    """The control. Without it the test above would pass for an
    implementation that added a day to everything."""
    company = make_company(pg_db, "London Ltd", timezone="UTC")
    moment = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)

    assert engine.document_date_in_company_timezone(company, moment) == date(
        2026, 12, 31
    )


# ─── Back-dating: allowed, with a warning (Mathias, 2026-10-20) ─────────────


def test_a_back_dated_document_is_allowed_and_warns(pg_db, world):
    _allocate(pg_db, world, date(2026, 6, 1))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2026, 5, 1))

    assert plan.number == "AR-2026-0002", "a back-dated document was refused"
    codes = [warning.code for warning in plan.warnings]
    assert codes == [engine.OUT_OF_ORDER]
    warning = plan.warnings[0]
    assert warning.latest_document_date == date(2026, 6, 1)
    assert warning.latest_number == "AR-2026-0001"
    assert "2026-05-01" in warning.message and "2026-06-01" in warning.message


def test_a_document_dated_the_same_day_does_not_warn(pg_db, world):
    """The boundary. "Before" means strictly before — two invoices on one day
    is the ordinary case and warning about it would train people to ignore
    the warning."""
    _allocate(pg_db, world, date(2026, 6, 1))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2026, 6, 1))

    assert plan.warnings == []


def test_a_back_dated_document_in_an_earlier_period_uses_that_periods_counter(
    pg_db, world
):
    """**The riskiest rule in this part.**

    2025 reached 12. 2026 is on 3. A document dated in 2025 must be
    `AR-2025-0013` — 2025's own counter — and 2026's counter must not move.
    The obvious wrong implementation takes `next_value`, which would issue
    `AR-2025-0003` (a number 2025 already used, so a duplicate) AND leave a
    hole at 3 in 2026 (so a gap). Both failure modes from one mistake.
    """
    for day in range(1, 13):
        _allocate(pg_db, world, date(2025, 2, day))
    pg_db.commit()
    # 2026 opens and takes two.
    _allocate(pg_db, world, date(2026, 1, 10))
    _allocate(pg_db, world, date(2026, 1, 11))
    pg_db.commit()

    series_before = pg_db.query(NumberSeries).filter_by(id=world["series"].id).one()
    assert (series_before.current_period, series_before.next_value) == ("2026", 3)

    plan = _allocate(pg_db, world, date(2025, 11, 30))
    pg_db.commit()

    assert plan.number == "AR-2025-0013", (
        "a 2025 document did not draw from 2025's counter"
    )
    assert plan.value == 13
    assert plan.period == "2025"
    assert plan.warnings[0].code == engine.OUT_OF_ORDER

    pg_db.expire_all()
    series_after = pg_db.query(NumberSeries).filter_by(id=world["series"].id).one()
    assert (series_after.current_period, series_after.next_value) == ("2026", 3), (
        "a back-dated allocation moved the CURRENT period's counter; the next "
        "2026 document would skip a number"
    )

    assert _allocate(pg_db, world, date(2026, 1, 12)).number == "AR-2026-0003"


def test_an_earlier_period_with_no_history_starts_at_one(pg_db, world):
    """A company entering its first 2024 document in 2026. There is nothing
    in 2024, so 2024's counter starts where every counter starts."""
    _allocate(pg_db, world, date(2026, 1, 10))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2024, 7, 1))

    assert plan.number == "AR-2024-0001"
    assert plan.value == 1


def test_back_dating_a_continuous_series_takes_the_next_number_and_warns(
    pg_db, world
):
    """Under `never` there is one counter and the date does not choose it.
    The number is simply the next one; the warning says it is out of order."""
    world["series"].reset = "never"
    pg_db.commit()
    _allocate(pg_db, world, date(2026, 6, 1))
    _allocate(pg_db, world, date(2026, 6, 2))
    pg_db.commit()

    plan = _allocate(pg_db, world, date(2025, 3, 1))

    assert plan.number == "AR-2025-0003"
    assert plan.value == 3
    assert plan.warnings[0].code == engine.OUT_OF_ORDER


def test_back_dating_a_continuous_series_never_drags_the_period_backwards(
    pg_db, world
):
    """`current_period` only ever moves forward.

    It is not read under `never`, but it decides where a LATER switch to
    "restart every year" takes effect — and a boundary in the past is a
    boundary that has already gone by, which would restart the counter on the
    very next document.
    """
    world["series"].reset = "never"
    pg_db.commit()
    _allocate(pg_db, world, date(2026, 6, 1))
    pg_db.commit()

    _allocate(pg_db, world, date(2024, 1, 1))
    pg_db.commit()

    pg_db.expire_all()
    series = pg_db.query(NumberSeries).filter_by(id=world["series"].id).one()
    assert series.current_period == "2026"


# ─── Switching modes ────────────────────────────────────────────────────────


def test_yearly_to_continuous_keeps_the_counter_where_it_is(pg_db, world):
    """No gap and no repeat: the next number is the one the yearly series
    would have issued."""
    for day in (1, 2, 3):
        _allocate(pg_db, world, date(2026, 4, day))
    pg_db.commit()

    world["series"].reset = "never"
    pg_db.commit()

    assert _allocate(pg_db, world, date(2026, 4, 4)).number == "AR-2026-0004"


def test_continuous_to_yearly_takes_effect_at_the_next_boundary(pg_db, world):
    """Not immediately — the current period carries on from where it is.

    A series that has issued 412 numbers continuously and is then set to
    restart annually must not jump back to 1 in the middle of the year: that
    would re-issue numbers 1..412 inside the same year token. 2027 is where
    it restarts.
    """
    world["series"].reset = "never"
    pg_db.commit()
    for day in range(1, 6):
        _allocate(pg_db, world, date(2026, 4, day))
    pg_db.commit()

    world["series"].reset = "yearly"
    pg_db.commit()

    carried_on = _allocate(pg_db, world, date(2026, 4, 20))
    assert carried_on.number == "AR-2026-0006", (
        "switching to yearly restarted the counter mid-year"
    )
    assert carried_on.starts_new_period is False
    pg_db.commit()

    new_year = _allocate(pg_db, world, date(2027, 1, 2))
    assert new_year.number == "AR-2027-0001"
    assert new_year.starts_new_period is True


def test_neither_switch_can_re_issue_an_allocated_number(pg_db, world):
    """Across a switch in each direction, every number is distinct.

    The database holds this (`unique (series_id, number)`); this reads the
    table afterwards so a change that started re-issuing would show as a
    duplicate here rather than as a constraint error somewhere nobody looks.
    """
    for day in range(1, 4):
        _allocate(pg_db, world, date(2026, 5, day))
    world["series"].reset = "never"
    pg_db.flush()
    for day in range(4, 7):
        _allocate(pg_db, world, date(2026, 5, day))
    world["series"].reset = "yearly"
    pg_db.flush()
    for day in range(7, 10):
        _allocate(pg_db, world, date(2026, 5, day))
    pg_db.commit()

    numbers = [row.number for row in pg_db.query(NumberAllocation).all()]
    assert len(numbers) == 9
    assert len(set(numbers)) == 9, f"a number was issued twice: {sorted(numbers)}"
    assert sorted(numbers) == [f"AR-2026-{n:04d}" for n in range(1, 10)]


# ─── Collisions ─────────────────────────────────────────────────────────────


def test_a_number_already_issued_is_refused_rather_than_duplicated(pg_db, world):
    """A `never` series back-dated onto a number that year already used.

    2025 ran to 10. The series is then continuous and sits at 5. A document
    dated 2025 would spell `AR-2025-0005`, which exists. Refused by name —
    never a second row, and never a silent 500.

    This is the sharp edge of allowing out-of-order dates, and it is reported
    rather than smoothed over: retrying with 6, 7, 8 … would walk the counter
    past 10 and leave a five-number gap, which is the other thing rule 5
    forbids.
    """
    for day in range(1, 11):
        _allocate(pg_db, world, date(2025, 3, day))
    pg_db.commit()

    world["series"].reset = "never"
    world["series"].next_value = 5
    pg_db.commit()

    with pytest.raises(engine.NumberCollision) as caught:
        _allocate(pg_db, world, date(2025, 12, 1))

    assert caught.value.number == "AR-2025-0005"
    pg_db.rollback()
    assert pg_db.query(NumberAllocation).count() == 10, "a duplicate was written"


def test_the_transaction_survives_a_collision(pg_db, world):
    """The SAVEPOINT. Without it the caller's transaction is aborted and the
    409 it wants to report cannot be serialised — every later statement
    raises `PendingRollbackError` instead."""
    for day in range(1, 11):
        _allocate(pg_db, world, date(2025, 3, day))
    pg_db.commit()
    world["series"].reset = "never"
    world["series"].next_value = 5
    pg_db.commit()

    with pytest.raises(engine.NumberCollision):
        _allocate(pg_db, world, date(2025, 12, 1))

    # The session is still usable — this is the assertion, not the query.
    assert pg_db.query(func.count(NumberAllocation.id)).scalar() == 10


# ─── Concurrency ────────────────────────────────────────────────────────────


def _allocate_in_threads(pg_engine, company_id, dates, *, threads, per_thread):
    """`threads` workers each take `per_thread` numbers, each in its own session.

    One session per thread and a commit per allocation: the row lock is held
    only for the allocation itself, which is the shape production has
    (`get_db` opens a session per request) and the shape in which the lock
    can actually be contended. A single shared session would serialise
    everything inside one transaction and prove nothing.
    """
    Session = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    ready = threading.Barrier(threads)
    taken: list[tuple[str, int, str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int):
        session = Session()
        try:
            ready.wait(timeout=60)
            for step in range(per_thread):
                document_date = dates[(index + step) % len(dates)]
                plan = engine.allocate_number(
                    session,
                    company_id=company_id,
                    document_type="invoice",
                    document_date=document_date,
                )
                session.commit()
                with lock:
                    taken.append((plan.number, plan.value, plan.period))
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            session.rollback()
            with lock:
                errors.append(exc)
        finally:
            session.close()

    workers = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(threads)
    ]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=180)

    return taken, errors


def test_twenty_threads_taking_fifty_numbers_each_produce_exactly_one_to_a_thousand(
    pg_engine, pg_db, world
):
    """1000 numbers, no duplicate, no gap. The whole of rule 5 in one test.

    `SELECT … FOR UPDATE` is what makes it true: without it two threads read
    the same `next_value`, both format the same number, and one of them loses
    to the unique constraint — which shows up here as fewer than 1000
    allocations AND an error, not as a silent duplicate. The mutation that
    removes `with_for_update()` is the one this test exists for.
    """
    company_id = world["company"].id
    pg_db.commit()

    taken, errors = _allocate_in_threads(
        pg_engine, company_id, [date(2026, 6, 1)], threads=20, per_thread=50
    )

    assert not errors, f"{len(errors)} allocation(s) failed: {errors[:3]}"
    assert len(taken) == 1000, f"{len(taken)} numbers taken, expected 1000"

    values = sorted(value for _, value, _ in taken)
    assert values == list(range(1, 1001)), (
        "the sequence is not 1..1000 — "
        f"{len(set(values))} distinct values, lowest {values[0]}, "
        f"highest {values[-1]}"
    )

    numbers = [number for number, _, _ in taken]
    assert len(set(numbers)) == 1000, "a number was issued twice"

    pg_db.expire_all()
    stored = pg_db.query(NumberAllocation).count()
    assert stored == 1000, f"{stored} rows for 1000 allocations"
    series = pg_db.query(NumberSeries).filter_by(id=world["series"].id).one()
    assert series.next_value == 1001


def test_mixed_concurrency_keeps_every_period_distinct_and_gap_free(
    pg_engine, pg_db, world
):
    """Threads allocating into the current period AND into two closed ones.

    The case the back-dating rule has to survive under contention: the
    current period's counter lives on the series row, each closed period's
    counter is `MAX(value)` over that period's own allocations, and both are
    read under the SAME row lock. Compute the closed period's value from
    `series.next_value` instead and this test fails — either as a duplicate
    (the constraint refuses it) or as a sequence with holes in it.

    2025 and 2024 are seeded with history first, so the earlier-period path
    is incrementing a real counter rather than starting from empty.
    """
    for day in range(1, 6):
        _allocate(pg_db, world, date(2025, 2, day))
    for day in range(1, 4):
        _allocate(pg_db, world, date(2024, 2, day))
    _allocate(pg_db, world, date(2026, 1, 5))
    pg_db.commit()

    dates = [
        date(2026, 6, 1),
        date(2025, 6, 1),
        date(2024, 6, 1),
        date(2026, 7, 1),
    ]
    taken, errors = _allocate_in_threads(
        pg_engine, world["company"].id, dates, threads=12, per_thread=20
    )

    assert not errors, f"{len(errors)} allocation(s) failed: {errors[:3]}"
    assert len(taken) == 240

    pg_db.expire_all()
    for period, already in (("2026", 1), ("2025", 5), ("2024", 3)):
        values = sorted(
            row.value
            for row in pg_db.query(NumberAllocation)
            .filter(NumberAllocation.period == period)
            .all()
        )
        assert values == list(range(1, len(values) + 1)), (
            f"period {period} is not gap-free: {values}"
        )
        assert len(values) > already, f"period {period} took no new numbers"

    numbers = [row.number for row in pg_db.query(NumberAllocation).all()]
    assert len(set(numbers)) == len(numbers), "a number was issued twice"


def test_a_thread_that_rolls_back_leaves_no_gap(pg_engine, pg_db, world):
    """A failed finalize in the middle of a run.

    The rolled-back allocation consumes nothing, so the numbers that follow
    it close up behind it rather than skipping one — which is what makes
    "gap-free" a property of the sequence and not of the happy path.
    """
    Session = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    company_id = world["company"].id
    pg_db.commit()

    for _ in range(3):
        session = Session()
        engine.allocate_number(
            session,
            company_id=company_id,
            document_type="invoice",
            document_date=date(2026, 6, 1),
        )
        session.commit()
        session.close()

    doomed = Session()
    plan = engine.allocate_number(
        doomed,
        company_id=company_id,
        document_type="invoice",
        document_date=date(2026, 6, 1),
    )
    assert plan.value == 4
    doomed.rollback()
    doomed.close()

    session = Session()
    after = engine.allocate_number(
        session,
        company_id=company_id,
        document_type="invoice",
        document_date=date(2026, 6, 1),
    )
    session.commit()
    session.close()

    assert after.value == 4, (
        f"the rolled-back allocation consumed a number; next was {after.value}"
    )

    pg_db.expire_all()
    values = sorted(row.value for row in pg_db.query(NumberAllocation).all())
    assert values == [1, 2, 3, 4]


# ─── Seeding ────────────────────────────────────────────────────────────────


def test_a_new_company_gets_every_default_series(pg_client, pg_db):
    """Through the endpoint, because `create_company` is what seeds them and
    a company that cannot number its first invoice is a company that has to
    be configured on the day it issues one."""
    root = make_superadmin(pg_db, "root@example.com")
    pg_db.add(SiteSettings(org_name="YON", require_2fa=False))
    pg_db.commit()

    response = pg_client.post(
        "/companies",
        headers=auth_headers(root),
        json={"legal_name": "Brand New GmbH"},
    )
    assert response.status_code == 201, response.text
    company_id = response.json()["id"]

    rows = (
        pg_db.query(NumberSeries)
        .filter(NumberSeries.company_id == uuid.UUID(company_id))
        .all()
    )
    # The eight DOCUMENT series plus the two party ones (P1b): a customer
    # and a supplier number use the same machinery, because the promise —
    # a number issued once, from a counter a user can re-point — is the
    # same one, and a second mechanism for it would be a second place for
    # that promise to break.
    assert {row.document_type for row in rows} == {
        document_type
        for document_type, _, _ in engine.DEFAULT_SERIES + engine.PARTY_SERIES
    }
    by_type = {row.document_type: row for row in rows}
    assert by_type["invoice"].pattern == "AR-{YYYY}-{SEQ}"
    assert by_type["invoice"].reset == "yearly"
    assert by_type["invoice"].padding == 4
    assert all(row.is_active for row in rows)


def test_seeding_twice_adds_nothing(pg_db, world):
    """Idempotent on document type, so the migration's backfill and
    `create_company` cannot race to create the same row."""
    before = pg_db.query(func.count(NumberSeries.id)).scalar()
    engine.seed_number_series(pg_db, company_id=world["company"].id)
    pg_db.commit()

    created = engine.seed_number_series(pg_db, company_id=world["company"].id)
    pg_db.commit()

    assert created == []
    assert pg_db.query(func.count(NumberSeries.id)).scalar() == before + 9, (
        "the fixture's hand-made invoice series was duplicated or the seed "
        "count changed"
    )


# ─── The endpoints ──────────────────────────────────────────────────────────


def test_the_listing_shows_the_seeded_series(pg_client, pg_db, world):
    engine.seed_number_series(pg_db, company_id=world["company"].id)
    pg_db.commit()

    response = pg_client.get("/company/number-series", headers=_headers(world))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == len(engine.DEFAULT_SERIES) + len(engine.PARTY_SERIES)
    invoice = next(entry for entry in body if entry["document_type"] == "invoice")
    assert invoice["pattern"] == "AR-{YYYY}-{SEQ}"
    assert invoice["reset"] == "yearly"
    assert invoice["allocated_count"] == 0
    assert invoice["latest_number"] is None
    # A customer number does not restart and carries no year token: one that
    # restarted every January would give two customers the same number.
    customer = next(entry for entry in body if entry["document_type"] == "customer")
    assert customer["pattern"] == "K-{SEQ}"
    assert customer["reset"] == "never"


def test_the_preview_allocates_nothing(pg_client, pg_db, world):
    """Called twice, the same answer, and no row either time. A preview that
    consumed a number would make every page load a gap."""
    body = {
        "series_id": str(world["series"].id),
        "pattern": "AR-{YYYY}-{SEQ}",
        "reset": "yearly",
        "padding": 4,
        "document_date": "2026-03-14",
    }
    first = pg_client.post(
        "/company/number-series/preview", headers=_headers(world), json=body
    )
    second = pg_client.post(
        "/company/number-series/preview", headers=_headers(world), json=body
    )

    assert first.status_code == 200, first.text
    assert first.json()["number"] == "AR-2026-0001"
    assert second.json()["number"] == "AR-2026-0001"
    assert pg_db.query(NumberAllocation).count() == 0
    pg_db.expire_all()
    assert (
        pg_db.query(NumberSeries).filter_by(id=world["series"].id).one().next_value == 1
    )


def test_the_preview_without_a_date_uses_the_companys_today(pg_client, world):
    """No `document_date` means today where the company is, which the engine
    derives — the web app is never asked what day it is."""
    response = pg_client.post(
        "/company/number-series/preview",
        headers=_headers(world),
        json={
            "series_id": str(world["series"].id),
            "pattern": "AR-{YYYY}-{SEQ}",
            "reset": "yearly",
            "padding": 4,
        },
    )

    assert response.status_code == 200, response.text
    today = engine.document_date_in_company_timezone(world["company"])
    assert response.json()["number"] == f"AR-{today.year}-0001"
    assert response.json()["period"] == f"{today.year}"


def test_the_preview_with_an_earlier_date_carries_the_out_of_order_warning(
    pg_client, pg_db, world
):
    """How somebody can see the consequence of a back-dated document before
    P2 has a finalize screen to show it in."""
    _allocate(pg_db, world, date(2026, 6, 1))
    pg_db.commit()

    response = pg_client.post(
        "/company/number-series/preview",
        headers=_headers(world),
        json={
            "series_id": str(world["series"].id),
            "pattern": "AR-{YYYY}-{SEQ}",
            "reset": "yearly",
            "padding": 4,
            "document_date": "2025-12-31",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["number"] == "AR-2025-0001"
    assert [w["code"] for w in body["warnings"]] == [engine.OUT_OF_ORDER]
    assert body["warnings"][0]["latest_number"] == "AR-2026-0001"
    assert body["warnings"][0]["latest_document_date"] == "2026-06-01"
    assert pg_db.query(NumberAllocation).count() == 1, "the preview allocated"


def test_previewing_an_unsaved_pattern_needs_no_series(pg_client, world):
    """The create form: there is no counter and no history yet, so the
    preview is value 1."""
    response = pg_client.post(
        "/company/number-series/preview",
        headers=_headers(world),
        json={
            "pattern": "XX-{YY}/{SEQ}",
            "reset": "never",
            "padding": 2,
            "document_date": "2026-03-14",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["number"] == "XX-26/01"


def test_previewing_a_yearly_pattern_without_a_year_token_is_refused(
    pg_client, world
):
    response = pg_client.post(
        "/company/number-series/preview",
        headers=_headers(world),
        json={"pattern": "AR-{SEQ}", "reset": "yearly", "padding": 4},
    )

    assert response.status_code == 422, response.text
    assert "{YYYY}" in response.json()["detail"]


def test_previewing_another_companys_series_is_404(pg_client, pg_db, world):
    """`series_id` travels in the BODY here, so the path-substituting
    isolation suite cannot reach it — this is its case."""
    theirs = make_number_series(pg_db, world["other"], document_type="quote")
    pg_db.commit()

    response = pg_client.post(
        "/company/number-series/preview",
        headers=_headers(world),
        json={
            "series_id": str(theirs.id),
            "pattern": "AR-{YYYY}-{SEQ}",
            "reset": "yearly",
            "padding": 4,
        },
    )

    assert response.status_code == 404, response.text


def test_creating_a_yearly_series_without_a_year_token_is_refused(
    pg_client, pg_db, world
):
    response = pg_client.post(
        "/company/number-series",
        headers=_headers(world),
        json={
            "document_type": "dunning",
            "name": "Mahnungen",
            "pattern": "MA-{SEQ}",
            "reset": "yearly",
        },
    )

    assert response.status_code == 422, response.text
    assert "January" in response.json()["detail"]
    assert pg_db.query(NumberSeries).filter_by(document_type="dunning").count() == 0


def test_creating_a_continuous_series_without_a_year_token_is_accepted(
    pg_client, world
):
    response = pg_client.post(
        "/company/number-series",
        headers=_headers(world),
        json={
            "document_type": "dunning",
            "name": "Mahnungen",
            "pattern": "MA-{SEQ}",
            "reset": "never",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["pattern"] == "MA-{SEQ}"


def test_a_second_active_series_for_one_document_type_is_refused(pg_client, world):
    """`allocate_number` asks for THE active series; two of them would make
    which one a document gets depend on row order."""
    response = pg_client.post(
        "/company/number-series",
        headers=_headers(world),
        json={
            "document_type": "invoice",
            "name": "A second one",
            "pattern": "ZZ-{YYYY}-{SEQ}",
        },
    )

    assert response.status_code == 409, response.text
    assert "already has an active series" in response.json()["detail"]


def test_an_inactive_series_does_not_block_a_new_active_one(pg_client, pg_db, world):
    """Which is why the index is partial: a superseded series is kept, and
    keeping two of them must stay possible."""
    world["series"].is_active = False
    pg_db.commit()

    response = pg_client.post(
        "/company/number-series",
        headers=_headers(world),
        json={
            "document_type": "invoice",
            "name": "The new one",
            "pattern": "RE-{YYYY}-{SEQ}",
        },
    )

    assert response.status_code == 201, response.text


def test_lowering_next_value_past_an_issued_number_is_refused(
    pg_client, pg_db, world
):
    for day in range(1, 6):
        _allocate(pg_db, world, date(2026, 3, day))
    pg_db.commit()

    response = pg_client.patch(
        f"/company/number-series/{world['series'].id}",
        headers=_headers(world),
        json={"next_value": 3},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "AR-2026-0003" in detail
    assert "cannot be below 6" in detail
    pg_db.expire_all()
    assert (
        pg_db.query(NumberSeries).filter_by(id=world["series"].id).one().next_value == 6
    )


def test_raising_next_value_is_allowed(pg_client, pg_db, world):
    """A deliberate gap is a decision somebody is entitled to take — a
    company that reserved a block of numbers in its old system."""
    _allocate(pg_db, world, date(2026, 3, 1))
    pg_db.commit()

    response = pg_client.patch(
        f"/company/number-series/{world['series'].id}",
        headers=_headers(world),
        json={"next_value": 500},
    )

    assert response.status_code == 200, response.text
    assert response.json()["next_value"] == 500


def test_a_pattern_change_that_would_re_issue_a_number_is_refused_by_name(
    pg_client, pg_db, world
):
    """`AR-{YYYY}-{SEQ}` → `AR-{SEQ}` on a continuous series whose earlier
    numbers were already spelled that way. Named, never duplicated."""
    world["series"].reset = "never"
    world["series"].pattern = "AR-{SEQ}"
    pg_db.commit()
    for day in range(1, 4):
        _allocate(pg_db, world, date(2026, 3, day))
    pg_db.commit()
    # Walk the counter back onto an issued number WITHOUT the router's help,
    # so the pattern check below is what refuses rather than the next_value
    # check.
    world["series"].next_value = 2
    pg_db.commit()

    response = pg_client.patch(
        f"/company/number-series/{world['series'].id}",
        headers=_headers(world),
        json={"padding": 4},
    )

    assert response.status_code == 409, response.text
    assert "AR-0002" in response.json()["detail"] or "0002" in response.json()["detail"]


def test_the_endpoints_need_the_number_series_permission(pg_client, pg_db, world):
    """An accountant can read the books and cannot re-point the numbering.

    Changing a number series is a structural act, not an accounting one —
    rule 5's gap-free promise is what is at stake — so it is owner/admin
    only. 404, like every other refusal in this layer.
    """
    accountant = make_user(pg_db, "accountant@example.com")
    grant(pg_db, world["company"], accountant, CompanyRole.accountant)
    pg_db.commit()

    headers = auth_headers(accountant, world["company"])
    assert pg_client.get("/company/number-series", headers=headers).status_code == 404
    assert (
        pg_client.post(
            "/company/number-series",
            headers=headers,
            json={"document_type": "letter", "name": "x", "pattern": "BR-{SEQ}"},
        ).status_code
        == 404
    )


# ─── The migration's backfill ───────────────────────────────────────────────
#
# Driven as a subprocess against a scratch database at revision 0007, which is
# how production and CI run alembic. The migration carries its own copy of the
# eight defaults rather than importing the service — see its docstring — so
# what is checked here is the EFFECT, and the test above
# (`test_a_new_company_gets_the_eight_default_series`) checks the service's.
# Two tests of two behaviours, rather than one test of one shared literal that
# would stop being true the day the defaults legitimately change.


def _scratch_database(base_url: str):
    from urllib.parse import urlsplit, urlunsplit

    name = f"filmbill_mig_{uuid.uuid4().hex[:12]}"

    def _with(db: str) -> str:
        return urlunsplit(urlsplit(base_url)._replace(path=f"/{db}"))

    from sqlalchemy import create_engine, text

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
def at_revision_0007(pg_engine):
    """A fresh database at the revision before number series existed."""
    import os

    from sqlalchemy import create_engine, text

    name, url, admin = _scratch_database(os.environ["DATABASE_URL"])
    result = _alembic(url, "upgrade", "0007_bank_account_iban_unique")
    if result.returncode != 0:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        pytest.fail(f"could not migrate to 0007:\n{result.stderr}")

    engine = create_engine(url)
    try:
        yield engine, url
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_the_migration_backfills_existing_companies(at_revision_0007):
    """A company that predates number series gets all eight.

    The server-test instance and Mathias's dev database both have one, and a
    company that cannot number its first invoice is a company that has to be
    configured on the day it issues one.
    """
    from sqlalchemy import text

    # `db_engine`, not `engine` — `engine` is this module's alias for the
    # number-series service, and shadowing it here would be the kind of
    # confusion that makes a later edit test the wrong thing.
    db_engine, url = at_revision_0007
    company_id = uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(:id, 'Pre-existing OG', 'EUR', 'de', 1, 'Europe/Vienna', "
                "'[]', false)"
            ),
            {"id": company_id},
        )

    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT document_type, pattern, reset, padding, next_value, "
                "current_period, is_active FROM number_series "
                "WHERE company_id = :id ORDER BY document_type"
            ),
            {"id": company_id},
        ).fetchall()

    # The eight DOCUMENT series plus the two party ones (P1b): a customer
    # and a supplier number use the same machinery, because the promise —
    # a number issued once, from a counter a user can re-point — is the
    # same one, and a second mechanism for it would be a second place for
    # that promise to break.
    assert {row.document_type for row in rows} == {
        document_type
        for document_type, _, _ in engine.DEFAULT_SERIES + engine.PARTY_SERIES
    }
    by_type = {row.document_type: row for row in rows}
    assert by_type["invoice"].pattern == "AR-{YYYY}-{SEQ}"
    assert by_type["quote"].pattern == "AN-{YYYY}-{SEQ}"
    assert by_type["letter"].pattern == "BR-{YYYY}-{SEQ}"
    # The eight DOCUMENT series restart yearly; the two party ones never do
    # (a customer number that restarted would be reissued). Asserted per
    # group rather than over all of them, which is what made this fail
    # usefully when P1b added the second kind.
    document_rows = [
        row for row in rows if row.document_type not in ("customer", "supplier")
    ]
    party_rows = [
        row for row in rows if row.document_type in ("customer", "supplier")
    ]
    assert len(document_rows) == 8 and len(party_rows) == 2
    assert all(row.reset == "yearly" for row in document_rows)
    assert all(row.reset == "never" for row in party_rows)
    assert all("{YYYY}" in row.pattern for row in document_rows)
    assert all("{YYYY}" not in row.pattern for row in party_rows)
    assert all(row.padding == 4 for row in rows)
    assert all(row.next_value == 1 for row in rows)
    assert all(row.current_period is None for row in rows)
    assert all(row.is_active for row in rows)


def test_the_backfill_is_idempotent(at_revision_0007):
    """`ON CONFLICT DO NOTHING`, so a half-seeded database gains only what is
    missing rather than failing on the partial unique index."""
    from sqlalchemy import text

    db_engine, url = at_revision_0007
    company_id = uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO companies (id, legal_name, default_currency, "
                "default_language, fiscal_year_start_month, timezone, "
                "require_2fa_roles, tax_advisor_reports) VALUES "
                "(:id, 'Pre-existing OG', 'EUR', 'de', 1, 'UTC', '[]', false)"
            ),
            {"id": company_id},
        )

    assert _alembic(url, "upgrade", "head").returncode == 0
    # Down and up again: the table is dropped and recreated, so this proves
    # the backfill survives being run a second time on the same company.
    assert _alembic(url, "downgrade", "0007_bank_account_iban_unique").returncode == 0
    result = _alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    with db_engine.connect() as conn:
        types = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT document_type FROM number_series "
                    "WHERE company_id = :id"
                ),
                {"id": company_id},
            )
        }
    # Migration 0008's own eight, plus the two migration 0012 backfills — a
    # customer and a supplier number (P1b). Asserted as a SET rather than a
    # count, so the next migration that adds one says which.
    assert types == {
        "quote",
        "order_confirmation",
        "delivery_note",
        "invoice",
        "credit_note",
        "self_billing",
        "dunning",
        "letter",
        "customer",
        "supplier",
    }
