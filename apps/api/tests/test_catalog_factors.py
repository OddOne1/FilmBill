"""The multi-day rental factor lookup — SCOPE §4.3, and a `[Guessing]` rule.

The table in every assertion below is the one the A1 prompt specified:

    1 day → 1.0 · 2 → 1.5 · 3 → 2.0 · 5 → 2.8 · 7 → 3.5

and the cases are the ones it named, because the rule is a guess and these
numbers are what Mathias has to look at to agree or disagree with it (SCOPE
D71). The four that matter:

  * 5 days → 2.8. An exact entry.
  * 4 days → 2.0. **Down to the 3-day entry, not up to the 5-day one.**
  * 6 days → 2.8. Same rule, and the one a client queries.
  * 9 days → 3.5. Above the table: the highest entry, not an extrapolation.

No interpolation anywhere: 6 days is 2.8 and never 3.15.
"""

from decimal import Decimal

import pytest

from apps.api.billing.catalog import (
    NO_FACTOR,
    RULE_BELOW_LOWEST,
    RULE_EXACT,
    RULE_HIGHEST,
    RULE_NEXT_LOWER,
    RULE_NO_TABLE,
    FactorError,
    billable_days,
    explain_factor,
    factor_for,
    normalise_table,
)

#: The prompt's table. As strings, because a float in a factor table is the
#: thing rule 1 exists to prevent and a test that typed one would be
#: asserting against a value the system refuses to store.
PROMPT_TABLE = {1: "1.0", 2: "1.5", 3: "2.0", 5: "2.8", 7: "3.5"}


@pytest.mark.parametrize(
    "days, expected",
    [
        (1, "1.0"),
        (2, "1.5"),
        (3, "2.0"),
        # The whole point. 4 days has no entry; the rule takes the next
        # LOWER one, so four days are billed as two, not as 2.8.
        (4, "2.0"),
        (5, "2.8"),
        (6, "2.8"),
        (7, "3.5"),
        # Above the highest entry the highest entry holds. Nine days is not
        # 4.5: the table stops at 7 and inventing a figure past it would be
        # inventing a price.
        (9, "3.5"),
        (365, "3.5"),
    ],
)
def test_the_prompts_table_gives_the_prompts_answers(days, expected):
    assert factor_for(PROMPT_TABLE, days) == Decimal(expected)


def test_four_days_does_not_round_up():
    """Named on its own, because this is the mutation the prompt asked for.

    A lookup that took the next HIGHER entry would answer 2.8 here and
    would still pass every exact-match case above — 1, 2, 3, 5 and 7 are all
    in the table. This assertion is the only one that fails, which is why it
    is a test of its own with a name that says what it is for.
    """
    assert factor_for(PROMPT_TABLE, 4) == Decimal("2.0")
    assert factor_for(PROMPT_TABLE, 4) != Decimal("2.8")


def test_six_days_does_not_round_up_either():
    assert factor_for(PROMPT_TABLE, 6) == Decimal("2.8")


def test_nothing_is_interpolated():
    """6 days is 2.8, not the 3.15 half way between 2.8 and 3.5."""
    assert factor_for(PROMPT_TABLE, 6) != Decimal("3.15")
    assert factor_for(PROMPT_TABLE, 4) != Decimal("2.4")


def test_below_the_lowest_entry_is_face_value():
    """A table starting at 2 days says nothing about 1, so 1 day is 1 day."""
    assert factor_for({2: "1.5", 3: "2.0"}, 1) == NO_FACTOR == Decimal("1")


def test_an_empty_table_is_face_value():
    assert factor_for({}, 5) == Decimal("1")


@pytest.mark.parametrize("days", [0, -1, -30])
def test_zero_or_fewer_days_is_an_error_and_not_a_factor(days):
    """Answering 1 would price an unfilled field as a full day."""
    with pytest.raises(FactorError) as caught:
        factor_for(PROMPT_TABLE, days)
    assert str(days) in str(caught.value)


@pytest.mark.parametrize("days", [2.5, "3", None, True])
def test_a_fractional_or_non_integer_day_count_is_refused(days):
    with pytest.raises(FactorError):
        factor_for(PROMPT_TABLE, days)


def test_billable_days_is_the_same_number_under_the_other_name():
    """Two names, one value. A test, because a second implementation of
    "how many days do I write on the quote" is exactly how the two would
    drift apart by one rounding step."""
    for days in (1, 4, 6, 9):
        assert billable_days(PROMPT_TABLE, days) == factor_for(PROMPT_TABLE, days)


# ─── The explanation, which the API serves and a screen shows ───────────────


@pytest.mark.parametrize(
    "days, matched, rule",
    [
        (5, 5, RULE_EXACT),
        (4, 3, RULE_NEXT_LOWER),
        (6, 5, RULE_NEXT_LOWER),
        (9, 7, RULE_HIGHEST),
    ],
)
def test_the_lookup_says_which_row_it_used(days, matched, rule):
    """`6 days → 2.8, from the 5-day entry`.

    The row and the rule, not just the number: the rule is a guess, and a
    screen that only showed 2.8 would make it impossible for anybody to
    notice they disagree with it.
    """
    factor, used, reported = explain_factor(PROMPT_TABLE, days)
    assert (used, reported) == (matched, rule)
    assert factor == factor_for(PROMPT_TABLE, days)


def test_below_the_lowest_entry_names_its_own_rule():
    factor, used, rule = explain_factor({2: "1.5"}, 1)
    assert (factor, used, rule) == (Decimal("1"), None, RULE_BELOW_LOWEST)


def test_an_empty_table_names_its_own_rule():
    assert explain_factor({}, 4) == (Decimal("1"), None, RULE_NO_TABLE)


def test_above_the_table_is_reported_differently_from_a_gap():
    """`highest_listed` and `next_lower` are the same arithmetic and are
    reported apart, because "we rented it for nine days and it says seven"
    is the question somebody actually asks."""
    assert explain_factor(PROMPT_TABLE, 6)[2] == RULE_NEXT_LOWER
    assert explain_factor(PROMPT_TABLE, 9)[2] == RULE_HIGHEST


# ─── The table itself ───────────────────────────────────────────────────────


def test_a_table_can_be_pairs_a_mapping_or_rows():
    """The ORM rows a router already loaded, without building a dict first —
    while this module still never imports the ORM."""

    class Row:
        def __init__(self, days, factor):
            self.days = days
            self.factor = Decimal(factor)

    expected = {1: Decimal("1.0"), 3: Decimal("2.0")}
    assert normalise_table({1: "1.0", 3: "2.0"}) == expected
    assert normalise_table([(1, "1.0"), (3, "2.0")]) == expected
    assert normalise_table([Row(1, "1.0"), Row(3, "2.0")]) == expected


@pytest.mark.parametrize(
    "table, fragment",
    [
        ({0: "1.0"}, "N starts at 1"),
        ({-2: "1.0"}, "N starts at 1"),
        ({1: "0"}, "greater than zero"),
        ({1: "-1"}, "greater than zero"),
        ({1: "not a number"}, "the factor for 1 day(s)"),
        ({"two": "1.5"}, "whole number of days"),
        ([(1, "1.0", "extra")], "(days, factor) pair"),
        (["nonsense"], "cannot read a factor entry"),
    ],
)
def test_every_refusal_names_the_offending_entry(table, fragment):
    """A factor table is typed in by hand, and "row 3 is not a number" is
    the difference between a fix and a bisect."""
    with pytest.raises(FactorError) as caught:
        normalise_table(table)
    assert fragment in str(caught.value)


def test_two_different_factors_for_one_day_count_is_refused():
    with pytest.raises(FactorError) as caught:
        normalise_table([(5, "2.8"), (5, "3.0")])
    assert "two different factors for 5 day(s)" in str(caught.value)


def test_the_same_factor_twice_is_not_a_conflict():
    """Repeating a row is a duplicate, not a disagreement, and a CSV that
    lists 5 → 2.8 twice is not a file anybody has to fix."""
    assert normalise_table([(5, "2.8"), (5, "2.8")]) == {5: Decimal("2.8")}


def test_a_factor_is_never_a_float():
    for days in (1, 4, 6, 9):
        assert isinstance(factor_for(PROMPT_TABLE, days), Decimal)
    for value in normalise_table(PROMPT_TABLE).values():
        assert isinstance(value, Decimal)
