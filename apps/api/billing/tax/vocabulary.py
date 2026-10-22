"""The closed vocabulary a tax rule may be written in, and its validation.

**Closed on purpose.** A rule is data a user can edit (CLAUDE.md rule 7), and
data a user can edit is data a user can misspell. If `buyer_vat_verfied` were
accepted and simply never matched, the rule would sit in the list looking
correct and the engine would fall through to the next one — which is how a
reverse charge becomes 20 % domestic tax without anybody being told. So every
key and every value is checked against the lists below, and anything else is
an error with a readable message naming what was wrong and what was allowed.

The same reasoning covers the EN 16931 categories and their rates: a category
that means "exempt" with a 20 % rate attached is not a configuration, it is a
contradiction, and the only safe moment to refuse it is before it is stored.
"""

from decimal import Decimal
from typing import Any, Mapping

from .treatments import TREATMENTS


class PackError(ValueError):
    """A rule, code or pack that cannot be stored as written.

    A `ValueError` subclass so a router can turn it into a 422 and a pack
    loader can let it fail the test suite, without either of them needing to
    know about the other.
    """


# ─── Conditions ─────────────────────────────────────────────────────────────

#: Where the buyer is, relative to the seller. Derived from the two countries
#: by `regions.region_of` — never entered, so the engine and a rule cannot
#: disagree about what "EU other" means.
BUYER_REGIONS: tuple[str, ...] = ("same_country", "eu_other", "non_eu")

#: A business buyer may be reverse-charged; a private one may not. "Business"
#: here means "has a VAT ID we could verify", not "is a company".
BUYER_TYPES: tuple[str, ...] = ("business", "private")

#: Goods and services part because the place-of-supply answer differs: EU B2B
#: services reverse-charge, EU B2B goods are an intra-community supply.
SUPPLY_KINDS: tuple[str, ...] = ("goods", "services")

#: `{condition key: allowed values}`. `None` means "a bool".
#:
#: This mapping IS the vocabulary — adding a condition is a line here plus
#: the one place in the engine that evaluates it, and the test
#: `test_every_condition_key_is_evaluated` fails if a key is added here and
#: nowhere else. A condition the engine silently ignores is the worst of the
#: available failures: the rule matches more cases than it says it does.
CONDITION_KEYS: Mapping[str, tuple[str, ...] | None] = {
    "seller_is_kleinunternehmer": None,
    "buyer_region": BUYER_REGIONS,
    "buyer_type": BUYER_TYPES,
    "buyer_vat_verified": None,
    "supply_kind": SUPPLY_KINDS,
}


# ─── Outcomes ───────────────────────────────────────────────────────────────

#: In an outcome's `{"class": ...}`, the item's own tax class rather than a
#: fixed one. "Domestic rate of the item's tax class" (SCOPE §6.3) cannot be
#: written as a class key, because which class it is depends on the line.
#:
#: Spelled with an `@` so it can never collide with a class key a pack or a
#: user defines — `standard`, `reduced`, `zero` are all plausible names, and
#: `@item` is not a name anybody would choose.
ITEM_CLASS_SENTINEL = "@item"

#: Warning codes an outcome may raise, and the engine may add. Closed for the
#: same reason the conditions are: the web renders a sentence per code, and a
#: code nothing renders is a warning nobody sees.
WARNING_CODES: tuple[str, ...] = (
    #: An EU business buyer whose VAT ID has not been verified. Reverse charge
    #: is not proposed, domestic tax is, and this says why.
    "buyer_vat_id_not_verified",
    #: The legal note shipped with the code has not been confirmed by anybody
    #: qualified. Every note ships this way (see the pack headers).
    "legal_note_not_reviewed",
    #: The resolved code is inactive — the generic pack's standard rate, which
    #: ships at 0 % precisely so nobody invoices an invented rate.
    "code_inactive",
    #: The company has no country, so no rule can be evaluated at all.
    "home_country_missing",
    #: The BUYER has no country, so there is no way to tell a domestic sale
    #: from an export. Separate from the one above because the fix is in a
    #: different place and the person who can make it is a different person.
    "buyer_country_missing",
)


# ─── EN 16931 categories ────────────────────────────────────────────────────

#: The EN 16931 VAT category codes, as SCOPE §6.1 lists them.
CATEGORIES: tuple[str, ...] = ("S", "Z", "E", "AE", "K", "G", "O", "L", "M")

#: Categories that are 0 % by definition, so a non-zero rate on one is a
#: contradiction rather than a choice: zero-rated, exempt, reverse charge,
#: intra-community supply, export, outside scope.
#:
#: `S` (standard), `L` and `M` (the Canary Islands and Ceuta/Melilla rates)
#: carry real rates and are not in here.
ZERO_RATE_CATEGORIES: tuple[str, ...] = ("Z", "E", "AE", "K", "G", "O")


def validate_rate_for_category(code: str, category: str, rate: Decimal) -> None:
    """Refuse a category/rate pair that cannot both be true.

    Raised rather than corrected. Silently zeroing the rate would make the
    stored row disagree with what the user typed, and silently changing the
    category would decide a tax question on their behalf.
    """
    if category not in CATEGORIES:
        raise PackError(
            f"Tax code {code!r}: category {category!r} is not an EN 16931 "
            f"category. Allowed: {', '.join(CATEGORIES)}."
        )
    if rate < 0:
        raise PackError(f"Tax code {code!r}: a rate cannot be negative ({rate}).")
    if category in ZERO_RATE_CATEGORIES and rate != 0:
        raise PackError(
            f"Tax code {code!r}: category {category!r} is a zero-rate "
            f"category, so its rate must be 0 and not {rate}. Categories "
            f"that must be 0: {', '.join(ZERO_RATE_CATEGORIES)}."
        )


# ─── Rules ──────────────────────────────────────────────────────────────────


def validate_conditions(conditions: Any, *, where: str) -> None:
    """Refuse anything that is not a mapping of known keys to known values.

    `where` names the rule in the message, because a pack has eight of them
    and "unknown condition key" without a name is a message somebody has to
    bisect a YAML file to act on.
    """
    if not isinstance(conditions, Mapping):
        raise PackError(
            f"{where}: conditions must be a mapping of condition keys to "
            f"values, got {type(conditions).__name__}."
        )

    for key, value in conditions.items():
        if key not in CONDITION_KEYS:
            raise PackError(
                f"{where}: unknown condition key {key!r}. Allowed: "
                f"{', '.join(sorted(CONDITION_KEYS))}."
            )
        allowed = CONDITION_KEYS[key]
        if allowed is None:
            if not isinstance(value, bool):
                raise PackError(
                    f"{where}: condition {key!r} takes true or false, got "
                    f"{value!r}."
                )
        elif value not in allowed:
            raise PackError(
                f"{where}: condition {key!r} does not take {value!r}. "
                f"Allowed: {', '.join(allowed)}."
            )


def validate_outcome(outcome: Any, *, where: str) -> None:
    """Refuse an outcome that does not name a treatment and a code.

    Shape: `{treatment, code: {class: <key>} | {code: <code>}, warning?}`.
    The `warning` key is optional and is what lets the shipped
    "EU business, VAT ID not verified" rule explain itself as DATA — the
    alternative was a condition the engine recognises by name, which is a
    country's interpretation hidden in code (D19).

    This checks the SHAPE. Whether the class or code it names exists is a
    question about one company's tables, and the pack loader and the engine
    each answer it where they have those tables.
    """
    if not isinstance(outcome, Mapping):
        raise PackError(
            f"{where}: outcome must be a mapping, got "
            f"{type(outcome).__name__}."
        )

    unknown = sorted(set(outcome) - {"treatment", "code", "warning"})
    if unknown:
        raise PackError(
            f"{where}: unknown outcome key(s) {', '.join(unknown)}. "
            f"Allowed: treatment, code, warning."
        )

    treatment = outcome.get("treatment")
    if treatment not in TREATMENTS:
        raise PackError(
            f"{where}: treatment {treatment!r} is not one of "
            f"{', '.join(TREATMENTS)}. Treatments are a system enum — they "
            f"drive behaviour, so a pack cannot invent one."
        )

    target = outcome.get("code")
    if not isinstance(target, Mapping) or len(target) != 1:
        raise PackError(
            f"{where}: outcome.code must be exactly one of "
            f"{{class: <key>}} or {{code: <code>}}, got {target!r}."
        )
    (kind, value), = target.items()
    if kind not in ("class", "code"):
        raise PackError(
            f"{where}: outcome.code key must be 'class' or 'code', got "
            f"{kind!r}."
        )
    if not isinstance(value, str) or not value:
        raise PackError(
            f"{where}: outcome.code.{kind} must be a non-empty string, got "
            f"{value!r}."
        )

    warning = outcome.get("warning")
    if warning is not None and warning not in WARNING_CODES:
        raise PackError(
            f"{where}: unknown warning {warning!r}. Allowed: "
            f"{', '.join(WARNING_CODES)}."
        )
