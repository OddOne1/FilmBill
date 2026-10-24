"""Comparing a party's name with a register's.

Pure and fast. The fixtures are the ones a user will actually hit, and the
hard cases are the two that cost something:

  * a **false mismatch** sends somebody chasing a non-problem, and a warning
    that cries wolf is a warning people click past — after which the real
    one is invisible too;
  * a **false match** hides a valid VAT number belonging to somebody else,
    which is the only thing this check exists to catch.

So `partial` is a wide band on purpose, and `unknown` is its own answer
rather than a polite mismatch.
"""

import pytest

from apps.api.billing.parties import name_match
from apps.api.billing.parties.name_match import (
    MATCH,
    MISMATCH,
    PARTIAL,
    UNKNOWN,
    compare_addresses,
    compare_names,
    normalise_name,
)


# ─── The golden fixtures ────────────────────────────────────────────────────


def test_a_register_holding_the_full_legal_name_is_partial_not_mismatch():
    """"ORF Österreichischer Rundfunk" against "Österreichischer Rundfunk".

    **`partial`, and here is why.** Every word the register has appears in
    the party's name, so containment is complete and the score starts at 96;
    the one extra word ("ORF") costs 6, giving 90 — just under the match
    threshold of 92.

    That is the intended answer rather than a near miss. The two names are
    almost certainly the same company, and `partial` says exactly that:
    "close but not the same, worth a look". Calling it `match` would mean a
    register answer two words short of the party's name never raises
    anything, which is the shape a wrong-number-right-company mix-up takes.
    """
    comparison = compare_names(
        "ORF Österreichischer Rundfunk", "Österreichischer Rundfunk"
    )
    assert comparison.result == PARTIAL
    assert comparison.score == 90
    assert "close but not the same" in comparison.detail


def test_a_spelled_out_legal_form_still_matches():
    """"Mustermann GmbH" against "MUSTERMANN GESELLSCHAFT M.B.H.".

    The same company, and no amount of string distance would say so while
    the legal form is still in the string — the form is longer than the
    name. This is the case the legal-form list exists for.
    """
    comparison = compare_names("Mustermann GmbH", "MUSTERMANN GESELLSCHAFT M.B.H.")
    assert comparison.result == MATCH
    assert comparison.score == 100
    # And it shows what it compared, so a reviewer can see what the stripping
    # did rather than having to trust it.
    assert comparison.normalised_party == "mustermann"
    assert comparison.normalised_registry == "mustermann"


def test_a_different_company_is_a_mismatch():
    comparison = compare_names("Alpha Film GmbH", "Beta Media GmbH")
    assert comparison.result == MISMATCH
    assert "belonging to somebody else is exactly what this check is for" in (
        comparison.detail
    )


def test_a_placeholder_is_unknown_and_says_germany_always_does_this():
    """Germany returns `---` for every VIES lookup — it does not disclose
    names. Reporting that as a mismatch would fire on every German customer,
    every time, which is how a warning becomes noise."""
    comparison = compare_names("Berlin Produktion GmbH", "---")
    assert comparison.result == UNKNOWN
    assert comparison.score == 0
    assert "Germany answers every lookup this way" in comparison.detail


@pytest.mark.parametrize("placeholder", ["---", "--", "-", "", "   ", "N/A", "n.a.", "***", "unknown"])
def test_every_placeholder_shape_is_unknown(placeholder):
    """Checked on the folded value, so `"- - -"` and `"N/A"` land here
    rather than three of them being compared as if they were names."""
    assert compare_names("Alpha GmbH", placeholder).result == UNKNOWN


def test_eszett_and_diacritics_fold_together():
    """"Straßen GmbH" and "Strassen Gesellschaft m.b.H." are one company.

    `ß` needs its own rule: NFKD leaves it alone because it has no
    decomposition, so without the transliteration it would survive and the
    two names would differ by a character.
    """
    assert compare_names("Straßen GmbH", "Strassen Gesellschaft m.b.H.").result == (
        MATCH
    )
    assert compare_names("Zürich Film AG", "Zuerich Film Aktiengesellschaft").result in (
        MATCH,
        PARTIAL,
    )
    assert compare_names("Österreichische Post AG", "Osterreichische Post AG").result == (
        MATCH
    )


def test_punctuation_alone_never_makes_a_difference():
    """Registers differ on commas and hyphens by convention, not by
    company."""
    for left, right in [
        ("Alpha-Film, GmbH", "Alpha Film GmbH"),
        ("Meier & Sohn KG", "Meier und Sohn KG"),
        ("A.B.C. Media Ltd", "ABC Media Limited"),
    ]:
        assert compare_names(left, right).result == MATCH, (left, right)


def test_word_order_is_not_identity():
    """Registers routinely invert the parts of a name, and a human has no
    doubt about it. Sorting the tokens is the one aggressive step in the
    normaliser and this is what earns it."""
    assert compare_names(
        "Rundfunk Österreichischer GmbH", "Österreichischer Rundfunk GmbH"
    ).result == MATCH


def test_a_legal_form_inside_a_word_is_not_stripped():
    """"ag" must not be removed from "Hagenberg".

    The bounded regex matters: without it, "Hagenberg Software AG" would
    normalise to something with a hole in it and compare against nothing
    sensible.
    """
    assert normalise_name("Hagenberg Software AG") == "hagenberg software"
    assert "hagenberg" in normalise_name("Hagenberg AG")


def test_a_longer_legal_form_is_stripped_before_a_shorter_one():
    """"gesellschaft mit beschränkter haftung" has to go before
    "gesellschaft", or "mit beschrankter haftung" is left behind as if it
    were part of the name."""
    assert normalise_name(
        "Beispiel Gesellschaft mit beschränkter Haftung"
    ) == "beispiel"


def test_a_name_that_is_only_a_legal_form_does_not_become_nothing():
    """"Die Firma GmbH" is a real thing to be called. Reducing it to the
    empty string would make it compare equal to every other such name."""
    assert normalise_name("Die Firma GmbH") != ""
    assert compare_names("Die Firma GmbH", "Die Firma GmbH").result == MATCH


def test_an_extra_word_costs_a_little_and_four_cost_a_lot():
    """The gradient `partial` relies on: one extra word is close, five is
    not the same company."""
    one = compare_names("Alpha Film Produktion", "Alpha Film")
    many = compare_names("Alpha Film Beta Gamma Delta Epsilon Zeta", "Alpha Film")

    assert one.result == PARTIAL
    assert one.score > many.score
    assert many.result in (PARTIAL, MISMATCH)


def test_a_party_with_no_name_is_unknown_not_a_mismatch():
    assert compare_names("", "Alpha GmbH").result == UNKNOWN
    assert compare_names("   ", "Alpha GmbH").result == UNKNOWN


def test_every_verdict_carries_a_sentence():
    """A chip that says "mismatch" with nothing beside it is a chip people
    learn to ignore — rule 17c's reasoning applied to a result."""
    for left, right in [
        ("Alpha GmbH", "Alpha GmbH"),
        ("Alpha GmbH", "Alpha Film GmbH"),
        ("Alpha GmbH", "Beta GmbH"),
        ("Alpha GmbH", "---"),
    ]:
        comparison = compare_names(left, right)
        assert comparison.detail.strip(), (left, right)
        assert comparison.result in name_match.RESULTS


def test_the_comparison_never_changes_either_name():
    """It is a judgement with no side effects. FilmBill does not write a
    register's name onto a party: registers hold abbreviated and stale
    forms, and the party's name is what goes on an invoice."""
    party = "Alpha Film GmbH"
    registry = "ALPHA FILM GESELLSCHAFT M.B.H."
    compare_names(party, registry)

    assert party == "Alpha Film GmbH"
    assert registry == "ALPHA FILM GESELLSCHAFT M.B.H."


# ─── Addresses, informational only ─────────────────────────────────────────


def test_a_transliterated_address_agrees():
    """A register writing "Wuerzburggasse" must not be reported as differing
    from our "Würzburggasse"."""
    assert compare_addresses(
        "Würzburggasse 30, 1136 Wien", "Wuerzburggasse 30, 1136 Wien"
    ) == "match"


def test_a_different_address_differs():
    assert compare_addresses(
        "Hauptstrasse 1, 1010 Wien", "Nebenweg 9, 5020 Salzburg"
    ) == "differs"


def test_a_missing_address_on_either_side_is_unknown():
    """Not `differs`: we have nothing to compare, and saying "differs" would
    put a warning on every party whose address we never asked for."""
    assert compare_addresses(None, "Hauptstrasse 1") == "unknown"
    assert compare_addresses("Hauptstrasse 1", None) == "unknown"
    assert compare_addresses("Hauptstrasse 1", "---") == "unknown"


def test_the_address_comparison_has_no_partial():
    """Three values, because nobody acts differently on a fourth. It is
    informational — an address that differs is worth seeing, not worth
    blocking anything."""
    for left, right in [
        ("A 1, 1010 Wien", "A 1, 1010 Wien"),
        ("A 1, 1010 Wien", "B 2, 5020 Salzburg"),
        ("A 1, 1010 Wien", None),
    ]:
        assert compare_addresses(left, right) in ("match", "differs", "unknown")


# ─── The data file is the source ───────────────────────────────────────────


def test_the_thresholds_come_from_the_data_file():
    """Not constants in the module: the bands will be tuned against real
    data, and tuning them must not need a release (rule 7)."""
    data = name_match._data()
    assert data["thresholds"]["match"] > data["thresholds"]["partial"]
    assert 0 < data["thresholds"]["partial"] < data["thresholds"]["match"] <= 100


def test_adding_a_legal_form_to_the_data_file_changes_the_answer():
    """The data file is load-bearing, not decoration.

    Asserted by behaviour: a made-up legal form is NOT stripped, so the two
    names differ; with it in the list they would match. That is the
    difference between data that is read and data that is merely present.
    """
    name_match._legal_forms.cache_clear()
    name_match._data.cache_clear()
    before = compare_names("Alpha Zzzgmbh", "Alpha")
    assert before.result in (PARTIAL, MISMATCH)

    data = name_match._data()
    data["legal_forms"].append("zzzgmbh")
    name_match._legal_forms.cache_clear()
    try:
        after = compare_names("Alpha Zzzgmbh", "Alpha")
        assert after.result == MATCH
    finally:
        data["legal_forms"].remove("zzzgmbh")
        name_match._legal_forms.cache_clear()
        name_match._data.cache_clear()
