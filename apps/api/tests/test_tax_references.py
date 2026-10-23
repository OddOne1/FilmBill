"""The reference guard: what still points at a tax code or class.

**The failure this prevents is silent.** Nothing is ever deleted, so
retiring a rate or a no-tax case means deactivating it, and a deactivated
code is one the engine refuses to propose from. Switch off `EU-AE` and the
reverse-charge rule still matches, still resolves to it, and every EU B2B
sale becomes unpriceable — `blocking`, with no indication which switch did
it, surfacing in P2's finalize to somebody who did not make the change.

So the tests here are in two halves. One half proves a referenced code is
refused with the referrer NAMED, because a count is not something anybody
can act on. The other half proves the registration point works — a referrer
registered at test time is honoured — because P1b, P1c and P2 each add one,
and a guard that silently ignores a new source is worse than no guard: it
reads as protection.
"""

import uuid

import pytest

from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxClass, TaxCode, TaxRule
from apps.api.services import tax_references, tax_service
from apps.api.services.tax_references import (
    Referrer,
    references_to_class,
    references_to_code,
)

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)


@pytest.fixture
def seeded(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "guard-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _code(pg_db, company, code: str) -> TaxCode:
    return (
        pg_db.query(TaxCode)
        .filter(TaxCode.company_id == company.id, TaxCode.code == code)
        .one()
    )


def _class(pg_db, company, key: str) -> TaxClass:
    return (
        pg_db.query(TaxClass)
        .filter(TaxClass.company_id == company.id, TaxClass.key == key)
        .one()
    )


# ─── What the function finds ─────────────────────────────────────────────────


def test_a_code_a_rule_names_is_reported_with_the_rules_name(pg_db, seeded):
    """`EU-AE` is named by the shipped reverse-charge rule."""
    found = references_to_code(pg_db, _code(pg_db, seeded["company"], "EU-AE").id)

    assert [referrer.kind for referrer in found] == ["rule"]
    assert "reverse charge" in found[0].name.lower()
    assert "rule “" in str(found[0])


def test_a_code_nothing_names_is_unreferenced(pg_db, seeded):
    """`AT-13` is a rate the shipped rules reach only through `@item`."""
    assert references_to_code(pg_db, _code(pg_db, seeded["company"], "AT-13").id) == []


def test_an_inactive_rule_is_not_a_reference(pg_db, seeded):
    """A deactivated rule resolves nothing, so it is not a reason to keep a
    code switched on. Reactivating it against a deactivated code is a state
    the engine already reports, by name."""
    company = seeded["company"]
    rule = (
        pg_db.query(TaxRule)
        .filter(TaxRule.company_id == company.id, TaxRule.position == 30)
        .one()
    )
    rule.is_active = False
    pg_db.commit()

    assert references_to_code(pg_db, _code(pg_db, company, "EU-AE").id) == []


def test_the_item_sentinel_is_not_a_class_reference(pg_db, seeded):
    """The decision that makes the feature usable at all.

    Every shipped domestic rule resolves `{class: "@item"}` — "whatever the
    line's item carries". If that counted as a reference, no class could ever
    be retired in any company. What a rule EXPLICITLY names does count, which
    the next test shows.
    """
    for key in ("standard", "reduced", "reduced_2", "zero"):
        assert references_to_class(
            pg_db, _class(pg_db, seeded["company"], key).id
        ) == [], key


def test_a_class_a_rule_names_explicitly_is_reported(pg_db, seeded):
    company = seeded["company"]
    pg_db.add(
        TaxRule(
            company_id=company.id,
            position=25,
            name="Always the reduced rate",
            conditions={},
            outcome={"treatment": "domestic", "code": {"class": "reduced"}},
        )
    )
    pg_db.commit()

    found = references_to_class(pg_db, _class(pg_db, company, "reduced").id)

    assert [referrer.name for referrer in found] == ["Always the reduced rate"]


def test_a_class_inherits_the_references_to_its_own_code(pg_db, seeded):
    """A rate is a 1:1 pair, so retiring the class switches the code off too —
    which breaks a rule naming the code just as surely. One call answers both,
    so the two cannot drift, and a referrer registered for codes in P2
    protects rates without being registered twice.
    """
    company = seeded["company"]
    # Point the `zero` class at EU-AE, which a rule names. Contrived, and the
    # shape of what P1c's items will do for real.
    zero = _class(pg_db, company, "zero")
    zero.code_id = _code(pg_db, company, "EU-AE").id
    pg_db.commit()

    found = references_to_class(pg_db, zero.id)

    assert found, "the class did not inherit its code's referrers"
    assert any("reverse charge" in referrer.name.lower() for referrer in found)


def test_an_id_that_names_nothing_reports_nothing(pg_db, seeded):
    """True rather than an exception: the caller has already answered 404 for
    a row that is not theirs, so reaching here means the row went away
    between two statements."""
    assert references_to_code(pg_db, uuid.uuid4()) == []
    assert references_to_class(pg_db, uuid.uuid4()) == []


def test_references_are_per_company(pg_db, seeded):
    """Another company's rule naming the same code string is not a reason to
    keep this company's code switched on."""
    company = seeded["company"]
    other = make_company(pg_db, "Somebody Else GmbH", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=other)
    pg_db.commit()
    # Deactivate THIS company's reverse-charge rule; the other company's is
    # still active and names the same code string.
    rule = (
        pg_db.query(TaxRule)
        .filter(TaxRule.company_id == company.id, TaxRule.position == 30)
        .one()
    )
    rule.is_active = False
    pg_db.commit()

    assert references_to_code(pg_db, _code(pg_db, company, "EU-AE").id) == []
    assert references_to_code(pg_db, _code(pg_db, other, "EU-AE").id) != []


# ─── The registration point ─────────────────────────────────────────────────


def test_a_referrer_registered_at_test_time_is_honoured(pg_db, seeded):
    """P1b, P1c and P2 each register one here. This proves the seam works.

    A guard that silently ignored a new source would be worse than no guard,
    because it reads as protection — so the source is registered, exercised
    through the real function, and removed in a `finally` so the rest of the
    suite is unaffected.
    """
    company = seeded["company"]
    target = _code(pg_db, company, "AT-13")
    assert references_to_code(pg_db, target.id) == [], "not a clean start"

    def _pretend_items(db, code):
        if code.code == "AT-13":
            return [Referrer(kind="item", name="Studio A")]
        return []

    tax_references.register_code_referrers(_pretend_items)
    try:
        found = references_to_code(pg_db, target.id)
        assert [str(referrer) for referrer in found] == ['item “Studio A”']
        # And a class whose code it is inherits it.
        zero = _class(pg_db, company, "zero")
        zero.code_id = target.id
        pg_db.commit()
        assert any(
            referrer.name == "Studio A"
            for referrer in references_to_class(pg_db, zero.id)
        )
    finally:
        tax_references._CODE_SOURCES.remove(_pretend_items)

    assert references_to_code(pg_db, target.id) == [], "the source leaked"


def test_registering_the_same_source_twice_does_not_double_the_sentence(pg_db):
    """A module imported twice must not make every referrer appear twice."""
    def _source(db, code):
        return []

    before = tax_references.registered_sources()
    tax_references.register_code_referrers(_source)
    tax_references.register_code_referrers(_source)
    try:
        assert tax_references.registered_sources()[0] == before[0] + 1
    finally:
        tax_references._CODE_SOURCES.remove(_source)


def test_the_shipped_sources_are_registered():
    """The guard against the whole file being vacuous: a module whose
    decorators never ran would make every "unreferenced" answer above true
    by default."""
    code_sources, class_sources = tax_references.registered_sources()
    assert code_sources >= 1
    assert class_sources >= 2


# ─── Through the API ────────────────────────────────────────────────────────


def test_deactivating_a_referenced_code_is_refused_and_names_the_rule(
    pg_client, pg_db, seeded
):
    """Acceptance item 5. The sentence has to say which rule, because
    "something uses it" leaves the user with nowhere to go."""
    code = _code(pg_db, seeded["company"], "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "EU-AE" in detail
    assert "rule “" in detail
    assert "reverse charge" in detail.lower()
    pg_db.expire_all()
    assert _code(pg_db, seeded["company"], "EU-AE").is_active is True


def test_deactivating_an_unreferenced_no_tax_case_works(pg_client, pg_db, seeded):
    created = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "Exempt – training", "category": "E"},
    ).json()

    response = pg_client.patch(
        f"/company/tax/codes/{created['id']}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False


def test_retiring_an_unreferenced_rate_works(pg_client, pg_db, seeded):
    """Acceptance item 5's second half."""
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Super-reduced", "rate": "4.5"},
    ).json()

    response = pg_client.patch(
        f"/company/tax/classes/{created['tax_class']['id']}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False


def test_retiring_a_rate_whose_code_a_rule_names_is_refused(
    pg_client, pg_db, seeded
):
    """Through the class endpoint, because that is how a rate is retired —
    and the guard has to reach the code's referrers from there."""
    company = seeded["company"]
    pg_db.add(
        TaxRule(
            company_id=company.id,
            position=25,
            name="Studio rental is always AT-13",
            conditions={},
            outcome={"treatment": "domestic", "code": {"code": "AT-13"}},
        )
    )
    pg_db.commit()
    reduced_2 = _class(pg_db, company, "reduced_2")

    response = pg_client.patch(
        f"/company/tax/classes/{reduced_2.id}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "Ermäßigt (13 %)" in detail
    assert "Studio rental is always AT-13" in detail
    pg_db.expire_all()
    assert _class(pg_db, company, "reduced_2").is_active is True
    assert _code(pg_db, company, "AT-13").is_active is True


def test_reactivating_always_works_even_when_referenced(
    pg_client, pg_db, seeded
):
    """The guard is about switching OFF. A reference is a reason to be on."""
    company = seeded["company"]
    code = _code(pg_db, company, "EU-AE")
    code.is_active = False
    pg_db.commit()

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"is_active": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True


def test_a_rename_of_a_referenced_rate_is_not_blocked(pg_client, pg_db, seeded):
    """Only deactivation is guarded. A label is free to change — the KEY is
    what anything points at, and that cannot change at all."""
    company = seeded["company"]
    pg_db.add(
        TaxRule(
            company_id=company.id,
            position=25,
            name="Names the reduced class",
            conditions={},
            outcome={"treatment": "domestic", "code": {"class": "reduced"}},
        )
    )
    pg_db.commit()
    reduced = _class(pg_db, company, "reduced")

    response = pg_client.patch(
        f"/company/tax/classes/{reduced.id}",
        headers=_headers(seeded),
        json={"label": "Ermäßigt (10 %) — geprüft"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["label"] == "Ermäßigt (10 %) — geprüft"


def test_a_code_already_inactive_can_be_patched_without_tripping_the_guard(
    pg_client, pg_db, seeded
):
    """The guard fires on the TRANSITION, not on the state.

    Without that, editing the name of an already-retired referenced code
    would be impossible — and "I cannot fix the label on a code I retired
    last year" is the kind of dead end that makes people edit the database.
    """
    company = seeded["company"]
    code = _code(pg_db, company, "EU-AE")
    code.is_active = False
    pg_db.commit()

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"name": "Reverse charge (retired)", "is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Reverse charge (retired)"
