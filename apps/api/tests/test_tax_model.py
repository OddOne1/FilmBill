"""The three tax tables: the constraints, and the migration that backfills them.

Against a real PostgreSQL, because every property here IS the database's:
a unique index, three CHECK constraints and a foreign key that refuses
`ON DELETE CASCADE`. A mocked session would accept all of them and prove
nothing (CLAUDE.md 17b).

The CHECK on the zero-rate categories is the one worth reading. "Exempt at
20 %" is not a configuration a user should be able to store, and the
application refuses it with a sentence — but the application is not the only
thing that writes to this database. A migration, a psql session and a future
bug all go round it, and the constraint does not.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from apps.api.billing.tax import CATEGORIES, ZERO_RATE_CATEGORIES
from apps.api.models.tax import TaxClass, TaxCode, TaxRule

from .company_factories import make_company

#: A rate nobody could mistake for a real one, so a row that leaks into
#: another assertion is recognisable.
PROBE_RATE = Decimal("17.5000")


def _code(company, **over) -> TaxCode:
    fields = {
        "company_id": company.id,
        "code": "XX-PROBE",
        "name": "Probe",
        "rate": PROBE_RATE,
        "category": "S",
        "legal_notes": {},
        "notes_reviewed": False,
        "reporting_mapping": {},
        "is_active": True,
        "origin_pack": None,
    }
    fields.update(over)
    return TaxCode(**fields)


# ─── The code table ─────────────────────────────────────────────────────────


def test_a_tax_code_round_trips_as_a_decimal_percent(pg_db):
    """Rule 1: a rate is a Decimal, and 20 % is stored as 20, not as 0.2."""
    company = make_company(pg_db, "Rate OG")
    pg_db.add(_code(company, code="AT-20", rate=Decimal("20")))
    pg_db.commit()

    stored = pg_db.query(TaxCode).filter(TaxCode.code == "AT-20").one()
    assert stored.rate == Decimal("20.0000")
    assert isinstance(stored.rate, Decimal)
    # Four decimal places, so a 2.5 % rate is not quietly a 3 % one.
    assert str(stored.rate) == "20.0000"


def test_the_same_code_twice_in_one_company_is_refused(pg_db):
    """`uq_tax_codes_company_code`. Two `AT-20`s means a document line can
    reference either, and which one it got would depend on row order."""
    company = make_company(pg_db, "Dup OG")
    pg_db.add(_code(company, code="AT-20"))
    pg_db.commit()

    pg_db.add(_code(company, code="AT-20", name="The other one"))
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_tax_codes_company_code" in str(caught.value)
    pg_db.rollback()


def test_the_same_code_in_two_companies_is_fine(pg_db):
    """The uniqueness is per company. Everybody's standard rate is `AT-20`."""
    a = make_company(pg_db, "A OG")
    b = make_company(pg_db, "B OG")
    pg_db.add(_code(a, code="AT-20"))
    pg_db.add(_code(b, code="AT-20"))
    pg_db.commit()

    assert pg_db.query(TaxCode).filter(TaxCode.code == "AT-20").count() == 2


@pytest.mark.parametrize("category", ZERO_RATE_CATEGORIES)
def test_a_zero_rate_category_with_a_rate_is_refused(pg_db, category):
    """Every one of the six, not one of them.

    A parametrised case per category, because the CHECK is a list and a list
    is where an omission hides: `AE` without `K` would pass a single-case
    test and let an intra-community supply carry 20 %.
    """
    company = make_company(pg_db, f"Zero {category} OG")
    pg_db.add(_code(company, code=f"X-{category}", category=category, rate=Decimal("20")))

    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_tax_codes_zero_rate_categories" in str(caught.value)
    pg_db.rollback()


@pytest.mark.parametrize("category", ZERO_RATE_CATEGORIES)
def test_a_zero_rate_category_at_zero_is_accepted(pg_db, category):
    """The other half. Without it the test above would pass against a CHECK
    that refused the category outright, which is a different bug."""
    company = make_company(pg_db, f"OK {category} OG")
    pg_db.add(_code(company, code=f"Y-{category}", category=category, rate=Decimal("0")))
    pg_db.commit()

    stored = pg_db.query(TaxCode).filter(TaxCode.code == f"Y-{category}").one()
    assert stored.category == category


@pytest.mark.parametrize("category", ("S", "L", "M"))
def test_a_positive_rate_category_keeps_its_rate(pg_db, category):
    """`S`, `L` and `M` carry real rates and must not be swept into the CHECK."""
    company = make_company(pg_db, f"Pos {category} OG")
    pg_db.add(_code(company, code=f"Z-{category}", category=category, rate=PROBE_RATE))
    pg_db.commit()

    assert (
        pg_db.query(TaxCode).filter(TaxCode.code == f"Z-{category}").one().rate
        == PROBE_RATE
    )


def test_a_category_outside_en16931_is_refused(pg_db):
    """A typo'd category is a code no e-invoice validator will accept."""
    company = make_company(pg_db, "Cat OG")
    pg_db.add(_code(company, code="BAD", category="X"))

    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_tax_codes_category" in str(caught.value)
    pg_db.rollback()


def test_a_negative_rate_is_refused(pg_db):
    company = make_company(pg_db, "Neg OG")
    pg_db.add(_code(company, code="NEG", rate=Decimal("-1")))

    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "ck_tax_codes_rate_not_negative" in str(caught.value)
    pg_db.rollback()


def test_the_check_constraint_and_the_vocabulary_agree(pg_db):
    """The two lists are written twice — here is what keeps them the same.

    `models/tax.py` spells the categories in SQL and
    `billing/tax/vocabulary.py` spells them in Python, deliberately: the
    database's floor must not import the thing it is a floor under. The cost
    of that is two literals, and this is the test that pays it.

    Driven through the DATABASE, not by comparing the two strings: every
    category the vocabulary allows is inserted, and every category it does not
    is attempted. A text comparison would pass while the CHECK was never
    created at all.
    """
    company = make_company(pg_db, "Agree OG")
    for index, category in enumerate(CATEGORIES):
        rate = Decimal("0") if category in ZERO_RATE_CATEGORIES else PROBE_RATE
        pg_db.add(_code(company, code=f"AGREE-{index}", category=category, rate=rate))
    pg_db.commit()
    assert pg_db.query(TaxCode).filter(TaxCode.company_id == company.id).count() == len(
        CATEGORIES
    )

    for category in ("X", "AA", "s"):
        pg_db.add(_code(company, code=f"NO-{category}", category=category))
        with pytest.raises(IntegrityError):
            pg_db.commit()
        pg_db.rollback()


# ─── The class table ────────────────────────────────────────────────────────


def test_a_class_points_at_a_code(pg_db):
    company = make_company(pg_db, "Class OG")
    code = _code(company, code="AT-20", rate=Decimal("20"))
    pg_db.add(code)
    pg_db.commit()

    pg_db.add(
        TaxClass(
            company_id=company.id,
            key="standard",
            label="Normalsteuersatz",
            code_id=code.id,
            position=10,
        )
    )
    pg_db.commit()

    stored = pg_db.query(TaxClass).filter(TaxClass.company_id == company.id).one()
    assert stored.key == "standard"
    assert stored.code_id == code.id


def test_the_same_class_key_twice_in_one_company_is_refused(pg_db):
    """An item names its class by key. Two `standard`s and the item's rate
    depends on which row is read first."""
    company = make_company(pg_db, "ClassDup OG")
    code = _code(company, code="AT-20", rate=Decimal("20"))
    pg_db.add(code)
    pg_db.commit()

    for label in ("First", "Second"):
        pg_db.add(
            TaxClass(
                company_id=company.id,
                key="standard",
                label=label,
                code_id=code.id,
            )
        )
    with pytest.raises(IntegrityError) as caught:
        pg_db.commit()
    assert "uq_tax_classes_company_key" in str(caught.value)
    pg_db.rollback()


def test_deleting_a_code_a_class_points_at_is_refused(pg_db):
    """`ON DELETE RESTRICT`, not CASCADE.

    Cascading would delete the class and leave every item in it with no rate
    at all — a silent repricing. Retiring a code is `is_active = false`, which
    keeps the issued documents explicable.
    """
    company = make_company(pg_db, "Restrict OG")
    code = _code(company, code="AT-20", rate=Decimal("20"))
    pg_db.add(code)
    pg_db.commit()
    pg_db.add(
        TaxClass(
            company_id=company.id, key="standard", label="Standard", code_id=code.id
        )
    )
    pg_db.commit()

    pg_db.delete(code)
    with pytest.raises(IntegrityError):
        pg_db.commit()
    pg_db.rollback()

    assert pg_db.query(TaxCode).filter(TaxCode.id == code.id).count() == 1


def test_deleting_a_company_takes_its_tax_configuration_with_it(pg_db):
    """`company_id` cascades, as `CompanyScoped` declares.

    The order matters and is the point: the class's RESTRICT on the code must
    not block the company's CASCADE, or deleting a company would be
    impossible.
    """
    company = make_company(pg_db, "Cascade OG")
    code = _code(company, code="AT-20", rate=Decimal("20"))
    pg_db.add(code)
    pg_db.commit()
    pg_db.add(
        TaxClass(
            company_id=company.id, key="standard", label="Standard", code_id=code.id
        )
    )
    pg_db.add(
        TaxRule(
            company_id=company.id,
            position=10,
            name="Probe",
            conditions={"buyer_region": "same_country"},
            outcome={"treatment": "domestic", "code": {"class": "@item"}},
        )
    )
    pg_db.commit()
    company_id = company.id

    pg_db.execute(text("DELETE FROM companies WHERE id = :i"), {"i": company_id})
    pg_db.commit()

    for model in (TaxCode, TaxClass, TaxRule):
        assert pg_db.query(model).filter(model.company_id == company_id).count() == 0


# ─── The rule table ─────────────────────────────────────────────────────────


def test_a_rule_stores_its_conditions_and_outcome_as_json(pg_db):
    """JSONB in, the same structure out — including the booleans.

    A condition of `false` that came back as the string `"false"` would be
    truthy in Python, and the rule "EU business WITHOUT a verified VAT ID"
    would match the verified case too.
    """
    company = make_company(pg_db, "Rule OG")
    pg_db.add(
        TaxRule(
            company_id=company.id,
            position=50,
            name="EU business without a verified VAT ID",
            conditions={
                "buyer_region": "eu_other",
                "buyer_type": "business",
                "buyer_vat_verified": False,
            },
            outcome={
                "treatment": "domestic",
                "code": {"class": "@item"},
                "warning": "buyer_vat_id_not_verified",
            },
        )
    )
    pg_db.commit()

    stored = pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).one()
    assert stored.conditions["buyer_vat_verified"] is False
    assert stored.outcome["code"] == {"class": "@item"}
    assert stored.outcome["warning"] == "buyer_vat_id_not_verified"


def test_two_rules_may_share_a_position(pg_db):
    """Deliberately not unique — see the model.

    Re-ordering a list through a unique column needs either a temporary
    duplicate or a deferred constraint, and this asserts the cheaper choice
    was actually made rather than merely intended.
    """
    company = make_company(pg_db, "Order OG")
    for name in ("First", "Second"):
        pg_db.add(
            TaxRule(
                company_id=company.id,
                position=10,
                name=name,
                conditions={},
                outcome={"treatment": "domestic", "code": {"class": "standard"}},
            )
        )
    pg_db.commit()

    assert pg_db.query(TaxRule).filter(TaxRule.company_id == company.id).count() == 2
