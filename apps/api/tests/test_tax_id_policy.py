"""The check policy, and the combination somebody actually gets wrong.

Pure validation, so these are fast and exhaustive. The one that matters is
`max_check_age_days < recheck_interval_days`: it would guarantee that every
identifier spends part of each cycle unverified, and an unverified EU
business buyer is charged domestic VAT. Nobody chooses that on purpose, so
the refusal says the arithmetic out loud.
"""

import pytest

from apps.api.services.tax_id_policy import (
    DEFAULT_INITIAL_OFFSETS,
    DEFAULT_MAX_CHECK_AGE_DAYS,
    DEFAULT_RECHECK_INTERVAL_DAYS,
    INTERVAL_CHOICES,
    MAX_INITIAL_OFFSETS,
    PolicyError,
    TaxIdCheckPolicy,
    load_policy,
    save_policy,
    validate,
)


def test_the_defaults_are_the_documented_ones():
    """[Guessing], and written down so a change to them is a change to a
    stated decision rather than to a magic number (D62)."""
    policy = TaxIdCheckPolicy()

    assert policy.initial_check_offsets == (0, 1, 7)
    assert policy.recheck_interval_days == 90
    assert policy.max_check_age_days == 180
    # Twice the interval, so one missed cycle does not un-verify anybody and
    # two do.
    assert policy.max_check_age_days == 2 * policy.recheck_interval_days


def test_an_empty_policy_is_the_defaults():
    assert validate({}) == TaxIdCheckPolicy()


def test_an_age_shorter_than_the_interval_is_refused_with_the_arithmetic():
    """The combination somebody gets wrong. The refusal does the subtraction
    for them and says what it costs, because "invalid combination" would
    leave them guessing which half to change."""
    with pytest.raises(PolicyError) as caught:
        validate({"recheck_interval_days": 90, "max_check_age_days": 30})

    message = str(caught.value)
    assert "60 days of each cycle unverified" in message
    assert "charged domestic VAT" in message
    # And it offers both ways out.
    assert "at least 90 days" in message
    assert "at least every 30" in message


def test_an_age_equal_to_the_interval_is_allowed():
    """The boundary. Exactly equal means a check expires as its replacement
    is due, which is tight but not wrong."""
    policy = validate({"recheck_interval_days": 90, "max_check_age_days": 90})
    assert policy.max_check_age_days == 90


def test_turning_re_checking_off_makes_any_age_acceptable():
    """There is no cycle to fall out of. A self-hosted installation with no
    outbound internet should be able to turn scheduling off rather than
    accumulate `unavailable` rows for ever."""
    policy = validate({"recheck_interval_days": None, "max_check_age_days": 7})
    assert policy.recheck_interval_days is None
    assert policy.max_check_age_days == 7


def test_an_age_of_none_means_a_check_counts_for_ever():
    policy = validate({"max_check_age_days": None})
    assert policy.max_check_age_days is None


@pytest.mark.parametrize("interval", [choice for choice in INTERVAL_CHOICES if choice])
def test_every_offered_interval_is_accepted(interval):
    validate(
        {"recheck_interval_days": interval, "max_check_age_days": interval * 2}
    )


def test_an_interval_nobody_offered_is_refused_and_lists_the_choices():
    """The list is deliberately short: an interval somebody typed by hand is
    one nobody thought about."""
    with pytest.raises(PolicyError) as caught:
        validate({"recheck_interval_days": 45})

    assert "not one of the intervals offered" in str(caught.value)
    assert "7, 30, 90, 180, 365" in str(caught.value)


def test_an_unknown_setting_is_refused_rather_than_ignored():
    """A typo'd key that is silently dropped looks exactly like a setting
    that does not work."""
    with pytest.raises(PolicyError) as caught:
        validate({"recheck_interval_dayz": 90})

    assert "recheck_interval_dayz" in str(caught.value)
    assert "initial_check_offsets" in str(caught.value)


def test_an_empty_initial_series_is_refused_and_suggests_the_minimum():
    with pytest.raises(PolicyError) as caught:
        validate({"initial_check_offsets": []})
    assert "[0] means" in str(caught.value)


def test_too_many_initial_checks_are_refused_for_the_registrys_sake():
    """Not for ours. Registries publish concurrency limits and block
    addresses that exceed them, so more checks would mean fewer answers."""
    with pytest.raises(PolicyError) as caught:
        validate({"initial_check_offsets": list(range(MAX_INITIAL_OFFSETS + 1))})

    assert "block addresses that exceed them" in str(caught.value)


def test_an_out_of_order_series_is_refused_and_offers_the_sorted_one():
    with pytest.raises(PolicyError) as caught:
        validate({"initial_check_offsets": [7, 1, 0]})
    assert "Did you mean [0, 1, 7]" in str(caught.value)


def test_the_same_day_twice_is_refused():
    """It would ask the registry the same question twice for the same
    answer."""
    with pytest.raises(PolicyError) as caught:
        validate({"initial_check_offsets": [0, 1, 1, 7]})
    assert "same day twice" in str(caught.value)


@pytest.mark.parametrize("bad", [[0, "soon"], [0, 1.5], [0, True], [0, -1], [0, 500]])
def test_an_offset_that_is_not_a_usable_number_of_days_is_refused(bad):
    """`True` is in there on purpose: `isinstance(True, int)` is True in
    Python, so a boolean would otherwise sail through as the offset 1."""
    with pytest.raises(PolicyError):
        validate({"initial_check_offsets": bad})


def test_a_party_override_wins_over_the_company_interval():
    policy = validate({"recheck_interval_days": 90, "max_check_age_days": 180})

    assert policy.interval_for(None) == 90
    assert policy.interval_for(7) == 7
    # Zero is meaningless rather than "immediately", so it is treated as
    # absent — otherwise a stray 0 would schedule a check every zero days.
    assert policy.interval_for(0) == 90


@pytest.mark.realdb
def test_a_saved_policy_comes_back(pg_db):
    from .company_factories import make_company

    company = make_company(pg_db, "Policy OG", address_country="AT")
    pg_db.commit()

    save_policy(
        pg_db,
        company_id=company.id,
        raw={
            "initial_check_offsets": [0, 1],
            "recheck_interval_days": 30,
            "max_check_age_days": 60,
        },
    )
    pg_db.commit()

    loaded = load_policy(pg_db, company_id=company.id)
    assert loaded.initial_check_offsets == (0, 1)
    assert loaded.recheck_interval_days == 30
    assert loaded.max_check_age_days == 60


@pytest.mark.realdb
def test_unreadable_stored_data_falls_back_to_the_defaults(pg_db):
    """A policy row that somehow became invalid must not make every party
    page 500. The defaults are safe, and the settings screen will refuse to
    save the bad value the moment somebody opens it."""
    from .company_factories import make_company

    company = make_company(pg_db, "Policy OG", address_country="AT")
    company.tax_id_check_policy = {"recheck_interval_days": 45}
    pg_db.commit()

    assert load_policy(pg_db, company_id=company.id) == TaxIdCheckPolicy()


@pytest.mark.realdb
def test_a_company_with_no_policy_set_gets_the_defaults(pg_db):
    from .company_factories import make_company

    company = make_company(pg_db, "Policy OG", address_country="AT")
    pg_db.commit()

    assert load_policy(pg_db, company_id=company.id) == TaxIdCheckPolicy()
