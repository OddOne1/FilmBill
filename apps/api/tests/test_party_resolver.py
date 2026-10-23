"""The inheritance rule, as a table of chains and expected sources.

Pure — no database, no client. That is the point of
`apps/api/billing/parties/` existing: "which address does this invoice use"
is the question in this system with the most ways to be subtly wrong, and a
table is the only form in which somebody who is not a programmer can review
the answer.

SCOPE §12's S5 scenario is the first case: invoice the ORF, addressed to a
named editor in a named department. The expensive mistakes are all in here —
taking the contact name from the organisation, skipping the department level,
letting the billing address fall back to a postal one — and each has a
mutation behind it.
"""

import pytest

from apps.api.billing.parties import (
    LEVEL_DEPARTMENT,
    LEVEL_ORGANISATION,
    LEVEL_PERSON,
    PartyAddressValue,
    PartyLevel,
    recipient_buyer_type,
    resolve_recipient,
    tax_inputs,
)

#: A complete organisation: the S5 scenario's ORF.
ORF_ADDRESS = PartyAddressValue(
    street="Würzburggasse 30", zip="1136", city="Wien", country="AT"
)
#: The department's own address, for the case where it has one.
DEPARTMENT_ADDRESS = PartyAddressValue(
    street="Argentinierstraße 30a", zip="1040", city="Wien", country="AT"
)
#: A person's PRIVATE address. It is a postal address, never a billing one,
#: and the test that pins that is the point of it existing.
PERSON_POSTAL = PartyAddressValue(
    street="Privatgasse 1", zip="1010", city="Wien", country="AT"
)


def organisation(**over) -> PartyLevel:
    fields = dict(
        level=LEVEL_ORGANISATION,
        id="org",
        name="ORF",
        billing_address=ORF_ADDRESS,
        vat_id="ATU16360407",
        vat_verified=True,
        language="de",
        payment_terms_days=30,
    )
    fields.update(over)
    return PartyLevel(**fields)


def department(**over) -> PartyLevel:
    fields = dict(level=LEVEL_DEPARTMENT, id="dept", name="Redaktion Kultur")
    fields.update(over)
    return PartyLevel(**fields)


def person(**over) -> PartyLevel:
    fields = dict(level=LEVEL_PERSON, id="per", name="Maria Huber")
    fields.update(over)
    return PartyLevel(**fields)


# ─── S5 and the rest of the inheritance table ───────────────────────────────


def test_s5_a_contact_under_a_department_with_no_address():
    """SCOPE §12 S5, the whole scenario in one assertion set.

    The address comes from the organisation, two levels up; the contact name
    stays the person's; the invoice is made out to the organisation. Each of
    the three is a separate decision and each has its own mutation.
    """
    resolved = resolve_recipient([person(), department(), organisation()])

    assert resolved.billing_address.value == ORF_ADDRESS
    assert resolved.billing_address.source == LEVEL_ORGANISATION
    assert resolved.contact_name.value == "Maria Huber"
    assert resolved.contact_name.source == LEVEL_PERSON
    assert resolved.invoice_name.value == "ORF"
    assert resolved.invoice_name.source == LEVEL_ORGANISATION
    assert resolved.vat_id.value == "ATU16360407"
    assert resolved.vat_id.source == LEVEL_ORGANISATION


def test_the_contact_name_never_comes_from_the_organisation():
    """The one field that does NOT inherit.

    An invoice addressed to "ORF" when it should say "Maria Huber, Redaktion
    Kultur" goes to a post room instead of to the person who ordered the
    work, and nobody notices for a month.
    """
    resolved = resolve_recipient([person(), department(), organisation()])

    assert resolved.contact_name.value != "ORF"
    assert resolved.contact_name.value == "Maria Huber"
    assert resolved.contact_name.inherited_from(LEVEL_PERSON) is False


def test_a_departments_own_address_wins_over_the_organisations():
    resolved = resolve_recipient(
        [person(), department(billing_address=DEPARTMENT_ADDRESS), organisation()]
    )

    assert resolved.billing_address.value == DEPARTMENT_ADDRESS
    assert resolved.billing_address.source == LEVEL_DEPARTMENT


def test_the_persons_own_address_wins_over_both():
    own = PartyAddressValue(street="Eigengasse 2", zip="1020", city="Wien", country="AT")
    resolved = resolve_recipient(
        [
            person(billing_address=own),
            department(billing_address=DEPARTMENT_ADDRESS),
            organisation(),
        ]
    )

    assert resolved.billing_address.value == own
    assert resolved.billing_address.source == LEVEL_PERSON


def test_the_department_level_is_not_skipped():
    """A department between the person and the organisation is consulted.

    Written as its own case because "skip the middle level" is a mutation
    that passes every other test in this file: the organisation's address is
    the right answer whenever the department has none.
    """
    resolved = resolve_recipient(
        [person(), department(billing_address=DEPARTMENT_ADDRESS, vat_id="ATU00000000"), organisation()]
    )

    assert resolved.billing_address.source == LEVEL_DEPARTMENT
    assert resolved.vat_id.value == "ATU00000000"
    assert resolved.vat_id.source == LEVEL_DEPARTMENT


def test_a_billing_address_never_falls_back_to_a_postal_one():
    """A person's private address is not where an invoice goes.

    The chain has a postal address at the nearest level and a billing
    address two levels up, and the billing one has to win. A fallback across
    KINDS would send the invoice to somebody's flat.
    """
    resolved = resolve_recipient(
        [person(postal_address=PERSON_POSTAL), department(), organisation()]
    )

    assert resolved.billing_address.value == ORF_ADDRESS
    assert resolved.billing_address.source == LEVEL_ORGANISATION


def test_an_address_with_no_street_and_no_city_does_not_shadow_a_real_one():
    """A department created for its name alone has no address.

    `department or organisation` would pick it, and the invoice would go out
    with a country and nothing else. Both halves are checked: the
    almost-empty one loses, and one with a street wins.
    """
    almost = PartyAddressValue(country="AT")
    resolved = resolve_recipient(
        [person(), department(billing_address=almost), organisation()]
    )

    assert resolved.billing_address.value == ORF_ADDRESS
    assert resolved.billing_address.source == LEVEL_ORGANISATION

    with_city = PartyAddressValue(city="Graz", country="AT")
    resolved = resolve_recipient(
        [person(), department(billing_address=with_city), organisation()]
    )
    assert resolved.billing_address.source == LEVEL_DEPARTMENT


def test_a_blank_string_counts_as_absent():
    """A form posting `""` for an untouched box must not shadow a parent."""
    resolved = resolve_recipient(
        [person(language="   "), department(language=""), organisation()]
    )

    assert resolved.language.value == "de"
    assert resolved.language.source == LEVEL_ORGANISATION


def test_a_person_with_no_parent_resolves_from_itself():
    own = PartyAddressValue(street="Allein 1", zip="1010", city="Wien", country="AT")
    resolved = resolve_recipient(
        [person(billing_address=own, vat_id="ATU99999999")]
    )

    assert resolved.own_level == LEVEL_PERSON
    assert resolved.billing_address.source == LEVEL_PERSON
    assert resolved.contact_name.value == "Maria Huber"
    # With no organisation above, the invoice is made out to the person.
    assert resolved.invoice_name.value == "Maria Huber"
    assert resolved.invoice_name.source == LEVEL_PERSON


def test_an_organisation_on_its_own_resolves_from_itself():
    resolved = resolve_recipient([organisation()])

    assert resolved.contact_name.value == "ORF"
    assert resolved.invoice_name.value == "ORF"
    assert resolved.billing_address.source == LEVEL_ORGANISATION


def test_the_verified_flag_travels_with_the_vat_id_it_belongs_to():
    """Resolved separately it would eventually describe a different number.

    The organisation's number is verified; the department has its own and
    unverified one. The department's wins, and so does its flag — otherwise
    a reverse charge would be proposed on the organisation's evidence for a
    number that is not the organisation's.
    """
    resolved = resolve_recipient(
        [
            person(),
            department(vat_id="ATU00000000", vat_verified=False),
            organisation(vat_id="ATU16360407", vat_verified=True),
        ]
    )

    assert resolved.vat_id.value == "ATU00000000"
    assert resolved.vat_verified.value is False
    assert resolved.vat_verified.source == LEVEL_DEPARTMENT
    assert tax_inputs(resolved)["buyer_vat_verified"] is False


def test_an_empty_chain_is_a_programming_error():
    with pytest.raises(ValueError) as caught:
        resolve_recipient([])
    assert "at least the party itself" in str(caught.value)


def test_a_resolved_recipient_is_a_detached_immutable_snapshot():
    """P2 stores one of these on a finalized document (rule 3).

    Two properties: it cannot be edited afterwards, and it does not change
    when the rows it came from change. The second is the one that matters —
    an ORM row would silently follow the organisation to its new office and
    restate an issued invoice.
    """
    org = organisation()
    before = resolve_recipient([person(), department(), org])

    with pytest.raises(Exception):
        before.billing_address = None  # type: ignore[misc]

    moved = organisation(
        billing_address=PartyAddressValue(
            street="Neue Gasse 1", zip="1030", city="Wien", country="AT"
        )
    )
    after = resolve_recipient([person(), department(), moved])

    assert after.billing_address.value.street == "Neue Gasse 1"
    assert before.billing_address.value == ORF_ADDRESS, (
        "a previously resolved recipient changed when the organisation did"
    )


def test_the_tax_country_falls_back_to_the_billing_addresss_country():
    """The only other thing in the chain that could answer it."""
    resolved = resolve_recipient([person(), department(), organisation()])
    assert resolved.tax_country.value == "AT"
    assert resolved.tax_country.source == LEVEL_ORGANISATION

    explicit = resolve_recipient(
        [person(), department(tax_country="DE"), organisation()]
    )
    assert explicit.tax_country.value == "DE"
    assert explicit.tax_country.source == LEVEL_DEPARTMENT


def test_every_resolved_field_carries_a_source():
    """A value with no provenance is one somebody has to guess the origin of,
    and on an invoice that guess is expensive."""
    resolved = resolve_recipient([person(), department(), organisation()])

    for name in (
        "contact_name",
        "invoice_name",
        "billing_address",
        "vat_id",
        "tax_country",
        "language",
        "payment_terms_days",
    ):
        entry = resolved.field(name)
        assert entry.value is not None, name
        assert entry.source in (
            LEVEL_PERSON,
            LEVEL_DEPARTMENT,
            LEVEL_ORGANISATION,
        ), name


# ─── Business status, all four reasons ──────────────────────────────────────


def test_an_organisation_is_a_business():
    resolved = resolve_recipient([organisation()])
    assert recipient_buyer_type(resolved) == ("business", "organisation")


def test_a_department_of_one_is_a_business_too():
    resolved = resolve_recipient([department(), organisation()])
    assert recipient_buyer_type(resolved) == ("business", "organisation")


def test_a_person_inside_an_organisation_is_a_business():
    resolved = resolve_recipient([person(), department(), organisation()])
    assert recipient_buyer_type(resolved) == ("business", "organisation")


def test_a_person_with_a_vat_id_is_a_business():
    """A sole trader. Treating them as private would charge domestic VAT on a
    supply that should reverse-charge — which is §17's new question."""
    resolved = resolve_recipient([person(vat_id="ATU99999999")])
    assert recipient_buyer_type(resolved) == ("business", "person_with_vat_id")


def test_a_person_without_one_is_private():
    resolved = resolve_recipient([person()])
    assert recipient_buyer_type(resolved) == ("private", "person")


@pytest.mark.parametrize("status", ["business", "private"])
def test_an_explicit_status_wins_over_the_derivation(status):
    resolved = resolve_recipient([person(business_status=status)])
    assert recipient_buyer_type(resolved) == (status, "explicit")

    # And on an organisation, in the other direction.
    resolved = resolve_recipient([organisation(business_status=status)])
    assert recipient_buyer_type(resolved) == (status, "explicit")


def test_an_explicit_status_inherits_like_everything_else():
    resolved = resolve_recipient([person(), department(), organisation(business_status="private")])
    assert recipient_buyer_type(resolved) == ("private", "explicit")


# ─── The engine's inputs ────────────────────────────────────────────────────


def test_tax_inputs_are_exactly_the_engines_buyer_fields():
    """The keys are the engine's own argument names, so a caller cannot
    misspell one and have it silently default."""
    from apps.api.billing.tax import TaxInputs

    resolved = resolve_recipient([person(), department(), organisation()])
    inputs = tax_inputs(resolved)

    engine_fields = set(TaxInputs.__dataclass_fields__)
    for key in ("buyer_country", "buyer_type", "buyer_vat_verified", "party_override"):
        assert key in inputs
        assert key in engine_fields, f"{key} is not an engine input"
    # `buyer_type_reason` is deliberately not one — it is for the screen.
    assert "buyer_type_reason" in inputs
    assert "buyer_type_reason" not in engine_fields


def test_tax_inputs_carry_the_override_in_the_engines_own_shape():
    override = {"kind": "code", "code": "AT-20"}
    resolved = resolve_recipient([person(), department(), organisation(tax_override=override)])

    assert tax_inputs(resolved)["party_override"] == override


def test_tax_inputs_for_a_private_person_abroad():
    resolved = resolve_recipient(
        [
            person(
                billing_address=PartyAddressValue(
                    street="Bahnhofstrasse 1", zip="8001", city="Zürich", country="CH"
                )
            )
        ]
    )

    assert tax_inputs(resolved) == {
        "buyer_country": "CH",
        "buyer_type": "private",
        "buyer_vat_verified": False,
        "party_override": None,
        "buyer_type_reason": "person",
    }
