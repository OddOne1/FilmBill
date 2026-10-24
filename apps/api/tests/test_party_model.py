"""The party tables and the tree's rules, against a real PostgreSQL.

Every property here is the database's or the service's: a CHECK, a partial
unique index, an append-only trigger, a parent rule that needs a join.

The theme is the one P1a-2 established and this extends: **nothing is ever
deleted**, so the constraints are about what may exist rather than about what
may be removed. And the tree is deliberately only three deep — an arbitrary
hierarchy makes "which address does this invoice use" a question with no
bounded answer.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from apps.api.models.company import CompanyRole
from apps.api.models.number_series import NumberSeries
from apps.api.models.party import (
    Party,
    PartyAddress,
    PartyBankAccount,
    PartyContactPoint,
    PartyVatCheck,
)
from apps.api.services import party_service, tax_service

from .company_factories import (
    make_company,
    make_party,
    make_party_address,
    make_party_tax_id,
    make_user,
)


@pytest.fixture
def company(pg_db):
    return make_company(pg_db, "YON Studio OG", address_country="AT")


# ─── The tree ───────────────────────────────────────────────────────────────


def test_an_organisation_cannot_have_a_parent_even_in_sql(pg_db, company):
    """The one parent rule that needs no join, so the database holds it."""
    parent = make_party(pg_db, company, name="ORF")
    pg_db.add(
        Party(
            company_id=company.id,
            type="organisation",
            name="Sub-ORF",
            parent_id=parent.id,
            tags=[],
            einvoice_routing={},
        )
    )

    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_parties_organisation_has_no_parent" in str(caught.value)
    pg_db.rollback()


def test_a_party_cannot_be_its_own_parent_even_in_sql(pg_db, company):
    row = make_party(pg_db, company, type="person", name="Maria")

    with pytest.raises(IntegrityError) as caught:
        pg_db.execute(
            text("UPDATE parties SET parent_id = :i WHERE id = :i"),
            {"i": row.id},
        )
        pg_db.commit()
    assert "ck_parties_not_its_own_parent" in str(caught.value)
    pg_db.rollback()


def test_a_type_outside_the_three_is_refused(pg_db, company):
    pg_db.add(
        Party(
            company_id=company.id,
            type="syndicate",
            name="Nope",
            tags=[],
            einvoice_routing={},
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_parties_type" in str(caught.value)
    pg_db.rollback()


def test_a_department_needs_an_organisation_above_it(pg_db, company):
    """A standalone department is not a thing that can be invoiced."""
    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="department",
            parent_id=None,
        )
    assert "belongs to an organisation" in str(caught.value)


def test_a_department_cannot_belong_to_a_person(pg_db, company):
    """Needs the parent's TYPE, so it is the service's — with a sentence that
    names what the parent actually is."""
    person = make_party(pg_db, company, type="person", name="Maria")

    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="department",
            parent_id=person.id,
        )
    message = str(caught.value)
    assert "cannot belong to a person" in message
    assert "Maria" in message


def test_a_person_may_belong_to_an_organisation_or_a_department_or_nothing(
    pg_db, company
):
    org = make_party(pg_db, company, name="ORF")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)

    assert (
        party_service.validate_parent(
            pg_db, company_id=company.id, party_type="person", parent_id=org.id
        ).id
        == org.id
    )
    assert (
        party_service.validate_parent(
            pg_db, company_id=company.id, party_type="person", parent_id=dept.id
        ).id
        == dept.id
    )
    assert (
        party_service.validate_parent(
            pg_db, company_id=company.id, party_type="person", parent_id=None
        )
        is None
    )


def test_another_companys_parent_is_not_found(pg_db, company):
    """The same 404 a stranger gets (rule 6): answering differently would
    confirm the row exists."""
    other = make_company(pg_db, "Somebody Else GmbH")
    theirs = make_party(pg_db, other, name="Their ORF")

    with pytest.raises(party_service.ParentNotFound):
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="department",
            parent_id=theirs.id,
        )


def test_every_shape_a_cycle_would_need_is_refused_by_the_type_rule(
    pg_db, company
):
    """A cycle is structurally unreachable, and that is worth writing down.

    With three types and these rules there is no sequence of valid edits
    that closes a loop: an organisation may have no parent at all, a
    department's parent must be an organisation, and a person may not be a
    parent of anything. So each shape a cycle would need is refused by the
    TYPE rule, before the cycle check is consulted — which is the right
    order, because "a department cannot belong to a department" is a better
    sentence than "that would close a loop".

    The explicit cycle walk in `validate_parent` and the `id <> parent_id`
    CHECK are therefore floors under a future fourth type rather than paths
    anybody takes today. The one that IS load-bearing is `build_chain`'s
    bound, against a loop that got in by a restore or a hand-written UPDATE
    — tested below.
    """
    org = make_party(pg_db, company, name="ORF")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)
    person = make_party(pg_db, company, type="person", name="Maria", parent=dept)

    # A department under a department.
    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="department",
            parent_id=dept.id,
            party_id=dept.id,
        )
    assert "cannot belong to a department" in str(caught.value)

    # A department under a person.
    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="department",
            parent_id=person.id,
            party_id=dept.id,
        )
    assert "cannot belong to a person" in str(caught.value)

    # An organisation under anything.
    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="organisation",
            parent_id=dept.id,
            party_id=org.id,
        )
    assert "top of the tree" in str(caught.value)


def test_the_cycle_walk_refuses_a_loop_if_one_is_ever_reachable(pg_db, company):
    """The floor, exercised directly.

    Reached by handing `validate_parent` a pairing the type rule would allow
    — a person under a department — where that department has been forced
    below the person behind the validation's back. That is the only way to
    make the walk fire today, and it has to fire rather than recurse.
    """
    org = make_party(pg_db, company, name="ORF")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)
    person = make_party(pg_db, company, type="person", name="Maria", parent=dept)
    pg_db.execute(
        text("UPDATE parties SET parent_id = :p WHERE id = :d"),
        {"p": person.id, "d": dept.id},
    )
    pg_db.commit()

    with pytest.raises(party_service.PartyError) as caught:
        party_service.validate_parent(
            pg_db,
            company_id=company.id,
            party_type="person",
            parent_id=dept.id,
            party_id=person.id,
        )
    assert "inside its own hierarchy" in str(caught.value)


def test_the_tree_cannot_be_four_levels_deep(pg_db, company):
    """Only reachable by trying to hang a person off a person, which the
    type rule already refuses — so the depth check is the floor under a
    future fourth type rather than a path anybody takes today."""
    org = make_party(pg_db, company, name="ORF")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)
    person = make_party(pg_db, company, type="person", name="Maria", parent=dept)

    assert party_service._depth_of(pg_db, company.id, org) == 1
    assert party_service._depth_of(pg_db, company.id, dept) == 2
    assert party_service._depth_of(pg_db, company.id, person) == 3


def test_build_chain_is_bounded_even_against_a_cycle_in_the_rows(pg_db, company):
    """The validation refuses cycles on write; this is the floor under it.

    A loop that got in some other way — a restore, a hand-written UPDATE —
    must not hang a request.
    """
    org = make_party(pg_db, company, name="ORF")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)
    person = make_party(pg_db, company, type="person", name="Maria", parent=dept)
    # Close the loop behind the validation's back. Note WHICH loop: an
    # organisation with a parent is refused by a CHECK, so the only loop the
    # database itself permits is one the type rule would have caught —
    # department under person, person under department.
    pg_db.execute(
        text("UPDATE parties SET parent_id = :p WHERE id = :d"),
        {"p": person.id, "d": dept.id},
    )
    pg_db.commit()
    pg_db.expire_all()

    chain = party_service.build_chain(
        pg_db, pg_db.query(Party).filter(Party.id == dept.id).one()
    )

    assert len(chain) <= party_service.MAX_DEPTH + 1


# ─── Numbers, addresses, contacts, banking ──────────────────────────────────


def test_the_party_number_is_unique_per_company_when_set(pg_db, company):
    make_party(pg_db, company, name="One", party_number="K-0001")
    pg_db.add(
        Party(
            company_id=company.id,
            type="organisation",
            name="Two",
            party_number="K-0001",
            tags=[],
            einvoice_routing={},
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_parties_company_number" in str(caught.value)
    pg_db.rollback()


def test_several_parties_with_no_number_are_fine(pg_db, company):
    """The index is partial, because "no number yet" is the normal state."""
    for name in ("One", "Two", "Three"):
        make_party(pg_db, company, name=name)

    assert pg_db.query(Party).filter(Party.company_id == company.id).count() == 3


def test_the_same_party_number_in_two_companies_is_fine(pg_db, company):
    other = make_company(pg_db, "Somebody Else GmbH")
    make_party(pg_db, company, name="Ours", party_number="K-0001")
    make_party(pg_db, other, name="Theirs", party_number="K-0001")

    assert pg_db.query(Party).filter(Party.party_number == "K-0001").count() == 2


def test_a_company_gets_a_customer_and_a_supplier_series(pg_db):
    """Customer numbers use the same machinery documents do (P0b-3)."""
    from apps.api.services.company_service import create_company

    owner = make_user(pg_db, "series-owner@example.com")
    fresh = create_company(
        pg_db, owner=owner, legal_name="Fresh OG", address_country="AT"
    )
    pg_db.commit()

    series = {
        row.document_type: row
        for row in pg_db.query(NumberSeries).filter(
            NumberSeries.company_id == fresh.id
        )
    }
    assert series["customer"].pattern == "K-{SEQ}"
    assert series["customer"].reset == "never"
    assert series["customer"].padding == 4
    assert series["supplier"].pattern == "L-{SEQ}"
    assert series["supplier"].reset == "never"
    # And the eight document series are still there beside them.
    assert "invoice" in series and len(series) == 10


def test_a_number_is_allocated_when_a_party_becomes_a_customer(pg_db, company):
    tax_service.seed_tax_configuration(pg_db, company=company)
    from apps.api.services.number_series import seed_number_series

    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    party = make_party(pg_db, company, name="ORF", is_customer=True)

    number = party_service.allocate_party_number(pg_db, party=party)
    pg_db.commit()

    assert number == "K-0001"
    assert party.party_number == "K-0001"


def test_a_party_that_is_both_gets_the_customer_number(pg_db, company):
    """One party, one number, and the customer side is the one a document
    shows."""
    from apps.api.services.number_series import seed_number_series

    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    party = make_party(
        pg_db, company, name="Both ways", is_customer=True, is_supplier=True
    )

    assert party_service.allocate_party_number(pg_db, party=party).startswith("K-")


def test_a_supplier_gets_the_supplier_series(pg_db, company):
    from apps.api.services.number_series import seed_number_series

    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    party = make_party(pg_db, company, name="Kamera GmbH", is_supplier=True)

    assert party_service.allocate_party_number(pg_db, party=party) == "L-0001"


def test_a_party_that_already_has_a_number_keeps_it(pg_db, company):
    """A company migrating from another system has its own numbers."""
    from apps.api.services.number_series import seed_number_series

    seed_number_series(pg_db, company_id=company.id)
    pg_db.commit()
    party = make_party(
        pg_db, company, name="Imported", is_customer=True, party_number="ALT-42"
    )

    assert party_service.allocate_party_number(pg_db, party=party) == "ALT-42"


def test_a_party_that_is_neither_gets_no_number(pg_db, company):
    party = make_party(pg_db, company, type="person", name="Maria")
    assert party_service.allocate_party_number(pg_db, party=party) is None


def test_one_default_address_per_party_and_kind(pg_db, company):
    party = make_party(pg_db, company, name="ORF")
    make_party_address(pg_db, company, party, kind="billing", is_default=True)
    pg_db.add(
        PartyAddress(
            company_id=company.id,
            party_id=party.id,
            kind="billing",
            street="Another",
            is_default=True,
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_party_addresses_default_per_kind" in str(caught.value)
    pg_db.rollback()


def test_several_non_default_addresses_of_one_kind_are_fine(pg_db, company):
    """A production company has three delivery addresses and one usual one."""
    party = make_party(pg_db, company, name="ORF")
    make_party_address(pg_db, company, party, kind="delivery", is_default=True)
    for street in ("Halle 2", "Halle 3"):
        make_party_address(
            pg_db, company, party, kind="delivery", street=street, is_default=False
        )

    assert (
        pg_db.query(PartyAddress)
        .filter(PartyAddress.party_id == party.id)
        .count()
        == 3
    )


def test_a_billing_and_a_delivery_default_coexist(pg_db, company):
    party = make_party(pg_db, company, name="ORF")
    make_party_address(pg_db, company, party, kind="billing", is_default=True)
    make_party_address(pg_db, company, party, kind="delivery", is_default=True)

    assert (
        pg_db.query(PartyAddress)
        .filter(PartyAddress.party_id == party.id, PartyAddress.is_default.is_(True))
        .count()
        == 2
    )


def test_one_primary_contact_per_kind_and_purpose(pg_db, company):
    """"The invoice goes to accounts payable and the questions to the editor"
    is the normal arrangement, so `purpose` is part of the key."""
    party = make_party(pg_db, company, name="ORF")
    for purpose in ("general", "invoice"):
        pg_db.add(
            PartyContactPoint(
                company_id=company.id,
                party_id=party.id,
                kind="email",
                value=f"{purpose}@orf.at",
                purpose=purpose,
                is_primary=True,
            )
        )
    pg_db.commit()

    pg_db.add(
        PartyContactPoint(
            company_id=company.id,
            party_id=party.id,
            kind="email",
            value="second@orf.at",
            purpose="invoice",
            is_primary=True,
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_party_contact_points_primary" in str(caught.value)
    pg_db.rollback()


def test_the_same_iban_twice_on_one_party_is_refused(pg_db, company):
    party = make_party(pg_db, company, name="Kamera GmbH", is_supplier=True)
    for _ in range(2):
        pg_db.add(
            PartyBankAccount(
                company_id=company.id,
                party_id=party.id,
                iban="AT611904300234573201",
            )
        )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_party_bank_accounts_party_iban" in str(caught.value)
    pg_db.rollback()


# ─── The VAT-check history is append-only ───────────────────────────────────


def _a_check(
    pg_db,
    company,
    party,
    status="valid",
    vat_id="ATU16360407",
    *,
    tax_id=None,
    provider="vies",
):
    """One check row, pointed at the identifier it is a check OF.

    `tax_id_id` is not optional in practice, and this helper learned that the
    hard way: while it left the column NULL, every test asserting
    "not verified" passed because no check could be FOUND, not because the
    check said no. That is trap 18 — a row protected by the wrong guard —
    and it is why the parameter defaults to the party's primary identifier
    rather than to None.
    """
    if tax_id is None:
        tax_id = party_service.primary_tax_id(pg_db, party)
    row = PartyVatCheck(
        company_id=company.id,
        party_id=party.id,
        tax_id_id=tax_id.id if tax_id is not None else None,
        vat_id=vat_id,
        country="AT",
        kind=tax_id.kind if tax_id is not None else "vat",
        provider=provider,
        status=status,
        response={"valid": status == "valid"},
    )
    pg_db.add(row)
    pg_db.commit()
    pg_db.refresh(row)
    return row


def test_the_triggers_exist(pg_db):
    """The guard for the three tests below: they assert a refusal, and a
    refusal that never happens because the trigger was not created would
    look identical to one that did."""
    names = {
        row[0]
        for row in pg_db.execute(
            text(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = "
                "'party_vat_checks'::regclass AND NOT tgisinternal"
            )
        )
    }
    assert names == {
        "party_vat_checks_no_update_delete",
        "party_vat_checks_no_truncate",
    }


def test_a_vat_check_cannot_be_updated(pg_db, company):
    """The row is the evidence for a reverse charge. Evidence that can be
    edited afterwards is not evidence."""
    party = make_party(pg_db, company, name="ORF")
    row = _a_check(pg_db, company, party)

    with pytest.raises(Exception) as caught:
        pg_db.execute(
            text("UPDATE party_vat_checks SET status = 'valid' WHERE id = :i"),
            {"i": row.id},
        )
        pg_db.commit()
    assert "append-only" in str(caught.value)
    pg_db.rollback()


def test_a_vat_check_cannot_be_deleted(pg_db, company):
    party = make_party(pg_db, company, name="ORF")
    row = _a_check(pg_db, company, party)

    with pytest.raises(Exception) as caught:
        pg_db.execute(
            text("DELETE FROM party_vat_checks WHERE id = :i"), {"i": row.id}
        )
        pg_db.commit()
    assert "append-only" in str(caught.value)
    pg_db.rollback()


def test_the_table_cannot_be_truncated(pg_db, company):
    """A statement-level trigger, because TRUNCATE fires no row-level ones —
    the lesson from `audit_events`."""
    party = make_party(pg_db, company, name="ORF")
    _a_check(pg_db, company, party)

    with pytest.raises(Exception) as caught:
        pg_db.execute(text("TRUNCATE party_vat_checks"))
        pg_db.commit()
    assert "append-only" in str(caught.value)
    pg_db.rollback()


def test_a_status_outside_the_four_is_refused(pg_db, company):
    party = make_party(pg_db, company, name="ORF")
    pg_db.add(
        PartyVatCheck(
            company_id=company.id,
            party_id=party.id,
            vat_id="ATU1",
            country="AT",
            status="probably",
            response={},
        )
    )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_party_vat_checks_status" in str(caught.value)
    pg_db.rollback()


# ─── `buyer_vat_verified` is the latest check of the CURRENT number ─────────


def test_verified_is_the_latest_check_of_the_current_number(pg_db, company):
    party = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    assert party_service.vat_is_verified(pg_db, party) is False

    _a_check(pg_db, company, party, status="valid")
    assert party_service.vat_is_verified(pg_db, party) is True


def test_a_later_invalid_check_un_verifies(pg_db, company):
    party = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    _a_check(pg_db, company, party, status="valid")
    _a_check(pg_db, company, party, status="invalid")

    assert party_service.vat_is_verified(pg_db, party) is False


def test_a_new_identifier_is_not_verified_by_the_old_ones_check(pg_db, company):
    """Changing the number resets verification, and P1b-2 makes that
    structural rather than remembered.

    In P1b the check row stored the number it checked and the service
    compared strings. Now the check POINTS AT the identifier row, and
    changing a value retires that row and creates a new one — so an old
    check cannot follow a new number even in principle. There is no
    comparison left to get wrong, which is the stronger version of the same
    guarantee: a reset that depends on being remembered is one that
    eventually is not.
    """
    party = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    old_id = party_service.primary_tax_id(pg_db, party)
    _a_check(pg_db, company, party, status="valid", vat_id="ATU16360407")
    assert party_service.vat_is_verified(pg_db, party) is True

    # What the endpoint does on a value change: retire, then create.
    old_id.is_primary = False
    old_id.is_active = False
    new_id = make_party_tax_id(
        pg_db,
        company,
        party,
        kind="vat",
        value="ATU99999999",
        country="AT",
        is_primary=True,
    )
    pg_db.commit()

    assert party_service.vat_is_verified(pg_db, party) is False
    # And the old check is still there, still attached to the number it was
    # about — it is the evidence for invoices already issued (D59).
    assert party_service.latest_completed_check(pg_db, old_id).status == "valid"
    assert party_service.latest_completed_check(pg_db, new_id) is None


def test_an_unavailable_check_is_not_verified(pg_db, company):
    """A failure to obtain an answer is never evidence of anything."""
    party = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    _a_check(pg_db, company, party, status="unavailable")

    assert party_service.vat_is_verified(pg_db, party) is False


def test_a_party_with_no_vat_id_is_not_verified(pg_db, company):
    party = make_party(pg_db, company, name="Private person", type="person")
    assert party_service.vat_is_verified(pg_db, party) is False


# ─── Resolution from real rows ──────────────────────────────────────────────


def test_resolution_from_rows_matches_the_s5_scenario(pg_db, company):
    """The service's half: the join that turns rows into a chain.

    Covered here as well as in the pure table, because `code_id`-style joins
    are where a resolver silently loses a level.
    """
    org = make_party(pg_db, company, name="ORF", vat_id="ATU16360407")
    make_party_address(pg_db, company, org, kind="billing")
    dept = make_party(pg_db, company, type="department", name="Kultur", parent=org)
    person = make_party(pg_db, company, type="person", name="Maria", parent=dept)

    resolved = party_service.resolve(pg_db, person)

    assert resolved.contact_name.value == "Maria"
    assert resolved.contact_name.source == "person"
    assert resolved.invoice_name.value == "ORF"
    assert resolved.billing_address.source == "organisation"
    assert resolved.billing_address.value.city == "Wien"
    assert resolved.vat_id.value == "ATU16360407"
    assert resolved.vat_id.source == "organisation"
    assert resolved.ids["organisation"] == org.id


def test_an_address_with_no_default_is_still_used(pg_db, company):
    """A party with one address and nobody having ticked the box has an
    address, and refusing to see it would make the invoice say nothing."""
    org = make_party(pg_db, company, name="ORF")
    make_party_address(pg_db, company, org, kind="billing", is_default=False)

    resolved = party_service.resolve(pg_db, org)

    assert resolved.billing_address.value.city == "Wien"


def test_a_delivery_address_is_never_used_for_billing(pg_db, company):
    org = make_party(pg_db, company, name="ORF")
    make_party_address(
        pg_db, company, org, kind="delivery", street="Halle 2", city="Graz"
    )

    resolved = party_service.resolve(pg_db, org)

    assert resolved.billing_address.value is None
