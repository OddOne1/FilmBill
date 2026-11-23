"""The closed vocabulary of the calculation engine, and its one error type.

Every enumerated string the engine accepts is a tuple in this file, and
`check_member` is how a value gets compared against one. Two properties
matter and neither is free:

**A wrong value is named in the message.** `"nicht gültig"` for a typo like
`"days"` instead of `"day"` costs somebody an afternoon; "unknown period unit
'days' on line 'l1' — known units are day, flat, half_day, hour, km, month,
night, piece, week" costs them a second. The engine is reached from a CSV
import and from a form, so wrong values are the normal case, not the
exceptional one.

**There is no `other` and no fallthrough.** An unrecognised period unit is a
refusal, not a silently-ignored field: a line whose unit the engine did not
understand would still have produced a net, and that net would have been
right for the wrong reason until somebody noticed the PDF said the wrong
word.

`kind` and `tax_ref` are deliberately NOT in here. Both are opaque to the
engine (see `model.py`): `kind` is matched as a string by kind-filtered
adjustments, `tax_ref` is a grouping key, and the engine never decides what
either one means. Their vocabularies belong to the catalog and to the tax
module, which is where they can be validated against a company's own rows.
"""

from typing import Iterable, Optional

# ─── Period units (SCOPE §5.3) ──────────────────────────────────────────────

#: What `period_value` counts. Sorted, because the list appears in error
#: messages and an unsorted one makes a reader check twice whether their
#: value is in it.
#:
#: `flat`, `piece` and `km` carry no duration, which is why the uniform-days
#: rule in `engine.display_row_for_group` compares the PAIR
#: `(period_unit, period_value)` rather than the number alone: 1 flat and
#: 1 day are not the same row.
PERIOD_UNITS: tuple[str, ...] = (
    "day",
    "flat",
    "half_day",
    "hour",
    "km",
    "month",
    "night",
    "piece",
    "week",
)

# ─── Adjustments (SCOPE §5.4) ───────────────────────────────────────────────

#: `discount` subtracts, `surcharge` adds. The sign lives here and nowhere
#: else — an adjustment's `value` is always entered positive, and a
#: "discount of −5 %" that someone types as `-5` is refused rather than
#: quietly becoming a surcharge.
ADJUSTMENT_TYPES: tuple[str, ...] = ("discount", "surcharge")

#: `percent` of a base, or a flat `amount`.
ADJUSTMENT_MODES: tuple[str, ...] = ("percent", "amount")

#: `subtotal` — the container's own subtotal before any of its adjustments.
#: `subtotal_incl_previous` — that subtotal plus the adjustments already
#: applied in the same container, in order (SCOPE §5.4's "compound").
#: `kinds` — only the lines in the container whose `kind` is in the
#: adjustment's `kinds` list ("production fee 8 % on labor + equipment only").
ADJUSTMENT_BASES: tuple[str, ...] = ("subtotal", "subtotal_incl_previous", "kinds")

# ─── Structure ──────────────────────────────────────────────────────────────

#: SCOPE §5.4: "Max nesting depth **2**". A top-level group is depth 1, a
#: group inside it is depth 2, and a group inside THAT is refused. Lines may
#: sit at any depth including the document root.
MAX_GROUP_DEPTH = 2

#: The `tax_ref` of a line that names none. A budget never sets one — the
#: core path groups by this key and does not care what it is — so the empty
#: string is a real category here rather than a missing value, and
#: `apply_vat` demands a rate for it like for any other.
NO_TAX_REF = ""

#: Where an adjustment landed when its base contained no tax category to
#: allocate it to — an amount surcharge on an empty group, say. The amount is
#: still in the document total; it is parked under a key no rate will ever
#: match, so `apply_vat` refuses rather than inventing a category for it, and
#: `WARN_ADJUSTMENT_NOT_ALLOCATABLE` says which adjustment did it.
UNALLOCATED_TAX_REF = "__unallocated__"

# ─── Warnings ───────────────────────────────────────────────────────────────

#: SCOPE §5.4's uniform-days rule: this group cannot be shown collapsed as
#: "price/day × days" because its lines are not all charged for the same
#: period.
WARN_MIXED_DAYS_IN_GROUP = "mixed_days_in_group"

#: A collapsed group's per-period price does not multiply back to its
#: subtotal exactly (subtotal 100.00 over 3 days is 33.33/day, and
#: 3 × 33.33 is 99.99). The row's TOTAL is always the subtotal; this says the
#: breakdown beside it is a presentation of it and not an identity.
WARN_COLLAPSED_PRICE_NOT_EXACT = "collapsed_price_not_exact"

#: An adjustment whose base had no net to allocate against — see
#: `UNALLOCATED_TAX_REF`.
WARN_ADJUSTMENT_NOT_ALLOCATABLE = "adjustment_not_allocatable"

#: A `kinds` adjustment that matched no line at all. Not an error: a fee on
#: "labor + equipment_rental" is a legitimate thing to carry on a document
#: that happens to have neither this week. But it is worth saying, because
#: the other reading — a typo in a kind — looks identical on screen.
WARN_KIND_FILTER_MATCHED_NOTHING = "kind_filter_matched_nothing"

WARNING_CODES: tuple[str, ...] = (
    WARN_ADJUSTMENT_NOT_ALLOCATABLE,
    WARN_COLLAPSED_PRICE_NOT_EXACT,
    WARN_KIND_FILTER_MATCHED_NOTHING,
    WARN_MIXED_DAYS_IN_GROUP,
)

#: The sentence each code gets when nothing more specific is available.
#: Kept here so the API, the web app and a future PDF all say the same thing
#: about the same condition.
WARNING_TEXT: dict[str, str] = {
    WARN_MIXED_DAYS_IN_GROUP: (
        "Items in this group are charged for different numbers of days. To "
        "show days on the document, move the items with a different number "
        "of days into their own group."
    ),
    WARN_COLLAPSED_PRICE_NOT_EXACT: (
        "The price per period shown for this group is rounded, so it does "
        "not multiply back to the group total exactly. The total is correct."
    ),
    WARN_ADJUSTMENT_NOT_ALLOCATABLE: (
        "This adjustment has no net amount to be allocated against, so it "
        "belongs to no tax category. VAT cannot be computed until something "
        "it applies to exists."
    ),
    WARN_KIND_FILTER_MATCHED_NOTHING: (
        "This adjustment applies only to certain item kinds, and nothing in "
        "its scope has one of them, so it comes to zero."
    ),
}


# ─── The error ──────────────────────────────────────────────────────────────


class CalcError(ValueError):
    """The input cannot be calculated, and the message says why.

    One class rather than a hierarchy. Every caller does the same thing with
    it — turns it into a 422 with the text — and a hierarchy would invite a
    router to catch the wrong branch and answer 500 for a user's typo. The
    message is the API, so every raise site names the offending value and
    the line or group it was on.

    A `ValueError` subclass so that a caller which already handles bad input
    generically (the CSV importer) does not have to know this type exists.
    """


def check_member(
    value: object,
    allowed: Iterable[str],
    *,
    what: str,
    where: Optional[str] = None,
) -> str:
    """Return `value` if it is one of `allowed`, else raise naming both.

    `where` is the id of the line or group the value sits on. Optional only
    because some values (a document-level adjustment's mode) have no row to
    point at; pass it wherever there is one, because "unknown period unit
    'days'" on a sixty-line budget is a sentence somebody still has to go
    looking with.
    """
    options = sorted(allowed)
    if not isinstance(value, str):
        raise CalcError(
            f"{what} must be one of {', '.join(options)}; got "
            f"{type(value).__name__}" + (f" on {where}" if where else "")
        )
    if value not in options:
        raise CalcError(
            f"unknown {what} {value!r}"
            + (f" on {where}" if where else "")
            + f" — known values are {', '.join(options)}"
        )
    return value
