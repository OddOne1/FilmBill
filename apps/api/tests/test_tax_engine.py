"""`propose_tax`, as a table of cases with the answers written out.

**Golden fixtures, and the expectations are literals.** Every row below says
the code, the rate, the treatment, the rule and the warnings it expects. None
of them is computed from the thing under test, because a test that derives
its expectation from the engine agrees with the engine by construction and
would go on passing while the engine was wrong.

The tables come from the REAL shipped packs rather than from hand-built
dataclasses, so each case covers the pack and the engine together. That is
deliberate: the pack is where a rate is written down and the engine is what
reads it, and the bug that matters — "the reverse-charge rule never matches"
— lives in the join between them.

Read the table as the specification. SCOPE §12's S2b case is in it (a studio
item whose advisor says "Austrian VAT regardless of the customer"), as are
the four blocking cases, which exist because the one thing this function must
never do is answer a plausible rate for a question it cannot decide.
"""

from decimal import Decimal

import pytest

from apps.api.billing.tax import (
    OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS,
    OVERRIDE_FIXED_CODE,
    Override,
    TaxClassData,
    TaxCodeData,
    TaxInputs,
    TaxRuleData,
    load_pack,
    propose_tax,
)
from apps.api.billing.tax.packs import engine_tables


def _tables(pack_name: str):
    return engine_tables(load_pack(pack_name))


def _propose(pack_name: str, inputs: TaxInputs, lang: str = "en"):
    codes, classes, rules = _tables(pack_name)
    return propose_tax(inputs, codes, classes, rules, lang)


#: `(id, pack, inputs, expected)`. `expected` keys:
#:   treatment, code, rate, rule (position or "override:item"/"override:party"),
#:   warnings (a set of codes), would (treatment, code) or None, note (bool).
CASES: list[tuple] = [
    # ── Austria, domestic ────────────────────────────────────────────────
    (
        "at-domestic-standard",
        "at",
        TaxInputs("AT", "AT", item_class="standard"),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": 20,
            "warnings": set(),
        },
    ),
    (
        "at-domestic-reduced",
        "at",
        TaxInputs("AT", "AT", item_class="reduced"),
        {
            "treatment": "domestic",
            "code": "AT-10",
            "rate": "10",
            "rule": 20,
            "warnings": set(),
        },
    ),
    (
        "at-domestic-reduced-2",
        "at",
        TaxInputs("AT", "AT", item_class="reduced_2"),
        {
            "treatment": "domestic",
            "code": "AT-13",
            "rate": "13",
            "rule": 20,
            "warnings": set(),
        },
    ),
    (
        "at-domestic-zero",
        "at",
        TaxInputs("AT", "AT", item_class="zero"),
        {
            "treatment": "domestic",
            "code": "AT-0",
            "rate": "0",
            "rule": 20,
            "warnings": set(),
        },
    ),
    (
        # The same-country rule does not care whether it is goods or services.
        "at-domestic-goods",
        "at",
        TaxInputs("AT", "AT", supply_kind="goods"),
        {"treatment": "domestic", "code": "AT-20", "rate": "20", "rule": 20},
    ),
    (
        "at-domestic-private",
        "at",
        TaxInputs("AT", "AT", buyer_type="private"),
        {"treatment": "domestic", "code": "AT-20", "rate": "20", "rule": 20},
    ),
    # ── Austria → another EU country, business ───────────────────────────
    (
        "at-de-business-verified-services",
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="services"),
        {
            "treatment": "eu_reverse_charge",
            "code": "EU-AE",
            "rate": "0",
            "rule": 30,
            "warnings": {"legal_note_not_reviewed"},
            "note": "Reverse charge: VAT to be accounted for by the recipient.",
        },
    ),
    (
        "at-de-business-verified-goods",
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="goods"),
        {
            "treatment": "intra_community_supply",
            "code": "EU-K",
            "rate": "0",
            "rule": 40,
            "warnings": {"legal_note_not_reviewed"},
            "note": "Intra-community supply, exempt from VAT.",
        },
    ),
    (
        # [Guessing, §17 question 9(c)] — domestic plus a warning, never a
        # silent reverse charge.
        "at-de-business-unverified-services",
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=False, supply_kind="services"),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": 50,
            "warnings": {"buyer_vat_id_not_verified"},
        },
    ),
    (
        "at-de-business-unverified-goods",
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=False, supply_kind="goods"),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": 50,
            "warnings": {"buyer_vat_id_not_verified"},
        },
    ),
    (
        "at-de-business-unverified-reduced-class",
        "at",
        TaxInputs("AT", "DE", item_class="reduced"),
        {
            "treatment": "domestic",
            "code": "AT-10",
            "rate": "10",
            "rule": 50,
            "warnings": {"buyer_vat_id_not_verified"},
        },
    ),
    (
        # OSS is out of scope for v1, so a private EU buyer gets the domestic
        # rate and no warning pretending otherwise.
        "at-de-private",
        "at",
        TaxInputs("AT", "DE", buyer_type="private", buyer_vat_verified=False),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": 60,
            "warnings": set(),
        },
    ),
    # ── Austria → outside the EU ─────────────────────────────────────────
    (
        "at-us-business-services",
        "at",
        TaxInputs("AT", "US", buyer_vat_verified=False, supply_kind="services"),
        {
            "treatment": "not_taxable_in_country",
            "code": "EU-O",
            "rate": "0",
            "rule": 70,
            "warnings": {"legal_note_not_reviewed"},
            "note": (
                "Not taxable in the country of the supplier; the place of "
                "supply is abroad."
            ),
        },
    ),
    (
        "at-us-business-goods",
        "at",
        TaxInputs("AT", "US", supply_kind="goods"),
        {
            "treatment": "export",
            "code": "EU-G",
            "rate": "0",
            "rule": 80,
            "warnings": {"legal_note_not_reviewed"},
            "note": "Export delivery, exempt from VAT.",
        },
    ),
    (
        "at-us-private",
        "at",
        TaxInputs("AT", "US", buyer_type="private"),
        {"treatment": "domestic", "code": "AT-20", "rate": "20", "rule": 60},
    ),
    (
        "at-gb-business-services",
        "at",
        TaxInputs("AT", "GB", buyer_vat_verified=True, supply_kind="services"),
        {
            "treatment": "not_taxable_in_country",
            "code": "EU-O",
            "rate": "0",
            "rule": 70,
        },
    ),
    # ── Kleinunternehmer: exempt for every case ──────────────────────────
    (
        "at-kleinunternehmer-domestic",
        "at",
        TaxInputs("AT", "AT", seller_is_kleinunternehmer=True),
        {
            "treatment": "small_business_exemption",
            "code": "EU-KU",
            "rate": "0",
            "rule": 10,
            "warnings": {"legal_note_not_reviewed"},
            "note": "Small business exemption: no VAT is charged.",
        },
    ),
    (
        "at-kleinunternehmer-eu-verified-services",
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            supply_kind="services",
            seller_is_kleinunternehmer=True,
        ),
        {
            "treatment": "small_business_exemption",
            "code": "EU-KU",
            "rate": "0",
            "rule": 10,
        },
    ),
    (
        "at-kleinunternehmer-non-eu-goods",
        "at",
        TaxInputs(
            "AT", "US", supply_kind="goods", seller_is_kleinunternehmer=True
        ),
        {
            "treatment": "small_business_exemption",
            "code": "EU-KU",
            "rate": "0",
            "rule": 10,
        },
    ),
    (
        "at-kleinunternehmer-private",
        "at",
        TaxInputs("AT", "FR", buyer_type="private", seller_is_kleinunternehmer=True),
        {
            "treatment": "small_business_exemption",
            "code": "EU-KU",
            "rate": "0",
            "rule": 10,
        },
    ),
    # ── SCOPE §12 S2b: the studio item ──────────────────────────────────
    (
        # "Studio rental is Austrian VAT whatever the customer." The reverse
        # charge the rules would have proposed comes back as
        # `would_have_applied`, which is what P2's yellow warning shows.
        "s2b-studio-item-override-to-foreign-business",
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            supply_kind="services",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": "override:item",
            "warnings": set(),
            "would": ("eu_reverse_charge", "EU-AE"),
        },
    ),
    (
        # The same override on a DOMESTIC sale does not apply — there is
        # nothing to override — so the rules decide and nothing is flagged.
        "s2b-override-does-not-apply-domestically",
        "at",
        TaxInputs(
            "AT",
            "AT",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": 20,
            "would": None,
        },
    ),
    (
        # Nor to a private buyer abroad: the rules already answer domestic.
        "s2b-override-does-not-apply-to-a-private-buyer",
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_type="private",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
        {"treatment": "domestic", "code": "AT-20", "rule": 60, "would": None},
    ),
    (
        # A non-EU business buyer too: "foreign" is not "EU".
        "s2b-override-applies-outside-the-eu-as-well",
        "at",
        TaxInputs(
            "AT",
            "US",
            supply_kind="goods",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": "override:item",
            "would": ("export", "EU-G"),
        },
    ),
    # ── Fixed-code overrides, and the precedence between them ───────────
    (
        "party-override-to-a-fixed-code",
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            supply_kind="services",
            party_override=Override(OVERRIDE_FIXED_CODE, code="AT-20"),
        ),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rate": "20",
            "rule": "override:party",
            "would": ("eu_reverse_charge", "EU-AE"),
        },
    ),
    (
        # The decision under test: item beats party. [Guessing — §16 D48.]
        "item-override-beats-party-override",
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            supply_kind="services",
            item_override=Override(OVERRIDE_FIXED_CODE, code="AT-10"),
            party_override=Override(OVERRIDE_FIXED_CODE, code="AT-20"),
        ),
        {
            "treatment": "domestic",
            "code": "AT-10",
            "rate": "10",
            "rule": "override:item",
            "would": ("eu_reverse_charge", "EU-AE"),
        },
    ),
    (
        # An item override that does not apply falls through to the PARTY
        # override, not straight to the rules.
        "an-inapplicable-item-override-falls-through-to-the-party-one",
        "at",
        TaxInputs(
            "AT",
            "AT",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
            party_override=Override(OVERRIDE_FIXED_CODE, code="AT-10"),
        ),
        {
            "treatment": "domestic",
            "code": "AT-10",
            "rate": "10",
            "rule": "override:party",
            "would": ("domestic", "AT-20"),
        },
    ),
    (
        # An override can point at a treatment code too, and the treatment
        # follows the code's EN 16931 category.
        "override-to-a-treatment-code-takes-its-treatment",
        "at",
        TaxInputs(
            "AT",
            "AT",
            item_override=Override(OVERRIDE_FIXED_CODE, code="EU-AE"),
        ),
        {
            "treatment": "eu_reverse_charge",
            "code": "EU-AE",
            "rate": "0",
            "rule": "override:item",
            "would": ("domestic", "AT-20"),
        },
    ),
    (
        # An override to the SAME code the rules chose is not a change, so
        # there is nothing for P2 to warn about.
        "an-override-that-changes-nothing-reports-nothing",
        "at",
        TaxInputs(
            "AT",
            "AT",
            item_override=Override(OVERRIDE_FIXED_CODE, code="AT-20"),
        ),
        {
            "treatment": "domestic",
            "code": "AT-20",
            "rule": "override:item",
            "would": None,
        },
    ),
    # ── Germany ─────────────────────────────────────────────────────────
    (
        "de-domestic-standard",
        "de",
        TaxInputs("DE", "DE", item_class="standard"),
        {
            "treatment": "domestic",
            "code": "DE-19",
            "rate": "19",
            "rule": 20,
            "warnings": set(),
        },
    ),
    (
        "de-domestic-reduced",
        "de",
        TaxInputs("DE", "DE", item_class="reduced"),
        {"treatment": "domestic", "code": "DE-7", "rate": "7", "rule": 20},
    ),
    (
        "de-domestic-zero",
        "de",
        TaxInputs("DE", "DE", item_class="zero"),
        {"treatment": "domestic", "code": "DE-0", "rate": "0", "rule": 20},
    ),
    (
        "de-at-business-verified-services",
        "de",
        TaxInputs("DE", "AT", buyer_vat_verified=True, supply_kind="services"),
        {
            "treatment": "eu_reverse_charge",
            "code": "EU-AE",
            "rate": "0",
            "rule": 30,
        },
    ),
    (
        "de-at-business-verified-goods",
        "de",
        TaxInputs("DE", "AT", buyer_vat_verified=True, supply_kind="goods"),
        {
            "treatment": "intra_community_supply",
            "code": "EU-K",
            "rate": "0",
            "rule": 40,
        },
    ),
    (
        "de-at-business-unverified",
        "de",
        TaxInputs("DE", "AT", buyer_vat_verified=False),
        {
            "treatment": "domestic",
            "code": "DE-19",
            "rate": "19",
            "rule": 50,
            "warnings": {"buyer_vat_id_not_verified"},
        },
    ),
    (
        "de-at-private",
        "de",
        TaxInputs("DE", "AT", buyer_type="private"),
        {"treatment": "domestic", "code": "DE-19", "rate": "19", "rule": 60},
    ),
    (
        "de-us-business-services",
        "de",
        TaxInputs("DE", "US", supply_kind="services"),
        {
            "treatment": "not_taxable_in_country",
            "code": "EU-O",
            "rate": "0",
            "rule": 70,
        },
    ),
    (
        "de-us-business-goods",
        "de",
        TaxInputs("DE", "US", supply_kind="goods"),
        {"treatment": "export", "code": "EU-G", "rate": "0", "rule": 80},
    ),
    (
        "de-kleinunternehmer",
        "de",
        TaxInputs("DE", "DE", seller_is_kleinunternehmer=True),
        {
            "treatment": "small_business_exemption",
            "code": "EU-KU",
            "rate": "0",
            "rule": 10,
        },
    ),
    (
        "de-s2b-studio-item-override",
        "de",
        TaxInputs(
            "DE",
            "AT",
            buyer_vat_verified=True,
            supply_kind="services",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
        {
            "treatment": "domestic",
            "code": "DE-19",
            "rate": "19",
            "rule": "override:item",
            "would": ("eu_reverse_charge", "EU-AE"),
        },
    ),
]


@pytest.mark.parametrize(
    "pack, inputs, expected",
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_the_golden_fixtures(pack, inputs, expected):
    result = _propose(pack, inputs)

    assert not result.blocking, f"blocked: {result.reasons}"
    assert result.treatment == expected["treatment"]
    assert result.code == expected["code"]
    if "rate" in expected:
        assert result.rate == Decimal(expected["rate"])

    rule = expected["rule"]
    if isinstance(rule, int):
        assert result.applied_rule_position == rule
        assert result.applied_rule and not result.applied_rule.startswith(
            "override:"
        )
    else:
        assert result.applied_rule == rule
        assert result.applied_rule_position is None

    if "warnings" in expected:
        assert set(result.warning_codes()) == expected["warnings"]
    if "note" in expected:
        assert result.legal_note == expected["note"]
    if "would" in expected:
        if expected["would"] is None:
            assert result.would_have_applied is None
        else:
            treatment, code = expected["would"]
            assert result.would_have_applied is not None
            assert result.would_have_applied.treatment == treatment
            assert result.would_have_applied.code == code


def test_the_fixture_table_covers_every_shipped_rule():
    """The guard that keeps the table honest.

    A rule nothing exercises is a rule nobody would notice stopping working —
    and the way it stops working is by never matching, which no other test
    would see.
    """
    exercised = {
        case[3]["rule"] for case in CASES if isinstance(case[3]["rule"], int)
    }
    assert exercised == {10, 20, 30, 40, 50, 60, 70, 80}


def test_the_fixture_table_covers_both_packs_and_both_override_kinds():
    packs = {case[1] for case in CASES}
    assert packs == {"at", "de"}
    rules = {case[3]["rule"] for case in CASES}
    assert "override:item" in rules and "override:party" in rules


# ─── Blocking: the cases where it must not guess ────────────────────────────


def test_a_company_without_a_country_blocks():
    """Acceptance item 8. No rule can be evaluated, so none is."""
    result = _propose("at", TaxInputs(None, "AT"))

    assert result.blocking is True
    assert result.code is None
    assert result.rate is None
    assert result.treatment is None
    assert set(result.warning_codes()) == {"home_country_missing"}
    assert "no country" in " ".join(result.reasons)


@pytest.mark.parametrize("country", [None, "", "   "])
def test_an_empty_country_counts_as_missing(country):
    assert _propose("at", TaxInputs(country, "AT")).blocking is True


def test_a_buyer_without_a_country_blocks():
    """Its own warning code: there is no way to tell a domestic sale from an
    export, and the person who can fix it is not the person who sets the
    company's country."""
    result = _propose("at", TaxInputs("AT", None))

    assert result.blocking is True
    assert result.rate is None
    assert set(result.warning_codes()) == {"buyer_country_missing"}


def test_an_inactive_resolved_code_blocks_rather_than_proposing_zero():
    """The generic pack's standard code, which is exactly this case.

    It is 0 % and inactive, and 0 % is a real number the engine could have
    returned. It does not: a company in a country FilmBill has no pack for
    must be told to set its rate, not handed one.
    """
    result = _propose("eu_generic", TaxInputs("FR", "FR"))

    assert result.blocking is True
    assert result.rate is None, "a rate came back from an inactive code"
    assert set(result.warning_codes()) == {"code_inactive"}
    assert "inactive" in " ".join(result.reasons)


def test_an_unknown_item_class_blocks_and_says_which_one():
    result = _propose("at", TaxInputs("AT", "AT", item_class="luxury"))

    assert result.blocking is True
    assert "luxury" in " ".join(result.reasons)


def test_an_override_naming_a_code_the_company_does_not_have_blocks():
    result = _propose(
        "at",
        TaxInputs(
            "AT",
            "AT",
            item_override=Override(OVERRIDE_FIXED_CODE, code="XX-99"),
        ),
    )

    assert result.blocking is True
    assert "XX-99" in " ".join(result.reasons)


def test_an_override_of_an_unknown_kind_blocks_rather_than_being_ignored():
    """Ignoring it would silently charge the rules' answer on a line somebody
    had deliberately overridden."""
    result = _propose(
        "at", TaxInputs("AT", "AT", item_override=Override("whatever"))
    )

    assert result.blocking is True
    assert "unknown kind" in " ".join(result.reasons)


def test_no_matching_rule_blocks():
    """Built by hand, because no shipped pack can reach it: a company that
    deactivated every rule must be told so, not given a default."""
    codes = [TaxCodeData("AT-20", "20 %", Decimal("20"), "S")]
    classes = [TaxClassData("standard", "Std", "AT-20")]
    rules = [
        TaxRuleData(
            10,
            "Off",
            {"buyer_region": "same_country"},
            {"treatment": "domestic", "code": {"class": "@item"}},
            is_active=False,
        )
    ]

    result = propose_tax(TaxInputs("AT", "AT"), codes, classes, rules)

    assert result.blocking is True
    assert "No tax rule matched" in " ".join(result.reasons)


def test_a_rule_pointing_at_a_missing_code_blocks():
    codes = [TaxCodeData("AT-20", "20 %", Decimal("20"), "S")]
    classes = [TaxClassData("standard", "Std", "AT-20")]
    rules = [
        TaxRuleData(
            10,
            "Dangling",
            {},
            {"treatment": "eu_reverse_charge", "code": {"code": "EU-AE"}},
        )
    ]

    result = propose_tax(TaxInputs("AT", "DE"), codes, classes, rules)

    assert result.blocking is True
    assert "EU-AE" in " ".join(result.reasons)


# ─── Reasons, warnings and the shape of the answer ──────────────────────────


def test_the_reasons_are_sentences_one_per_decision_step():
    result = _propose(
        "at", TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="services")
    )

    assert result.reasons[0] == "The seller is in AT."
    assert "another EU country" in result.reasons[1]
    assert result.reasons[2] == "The buyer's VAT ID is verified."
    assert result.reasons[3] == "The supply is services."
    assert "Rule 30" in result.reasons[4]
    assert "EU-AE" in result.reasons[5]
    for reason in result.reasons:
        assert reason.endswith("."), f"not a sentence: {reason!r}"


def test_the_country_is_upper_cased_in_the_reasons_whatever_was_passed():
    result = _propose("at", TaxInputs(" at ", "de", buyer_vat_verified=True))
    assert result.reasons[0] == "The seller is in AT."
    assert "DE" in result.reasons[1]


def test_a_small_business_seller_says_so_in_its_reasons():
    result = _propose("at", TaxInputs("AT", "AT", seller_is_kleinunternehmer=True))
    assert any("small business" in reason for reason in result.reasons)


def test_every_warning_carries_a_sentence_and_not_only_a_code():
    result = _propose("at", TaxInputs("AT", "DE", buyer_vat_verified=False))

    assert result.warnings
    for warning in result.warnings:
        assert warning.code and warning.text
        assert warning.text != warning.code
        assert len(warning.text) > 30, "that is a label, not an explanation"


def test_the_unverified_warning_explains_the_consequence():
    """17c's reasoning applied to a warning: a warning that does not say what
    to do is a warning somebody dismisses."""
    result = _propose("at", TaxInputs("AT", "DE", buyer_vat_verified=False))
    text = result.warnings[0].text
    assert "Verify it" in text


def test_the_legal_note_comes_back_in_the_requested_language():
    german = _propose(
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="services"),
        lang="de",
    )
    assert german.legal_note == "Steuerschuld geht auf den Leistungsempfänger über."


def test_a_language_the_note_does_not_have_gives_no_note_rather_than_english():
    """No silent fallback. A German invoice carrying an English sentence is a
    mistake somebody has to notice; a missing one the template can show as
    missing."""
    result = _propose(
        "at",
        TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="services"),
        lang="fr",
    )
    assert result.legal_note is None
    assert result.code == "EU-AE", "the rest of the proposal is unaffected"


def test_a_code_with_no_note_raises_no_not_reviewed_warning():
    """The warning is about an UNREVIEWED note, not about a missing one.
    `AT-20` ships without a note and needs none."""
    result = _propose("at", TaxInputs("AT", "AT"))
    assert result.legal_note is None
    assert "legal_note_not_reviewed" not in result.warning_codes()


def test_a_reviewed_note_raises_no_warning():
    """Hand-built, because nothing a pack ships is reviewed — which is the
    point of the flag."""
    codes = [
        TaxCodeData(
            "EU-AE",
            "RC",
            Decimal("0"),
            "AE",
            {"en": "Reverse charge."},
            notes_reviewed=True,
        )
    ]
    classes = [TaxClassData("standard", "Std", "EU-AE")]
    rules = [
        TaxRuleData(
            10, "All", {}, {"treatment": "eu_reverse_charge", "code": {"code": "EU-AE"}}
        )
    ]

    result = propose_tax(TaxInputs("AT", "DE"), codes, classes, rules)

    assert result.legal_note == "Reverse charge."
    assert result.warning_codes() == ()


def test_the_rate_is_a_decimal_and_never_a_float():
    result = _propose("at", TaxInputs("AT", "AT"))
    assert isinstance(result.rate, Decimal)
    assert result.rate == Decimal("20")


def test_the_proposal_is_immutable():
    """It is an answer, not a working area. P2 will carry one of these
    through a finalize flow and must not be able to edit it in passing."""
    result = _propose("at", TaxInputs("AT", "AT"))
    with pytest.raises(Exception):
        result.code = "AT-10"  # type: ignore[misc]


def test_the_engine_does_not_read_the_rules_list_twice_or_mutate_it():
    """Called twice with the same tables, the same answer.

    A generator passed as `rules` would be consumed by the first call; a
    sorted-in-place list would reorder somebody else's data.
    """
    codes, classes, rules = _tables("at")
    before = list(rules)

    first = propose_tax(TaxInputs("AT", "DE", buyer_vat_verified=True), codes, classes, rules)
    second = propose_tax(TaxInputs("AT", "DE", buyer_vat_verified=True), codes, classes, rules)

    assert first == second
    assert rules == before


def test_two_rules_at_the_same_position_resolve_deterministically():
    """A tie broken by row order is a tax decision that depends on the query
    plan. Broken by name instead, which is the same on every machine."""
    codes = [
        TaxCodeData("A", "A", Decimal("1"), "S"),
        TaxCodeData("B", "B", Decimal("2"), "S"),
    ]
    classes = [TaxClassData("standard", "Std", "A")]
    first = TaxRuleData(10, "aaa", {}, {"treatment": "domestic", "code": {"code": "A"}})
    second = TaxRuleData(10, "bbb", {}, {"treatment": "domestic", "code": {"code": "B"}})

    forwards = propose_tax(TaxInputs("AT", "AT"), codes, classes, [first, second])
    backwards = propose_tax(TaxInputs("AT", "AT"), codes, classes, [second, first])

    assert forwards.code == backwards.code == "A"


def test_an_inactive_rule_is_skipped_and_the_next_one_decides():
    """Hand-built, with two rules that both match.

    Written against the shipped pack first, which taught something worth
    keeping: deactivate rule 30 and an EU business buyer with a VERIFIED VAT
    ID buying services matches NOTHING — rule 50 is for unverified ones. The
    answer is then blocking, which is right, and it is asserted in the test
    below rather than papered over here.
    """
    codes = [
        TaxCodeData("FIRST", "First", Decimal("1"), "S"),
        TaxCodeData("SECOND", "Second", Decimal("2"), "S"),
    ]
    classes = [TaxClassData("standard", "Std", "FIRST")]
    rules = [
        TaxRuleData(
            10,
            "Off",
            {"buyer_region": "same_country"},
            {"treatment": "domestic", "code": {"code": "FIRST"}},
            is_active=False,
        ),
        TaxRuleData(
            20,
            "On",
            {"buyer_region": "same_country"},
            {"treatment": "domestic", "code": {"code": "SECOND"}},
        ),
    ]

    result = propose_tax(TaxInputs("AT", "AT"), codes, classes, rules)

    assert result.code == "SECOND"
    assert result.applied_rule_position == 20


def test_deactivating_the_reverse_charge_rule_blocks_rather_than_charging_vat():
    """What happens when a company removes a shipped rule.

    An EU business buyer with a verified VAT ID buying services is covered by
    rule 30 and by nothing else — rule 50 is explicitly for UNVERIFIED ones.
    So deactivating rule 30 leaves that combination with no rule, and the
    engine says so instead of falling back to a domestic rate the company did
    not ask for. Silently charging 20 % where a reverse charge was configured
    away is the wrong kind of helpful.
    """
    codes, classes, rules = _tables("at")
    without_reverse_charge = [
        TaxRuleData(
            rule.position,
            rule.name,
            rule.conditions,
            rule.outcome,
            is_active=rule.position != 30,
        )
        for rule in rules
    ]

    result = propose_tax(
        TaxInputs("AT", "DE", buyer_vat_verified=True, supply_kind="services"),
        codes,
        classes,
        without_reverse_charge,
    )

    assert result.blocking is True
    assert result.rate is None
    assert "No tax rule matched" in " ".join(result.reasons)


@pytest.mark.parametrize("pack", ["at", "de"])
def test_the_shipped_rules_cover_every_combination_of_the_vocabulary(pack):
    """48 combinations, and not one of them may block.

    The condition vocabulary is closed, so the whole input space is
    enumerable — which makes "is the shipped rule set complete" a question
    with an answer rather than a hope. A gap here would be a sale FilmBill
    cannot price at all, found by a user rather than by this.
    """
    from itertools import product

    from apps.api.billing.tax import BUYER_REGIONS, BUYER_TYPES, SUPPLY_KINDS

    codes, classes, rules = _tables(pack)
    seller = "AT" if pack == "at" else "DE"
    buyer_for = {
        "same_country": seller,
        "eu_other": "FR",
        "non_eu": "US",
    }

    gaps = []
    for klein, region, buyer_type, verified, kind in product(
        (False, True), BUYER_REGIONS, BUYER_TYPES, (False, True), SUPPLY_KINDS
    ):
        result = propose_tax(
            TaxInputs(
                seller,
                buyer_for[region],
                buyer_type=buyer_type,
                buyer_vat_verified=verified,
                supply_kind=kind,
                seller_is_kleinunternehmer=klein,
            ),
            codes,
            classes,
            rules,
        )
        if result.blocking:
            gaps.append((klein, region, buyer_type, verified, kind))

    assert not gaps, f"combinations no shipped rule covers: {gaps}"


def test_a_true_like_integer_does_not_satisfy_a_boolean_condition():
    """`1 == True` in Python. A condition that survived a JSON round-trip as
    `1` must not match `buyer_vat_verified: true` by accident — it would turn
    "not verified" into a reverse charge."""
    codes = [TaxCodeData("EU-AE", "RC", Decimal("0"), "AE")]
    classes = [TaxClassData("standard", "Std", "EU-AE")]
    rules = [
        TaxRuleData(
            10,
            "Verified only",
            {"buyer_vat_verified": True},
            {"treatment": "eu_reverse_charge", "code": {"code": "EU-AE"}},
        )
    ]

    matched = propose_tax(
        TaxInputs("AT", "DE", buyer_vat_verified=True), codes, classes, rules
    )
    assert matched.code == "EU-AE"

    # `buyer_vat_verified=1` is not a bool, so the rule must not match and
    # there is no other rule — the answer is blocking, not a reverse charge.
    loose = propose_tax(
        TaxInputs("AT", "DE", buyer_vat_verified=1), codes, classes, rules  # type: ignore[arg-type]
    )
    assert loose.blocking is True


def test_the_eu_member_list_can_be_overridden_per_call():
    """The seam that keeps the member list out of the packs."""
    codes, classes, rules = _tables("at")

    normally = propose_tax(
        TaxInputs("AT", "GB", buyer_vat_verified=True, supply_kind="services"),
        codes,
        classes,
        rules,
    )
    assert normally.code == "EU-O"

    as_if_in = propose_tax(
        TaxInputs("AT", "GB", buyer_vat_verified=True, supply_kind="services"),
        codes,
        classes,
        rules,
        eu_member_states=frozenset({"AT", "GB"}),
    )
    assert as_if_in.code == "EU-AE"


def test_would_have_applied_carries_the_rule_it_replaced():
    """Everything P2's yellow warning needs, in one object."""
    result = _propose(
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            supply_kind="services",
            item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS),
        ),
    )

    would = result.would_have_applied
    assert would is not None
    assert would.treatment == "eu_reverse_charge"
    assert would.code == "EU-AE"
    assert would.rate == Decimal("0")
    assert would.applied_rule_position == 30
    assert "reverse charge" in would.applied_rule.lower()
    assert would.legal_note == (
        "Reverse charge: VAT to be accounted for by the recipient."
    )


def test_the_override_reason_says_which_override_it_was():
    result = _propose(
        "at",
        TaxInputs(
            "AT",
            "DE",
            buyer_vat_verified=True,
            party_override=Override(OVERRIDE_FIXED_CODE, code="AT-20"),
        ),
    )
    assert any("party override" in reason for reason in result.reasons)


def test_an_inapplicable_override_says_so_in_the_reasons():
    """17c's reasoning: a control that did nothing must say why."""
    result = _propose(
        "at",
        TaxInputs(
            "AT", "AT", item_override=Override(OVERRIDE_DOMESTIC_FOR_FOREIGN_BUSINESS)
        ),
    )
    assert any("does not apply" in reason for reason in result.reasons)
