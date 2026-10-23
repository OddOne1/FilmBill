"""Changing a company's country without destroying its tax configuration.

Against a real PostgreSQL, because every property here is about rows: which
codes exist, which are active, which class points where.

**The bug this fixes.** Until P1b the pack was installed once, when the
country was first set, and a later change installed nothing — so a company
that mis-typed `DE` and corrected it to `AT` kept German codes for ever and
every invoice it issued carried the wrong rate, silently. Mathias,
2026-10-23: fix it now, while there is no real data.

The tests come in two groups, and the second is the one that earns its keep.
The first checks that the right things happen. The second checks that the
**wrong** things do not: an edited rate survives, a rate the company created
survives, a code something still points at stays switched on, and nothing is
ever deleted. Those are the four ways a reconciliation like this destroys
work, and each has a mutation behind it.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import Company, CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxClass, TaxCode, TaxRule
from apps.api.services import tax_country, tax_references, tax_service
from apps.api.services.tax_references import Referrer

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)


@pytest.fixture
def austrian(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "country-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _codes(pg_db, company) -> dict:
    return {
        row.code: row
        for row in pg_db.query(TaxCode).filter(TaxCode.company_id == company.id)
    }


def _active_codes(pg_db, company) -> set:
    return {
        code for code, row in _codes(pg_db, company).items() if row.is_active
    }


def _class_codes(pg_db, company) -> dict:
    rows = (
        pg_db.query(TaxClass, TaxCode)
        .join(TaxCode, TaxCode.id == TaxClass.code_id)
        .filter(TaxClass.company_id == company.id)
        .all()
    )
    return {entry.key: code.code for entry, code in rows}


def _preview(pg_client, world, country, mode="apply_pack"):
    response = pg_client.post(
        "/company/tax/country-change/preview",
        headers=_headers(world),
        json={"country": country, "mode": mode},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _apply(pg_client, world, country, mode="apply_pack"):
    response = pg_client.post(
        "/company/tax/country-change",
        headers=_headers(world),
        json={"country": country, "mode": mode},
    )
    assert response.status_code == 200, response.text
    return response.json()


#: Everything the plan reports as a change, so "preview == applied" compares
#: the whole thing rather than the two fields somebody remembered.
_PLAN_KEYS = (
    "from_country",
    "to_country",
    "pack",
    "mode",
    "first_set",
    "codes_added",
    "classes_repointed",
    "classes_without_equivalent",
    "classes_added",
    "codes_deactivated",
    "codes_reactivated",
    "codes_kept_because_referenced",
    "untouched",
    "rules_replaced",
    "warnings",
    "sentences",
    "counts",
)


# ─── What is supposed to happen ─────────────────────────────────────────────


def test_switching_austria_to_germany_installs_the_german_rates(
    pg_client, pg_db, austrian
):
    company = austrian["company"]

    _apply(pg_client, austrian, "DE")

    pg_db.expire_all()
    codes = _codes(pg_db, company)
    classes = _class_codes(pg_db, company)
    assert codes["DE-19"].rate == Decimal("19.0000")
    assert classes["standard"] == "DE-19"
    assert classes["reduced"] == "DE-7"
    assert classes["zero"] == "DE-0"
    assert (
        pg_db.query(Company).filter(Company.id == company.id).one().address_country
        == "DE"
    )
    # The Austrian rates are off, not gone — they still explain the documents
    # that used them.
    assert codes["AT-20"].is_active is False
    assert codes["AT-10"].is_active is False
    assert "AT-20" in codes


def test_a_class_the_new_country_has_no_key_for_is_kept_and_listed(
    pg_client, pg_db, austrian
):
    """Austria's 13 % and 4.9 % have no German counterpart.

    Dropping them would lose the only explanation of the items labelled with
    them, so they stay exactly as they are — which also means their codes
    stay active, and the plan says so in words.
    """
    company = austrian["company"]

    plan = _apply(pg_client, austrian, "DE")

    kept = {key for key, _code in plan["classes_without_equivalent"]}
    assert kept == {"reduced_2", "reduced_3"}
    pg_db.expire_all()
    classes = _class_codes(pg_db, company)
    assert classes["reduced_2"] == "AT-13"
    assert classes["reduced_3"] == "AT-4.9"
    assert _codes(pg_db, company)["AT-13"].is_active is True
    assert any("no equivalent" in line for line in plan["sentences"])


def test_a_round_trip_ends_with_the_same_active_shipped_codes_as_fresh_seeding(
    pg_client, pg_db, austrian
):
    """AT → DE → AT. The property that makes the action safe to use twice."""
    company = austrian["company"]
    fresh = _active_codes(pg_db, company)
    fresh_classes = _class_codes(pg_db, company)

    _apply(pg_client, austrian, "DE")
    _apply(pg_client, austrian, "AT")

    pg_db.expire_all()
    back = _active_codes(pg_db, company)
    # The German codes are present but off, so compare the ACTIVE set — which
    # is what decides what can be proposed.
    assert back - fresh == set(), f"unexpectedly active: {back - fresh}"
    assert fresh - back == set(), f"should be active again: {fresh - back}"
    assert _class_codes(pg_db, company) == fresh_classes


def test_the_shipped_rules_are_the_same_in_every_pack_so_nothing_replaces_them(
    pg_client, pg_db, austrian
):
    """Reported rather than assumed.

    All three packs inherit the identical eight rules from `eu_generic`, so a
    country switch changes no rule at all and `rules_replaced` is 0. The code
    still handles a genuine difference — a future pack with its own rules —
    and this test is what will fail, loudly and in the right place, the day
    one arrives.
    """
    company = austrian["company"]
    before = [
        (row.position, row.name) for row in
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id)
        .order_by(TaxRule.position)
    ]

    plan = _apply(pg_client, austrian, "DE")

    assert plan["rules_replaced"] == 0
    pg_db.expire_all()
    after = [
        (row.position, row.name) for row in
        pg_db.query(TaxRule).filter(TaxRule.company_id == company.id)
        .order_by(TaxRule.position)
    ]
    assert after == before


def test_applying_produces_exactly_what_the_preview_showed(
    pg_client, pg_db, austrian
):
    """A preview that is not what happens is worse than none — it is a
    promise. Compared over every field of the plan, not the two somebody
    remembered."""
    preview = _preview(pg_client, austrian, "DE")
    applied = _apply(pg_client, austrian, "DE")

    for key in _PLAN_KEYS:
        assert applied[key] == preview[key], key


def test_the_preview_writes_nothing(pg_client, pg_db, austrian):
    company = austrian["company"]
    before = (
        sorted(_codes(pg_db, company)),
        _class_codes(pg_db, company),
        _active_codes(pg_db, company),
        pg_db.query(AuditEvent).count(),
    )

    for country in ("DE", "FR", "AT"):
        _preview(pg_client, austrian, country)
        _preview(pg_client, austrian, country, mode="country_only")

    pg_db.expire_all()
    assert (
        sorted(_codes(pg_db, company)),
        _class_codes(pg_db, company),
        _active_codes(pg_db, company),
        pg_db.query(AuditEvent).count(),
    ) == before


def test_country_only_changes_the_country_and_nothing_else(
    pg_client, pg_db, austrian
):
    company = austrian["company"]
    before_codes = sorted(_codes(pg_db, company))
    before_classes = _class_codes(pg_db, company)
    before_active = _active_codes(pg_db, company)

    plan = _apply(pg_client, austrian, "DE", mode="country_only")

    pg_db.expire_all()
    assert (
        pg_db.query(Company).filter(Company.id == company.id).one().address_country
        == "DE"
    )
    assert sorted(_codes(pg_db, company)) == before_codes
    assert _class_codes(pg_db, company) == before_classes
    assert _active_codes(pg_db, company) == before_active
    # And it says what it did not do, because a silent no-op here would leave
    # a German company quoting Austrian rates.
    assert plan["warnings"], "country_only warned about nothing"
    assert any("not touched" in line for line in plan["warnings"])


def test_a_country_with_no_pack_warns_that_proposals_will_be_blocked(
    pg_client, pg_db, austrian
):
    """France has no pack, so the standard rate arrives at 0 % and off. The
    plain-words warning is the whole point: a blocked proposal with no
    explanation is the state this sentence prevents."""
    company = austrian["company"]

    plan = _apply(pg_client, austrian, "FR")

    assert any("set your rate" in line for line in plan["warnings"]), plan["warnings"]
    pg_db.expire_all()
    assert _class_codes(pg_db, company)["standard"] == "EU-STD"
    assert _codes(pg_db, company)["EU-STD"].is_active is False

    # And the engine agrees, which is what the warning is about.
    proposal = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian),
        json={"buyer_country": "FR"},
    ).json()
    assert proposal["blocking"] is True
    assert [w["code"] for w in proposal["warnings"]] == ["code_inactive"]


def test_the_first_set_path_is_unchanged(pg_client, pg_db):
    """A company with no tax rows still seeds through `PATCH /company`, with
    no dialog — there is nothing to reconcile."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "first-set@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    headers = auth_headers(owner, company)

    assert (
        pg_client.patch(
            "/company", headers=headers, json={"address_country": "AT"}
        ).status_code
        == 200
    )

    pg_db.expire_all()
    assert _class_codes(pg_db, company)["standard"] == "AT-20"


def test_the_preview_of_a_first_set_says_so(pg_client, pg_db):
    """So the screen knows it needs no dialog."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "first-preview@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()

    response = pg_client.post(
        "/company/tax/country-change/preview",
        headers=auth_headers(owner, company),
        json={"country": "AT"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["first_set"] is True


def test_an_unusable_country_is_refused_before_anything_happens(
    pg_client, austrian
):
    for country in ("ZZ1", "1", ""):
        response = pg_client.post(
            "/company/tax/country-change",
            headers=_headers(austrian),
            json={"country": country},
        )
        assert response.status_code == 422, (country, response.text)


def test_an_unknown_mode_is_refused_by_the_schema(pg_client, austrian):
    response = pg_client.post(
        "/company/tax/country-change",
        headers=_headers(austrian),
        json={"country": "DE", "mode": "wipe_everything"},
    )
    assert response.status_code == 422, response.text
    assert "mode" in response.text


# ─── What must NOT happen ───────────────────────────────────────────────────


def test_an_edited_rate_is_left_alone_rather_than_switched_off(
    pg_client, pg_db, austrian
):
    """The first of the four ways this could destroy work.

    "Edited" is checkable rather than remembered: the row differs from what
    its pack declares. An edited code is reported as **untouched** and stays
    active, because the edit is the signal that somebody is relying on it.

    `AT-10` on purpose, and the choice is the whole test. An earlier version
    used `AT-13`, which is also an Austrian code — and which survives for a
    completely different reason: the `reduced_2` class has no German
    equivalent, so it keeps pointing at it and the planner never reaches the
    edited check at all. The mutation that stops honouring edits passed
    against that test. `AT-10` is freed by the switch (`reduced` moves to
    `DE-7`), so the edited check is the only thing standing between it and
    deactivation.
    """
    company = austrian["company"]
    edited = _codes(pg_db, company)["AT-10"]
    edited.rate = Decimal("11")
    edited.name = "11 % (our advisor says so)"
    edited.notes_reviewed = True
    pg_db.commit()

    plan = _apply(pg_client, austrian, "DE")

    assert "AT-10" in plan["untouched"]
    assert "AT-10" not in plan["codes_deactivated"]
    pg_db.expire_all()
    after = _codes(pg_db, company)["AT-10"]
    assert after.is_active is True, "an edited rate was switched off"
    assert after.rate == Decimal("11.0000")
    assert after.name == "11 % (our advisor says so)"
    assert after.notes_reviewed is True


def test_an_edited_rate_survives_a_round_trip_and_comes_back_into_use(
    pg_client, pg_db, austrian
):
    """And the values are still the user's on the way back."""
    company = austrian["company"]
    edited = _codes(pg_db, company)["AT-20"]
    edited.rate = Decimal("21")
    edited.name = "21 % (our advisor says so)"
    pg_db.commit()

    _apply(pg_client, austrian, "DE")
    _apply(pg_client, austrian, "AT")

    pg_db.expire_all()
    after = _codes(pg_db, company)["AT-20"]
    assert after.rate == Decimal("21.0000")
    assert after.name == "21 % (our advisor says so)"
    assert after.is_active is True
    assert _class_codes(pg_db, company)["standard"] == "AT-20"


def test_a_rate_the_company_created_is_untouched(pg_client, pg_db, austrian):
    company = austrian["company"]
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(austrian),
        json={"label": "Super-reduced", "rate": "4.5"},
    ).json()

    plan = _apply(pg_client, austrian, "DE")

    code = created["tax_code"]["code"]
    assert code in plan["untouched"]
    assert code not in plan["codes_deactivated"]
    assert code not in plan["codes_added"]
    assert not any(
        entry[0] == created["tax_class"]["key"]
        for entry in plan["classes_repointed"]
    ), "a rate the company created was re-pointed at the new country's code"
    pg_db.expire_all()
    codes = _codes(pg_db, company)
    row = codes[code]
    assert row.is_active is True
    assert row.rate == Decimal("4.5000")
    assert row.name == "Super-reduced"
    # Its class is still pointing at it, and was not re-pointed anywhere.
    assert _class_codes(pg_db, company)[created["tax_class"]["key"]] == row.code


def test_a_no_tax_case_the_company_created_is_untouched(
    pg_client, pg_db, austrian
):
    company = austrian["company"]
    created = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(austrian),
        json={
            "label": "Exempt – training",
            "category": "E",
            "legal_notes": {"de": "Steuerfrei: Bildungsleistung."},
        },
    ).json()

    plan = _apply(pg_client, austrian, "DE")

    assert created["code"] in plan["untouched"]
    assert created["code"] not in plan["codes_deactivated"]
    pg_db.expire_all()
    row = _codes(pg_db, company)[created["code"]]
    assert row.is_active is True, "a no-tax case the company created was retired"
    assert row.legal_notes == {"de": "Steuerfrei: Bildungsleistung."}


def test_an_old_code_something_still_points_at_stays_active_and_is_named(
    pg_client, pg_db, austrian
):
    """The P1a-2 reference registry decides this.

    A referrer is registered at test time — which is also what P1b part 3
    does for real with a party's tax override — so this covers the seam and
    not just today's one source.
    """
    company = austrian["company"]
    target = _codes(pg_db, company)["AT-10"]

    def _pretend_items(db, code):
        if code.code == "AT-10":
            return [Referrer(kind="item", name="Studio A")]
        return []

    tax_references.register_code_referrers(_pretend_items)
    try:
        plan = _apply(pg_client, austrian, "DE")
    finally:
        tax_references._CODE_SOURCES.remove(_pretend_items)

    kept = {entry["code"]: entry["referrers"] for entry in plan["codes_kept_because_referenced"]}
    assert "AT-10" in kept
    assert kept["AT-10"] == ['item “Studio A”']
    assert "AT-10" not in plan["codes_deactivated"]
    pg_db.expire_all()
    assert _codes(pg_db, company)["AT-10"].is_active is True
    assert any("still points at" in line for line in plan["sentences"])


def test_nothing_is_ever_deleted(pg_client, pg_db, austrian):
    """Over a round trip, with a created rate and a created case in play."""
    company = austrian["company"]
    pg_client.post(
        "/company/tax/rates",
        headers=_headers(austrian),
        json={"label": "Mine", "rate": "3"},
    )
    pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(austrian),
        json={"label": "Also mine", "category": "O"},
    )
    before = set(_codes(pg_db, company))
    classes_before = set(_class_codes(pg_db, company))

    _apply(pg_client, austrian, "DE")
    _apply(pg_client, austrian, "FR")
    _apply(pg_client, austrian, "AT")

    pg_db.expire_all()
    assert before <= set(_codes(pg_db, company)), "a code disappeared"
    assert classes_before <= set(_class_codes(pg_db, company)), "a class disappeared"


def test_another_companys_configuration_is_untouched(pg_client, pg_db, austrian):
    other = make_company(pg_db, "Somebody Else GmbH", address_country="AT")
    tax_service.seed_tax_configuration(pg_db, company=other)
    pg_db.commit()
    before_codes = sorted(_codes(pg_db, other))
    before_active = _active_codes(pg_db, other)

    _apply(pg_client, austrian, "DE")

    pg_db.expire_all()
    assert sorted(_codes(pg_db, other)) == before_codes
    assert _active_codes(pg_db, other) == before_active
    assert (
        pg_db.query(Company).filter(Company.id == other.id).one().address_country
        == "AT"
    )


# ─── Audit, permissions ─────────────────────────────────────────────────────


def test_the_change_writes_one_audit_event_with_the_counts(
    pg_client, pg_db, austrian
):
    company = austrian["company"]

    plan = _apply(pg_client, austrian, "DE")

    events = (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "company.tax_country_changed")
        .all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.company_id == company.id
    assert event.user_id == austrian["owner"].id
    assert event.changes["address_country"] == ["AT", "DE"]
    assert "mode=apply_pack" in event.reason
    for name, value in plan["counts"].items():
        assert f"{name}={value}" in event.reason


def test_the_event_is_visible_in_the_audit_log(pg_client, pg_db, austrian):
    """Acceptance item 2."""
    _apply(pg_client, austrian, "DE")

    response = pg_client.get(
        "/company/audit-events?action=company.tax_country_changed",
        headers=_headers(austrian),
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


def test_a_refused_change_leaves_no_event_and_no_rows(pg_client, pg_db, austrian):
    company = austrian["company"]
    before = sorted(_codes(pg_db, company))

    assert (
        pg_client.post(
            "/company/tax/country-change",
            headers=_headers(austrian),
            json={"country": "Z"},
        ).status_code
        == 422
    )

    pg_db.expire_all()
    assert sorted(_codes(pg_db, company)) == before
    assert (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == "company.tax_country_changed")
        .count()
        == 0
    )


@pytest.mark.parametrize(
    "role, expected",
    [
        (CompanyRole.owner, 200),
        (CompanyRole.admin, 200),
        (CompanyRole.accountant, 404),
        (CompanyRole.producer, 404),
        (CompanyRole.staff, 404),
        (CompanyRole.tax_advisor, 404),
    ],
)
def test_only_owner_and_admin_may_change_the_country(
    pg_client, pg_db, austrian, role, expected
):
    """`company.settings.edit`, and the refusal is 404 (rule 6)."""
    company = austrian["company"]
    member = make_user(pg_db, f"switcher-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()

    for path in (
        "/company/tax/country-change/preview",
        "/company/tax/country-change",
    ):
        response = pg_client.post(
            path,
            headers=auth_headers(member, company),
            json={"country": "DE"},
        )
        assert response.status_code == expected, (path, response.text)


# ─── The planner on its own ─────────────────────────────────────────────────


def test_the_plan_has_sentences_for_everything_it_reports(pg_db, austrian):
    """A count without a sentence is a number nobody can act on."""
    plan = tax_country.plan_country_change(
        pg_db, company=austrian["company"], country="DE"
    )

    assert plan.codes_added and plan.classes_repointed
    text = " ".join(plan.sentences())
    for code in plan.codes_added:
        assert code in text
    for key, _old, _new in plan.classes_repointed:
        assert key in text


def test_a_plan_that_changes_nothing_says_so(pg_db, austrian):
    """Switching to the country you are already in."""
    plan = tax_country.plan_country_change(
        pg_db, company=austrian["company"], country="AT"
    )

    assert plan.counts()["codes_added"] == 0
    assert plan.counts()["classes_repointed"] == 0
    assert plan.sentences() == ["Nothing would change except the country itself."]
