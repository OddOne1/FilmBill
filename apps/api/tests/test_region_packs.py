"""The shipped region packs, and the loader that refuses a broken one.

Two halves, and the second is the one that earns its keep.

The first asserts what the three packs CONTAIN — the rates, the classes, the
eight rules in order — with the expected values written out rather than read
from the files under test. A test that loads a pack and compares it to itself
passes for any pack at all.

The second feeds the loader packs that are wrong in each of the ways a pack
can be wrong and asserts a readable refusal. That is the half that matters,
because the failure mode of "rules are data" is not a crash: it is a pack
that loads, looks right, and contains a rule that is merely never true. A
misspelled condition key, a class pointing at a renamed code, an exempt code
with a rate on it — each of those makes the engine fall through to the next
rule, which is how an EU reverse charge silently becomes 20 % domestic tax.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from apps.api.billing.tax import PackError
from apps.api.billing.tax.packs import (
    GENERIC_PACK,
    PACK_DIRECTORY,
    available_packs,
    load_pack,
    pack_for_country,
    pack_name_for_country,
)


# ─── What ships ─────────────────────────────────────────────────────────────


def test_the_three_shipped_packs_exist():
    assert set(available_packs()) == {"eu_generic", "at", "de"}


@pytest.mark.parametrize("name", ["eu_generic", "at", "de"])
def test_every_shipped_pack_loads(name):
    """Including the validation, which runs on load. A pack that cannot load
    is a company that cannot be created."""
    pack = load_pack(name)
    assert pack.pack == name
    assert pack.codes and pack.classes and pack.rules


def test_the_austrian_pack_has_the_rates_and_classes_scope_names():
    """Written out. `reduced` is the 10 % rate — see the pack header and
    §17 question 9(a); the prompt's own two statements disagreed."""
    pack = load_pack("at")
    rates = {entry.code: entry.rate for entry in pack.codes}

    assert rates["AT-20"] == Decimal("20")
    assert rates["AT-13"] == Decimal("13")
    assert rates["AT-10"] == Decimal("10")
    assert rates["AT-0"] == Decimal("0")

    assert rates["AT-4.9"] == Decimal("4.9")
    assert {entry.key: entry.code for entry in pack.classes} == {
        "standard": "AT-20",
        "reduced": "AT-10",
        "reduced_2": "AT-13",
        "reduced_3": "AT-4.9",
        "zero": "AT-0",
    }
    # Display order, so the picker reads standard → reduced → … → zero rather
    # than alphabetically.
    assert [entry.key for entry in pack.classes] == [
        "standard",
        "reduced",
        "reduced_2",
        "reduced_3",
        "zero",
    ]
    # The 4.9 % foodstuffs rate ships SWITCHED OFF, with its code — a
    # production company bills it roughly never, and an option in every
    # picker that nobody wants is worse than a switch somebody has to find.
    by_key = {entry.key: entry for entry in pack.classes}
    assert by_key["reduced_3"].is_active is False
    assert pack.code("AT-4.9").is_active is False
    assert all(
        by_key[key].is_active for key in ("standard", "reduced", "reduced_2", "zero")
    )
    # And Austria does NOT carry the generic pack's two unset placeholders.
    assert pack.code("EU-STD") is None
    assert pack.code("EU-RED") is None
    assert pack.countries == ("AT",)


def test_the_german_pack_has_two_positive_rates_and_no_second_reduced_class():
    pack = load_pack("de")
    rates = {entry.code: entry.rate for entry in pack.codes}

    assert rates["DE-19"] == Decimal("19")
    assert rates["DE-7"] == Decimal("7")
    assert {entry.key: entry.code for entry in pack.classes} == {
        "standard": "DE-19",
        "reduced": "DE-7",
        "zero": "DE-0",
    }
    assert pack.tax_class("reduced_2") is None
    assert pack.code("EU-STD") is None, "Germany has its own standard rate"
    assert pack.code("EU-RED") is None
    assert pack.countries == ("DE",)
    # It must not carry Austria's rates: `extends` inherits the generic pack,
    # not a sibling.
    assert not any(entry.code.startswith("AT-") for entry in pack.codes)


def test_every_rate_is_an_exact_decimal_and_never_a_float():
    """Rule 1, one layer earlier than usual: the pack is where the number is
    first written down, and `rate: 2.5` in YAML is a float."""
    for name in available_packs():
        pack = load_pack(name)
        for entry in pack.codes:
            assert isinstance(entry.rate, Decimal), f"{name}/{entry.code}"


def test_the_generic_pack_is_exactly_two_unset_rates():
    """The single most important property of the generic pack.

    A company in a country FilmBill has no pack for must not be handed a
    plausible-looking 20 %. Two rates, both 0 % and inactive, and the engine
    refuses to propose from either — the screen says "set your rate" and the
    + button is how a third one arrives (Mathias, 2026-10-23).
    """
    pack = load_pack(GENERIC_PACK)

    assert [entry.key for entry in pack.classes] == ["standard", "reduced"]
    for key, code in (("standard", "EU-STD"), ("reduced", "EU-RED")):
        entry = pack.tax_class(key)
        assert entry.code == code
        assert entry.is_active is True, "the class is usable; its rate is unset"
        rate_code = pack.code(code)
        assert rate_code.rate == Decimal("0")
        assert rate_code.category == "S"
        assert rate_code.is_active is False


def test_the_generic_pack_gives_zero_rating_no_class():
    """`EU-Z` exists as a code and nothing points at it.

    A tax class is what an ITEM is labelled with, and "zero-rated" is a
    property of a situation rather than of an item — so it is a no-tax case,
    chosen as an override. Austria and Germany both declare a `zero` class of
    their own, for rates that exist in their law.
    """
    pack = load_pack(GENERIC_PACK)

    assert pack.code("EU-Z") is not None
    assert pack.tax_class("zero") is None
    assert load_pack("at").tax_class("zero").code == "AT-0"


def test_every_treatment_code_in_the_generic_pack_is_zero_rated():
    """Reverse charge, intra-community, export, not-taxable, exempt, zero and
    the small-business code: every one is 0 % by definition, and the CHECK
    constraint in the database says so too."""
    pack = load_pack(GENERIC_PACK)
    for code in ("EU-AE", "EU-K", "EU-G", "EU-O", "EU-E", "EU-Z", "EU-KU"):
        entry = pack.code(code)
        assert entry is not None, f"{code} is missing; a rule points at it"
        assert entry.rate == Decimal("0"), code


def test_the_shipped_rules_are_scope_six_three_in_order():
    """The order IS the rule. Kleinunternehmer before everything, same-country
    before the EU cases, verified before unverified, services before goods."""
    pack = load_pack(GENERIC_PACK)

    assert [entry.position for entry in pack.rules] == [
        10, 20, 30, 40, 50, 60, 70, 80
    ]
    treatments = [entry.outcome["treatment"] for entry in pack.rules]
    assert treatments == [
        "small_business_exemption",
        "domestic",
        "eu_reverse_charge",
        "intra_community_supply",
        "domestic",
        "domestic",
        "not_taxable_in_country",
        "export",
    ]

    by_position = {entry.position: entry for entry in pack.rules}
    assert by_position[10].conditions == {"seller_is_kleinunternehmer": True}
    assert by_position[20].conditions == {"buyer_region": "same_country"}
    assert by_position[20].outcome["code"] == {"class": "@item"}
    assert by_position[30].conditions == {
        "buyer_region": "eu_other",
        "buyer_type": "business",
        "buyer_vat_verified": True,
        "supply_kind": "services",
    }
    assert by_position[40].conditions["supply_kind"] == "goods"
    assert by_position[50].conditions["buyer_vat_verified"] is False
    assert by_position[50].outcome["warning"] == "buyer_vat_id_not_verified"
    assert by_position[60].conditions == {"buyer_type": "private"}
    assert by_position[70].outcome["code"] == {"code": "EU-O"}
    assert by_position[80].outcome["code"] == {"code": "EU-G"}


def test_the_country_packs_inherit_the_rules_rather_than_repeating_them():
    """One copy of the eight rules. Eight countries with eight slightly
    different reverse-charge rules is the outcome this prevents."""
    generic = load_pack(GENERIC_PACK)
    for name in ("at", "de"):
        pack = load_pack(name)
        assert [entry.name for entry in pack.rules] == [
            entry.name for entry in generic.rules
        ]
        assert all(entry.origin_pack == GENERIC_PACK for entry in pack.rules)

    # And the YAML files really do not repeat them, which is the claim.
    for name in ("at", "de"):
        raw = yaml.safe_load((PACK_DIRECTORY / f"{name}.yaml").read_text())
        assert not raw.get("rules"), f"{name}.yaml declares its own rules"


def test_a_country_pack_carries_the_generic_treatment_codes_too():
    """An Austrian company needs `EU-AE`: its rules point at it.

    The treatment codes are inherited; the two unset RATE placeholders are
    dropped. That is the distinction `drop_codes` exists for.
    """
    pack = load_pack("at")
    codes = {entry.code: entry for entry in pack.codes}
    assert "EU-AE" in codes
    assert codes["EU-AE"].origin_pack == GENERIC_PACK
    assert codes["AT-20"].origin_pack == "at"


def test_every_legal_note_ships_in_both_languages_and_cites_no_statute():
    """Generic wording only.

    A paragraph reference nobody qualified has checked is worse on an invoice
    than no sentence at all, because it reads as authority. The digit check is
    crude on purpose: it catches `§ 19`, `Art. 138`, `UStG 1994` and anything
    else shaped like a citation.
    """
    pack = load_pack(GENERIC_PACK)
    with_notes = [entry for entry in pack.codes if entry.legal_notes]
    assert len(with_notes) >= 5, "the generic pack ships almost no notes"

    for entry in with_notes:
        assert set(entry.legal_notes) == {"en", "de"}, (
            f"{entry.code} has {sorted(entry.legal_notes)} — both languages "
            "or neither, or one of the two documents prints nothing"
        )
        for lang, text in entry.legal_notes.items():
            assert "§" not in text, f"{entry.code}/{lang} cites a paragraph"
            assert not any(char.isdigit() for char in text), (
                f"{entry.code}/{lang} contains a number, which in a legal "
                f"note is almost always a citation: {text!r}"
            )


def test_every_pack_warns_in_its_header_that_it_is_not_reviewed():
    """Asserted on the FILE, because the file is what somebody edits.

    This is a source-text assertion and it is the right one here: the subject
    IS the text of a comment addressed to a human reader (cf. CLAUDE.md
    rule 11, which is about tests that stand in for behaviour).
    """
    for name in available_packs():
        header = (PACK_DIRECTORY / f"{name}.yaml").read_text()[:2000]
        assert "NOT" in header and "REVIEWED" in header.upper(), (
            f"{name}.yaml does not say it is unreviewed"
        )
        assert "§17" in header or "SCOPE.md §17" in header, (
            f"{name}.yaml does not point at the Steuerberater questions"
        )


# ─── Country lookup ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "country, expected",
    [
        ("AT", "at"),
        ("at", "at"),
        (" AT ", "at"),
        ("DE", "de"),
        ("FR", GENERIC_PACK),
        ("US", GENERIC_PACK),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_pack_name_for_country(country, expected):
    assert pack_name_for_country(country) == expected


def test_a_company_without_a_country_gets_no_pack_rather_than_the_generic_one():
    """The decision, asserted. Seeding generically now would mean the "AT" a
    company is due never arrives, because seeding never overwrites."""
    assert pack_for_country(None) is None
    assert pack_for_country("") is None
    assert pack_for_country("AT").pack == "at"


def test_the_country_mapping_comes_from_the_packs_and_not_from_a_dict(tmp_path):
    """Adding a pack is adding a file.

    Proven by adding one: a pack claiming `SK` in a temporary directory is
    found for `SK` without any code change.
    """
    _write_minimal_generic(tmp_path)
    (tmp_path / "sk.yaml").write_text(
        "pack: sk\nname: Slovensko\nextends: eu_generic\ncountries: [SK]\n"
        "codes:\n  - code: SK-20\n    name: 20 %\n    rate: \"20\"\n"
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: SK-20\n"
    )

    assert pack_name_for_country("SK", directory=tmp_path) == "sk"
    pack = load_pack("sk", directory=tmp_path)
    assert pack.tax_class("standard").code == "SK-20"


# ─── Refusals ───────────────────────────────────────────────────────────────


def _write_minimal_generic(directory: Path) -> None:
    """A valid parent, so the tests below fail for their own reason."""
    (directory / "eu_generic.yaml").write_text(
        """
pack: eu_generic
name: Generic
countries: []
codes:
  - code: EU-STD
    name: Standard
    rate: "0"
    category: S
    active: false
  - code: EU-AE
    name: Reverse charge
    rate: "0"
    category: AE
classes:
  - key: standard
    label: Standard
    code: EU-STD
rules:
  - position: 10
    name: Same country
    conditions:
      buyer_region: same_country
    outcome:
      treatment: domestic
      code: { class: "@item" }
"""
    )


def _pack(directory: Path, body: str, name: str = "probe") -> None:
    (directory / f"{name}.yaml").write_text(body)


@pytest.mark.parametrize(
    "body, expected",
    [
        # Rates and categories.
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: 2.5\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "YAML float",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "20"\n'
            "    category: AE\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "zero-rate category",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "20"\n'
            "    category: ZZ\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "not an EN 16931 category",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "-1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "cannot be negative",
        ),
        # Duplicates and dangling references.
        (
            'pack: probe\ncodes:\n  - code: X\n    name: A\n    rate: "1"\n'
            '    category: S\n  - code: X\n    name: B\n    rate: "2"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "declared twice",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\n"
            "classes: [{key: standard, label: X, code: MISSING}]\nrules: []\n",
            "which this pack does not define",
        ),
        # The class list itself.
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: []\nrules: []\n",
            "no tax classes",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: reduced, label: X, code: X}]\n"
            "rules: []\n",
            "no `standard` tax class",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            '    category: S\nclasses: [{key: "@item", label: X, code: X}]\n'
            "rules: []\n",
            "is reserved",
        ),
        # Rules.
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules:\n  - position: 10\n    name: Typo\n    conditions:\n"
            "      buyer_vat_verfied: true\n    outcome:\n"
            "      treatment: domestic\n      code: { class: standard }\n",
            "unknown condition key",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules:\n  - position: 10\n    name: Bad code\n    conditions: {}\n"
            "    outcome:\n      treatment: domestic\n"
            "      code: { code: NOPE }\n",
            "does not define",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules:\n  - position: 10\n    name: Bad class\n    conditions: {}\n"
            "    outcome:\n      treatment: domestic\n"
            "      code: { class: nope }\n",
            "does not define",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules:\n  - position: 10\n    name: Invented\n    conditions: {}\n"
            "    outcome:\n      treatment: studio_special\n"
            "      code: { class: standard }\n",
            "system enum",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\nclasses: [{key: standard, label: X, code: X}]\n"
            "rules: []\n",
            "no rules",
        ),
        # Structure.
        ("pack: wrong_name\ncodes: []\nclasses: []\nrules: []\n", "but is named"),
        ("- a\n- b\n", "must be a mapping at the top level"),
        (
            'pack: probe\ncodes: {}\nclasses: [{key: standard, label: X, '
            "code: X}]\nrules: []\n",
            "`codes` must be a list",
        ),
        (
            'pack: probe\ncodes:\n  - code: X\n    name: X\n    rate: "1"\n'
            "    category: S\n    rat: 1\n"
            "classes: [{key: standard, label: X, code: X}]\nrules: []\n",
            "unknown key(s) rat",
        ),
    ],
)
def test_a_broken_pack_is_refused_with_a_readable_message(tmp_path, body, expected):
    _write_minimal_generic(tmp_path)
    _pack(tmp_path, body)

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    assert expected in str(caught.value), str(caught.value)


def test_the_minimal_generic_fixture_is_itself_valid(tmp_path):
    """The guard on every test above.

    If `_write_minimal_generic` were broken, each of those cases would raise
    `PackError` for the parent's reason rather than its own — and the
    substring assertions are loose enough that some would still pass. This is
    what stops them passing for the wrong reason.
    """
    _write_minimal_generic(tmp_path)
    pack = load_pack("eu_generic", directory=tmp_path)
    assert pack.tax_class("standard") is not None


def test_a_pack_that_does_not_exist_names_the_ones_that_do(tmp_path):
    _write_minimal_generic(tmp_path)
    with pytest.raises(PackError) as caught:
        load_pack("nowhere", directory=tmp_path)
    message = str(caught.value)
    assert "No region pack named 'nowhere'" in message
    assert "eu_generic" in message


def test_a_cycle_of_extends_is_refused_rather_than_recursing(tmp_path):
    """Two packs extending each other would otherwise recurse until the stack
    ran out, which is a traceback nobody can read."""
    _pack(tmp_path, "pack: a\nextends: b\ncodes: []\nclasses: []\n", name="a")
    _pack(tmp_path, "pack: b\nextends: a\ncodes: []\nclasses: []\n", name="b")

    with pytest.raises(PackError) as caught:
        load_pack("a", directory=tmp_path)
    assert "cycle" in str(caught.value)


def test_a_child_pack_replaces_a_parents_code_and_class_by_key(tmp_path):
    """What `extends` means, asserted both ways: replaced and inherited."""
    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncountries: [XX]\n'
        'codes:\n  - code: EU-STD\n    name: Ours now\n    rate: "25"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: Ours\n    code: EU-STD\n",
    )

    pack = load_pack("probe", directory=tmp_path)
    standard = pack.code("EU-STD")
    assert standard.rate == Decimal("25")
    assert standard.name == "Ours now"
    assert standard.is_active is True, "the child's `active` default applies"
    assert standard.origin_pack == "probe"
    # Inherited, untouched.
    assert pack.code("EU-AE").origin_pack == "eu_generic"
    assert [entry.name for entry in pack.rules] == ["Same country"]


def test_drop_codes_removes_an_inherited_code_and_its_orphaned_class(tmp_path):
    """What `drop_codes` is for, proven on a temporary pack.

    The parent's `standard` class points at `EU-STD`. A child that drops
    `EU-STD` and declares its own `standard` keeps a usable pack; the
    parent's class is replaced rather than left dangling.
    """
    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncountries: [XX]\n'
        "drop_codes: [EU-STD]\n"
        'codes:\n  - code: XX-20\n    name: 20 %\n    rate: "20"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: XX-20\n",
    )

    pack = load_pack("probe", directory=tmp_path)

    assert pack.code("EU-STD") is None
    assert pack.tax_class("standard").code == "XX-20"
    # The inherited treatment code is untouched: dropping is per code, not a
    # way of starting over.
    assert pack.code("EU-AE") is not None


def test_dropping_a_code_a_surviving_class_needs_is_refused(tmp_path):
    """The check that makes `drop_codes` safe.

    A pack that dropped its own standard rate and forgot to replace the class
    would load, look right, and resolve nothing for every domestic sale.
    """
    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncountries: [XX]\n'
        "drop_codes: [EU-STD]\n",
    )

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    # Either refusal is the right one and which arrives depends on how many
    # classes the parent had: the orphaned class goes with the dropped code,
    # so a pack with one class is left with none, and a pack with several is
    # left without a `standard`. Both say "tax class", and the point is that
    # it does not load.
    assert "tax class" in str(caught.value)


def test_dropping_a_code_a_rule_names_is_refused(tmp_path):
    """`EU-AE` is named by the rule this fixture declares."""
    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncountries: [XX]\n'
        "drop_codes: [EU-AE]\n"
        'codes:\n  - code: XX-20\n    name: 20 %\n    rate: "20"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: XX-20\n"
        "rules:\n  - position: 10\n    name: Reverse charge\n"
        "    conditions: {}\n    outcome:\n"
        "      treatment: eu_reverse_charge\n      code: { code: EU-AE }\n",
    )

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    assert "does not define" in str(caught.value)


def test_dropping_a_code_nobody_defines_is_refused(tmp_path):
    """A typo in `drop_codes` would otherwise be a silent no-op, and the
    author would believe a code was gone when it was not."""
    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncountries: [XX]\n'
        "drop_codes: [EU-STDD]\n"
        'codes:\n  - code: XX-20\n    name: 20 %\n    rate: "20"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: XX-20\n",
    )

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    message = str(caught.value)
    assert "EU-STDD" in message and "neither this pack" in message


def test_drop_codes_must_be_a_list_of_strings(tmp_path):
    _write_minimal_generic(tmp_path)
    _pack(tmp_path, "pack: probe\nextends: eu_generic\ndrop_codes: EU-STD\n")

    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    assert "`drop_codes` must be a list" in str(caught.value)


def test_a_class_may_ship_inactive_and_a_non_boolean_is_refused(tmp_path):
    """Austria's 4.9 % rate uses this. The refusal matters because YAML will
    happily hand you the string "false"."""
    _write_minimal_generic(tmp_path)
    body = (
        'pack: probe\nextends: eu_generic\n'
        'codes:\n  - code: XX-20\n    name: 20 %\n    rate: "20"\n'
        "    category: S\n"
        "classes:\n  - key: standard\n    label: 20 %\n    code: XX-20\n"
    )
    _pack(tmp_path, body + "    active: false\n")
    assert (
        load_pack("probe", directory=tmp_path).tax_class("standard").is_active
        is False
    )

    _pack(tmp_path, body + '    active: "false"\n')
    with pytest.raises(PackError) as caught:
        load_pack("probe", directory=tmp_path)
    assert "must be true or false" in str(caught.value)


def test_set_your_rate_is_exactly_the_unset_rates():
    """The condition the screen shows that hint on, generalised.

    Category `S`, rate 0, inactive. It must catch both generic placeholders
    and must NOT catch Austria's 4.9 % rate, which is inactive but has a real
    number in it — "set your rate" on a rate that is already set would send
    somebody looking for a field that is already filled in.
    """

    def unset(pack):
        return {
            entry.code
            for entry in pack.codes
            if entry.category == "S"
            and entry.rate == Decimal("0")
            and not entry.is_active
        }

    assert unset(load_pack(GENERIC_PACK)) == {"EU-STD", "EU-RED"}
    assert unset(load_pack("at")) == set()
    assert unset(load_pack("de")) == set()
    assert load_pack("at").code("AT-4.9").is_active is False


def test_loading_a_pack_twice_gives_the_same_thing(tmp_path):
    """No cache, no mutation, no order that depends on read order.

    Written against a temporary pack as well as the shipped ones, because a
    dict-merge that accidentally mutated its parent would show up only on the
    second load.
    """
    for _ in range(2):
        first, second = load_pack("at"), load_pack("at")
        assert first == second

    _write_minimal_generic(tmp_path)
    _pack(
        tmp_path,
        'pack: probe\nextends: eu_generic\ncodes:\n  - code: NEW\n'
        '    name: New\n    rate: "7"\n    category: S\nclasses:\n'
        "  - key: standard\n    label: New\n    code: NEW\n",
    )
    one = load_pack("probe", directory=tmp_path)
    two = load_pack("probe", directory=tmp_path)
    assert one == two
    # And the parent is not polluted by the child's declarations.
    parent = load_pack("eu_generic", directory=tmp_path)
    assert parent.code("NEW") is None
    assert parent.tax_class("standard").code == "EU-STD"
