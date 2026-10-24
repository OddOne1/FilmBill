"""Typed tax identifiers: the table's rules, and normalisation per kind.

Two subjects, kept apart on purpose.

**The normalisation** is pure and tested against each issuing authority's own
worked example, not against examples of our own devising — ours could be
wrong in exactly the way the implementation is, and then both would agree
and neither would be right. The Swiss arithmetic comes from eCH-0097 V5.2.0
section 2.4.2 and the Australian from `abr.business.gov.au/Help/AbnFormat`;
each is quoted in `region_packs/tax_id_formats.yaml` beside the weights.

**The table's rules** are tested against real Postgres, because the two that
matter are a partial unique index and a CHECK — neither of which exists on
SQLite, so a test that ran there would prove the harness and not the rule
(rule 17b).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.billing.parties import tax_ids as tax_id_formats
from apps.api.billing.parties.tax_ids import TaxIdFormatError, normalise
from apps.api.models.company import CompanyRole
from apps.api.models.party import PartyTaxId, PartyVatCheck
from apps.api.services import party_service, tax_service
from apps.api.services.number_series import seed_number_series

from .company_factories import (
    grant,
    make_company,
    make_party,
    make_party_tax_id,
    make_user,
)


@pytest.fixture
def company(pg_db):
    row = make_company(pg_db, "YON Studio OG", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=row)
    seed_number_series(pg_db, company_id=row.id)
    pg_db.commit()
    return row


# ─── The authorities' own arithmetic ────────────────────────────────────────


def test_the_swiss_check_digit_matches_the_standards_worked_example():
    """eCH-0097 V5.2.0 section 2.4.2 works 10932255 through by hand:
    multipliers 5 4 3 2 7 6 5 4, products 5+0+27+6+14+12+25+20 = 109,
    109 mod 11 = 10, 11 - 10 = 1, giving CHE-109322551.

    Asserted against the STANDARD's numbers at every step, so a transcription
    error in our weights cannot hide behind a matching mistake in the test.
    """
    entry = tax_id_formats.load_formats()["kinds"]["uid_ch"]
    assert entry["check_weights"] == [5, 4, 3, 2, 7, 6, 5, 4]
    assert entry["check_modulus"] == 11

    digits = "10932255"
    products = [
        int(d) * w for d, w in zip(digits, entry["check_weights"])
    ]
    assert products == [5, 0, 27, 6, 14, 12, 25, 20]
    assert sum(products) == 109
    assert sum(products) % 11 == 10
    assert tax_id_formats.ch_uid_check_digit(digits) == 1
    assert normalise("uid_ch", "CHE-109.322.551").value == "CHE109322551"


def test_a_swiss_check_digit_of_ten_means_the_number_is_never_issued():
    """The standard is explicit: "Falls sich als Prüfziffer eine 10 ergibt,
    so wird diese Nummer nicht vergeben."

    So the function returns None rather than a digit, and every candidate
    ending is refused — the alternative, picking 0 or 1, would accept a
    number that cannot exist.
    """
    # Found by search rather than asserted from memory: the test needs a
    # payload whose remainder is 1, and which one that is, is arithmetic.
    payload = next(
        candidate
        for candidate in (f"{n:08d}" for n in range(10_000_000, 10_000_200))
        if sum(
            int(d) * w for d, w in zip(candidate, [5, 4, 3, 2, 7, 6, 5, 4])
        )
        % 11
        == 1
    )
    assert tax_id_formats.ch_uid_check_digit(payload) is None
    for last in "0123456789":
        with pytest.raises(TaxIdFormatError):
            normalise("uid_ch", f"CHE{payload}{last}")


def test_a_remainder_of_zero_gives_a_check_digit_of_zero():
    """The standard's other special case: "Ergibt sich aus der Division der
    Produktensumme durch 11 eine ganze Zahl ohne Rest, wird als Prüfziffer
    die Null (0) eingesetzt." Not 11, which is what a naive
    `modulus - remainder` returns."""
    payload = next(
        candidate
        for candidate in (f"{n:08d}" for n in range(10_000_000, 10_000_200))
        if sum(
            int(d) * w for d, w in zip(candidate, [5, 4, 3, 2, 7, 6, 5, 4])
        )
        % 11
        == 0
    )
    assert tax_id_formats.ch_uid_check_digit(payload) == 0
    assert normalise("uid_ch", f"CHE{payload}0").value == f"CHE{payload}0"


def test_the_abn_check_matches_the_registers_worked_example():
    """abr.business.gov.au/Help/AbnFormat works 51 824 753 556 through:
    subtract 1 from the leading digit giving 41824753556, weights
    10 1 3 5 7 9 11 13 15 17 19, products 40+1+24+10+28+63+55+39+75+85+114
    = 534, 534 / 89 = 6 remainder 0, valid."""
    entry = tax_id_formats.load_formats()["kinds"]["abn"]
    assert entry["check_weights"] == [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    assert entry["check_modulus"] == 89

    digits = [int(c) for c in "51824753556"]
    digits[0] -= 1
    products = [d * w for d, w in zip(digits, entry["check_weights"])]
    assert products == [40, 1, 24, 10, 28, 63, 55, 39, 75, 85, 114]
    assert sum(products) == 534
    assert sum(products) % 89 == 0
    assert tax_id_formats.abn_is_valid("51824753556") is True
    assert normalise("abn", "51 824 753 556").value == "51824753556"


def test_an_abn_off_by_one_digit_is_refused():
    """The point of a check digit. `...557` differs from the register's own
    valid example by one, and must not be accepted."""
    assert tax_id_formats.abn_is_valid("51824753557") is False
    with pytest.raises(TaxIdFormatError) as caught:
        normalise("abn", "51824753557")
    # The refusal names the rule and where it is published, so somebody whose
    # real number we reject can tell us the rule is wrong.
    assert "modulus-89" in str(caught.value)
    assert "abr.business.gov.au" in str(caught.value)


# ─── Normalisation, per kind ────────────────────────────────────────────────


def test_separators_and_case_go_and_what_was_typed_is_kept():
    """Uniqueness has to mean what a human means by it: `ATU1234 5678` and
    `atu12345678` are one number, and a party must not be able to hold both
    with one of them silently winning."""
    result = normalise("vat", "  at-u 123 456 78 ")
    assert result.value == "ATU12345678"
    # And what the user typed survives, because people recognise their own
    # formatting and doubt a field that answers back differently.
    assert result.as_entered == "at-u 123 456 78"


def test_a_non_breaking_space_is_stripped():
    """The invisible one, left behind by a copy-paste from a PDF. It has cost
    an afternoon in every project that did not strip it."""
    assert normalise("vat", "ATU12345678 ").value == "ATU12345678"
    assert normalise("abn", "51 824 753 556").value == (
        "51824753556"
    )


def test_the_swiss_vat_suffix_is_recorded_and_not_validated():
    """`MWST` is the VAT register's addition, not part of eCH-0097's
    identifier. So it is stripped from the stored value — otherwise the same
    company typed two ways would be two numbers — and kept beside it, because
    the user typing it is telling us the company is VAT-registered."""
    result = normalise("uid_ch", "CHE-109.322.551 MWST")
    assert result.value == "CHE109322551"
    assert result.vat_suffix == "MWST"
    for suffix in ("TVA", "IVA"):
        assert normalise("uid_ch", f"CHE109322551 {suffix}").vat_suffix == suffix


def test_an_adm_prefix_is_accepted():
    """eCH-0097 section 2.4.1: CHE for companies, ADM for administrative
    units, and "Der numerische Teil wird nach den Regeln gemäss Kapitel
    2.4.2 im gleichen Vergabesystem generiert" — the same check digit. A
    Swiss public broadcaster could well be one."""
    assert normalise("uid_ch", "ADM-109.322.551").value == "ADM109322551"


def test_a_free_text_kind_keeps_its_own_shape():
    """A Firmenbuchnummer is `FN 123456a` and means exactly that. Mangling it
    into `FN123456A` would lose information for no gain, since there is no
    register here to normalise FOR."""
    assert normalise("company_register", "FN 123456a").value == "FN 123456a"
    assert normalise("tax_number", "12 345/6789").value == "12 345/6789"


def test_a_free_text_kind_refuses_a_pasted_paragraph():
    long = "x" * 200
    with pytest.raises(TaxIdFormatError) as caught:
        normalise("other", long)
    # And it points at the field that does take any length, rather than
    # leaving somebody to guess where the explanation should go.
    assert "notes field" in str(caught.value)


def test_an_ein_is_shape_only_and_says_why():
    """No published check digit and no register anyone may query for a third
    party's EIN. A pattern that invented a check would reject real numbers
    while proving nothing."""
    assert normalise("ein", "12-3456789").value == "123456789"
    with pytest.raises(TaxIdFormatError) as caught:
        normalise("ein", "12-345")
    assert "no public register" in str(caught.value)


def test_greek_numbers_keep_the_iso_country():
    """VIES calls Greece EL and ISO calls it GR. The stored country is the
    ISO one, because that is what `tax_country` and every region pack use;
    the provider converts on the way out."""
    assert normalise("vat", "EL123456789").country == "GR"
    assert normalise("vat", "GR123456789").country == "GR"


def test_an_unknown_kind_is_refused_by_name():
    with pytest.raises(TaxIdFormatError) as caught:
        normalise("crypto_wallet", "0xdeadbeef")
    assert "not a kind of tax identifier" in str(caught.value)
    # And lists them, so the caller does not have to read the source.
    assert "company_register" in str(caught.value)


def test_which_kinds_are_verifiable_is_data_and_says_what_it_means():
    """`uid_ch` is deliberately NOT verifiable (D61): no official
    programmatic interface to the Swiss UID register could be found, and no
    terms for automated querying are published. Format checking yes, lookup
    no — and the route to evidence is a human confirmation."""
    assert tax_id_formats.is_verifiable("vat") is True
    assert tax_id_formats.is_verifiable("abn") is True
    assert tax_id_formats.is_verifiable("uid_ch") is False
    assert tax_id_formats.is_verifiable("ein") is False
    assert tax_id_formats.is_verifiable("company_register") is False


def test_the_vat_like_list_agrees_between_the_data_and_the_model():
    """Two lists that drift would make the resolver and the validator
    disagree about which identifier is the one that counts."""
    from apps.api.models.party import VAT_LIKE_KINDS

    assert tuple(tax_id_formats.vat_like_kinds()) == VAT_LIKE_KINDS


def test_every_kind_in_the_model_is_in_the_data_file():
    """A kind the model allows but the data file has never heard of would be
    accepted by the CHECK constraint and then crash the normaliser."""
    from apps.api.models.party import TAX_ID_KINDS

    assert tuple(tax_id_formats.kinds()) == TAX_ID_KINDS


# ─── The table's rules, against real Postgres ──────────────────────────────


@pytest.mark.realdb
def test_the_same_kind_and_value_twice_is_refused(pg_db, company):
    """A duplicate is not two facts."""
    party = make_party(pg_db, company, name="ORF")
    make_party_tax_id(pg_db, company, party, value="ATU16360407")

    with pytest.raises(IntegrityError):
        make_party_tax_id(pg_db, company, party, value="ATU16360407")
    pg_db.rollback()


@pytest.mark.realdb
def test_the_same_value_under_two_kinds_is_allowed(pg_db, company):
    """Deliberately permitted: a number can legitimately be both a company
    register entry and a tax number in different countries, and refusing it
    would be a rule about strings rather than about identifiers."""
    party = make_party(pg_db, company, name="ORF")
    make_party_tax_id(pg_db, company, party, kind="tax_number", value="123456")
    make_party_tax_id(
        pg_db, company, party, kind="company_register", value="123456"
    )

    assert (
        pg_db.query(PartyTaxId).filter(PartyTaxId.party_id == party.id).count()
        == 2
    )


@pytest.mark.realdb
def test_only_one_primary_active_vat_like_identifier_is_allowed(pg_db, company):
    """The engine takes ONE buyer VAT ID. With two primaries, which one
    reached it would depend on row order — a wrong invoice nobody could
    explain afterwards."""
    party = make_party(pg_db, company, name="ORF")
    make_party_tax_id(
        pg_db, company, party, kind="vat", value="ATU16360407", is_primary=True
    )

    with pytest.raises(IntegrityError):
        make_party_tax_id(
            pg_db,
            company,
            party,
            kind="uid_ch",
            value="CHE109322551",
            is_primary=True,
        )
    pg_db.rollback()


@pytest.mark.realdb
def test_a_retired_primary_does_not_block_a_new_one(pg_db, company):
    """The index is partial for exactly this: changing a number retires the
    old row and creates a new one, and if retired rows counted, nobody could
    ever change a VAT ID."""
    party = make_party(pg_db, company, name="ORF")
    make_party_tax_id(
        pg_db,
        company,
        party,
        value="ATU16360407",
        is_primary=True,
        is_active=False,
    )
    make_party_tax_id(
        pg_db, company, party, value="ATU99999999", is_primary=True
    )

    assert party_service.primary_tax_id(pg_db, party).value == "ATU99999999"


@pytest.mark.realdb
def test_a_non_vat_like_kind_may_have_its_own_primary(pg_db, company):
    """The uniqueness is per VAT-like group, not per party: a party can
    sensibly have a primary VAT ID and a primary company register entry."""
    party = make_party(pg_db, company, name="ORF")
    make_party_tax_id(
        pg_db, company, party, kind="vat", value="ATU16360407", is_primary=True
    )
    make_party_tax_id(
        pg_db,
        company,
        party,
        kind="company_register",
        value="FN 123456a",
        is_primary=True,
    )

    assert (
        pg_db.query(PartyTaxId)
        .filter(PartyTaxId.party_id == party.id, PartyTaxId.is_primary.is_(True))
        .count()
        == 2
    )


@pytest.mark.realdb
def test_a_kind_outside_the_seven_is_refused_by_the_database(pg_db, company):
    """Not only by the service: the migration and the seed script write here
    too, and a CHECK is the only thing all three go through."""
    party = make_party(pg_db, company, name="ORF")
    pg_db.add(
        PartyTaxId(
            company_id=company.id,
            party_id=party.id,
            kind="crypto",
            value="0xdeadbeef",
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()


@pytest.mark.realdb
def test_an_empty_value_is_refused_by_the_database(pg_db, company):
    party = make_party(pg_db, company, name="ORF")
    pg_db.add(
        PartyTaxId(company_id=company.id, party_id=party.id, kind="vat", value="")
    )
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()


# ─── The check table still cannot be edited ────────────────────────────────


@pytest.mark.realdb
def test_the_append_only_triggers_survive_the_new_columns(pg_db, company):
    """Migration 0013 adds fifteen columns to `party_vat_checks` with
    ALTER TABLE ... ADD COLUMN, which does not disturb a row-level trigger.

    That sentence is in the migration's docstring, and this is what makes it
    true rather than merely claimed — the triggers are the reason a check is
    evidence, and a comment asserting they survived is not evidence of
    anything (rule 11).
    """
    party = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    tax_id = party_service.primary_tax_id(pg_db, party)
    row = PartyVatCheck(
        company_id=company.id,
        party_id=party.id,
        tax_id_id=tax_id.id,
        vat_id="ATU16360407",
        country="AT",
        kind="vat",
        provider="vies",
        status="valid",
        response={},
        registered_name="Österreichischer Rundfunk",
        name_match="match",
        qualified=True,
    )
    pg_db.add(row)
    pg_db.commit()

    with pytest.raises(Exception) as caught:
        pg_db.execute(
            PartyVatCheck.__table__.update()
            .where(PartyVatCheck.id == row.id)
            .values(status="invalid")
        )
        pg_db.commit()
    assert "append-only" in str(caught.value)
    pg_db.rollback()

    with pytest.raises(Exception):
        pg_db.execute(
            PartyVatCheck.__table__.delete().where(PartyVatCheck.id == row.id)
        )
        pg_db.commit()
    pg_db.rollback()


@pytest.mark.realdb
def test_a_manual_confirmation_cannot_be_recorded_as_qualified(pg_db, company):
    """The claim "a tax authority confirmed this" is the one nobody may make
    by accident, so the database refuses it and not only the service (D66)."""
    party = make_party(pg_db, company, name="Pacific Pictures", vat_id="123456789", tax_id_kind="ein")
    tax_id = party_service.primary_tax_id(pg_db, party)
    pg_db.add(
        PartyVatCheck(
            company_id=company.id,
            party_id=party.id,
            tax_id_id=tax_id.id if tax_id else None,
            vat_id="123456789",
            kind="ein",
            provider="manual",
            status="valid",
            qualified=True,
            response={},
        )
    )
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()


# ─── Golden fixtures: what reaches the engine ──────────────────────────────
#
# The engine itself is untouched by P1b-2. These assert that the NEW storage
# hands it exactly what the old column did, plus the two cases the old model
# could not express at all.


@pytest.mark.realdb
def test_a_departments_own_swiss_uid_overrides_the_organisations_vat_id(
    pg_db, company
):
    """Inheritance is per level and the nearest level wins — including when
    the two levels hold different KINDS of identifier.

    A real shape: an Austrian group with a Swiss subsidiary that bills in its
    own name. The department's UID is the one a document would carry, and it
    is not an EU VAT ID, so it cannot carry a reverse charge either.
    """
    org = make_party(pg_db, company, name="Group AG", vat_id="ATU16360407")
    dept = make_party(
        pg_db,
        company,
        type="department",
        name="Zürich Branch",
        parent=org,
        vat_id="CHE109322551",
        tax_id_kind="uid_ch",
        tax_country="CH",
    )
    pg_db.commit()

    resolved = party_service.resolve(pg_db, dept)

    assert resolved.vat_id.value == "CHE109322551"
    assert resolved.vat_id.source == "department"
    # The kind travels with the number, from the same level — so a screen
    # can say "Swiss UID" without guessing from the string.
    assert resolved.tax_id_kind.value == "uid_ch"
    assert resolved.tax_id_kind.source == "department"


@pytest.mark.realdb
def test_a_person_with_only_an_ein_has_no_vat_like_identifier(pg_db, company):
    """An EIN is not a VAT-like identifier, so it is not the buyer's number.

    This is the case the single column got wrong by construction: it would
    have held the EIN, the resolver would have reported it as the VAT ID, and
    `person_with_vat_id` would have made a private individual into a business
    on the strength of a US employer number.
    """
    person = make_party(
        pg_db,
        company,
        type="person",
        name="Jane Doe",
        vat_id="123456789",
        tax_id_kind="ein",
        tax_country="US",
    )
    pg_db.commit()

    resolved = party_service.resolve(pg_db, person)

    assert resolved.vat_id.value is None
    assert resolved.tax_id_kind.value is None
    from apps.api.billing.parties import recipient_buyer_type

    assert recipient_buyer_type(resolved) == ("private", "person")


@pytest.mark.realdb
def test_a_company_register_entry_alone_is_not_a_buyer_identifier(pg_db, company):
    """It says the entity exists, which nobody doubted. It is evidence of
    business status for the non-EU warning (D67) and nothing more."""
    org = make_party(
        pg_db, company, name="Alt GmbH", vat_id="FN 123456a",
        tax_id_kind="company_register",
    )
    pg_db.commit()

    resolved = party_service.resolve(pg_db, org)

    assert resolved.vat_id.value is None
    # Still a business, because it is an organisation — not because of the
    # register entry.
    from apps.api.billing.parties import recipient_buyer_type

    assert recipient_buyer_type(resolved) == ("business", "organisation")


@pytest.mark.realdb
def test_an_eu_business_without_a_verified_id_is_still_domestic(pg_db, company):
    """**The engine's behaviour must not change.** An EU business buyer whose
    number is not verified gets domestic treatment and a warning — never a
    reverse charge.

    Asserted end to end through the one engine, because this is the rule
    that costs real money if it ever flips: a reverse charge with nothing
    behind it leaves the seller owing the VAT.
    """
    from apps.api.billing.parties import tax_inputs

    org = make_party(
        pg_db, company, name="Berlin GmbH", vat_id="DE123456789",
        tax_country="DE",
    )
    pg_db.commit()

    inputs = tax_inputs(party_service.resolve(pg_db, org))
    assert inputs["buyer_type"] == "business"
    assert inputs["buyer_vat_verified"] is False
    # Nothing was relied on, so there is nothing to snapshot either (D64).
    assert inputs["tax_id_evidence"] is None

    proposal = _propose(pg_db, company, inputs)
    assert proposal.treatment == "domestic"
    assert "buyer_vat_id_not_verified" in [w.code for w in proposal.warnings]


@pytest.mark.realdb
def test_a_kleinunternehmer_gets_normal_austrian_vat_and_no_id_warning(
    pg_db, company
):
    """The case this release exists for (D65).

    An Austrian sole trader below the small-business threshold is a
    business, holds no UID, and a domestic sale to them carries ordinary
    Austrian VAT. There must be **no warning about the missing number**,
    because nothing is missing — the warning exists for an unverified
    cross-border claim, and this is neither.
    """
    from apps.api.billing.parties import recipient_buyer_type, tax_inputs

    trader = make_party(
        pg_db,
        company,
        type="person",
        name="Hans Muster",
        business_status="business",
        tax_country="AT",
        tax_status_note="Kleinunternehmer, keine UID",
    )
    pg_db.commit()

    resolved = party_service.resolve(pg_db, trader)
    # The reason says so in its own right, so the screen can explain the
    # empty field rather than leaving somebody to invent a number.
    assert recipient_buyer_type(resolved) == (
        "business",
        "explicit_without_tax_id",
    )
    assert resolved.tax_status_note.value == "Kleinunternehmer, keine UID"

    inputs = tax_inputs(resolved)
    proposal = _propose(pg_db, company, inputs)

    assert proposal.treatment == "domestic"
    # Decimal, not a string: the engine deals in Decimal throughout
    # (CLAUDE.md rule 1) and only the API serialises it.
    assert proposal.rate == Decimal("20.0000")
    assert [w.code for w in proposal.warnings] == []


@pytest.mark.realdb
def test_an_explicit_business_with_a_number_keeps_the_plain_reason(
    pg_db, company
):
    """The distinction the screen needs: `explicit` means somebody overrode
    the derivation, `explicit_without_tax_id` means there is no number to
    show and that is expected. Both are 'business'."""
    from apps.api.billing.parties import recipient_buyer_type

    trader = make_party(
        pg_db,
        company,
        type="person",
        name="Sole Trader",
        business_status="business",
        vat_id="ATU16360407",
        tax_country="AT",
    )
    pg_db.commit()

    assert recipient_buyer_type(party_service.resolve(pg_db, trader)) == (
        "business",
        "explicit",
    )


def _propose(pg_db, company, inputs: dict):
    """Run the one engine through the service the API uses.

    `tax_service.propose` rather than assembling `TaxInputs` here, because
    that function is where the seller side comes from the COMPANY and cannot
    be passed in — a fixture that built its own inputs could quietly test a
    seller that does not exist (rule 17b).
    """
    return tax_service.propose(
        pg_db,
        company=company,
        buyer_country=inputs["buyer_country"],
        buyer_type=inputs["buyer_type"],
        buyer_vat_verified=inputs["buyer_vat_verified"],
        supply_kind="services",
        party_override=inputs["party_override"],
    )


@pytest.mark.realdb
def test_a_foreign_business_without_a_number_is_domestic_not_reverse_charge(
    pg_db, company
):
    """**The rule that costs real money**, and the one case that can prove it.

    A German business with no VAT ID at all — their own small-business
    exemption, the German equivalent of a Kleinunternehmer. Being a business
    is not the same as being verified: a reverse charge needs the NUMBER,
    confirmed, and there is no number here. So the sale is domestic Austrian
    VAT with the warning, and the seller does not end up owing VAT they never
    charged.

    Written as a cross-border case deliberately. The domestic Austrian
    version of this test passes whether or not `buyer_vat_verified` is
    correct — domestic is domestic either way — so it cannot tell the rule
    from its neighbour. Only here does flipping the flag change the answer,
    which is the "pick the row only this guard protects" rule from trap 18.
    """
    from apps.api.billing.parties import recipient_buyer_type, tax_inputs

    trader = make_party(
        pg_db,
        company,
        type="person",
        name="Klaus Klein",
        business_status="business",
        tax_country="DE",
        tax_status_note="Kleinunternehmer nach §19 UStG, keine USt-IdNr.",
    )
    pg_db.commit()

    resolved = party_service.resolve(pg_db, trader)
    assert recipient_buyer_type(resolved) == (
        "business",
        "explicit_without_tax_id",
    )

    inputs = tax_inputs(resolved)
    assert inputs["buyer_country"] == "DE"
    assert inputs["buyer_type"] == "business"
    # Being told they are a business is not evidence about a number that
    # does not exist.
    assert inputs["buyer_vat_verified"] is False

    proposal = _propose(pg_db, company, inputs)
    assert proposal.treatment == "domestic"
    assert proposal.code == "AT-20"
    assert "buyer_vat_id_not_verified" in [w.code for w in proposal.warnings]


def test_the_module_raises_exactly_one_kind_of_error():
    """**One exception type out of this module, and only one.**

    This is not a style point. The first version propagated P1b's
    `VatFormatError` from the `vat` branch while the router caught only
    `TaxIdFormatError`; the escaping exception left the request's
    transaction aborted, and the symptom was the **test suite hanging** on
    teardown rather than a visible 500. A second exception type means every
    caller has to catch two, and one of them will catch one.

    Every kind is exercised with a value that must be refused, and anything
    other than `TaxIdFormatError` fails loudly with the type that leaked.
    """
    bad_per_kind = {
        # `ZZ123456789` is deliberately NOT here: a country the format
        # table does not know passes by design (P1b), so that a non-EU
        # supplier stays saveable. Asserted as an accept below.
        "vat": ["DE1", "12345678", "ATU123"],
        "uid_ch": ["CHE12345", "CHE109322552", "nonsense"],
        "abn": ["123", "51824753557", "abcdefghijk"],
        "ein": ["1234", "not digits"],
        "tax_number": ["", "   ", "x" * 200],
        "company_register": ["", "x" * 200],
        "other": ["", "x" * 200],
    }
    for kind, values in bad_per_kind.items():
        for value in values:
            try:
                normalise(kind, value)
            except TaxIdFormatError:
                continue
            except Exception as leaked:  # noqa: BLE001 - that is the subject
                raise AssertionError(
                    f"normalise({kind!r}, {value!r}) raised "
                    f"{type(leaked).__name__}, not TaxIdFormatError"
                ) from None
            raise AssertionError(
                f"normalise({kind!r}, {value!r}) was accepted"
            )


def test_a_refused_vat_id_keeps_p1bs_own_sentence():
    """The translation changes the exception type, not the message: P1b's
    per-country explanation is the useful part and a generic "invalid tax
    identifier" would throw it away."""
    with pytest.raises(TaxIdFormatError) as caught:
        normalise("vat", "ATU123")

    assert "AT VAT ID" in str(caught.value)
    assert "U[0-9]{8}" in str(caught.value)


def test_an_unknown_country_is_accepted_rather_than_guessed_at():
    """P1b's deliberate gap, re-asserted here because it looks like a hole.

    The format table covers the countries somebody wrote down. A number
    whose country it has never heard of passes: VIES decides validity, not
    this table, and refusing would make a supplier unsaveable over a
    pattern nobody has checked. The country still has to look like a
    country, and the WSDL's own length rule still applies.
    """
    accepted = normalise("vat", "ZZ123456789")
    assert accepted.value == "ZZ123456789"
    assert accepted.country == "ZZ"

    # The one officially published constraint is still enforced.
    with pytest.raises(TaxIdFormatError):
        normalise("vat", "ZZ1")
