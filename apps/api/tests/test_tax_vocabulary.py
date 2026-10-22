"""The closed vocabulary, the treatments and the region resolution.

Pure functions over plain data, so these are plain table tests. No database,
no client, no fixtures — which is the point of `apps/api/billing/` existing
(see `test_billing_import_boundary.py`).

The theme throughout: **a rule that is merely never true is worse than a rule
that is refused.** A misspelled condition key sits in the list looking
correct, never matches, and the engine falls through to the next rule — so an
EU reverse charge quietly becomes 20 % domestic tax. Every test below is about
making that outcome impossible to reach by typing.
"""

from decimal import Decimal

import pytest

from apps.api.billing.tax import (
    BUYER_REGIONS,
    BUYER_TYPES,
    CATEGORIES,
    CONDITION_KEYS,
    EU_MEMBER_STATES,
    SUPPLY_KINDS,
    TREATMENTS,
    WARNING_CODES,
    ZERO_RATE_CATEGORIES,
    PackError,
    region_of,
    treatment_label,
    validate_conditions,
    validate_outcome,
    validate_rate_for_category,
)
from apps.api.billing.tax.treatments import TREATMENT_LABELS


# ─── Conditions ─────────────────────────────────────────────────────────────


def test_the_shipped_condition_keys_are_the_five_scope_names():
    """Spelled out, so adding a sixth is a deliberate act with a test change.

    The vocabulary is the contract between a YAML pack, the database and the
    engine. A key that appears in one of the three and not the others is the
    whole failure mode of this module.
    """
    assert set(CONDITION_KEYS) == {
        "seller_is_kleinunternehmer",
        "buyer_region",
        "buyer_type",
        "buyer_vat_verified",
        "supply_kind",
    }


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        {"buyer_region": "same_country"},
        {"buyer_type": "business", "supply_kind": "services"},
        {"seller_is_kleinunternehmer": True},
        {"buyer_vat_verified": False},
        {
            "buyer_region": "eu_other",
            "buyer_type": "business",
            "buyer_vat_verified": True,
            "supply_kind": "goods",
        },
    ],
)
def test_valid_conditions_are_accepted(conditions):
    validate_conditions(conditions, where="rule 1")


@pytest.mark.parametrize(
    "conditions, expected_in_message",
    [
        ({"buyer_vat_verfied": True}, "unknown condition key"),
        ({"buyer_region": "EU"}, "does not take 'EU'"),
        ({"buyer_type": "company"}, "does not take 'company'"),
        ({"supply_kind": "work"}, "does not take 'work'"),
        ({"seller_is_kleinunternehmer": "yes"}, "takes true or false"),
        ({"buyer_vat_verified": 1}, "takes true or false"),
        ("not a mapping", "must be a mapping"),
        ([("buyer_region", "non_eu")], "must be a mapping"),
    ],
)
def test_invalid_conditions_are_refused_with_a_readable_message(
    conditions, expected_in_message
):
    """Readable, and naming the rule.

    A pack has eight rules; "unknown condition key" with no name attached is
    a message somebody bisects a YAML file to act on.
    """
    with pytest.raises(PackError) as caught:
        validate_conditions(conditions, where="rule 5 'EU business'")

    message = str(caught.value)
    assert expected_in_message in message
    assert "rule 5 'EU business'" in message


def test_a_bool_is_not_accepted_where_a_string_belongs():
    """`buyer_region: true` is the YAML author's other way of getting it wrong."""
    with pytest.raises(PackError):
        validate_conditions({"buyer_region": True}, where="rule")


def test_the_true_integer_one_is_not_a_boolean_here():
    """`1 == True` in Python, so `value not in allowed` and a loose
    `isinstance(value, int)` would both let `buyer_vat_verified: 1` through —
    and a YAML `1` is an int. The check is `isinstance(value, bool)`."""
    with pytest.raises(PackError):
        validate_conditions({"buyer_vat_verified": 1}, where="rule")
    validate_conditions({"buyer_vat_verified": True}, where="rule")


# ─── Outcomes ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome",
    [
        {"treatment": "domestic", "code": {"class": "standard"}},
        {"treatment": "domestic", "code": {"class": "@item"}},
        {"treatment": "eu_reverse_charge", "code": {"code": "EU-AE"}},
        {
            "treatment": "domestic",
            "code": {"class": "@item"},
            "warning": "buyer_vat_id_not_verified",
        },
    ],
)
def test_valid_outcomes_are_accepted(outcome):
    validate_outcome(outcome, where="rule 2")


@pytest.mark.parametrize(
    "outcome, expected_in_message",
    [
        ({"treatment": "domestic"}, "outcome.code must be exactly one of"),
        ({"code": {"class": "standard"}}, "is not one of"),
        (
            {"treatment": "reverse_charge", "code": {"code": "EU-AE"}},
            "is not one of",
        ),
        (
            {"treatment": "domestic", "code": {"class": "a", "code": "b"}},
            "exactly one of",
        ),
        (
            {"treatment": "domestic", "code": {"klass": "standard"}},
            "must be 'class' or 'code'",
        ),
        ({"treatment": "domestic", "code": {"class": ""}}, "non-empty string"),
        ({"treatment": "domestic", "code": "AT-20"}, "exactly one of"),
        (
            {
                "treatment": "domestic",
                "code": {"class": "@item"},
                "warning": "vat_unverified",
            },
            "unknown warning",
        ),
        (
            {
                "treatment": "domestic",
                "code": {"class": "@item"},
                "note": "something",
            },
            "unknown outcome key",
        ),
        ("not a mapping", "must be a mapping"),
    ],
)
def test_invalid_outcomes_are_refused(outcome, expected_in_message):
    with pytest.raises(PackError) as caught:
        validate_outcome(outcome, where="rule 7")
    assert expected_in_message in str(caught.value)
    assert "rule 7" in str(caught.value)


def test_a_pack_cannot_invent_a_treatment():
    """The message says why, because the why is not obvious from the outside:
    a treatment drives behaviour elsewhere in the system, so one that only a
    YAML file knows about is a branch nothing takes."""
    with pytest.raises(PackError) as caught:
        validate_outcome(
            {"treatment": "studio_special", "code": {"code": "AT-20"}},
            where="rule 9",
        )
    assert "system enum" in str(caught.value)


def test_every_treatment_has_a_label_in_both_languages():
    """A missing label renders as the raw key on a document.

    Asserted over `TREATMENTS` rather than over the label dict, so a
    treatment added to the tuple and forgotten in the labels fails here
    instead of on an invoice.
    """
    for treatment in TREATMENTS:
        assert treatment in TREATMENT_LABELS, f"{treatment} has no labels"
        for lang in ("de", "en"):
            label = treatment_label(treatment, lang)
            assert label and label != treatment, (
                f"{treatment} has no {lang} label"
            )


def test_an_unknown_language_falls_back_to_english_and_then_to_the_key():
    assert treatment_label("domestic", "fr") == "Domestic"
    assert treatment_label("not_a_treatment", "en") == "not_a_treatment"


# ─── Categories and rates ───────────────────────────────────────────────────


def test_the_nine_en16931_categories_are_the_scope_list():
    assert CATEGORIES == ("S", "Z", "E", "AE", "K", "G", "O", "L", "M")


def test_the_zero_rate_categories_are_a_subset_that_excludes_the_rated_ones():
    """`S`, `L` and `M` carry real rates. Sweeping one of them in would make
    the standard rate unstorable; leaving `AE` out would let a reverse charge
    carry 20 %."""
    assert set(ZERO_RATE_CATEGORIES) <= set(CATEGORIES)
    assert set(ZERO_RATE_CATEGORIES) == {"Z", "E", "AE", "K", "G", "O"}
    assert not set(ZERO_RATE_CATEGORIES) & {"S", "L", "M"}


@pytest.mark.parametrize("category", ZERO_RATE_CATEGORIES)
def test_a_zero_rate_category_with_a_rate_is_refused(category):
    with pytest.raises(PackError) as caught:
        validate_rate_for_category("X-1", category, Decimal("20"))
    message = str(caught.value)
    assert "zero-rate category" in message
    assert "X-1" in message


@pytest.mark.parametrize("category", ZERO_RATE_CATEGORIES)
def test_a_zero_rate_category_at_zero_is_accepted(category):
    validate_rate_for_category("X-1", category, Decimal("0"))


def test_a_negative_rate_is_refused():
    with pytest.raises(PackError) as caught:
        validate_rate_for_category("X-1", "S", Decimal("-0.0001"))
    assert "cannot be negative" in str(caught.value)


def test_an_unknown_category_is_refused_and_the_message_lists_the_allowed_ones():
    with pytest.raises(PackError) as caught:
        validate_rate_for_category("X-1", "ZZ", Decimal("0"))
    message = str(caught.value)
    assert "not an EN 16931 category" in message
    for category in CATEGORIES:
        assert category in message


def test_the_refusal_is_a_value_error():
    """So a router can turn it into a 422 without importing this module's own
    exception, and a pack loader can let it fail a test."""
    assert issubclass(PackError, ValueError)


# ─── Regions ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seller, buyer, expected",
    [
        ("AT", "AT", "same_country"),
        ("AT", "DE", "eu_other"),
        ("DE", "AT", "eu_other"),
        ("AT", "US", "non_eu"),
        ("AT", "GB", "non_eu"),
        ("AT", "CH", "non_eu"),
        # Case and whitespace, because a party imported from a CSV is not the
        # company form, which upper-cases.
        ("at", "de", "eu_other"),
        (" AT ", "at", "same_country"),
        # Neither country known is not "probably domestic" — see `region_of`.
        (None, "AT", None),
        ("AT", None, None),
        ("", "AT", None),
        ("AT", "   ", None),
    ],
)
def test_region_of(seller, buyer, expected):
    assert region_of(seller, buyer) == expected


def test_a_non_eu_seller_sees_every_other_country_as_non_eu():
    """From a Swiss seller's point of view there is no "EU other": the
    intra-community rules do not apply to them at all."""
    assert region_of("CH", "DE") == "non_eu"
    assert region_of("CH", "CH") == "same_country"


def test_the_member_list_is_the_twenty_seven_and_excludes_the_uk():
    assert len(EU_MEMBER_STATES) == 27
    assert "GB" not in EU_MEMBER_STATES
    assert {"AT", "DE", "FR", "IE", "HR"} <= EU_MEMBER_STATES


def test_the_member_list_can_be_overridden_without_changing_a_caller():
    """The seam that keeps this a constant rather than a region-pack entry:
    the day a pack needs its own list, it passes one."""
    assert region_of("AT", "GB", eu_member_states=frozenset({"AT", "GB"})) == (
        "eu_other"
    )


def test_the_warning_codes_are_closed_and_every_one_has_a_sentence():
    """Five: the prompt's four, plus the buyer having no country.

    A proposal cannot tell a domestic sale from an export without the buyer's
    country, and that is a different fix by a different person than the
    company's own missing country — so it is its own code rather than a
    reused one.
    """
    from apps.api.billing.tax import warning_text

    assert set(WARNING_CODES) == {
        "buyer_vat_id_not_verified",
        "legal_note_not_reviewed",
        "code_inactive",
        "home_country_missing",
        "buyer_country_missing",
    }
    for code in WARNING_CODES:
        text = warning_text(code)
        assert text != code, f"{code} has no sentence"
        assert text.endswith("."), f"{code}'s text is not a sentence: {text!r}"


def test_the_value_lists_are_tuples_so_nothing_can_append_to_them():
    """A vocabulary a caller can extend at runtime is not a closed one."""
    for values in (BUYER_REGIONS, BUYER_TYPES, SUPPLY_KINDS, CATEGORIES, TREATMENTS):
        assert isinstance(values, tuple)
