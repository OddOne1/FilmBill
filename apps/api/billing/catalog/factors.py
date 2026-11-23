"""Rental factors: SCOPE §4.3's multi-day factor table, and its lookup.

A factor table is `{days → factor}`: five rental days at a factor of 2.8 are
billed as 2.8 days, which is how every rental house in the business prices a
week (Rentman does the same). The table is per factor GROUP so that one
table serves forty cameras.

**The lookup rule is `[Guessing]` and is written up as an open question**
(SCOPE §16, D71). The prompt that specified it said so, and so does this
docstring, because the alternative — a plausible rule implemented silently —
is the kind of thing that is only discovered when a customer queries an
invoice. What is implemented:

  * an **exact** entry wins;
  * otherwise the **next lower** listed entry, so 4 days with a table of
    1/2/3/5/7 bills at the 3-day factor;
  * **above** the highest entry, the highest entry, so 9 days bills at the
    7-day factor;
  * **below** the lowest entry, `1`, so a company whose table starts at 2
    days charges a single day at face value;
  * **zero or fewer days** is an error, not a factor. A zero-day rental is
    not a cheap rental, it is a mistake, and answering `1` would price it as
    one day.

**No interpolation.** 6 days with a table of 5 → 2.8 and 7 → 3.5 bills at
2.8, not at 3.15. Interpolating would invent a price the company never
agreed to, and it is exactly the sort of thing that looks clever and then
has to be explained to a client.

Pure, like everything in `billing/`: the input is a mapping or a list of
pairs, not a `RentalFactorGroup` row, so the table above can be a test
fixture rather than four database inserts.
"""

from decimal import Decimal
from typing import Iterable, Mapping, Sequence, Union

try:  # pragma: no cover - exercised by whichever import path is in use
    from ...core.rounding import DecimalParseError, to_decimal
except ImportError:  # pragma: no cover
    from core.rounding import DecimalParseError, to_decimal  # type: ignore[no-redef]

#: What a day with no entry at or below it is worth. Face value.
NO_FACTOR = Decimal("1")

FactorTable = Union[Mapping[int, object], Sequence[Sequence[object]], Iterable[object]]


class FactorError(ValueError):
    """The table or the number of days cannot produce a factor.

    A `ValueError` and not a `CalcError`: a factor is resolved to a number by
    the catalog BEFORE the engine is called (the engine never looks a factor
    up — see `billing/calc/model.Line`), so this is the catalog's error and
    the engine never sees it.
    """


def normalise_table(table: FactorTable) -> dict[int, Decimal]:
    """`{days: factor}` with the keys as ints and the values as `Decimal`.

    Accepts a mapping, a list of `(days, factor)` pairs, or a list of objects
    with `.days` and `.factor` — the last so a router can hand over the ORM
    rows it already loaded without building a dict first, while this module
    still never imports the ORM.

    Every refusal names the offending entry: a factor table is typed in by
    hand, and "row 3 is not a number" is the difference between a fix and a
    bisect.
    """
    entries: list[tuple[object, object]] = []
    if isinstance(table, Mapping):
        entries = list(table.items())
    else:
        for entry in table:
            if hasattr(entry, "days") and hasattr(entry, "factor"):
                entries.append((entry.days, entry.factor))
            elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)):
                if len(entry) != 2:
                    raise FactorError(
                        f"a factor entry is a (days, factor) pair; got "
                        f"{len(entry)} values in {entry!r}"
                    )
                entries.append((entry[0], entry[1]))
            else:
                raise FactorError(
                    f"cannot read a factor entry from {entry!r} — give a "
                    f"mapping, (days, factor) pairs, or objects with .days "
                    f"and .factor"
                )

    normalised: dict[int, Decimal] = {}
    for raw_days, raw_factor in entries:
        if isinstance(raw_days, bool) or not isinstance(raw_days, int):
            raise FactorError(
                f"a factor table is keyed by a whole number of days; got "
                f"{raw_days!r}"
            )
        if raw_days <= 0:
            raise FactorError(
                f"a factor table cannot have an entry for {raw_days} days — "
                f"the table says what a rental of N days is billed as, and "
                f"N starts at 1"
            )
        try:
            factor = to_decimal(
                raw_factor, what=f"the factor for {raw_days} day(s)"
            )
        except DecimalParseError as error:
            # Re-raised as a `FactorError`, not allowed through. Everything
            # this function can refuse has to arrive as one exception type,
            # or the router that catches `FactorError` to make a 422 would
            # turn a typed-in "zwei" into a 500 — and the caller would have
            # to know that a factor table's refusals come in two flavours.
            raise FactorError(str(error)) from None
        if factor <= 0:
            raise FactorError(
                f"the factor for {raw_days} day(s) is {factor}; a factor "
                f"multiplies a price and has to be greater than zero"
            )
        if raw_days in normalised and normalised[raw_days] != factor:
            raise FactorError(
                f"the table has two different factors for {raw_days} day(s): "
                f"{normalised[raw_days]} and {factor}"
            )
        normalised[raw_days] = factor
    return normalised


#: The rules `explain_factor` can report, as the API serves them.
RULE_EXACT = "exact"
RULE_NEXT_LOWER = "next_lower"
#: Above the highest entry. A separate name from `next_lower` even though the
#: arithmetic is the same, because this is the case somebody questions — "we
#: rented it for nine days, why does it say seven" — and an answer that calls
#: it "next lower" does not look like an answer.
RULE_HIGHEST = "highest_listed"
RULE_BELOW_LOWEST = "below_lowest"
RULE_NO_TABLE = "no_table"
FACTOR_RULES: tuple[str, ...] = (
    RULE_EXACT,
    RULE_NEXT_LOWER,
    RULE_HIGHEST,
    RULE_BELOW_LOWEST,
    RULE_NO_TABLE,
)


def explain_factor(table: FactorTable, days: int) -> tuple[Decimal, "int | None", str]:
    """`(factor, the entry used, the rule)` — the lookup and its reasoning.

    `factor_for` is this function's first element and nothing else, so there
    is one lookup and the explanation cannot drift from the number. The
    explanation exists because the rule is `[Guessing]`: a screen that says
    "6 days → 2.8, from the 5-day entry" lets Mathias see the rule and
    disagree with it, and one that says "2.8" does not.
    """
    if isinstance(days, bool) or not isinstance(days, int):
        raise FactorError(
            f"rental days must be a whole number; got {days!r}. A half-day "
            f"rental is a different unit, not a fractional factor lookup."
        )
    if days <= 0:
        raise FactorError(
            f"a rental of {days} day(s) has no factor. Zero days is not a "
            f"cheap rental, it is an unfilled field — and answering 1 would "
            f"price it as a full day."
        )

    entries = normalise_table(table)
    if not entries:
        return NO_FACTOR, None, RULE_NO_TABLE

    if days in entries:
        return entries[days], days, RULE_EXACT

    at_or_below = [listed for listed in entries if listed <= days]
    if not at_or_below:
        # Below the lowest entry: face value. A company whose table starts
        # at 2 days has said nothing about 1 day, and the only answer that
        # invents nothing is "one day costs one day".
        return NO_FACTOR, None, RULE_BELOW_LOWEST

    matched = max(at_or_below)
    rule = RULE_HIGHEST if days > max(entries) else RULE_NEXT_LOWER
    return entries[matched], matched, rule


def factor_for(table: FactorTable, days: int) -> Decimal:
    """What `days` rental days are billed as, per the rule in the docstring.

    Returns a `Decimal`, always — never a float, and never `None`: a caller
    that got `None` would have to decide what it meant, and the two plausible
    decisions (1, or refuse) are the two this function makes explicitly.
    """
    factor, _matched, _rule = explain_factor(table, days)
    return factor


def billable_days(table: FactorTable, days: int) -> Decimal:
    """The same thing, named the way a document says it.

    `factor_for` answers "what is the multiplier"; this answers "how many
    days do I write on the quote", which is the same number and the one a
    line's `factor` field carries. Two names for one value because the screen
    and the engine ask the question in different words, and a reader of
    either should not have to translate.
    """
    return factor_for(table, days)
