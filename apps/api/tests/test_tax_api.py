"""The five tax endpoints, against a real PostgreSQL.

Three things are being proved, and the third is the one that would otherwise
rot quietly.

**Who may do what.** The whole role ladder, not a sample. Reads are
`company.view` — a rate is printed on every document the company sends, and
an accountant who cannot see the tax table cannot check an invoice against it
— and writes are `company.settings.edit`. A refusal is 404, not 403
(CLAUDE.md rule 6).

**What may be changed.** `code` and `category` are not in the update schema at
all, so sending either is a 422 that names the field. A tax code is
snapshotted onto every document line that used it (rule 3), and renaming
`AT-20` would restate invoices that are already issued.

**That the endpoint and the pure function agree.** `POST /company/tax/propose`
is compared to `propose_tax` over the same golden fixtures, case by case. Two
paths reach the engine — a pack through `packs.engine_tables` and rows
through `tax_service.engine_tables` — and nothing else in the suite would
notice them drifting apart.
"""

from decimal import Decimal

import pytest

from apps.api.billing.tax import (
    Override,
    TaxInputs,
    load_pack,
    propose_tax,
)
from apps.api.billing.tax.packs import engine_tables as pack_tables
from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxCode
from apps.api.services import tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)
from .test_tax_engine import CASES


@pytest.fixture
def austrian_company(pg_db):
    """A seeded Austrian company and one owner."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "tax-owner@example.com")
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


# ─── Reading ────────────────────────────────────────────────────────────────


def test_the_codes_list_serves_the_whole_pack_with_rates_as_strings(
    pg_client, austrian_company
):
    """Rule 1 at the type level: `"20.0000"`, not `20`.

    A JSON number here would become `rate: number` in the generated
    TypeScript, and the rule would be undone before anybody wrote a line of
    web code.
    """
    response = pg_client.get("/company/tax/codes", headers=_headers(austrian_company))

    assert response.status_code == 200, response.text
    body = {row["code"]: row for row in response.json()}
    assert body["AT-20"]["rate"] == "20.0000"
    assert isinstance(body["AT-20"]["rate"], str)
    assert body["AT-20"]["category"] == "S"
    assert body["AT-20"]["origin_pack"] == "at"
    assert body["EU-AE"]["origin_pack"] == "eu_generic"
    assert body["EU-AE"]["legal_notes"]["de"] == (
        "Steuerschuld geht auf den Leistungsempfänger über."
    )
    assert body["EU-AE"]["notes_reviewed"] is False
    assert body["AT-20"]["reporting_mapping"] == {}


def test_inactive_codes_are_listed_by_default(pg_client, austrian_company):
    """The generic pack's standard code is inactive at 0 % and is the one row
    on the screen that needs attention. Hiding it would hide the work."""
    response = pg_client.get("/company/tax/codes", headers=_headers(austrian_company))

    body = {row["code"]: row for row in response.json()}
    assert body["EU-STD"]["is_active"] is False

    only_active = pg_client.get(
        "/company/tax/codes?include_inactive=false",
        headers=_headers(austrian_company),
    )
    assert "EU-STD" not in {row["code"] for row in only_active.json()}


def test_the_classes_list_resolves_each_code_and_rate(pg_client, austrian_company):
    """So the picker reads "Normalsteuersatz — AT-20, 20 %" from one request.
    The web never works out a rate from a class (rule 2)."""
    response = pg_client.get(
        "/company/tax/classes", headers=_headers(austrian_company)
    )

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["key"] for row in rows] == [
        "standard",
        "reduced",
        "reduced_2",
        "zero",
    ], "classes must come back in display order, not alphabetically"
    by_key = {row["key"]: row for row in rows}
    assert by_key["standard"]["code"] == "AT-20"
    assert by_key["standard"]["rate"] == "20.0000"
    assert by_key["reduced"]["code"] == "AT-10"
    assert by_key["reduced"]["rate"] == "10.0000"
    assert by_key["standard"]["code_is_active"] is True


def test_the_rules_list_is_the_eight_in_order(pg_client, austrian_company):
    response = pg_client.get("/company/tax/rules", headers=_headers(austrian_company))

    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["position"] for row in rows] == [10, 20, 30, 40, 50, 60, 70, 80]
    assert rows[2]["outcome"]["treatment"] == "eu_reverse_charge"
    assert rows[2]["conditions"]["buyer_vat_verified"] is True
    # `JsonObject`, not a bare dict — otherwise openapi-typescript reads this
    # as `Record<string, never>` and the screen cannot read a single key.
    assert rows[1]["outcome"]["code"] == {"class": "@item"}


def test_a_company_with_no_tax_configuration_gets_empty_lists(pg_client, pg_db):
    """Not an error. The dev database and the server-test instance are in
    exactly this state until a country is set."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "bare@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    headers = auth_headers(owner, company)

    for path in ("codes", "classes", "rules"):
        response = pg_client.get(f"/company/tax/{path}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == []


# ─── Permissions ────────────────────────────────────────────────────────────

READ_PATHS = ("/company/tax/codes", "/company/tax/classes", "/company/tax/rules")


@pytest.mark.parametrize("role", list(CompanyRole))
def test_every_role_may_read_the_tax_tables(pg_client, pg_db, austrian_company, role):
    """`company.view`, which every member holds — the tax advisor included.

    A rate is not a secret: it is on every document the company sends. And an
    accountant or advisor who cannot see the table cannot check a document
    against it, which is most of what they are given access for.
    """
    company = austrian_company["company"]
    member = make_user(pg_db, f"reader-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()
    headers = auth_headers(member, company)

    for path in READ_PATHS:
        assert pg_client.get(path, headers=headers).status_code == 200, (
            f"{role.value} cannot read {path}"
        )
    assert (
        pg_client.post(
            "/company/tax/propose",
            headers=headers,
            json={"buyer_country": "AT"},
        ).status_code
        == 200
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
def test_only_owner_and_admin_may_change_a_code(
    pg_client, pg_db, austrian_company, role, expected
):
    """`company.settings.edit`, and the refusal is 404 (rule 6).

    The accountant is deliberately out. Changing a rate changes every future
    invoice, which is a structural act rather than a bookkeeping one — the
    same reasoning that put `number_series.manage` on owner and admin only.
    """
    company = austrian_company["company"]
    member = make_user(pg_db, f"writer-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()
    code = _code(pg_db, company, "AT-20")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=auth_headers(member, company),
        json={"name": "Changed by " + role.value},
    )

    assert response.status_code == expected, response.text
    pg_db.expire_all()
    if expected == 404:
        assert _code(pg_db, company, "AT-20").name == "20 % USt (Normalsteuersatz)"


def test_a_code_that_does_not_exist_is_404(pg_client, austrian_company):
    import uuid

    response = pg_client.patch(
        f"/company/tax/codes/{uuid.uuid4()}",
        headers=_headers(austrian_company),
        json={"name": "Nope"},
    )
    assert response.status_code == 404


# ─── Editing ────────────────────────────────────────────────────────────────


def test_the_rate_can_be_changed_and_comes_back_as_a_string(
    pg_client, pg_db, austrian_company
):
    """Acceptance item 7: change a rate to 21 and save."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "21"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rate"] == "21.0000"
    pg_db.expire_all()
    assert _code(pg_db, company, "AT-20").rate == Decimal("21.0000")


def test_a_rate_with_four_decimals_survives_exactly(
    pg_client, pg_db, austrian_company
):
    """2.5 % exists, and so does 8.1667 %. Rule 1: no rate through a float."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "8.1667"},
    )

    assert response.json()["rate"] == "8.1667"
    pg_db.expire_all()
    assert _code(pg_db, company, "AT-20").rate == Decimal("8.1667")


@pytest.mark.parametrize("field", ["code", "category"])
def test_the_code_and_the_category_cannot_be_changed(
    pg_client, pg_db, austrian_company, field
):
    """A 422 naming the field, not a silent no-op.

    `extra="forbid"` rather than leaving the field out quietly: a client that
    sends `code` has a wrong idea about this endpoint, and the useful answer
    says so.
    """
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={field: "XX"},
    )

    assert response.status_code == 422, response.text
    assert field in response.text
    pg_db.expire_all()
    stored = _code(pg_db, company, "AT-20")
    assert stored.code == "AT-20" and stored.category == "S"


def test_a_rate_on_a_zero_rate_category_is_refused_with_the_packs_own_message(
    pg_client, pg_db, austrian_company
):
    """"Reverse charge at 20 %" is a contradiction, not a configuration.

    The same function the pack loader uses, so a user and a broken pack get
    the same sentence. The database's CHECK is the floor under both.
    """
    company = austrian_company["company"]
    reverse_charge = _code(pg_db, company, "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{reverse_charge.id}",
        headers=_headers(austrian_company),
        json={"rate": "20"},
    )

    assert response.status_code == 422, response.text
    assert "zero-rate category" in response.json()["detail"]
    pg_db.expire_all()
    assert _code(pg_db, company, "EU-AE").rate == Decimal("0.0000")


def test_a_negative_rate_is_refused(pg_client, pg_db, austrian_company):
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "-5"},
    )

    assert response.status_code == 422, response.text
    pg_db.expire_all()
    assert _code(pg_db, company, "AT-20").rate == Decimal("20.0000")


def test_the_legal_notes_and_the_reviewed_flag_can_be_set_together(
    pg_client, pg_db, austrian_company
):
    """The point of the flag: somebody read the sentence and takes it on."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={
            "legal_notes": {"de": "Reverse Charge nach Rücksprache.", "en": "RC."},
            "notes_reviewed": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notes_reviewed"] is True
    assert body["legal_notes"]["de"] == "Reverse Charge nach Rücksprache."


def test_a_blank_legal_note_is_refused(pg_client, pg_db, austrian_company):
    """An empty string prints as nothing and reads as present. Removing a
    note means removing its language key."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"legal_notes": {"de": "   "}},
    )

    assert response.status_code == 422, response.text
    assert "blank" in response.text


def test_a_language_key_that_is_not_a_language_is_refused(
    pg_client, pg_db, austrian_company
):
    company = austrian_company["company"]
    code = _code(pg_db, company, "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"legal_notes": {"german": "Text"}},
    )

    assert response.status_code == 422, response.text


def test_deactivating_a_code_is_allowed_and_blocks_proposals_from_it(
    pg_client, pg_db, austrian_company
):
    """Retiring a code is `is_active = false`, never a delete — the issued
    documents that used it have to stay explicable."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    assert (
        pg_client.patch(
            f"/company/tax/codes/{code.id}",
            headers=_headers(austrian_company),
            json={"is_active": False},
        ).status_code
        == 200
    )

    proposal = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT"},
    ).json()
    assert proposal["blocking"] is True
    assert proposal["rate"] is None
    assert [warning["code"] for warning in proposal["warnings"]] == ["code_inactive"]


def test_a_patch_with_no_fields_changes_nothing_and_is_not_an_error(
    pg_client, pg_db, austrian_company
):
    """A save with no edits is a thing a form does, not an error."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")
    before = pg_db.query(AuditEvent).count()

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}", headers=_headers(austrian_company), json={}
    )

    assert response.status_code == 200, response.text
    assert response.json()["rate"] == "20.0000"
    assert pg_db.query(AuditEvent).count() == before


# ─── Audit ──────────────────────────────────────────────────────────────────


def _events(pg_db, action: str) -> list[AuditEvent]:
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == action)
        .order_by(AuditEvent.created_at)
        .all()
    )


def test_changing_a_rate_writes_a_tax_code_updated_event(
    pg_client, pg_db, austrian_company
):
    """Acceptance item 7's second half: the row is in the Audit log.

    Old and new values both, because "the rate is 21" answers nothing — "it
    was 20 on Tuesday and this person made it 21" is the whole question.
    """
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")

    pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "21"},
    )

    events = _events(pg_db, "tax_code.updated")
    assert len(events) == 1
    event = events[0]
    assert event.company_id == company.id
    assert event.user_id == austrian_company["owner"].id
    assert event.entity_type == "tax_codes"
    assert event.entity_id == code.id
    # `{field: [old, new]}` — see `services/audit.diff`.
    assert event.changes["rate"] == ["20.0000", "21.0000"]


def test_the_audit_row_covers_the_notes_and_the_reviewed_flag(
    pg_client, pg_db, austrian_company
):
    """"Who said this wording was right" is exactly what the log is read for."""
    company = austrian_company["company"]
    code = _code(pg_db, company, "EU-AE")

    pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"notes_reviewed": True, "legal_notes": {"en": "Checked."}},
    )

    event = _events(pg_db, "tax_code.updated")[0]
    assert event.changes["notes_reviewed"] == [False, True]
    assert event.changes["legal_notes"][1] == {"en": "Checked."}


def test_a_refused_edit_leaves_no_audit_row(pg_client, pg_db, austrian_company):
    """The event is written in the caller's transaction, before the commit.

    A log that recorded attempts as if they had happened would be worse than
    no log: every reader would have to know which rows to disbelieve.
    """
    company = austrian_company["company"]
    reverse_charge = _code(pg_db, company, "EU-AE")

    response = pg_client.patch(
        f"/company/tax/codes/{reverse_charge.id}",
        headers=_headers(austrian_company),
        json={"rate": "20"},
    )

    assert response.status_code == 422
    assert _events(pg_db, "tax_code.updated") == []


def test_the_event_is_visible_through_the_audit_log_endpoint(
    pg_client, pg_db, austrian_company
):
    """End to end: the log screen can actually find it.

    The event row existing and the Audit log showing it are two different
    claims — `audit_events` is not `CompanyScoped`, so the read path is a
    named function rather than `scoped()`.
    """
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")
    pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "21"},
    )

    response = pg_client.get(
        "/company/audit-events?action=tax_code.updated",
        headers=_headers(austrian_company),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["events"][0]["entity_type"] == "tax_codes"
    assert body["events"][0]["changes"]["rate"] == ["20.0000", "21.0000"]


def test_a_tax_advisor_can_read_the_event_but_not_cause_one(
    pg_client, pg_db, austrian_company
):
    """`audit.view` includes the advisor (D43); `company.settings.edit` does
    not. Both halves, because either alone would be a different system."""
    company = austrian_company["company"]
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, company, advisor, CompanyRole.tax_advisor)
    pg_db.commit()
    code = _code(pg_db, company, "AT-20")
    advisor_headers = auth_headers(advisor, company)

    assert (
        pg_client.patch(
            f"/company/tax/codes/{code.id}",
            headers=advisor_headers,
            json={"rate": "21"},
        ).status_code
        == 404
    )
    pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "21"},
    )

    response = pg_client.get(
        "/company/audit-events?action=tax_code.updated", headers=advisor_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


# ─── Proposing ──────────────────────────────────────────────────────────────


def _as_request(inputs: TaxInputs) -> dict:
    """A `TaxInputs` as the endpoint's body. The seller is NOT in it."""
    body: dict = {
        "buyer_country": inputs.buyer_country,
        "buyer_type": inputs.buyer_type,
        "buyer_vat_verified": inputs.buyer_vat_verified,
        "supply_kind": inputs.supply_kind,
        "item_class": inputs.item_class,
    }
    for name, override in (
        ("item_override", inputs.item_override),
        ("party_override", inputs.party_override),
    ):
        if override is not None:
            body[name] = {"kind": override.kind, "code": override.code}
    return body


#: The Austrian golden fixtures, which is every case the seeded company can
#: reach. The German ones need a German company and are covered against the
#: pure function in `test_tax_engine.py`.
_AT_CASES = [case for case in CASES if case[1] == "at"]


@pytest.mark.parametrize(
    "inputs, expected",
    [case[2:] for case in _AT_CASES],
    ids=[case[0] for case in _AT_CASES],
)
def test_the_propose_endpoint_matches_the_pure_function(
    pg_client, pg_db, austrian_company, inputs, expected
):
    """Case by case, the endpoint and `propose_tax` agree.

    Two paths reach the engine — a PACK through `packs.engine_tables` and
    ROWS through `tax_service.engine_tables` — and this is the only test that
    would notice them drifting. The expectation is still the literal from the
    fixture table, so "they agree" cannot be satisfied by both being wrong.
    """
    company = austrian_company["company"]
    if inputs.seller_is_kleinunternehmer:
        company.kleinunternehmer = True
        pg_db.commit()

    response = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json=_as_request(inputs),
    )

    assert response.status_code == 200, response.text
    body = response.json()

    codes, classes, rules = pack_tables(load_pack("at"))
    pure = propose_tax(inputs, codes, classes, rules, "en")

    assert body["blocking"] is pure.blocking is False
    assert body["treatment"] == pure.treatment == expected["treatment"]
    assert body["code"] == pure.code == expected["code"]
    if "rate" in expected:
        assert body["rate"] == str(pure.rate.quantize(Decimal("0.0001")))
        assert pure.rate == Decimal(expected["rate"])
    assert body["applied_rule"] == pure.applied_rule
    assert body["applied_rule_position"] == pure.applied_rule_position
    assert body["reasons"] == list(pure.reasons)
    assert [warning["code"] for warning in body["warnings"]] == list(
        pure.warning_codes()
    )
    assert body["legal_note"] == pure.legal_note
    if pure.would_have_applied is None:
        assert body["would_have_applied"] is None
    else:
        assert body["would_have_applied"]["code"] == pure.would_have_applied.code
        assert (
            body["would_have_applied"]["treatment"]
            == pure.would_have_applied.treatment
        )


def test_the_proposal_carries_a_localised_treatment_label(
    pg_client, austrian_company
):
    """Localised by the API, because a treatment's name belongs beside its
    definition — two screens spelling "Innergemeinschaftliche Lieferung"
    differently is what letting the client own it produces."""
    response = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={
            "buyer_country": "DE",
            "buyer_vat_verified": True,
            "supply_kind": "goods",
            "lang": "de",
        },
    )

    body = response.json()
    assert body["treatment"] == "intra_community_supply"
    assert body["treatment_label"] == "Innergemeinschaftliche Lieferung"
    assert body["legal_note"] == "Innergemeinschaftliche Lieferung, steuerfrei."


def test_the_seller_country_comes_from_the_company_and_cannot_be_sent(
    pg_client, austrian_company
):
    """A caller that could say "assume we are Austrian" would get a rate for
    a company that does not exist, and somebody would invoice it."""
    response = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT", "seller_country": "DE"},
    )

    assert response.status_code == 422, response.text
    assert "seller_country" in response.text

    accepted = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT"},
    )
    assert accepted.json()["seller_country"] == "AT"


def test_a_company_without_a_country_gets_a_blocking_proposal(pg_client, pg_db):
    """Acceptance item 8, through the API. No guessed rate, and a sentence
    saying where to fix it."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "blocked@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()

    response = pg_client.post(
        "/company/tax/propose",
        headers=auth_headers(owner, company),
        json={"buyer_country": "AT"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blocking"] is True
    assert body["rate"] is None and body["code"] is None
    assert body["seller_country"] is None
    assert [warning["code"] for warning in body["warnings"]] == [
        "home_country_missing"
    ]
    assert "Settings → Company" in body["warnings"][0]["text"]


def test_a_proposal_without_a_buyer_country_blocks(pg_client, austrian_company):
    response = pg_client.post(
        "/company/tax/propose", headers=_headers(austrian_company), json={}
    )

    body = response.json()
    assert body["blocking"] is True
    assert [warning["code"] for warning in body["warnings"]] == [
        "buyer_country_missing"
    ]


def test_the_small_business_flag_comes_from_the_company(
    pg_client, pg_db, austrian_company
):
    """Not from the request. One source of truth, and it is the one the
    invoice will read."""
    company = austrian_company["company"]

    before = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT"},
    ).json()
    assert before["code"] == "AT-20"

    company.kleinunternehmer = True
    pg_db.commit()

    after = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT"},
    ).json()
    assert after["code"] == "EU-KU"
    assert after["treatment"] == "small_business_exemption"


def test_an_override_naming_an_unknown_kind_is_refused_by_the_schema(
    pg_client, austrian_company
):
    """Refused at the edge rather than reaching the engine, so the message
    names the field instead of describing an internal state."""
    response = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT", "item_override": {"kind": "whatever"}},
    )

    assert response.status_code == 422, response.text
    assert "kind" in response.text


def test_the_proposal_endpoint_writes_nothing(pg_client, pg_db, austrian_company):
    """A preview that mutated anything would consume something on every
    keystroke of the screen that calls it."""
    before = (
        pg_db.query(TaxCode).count(),
        pg_db.query(AuditEvent).count(),
    )

    for payload in (
        {"buyer_country": "AT"},
        {"buyer_country": "DE", "buyer_vat_verified": True},
        {"buyer_country": "US", "supply_kind": "goods"},
        {
            "buyer_country": "DE",
            "item_override": {"kind": "domestic_for_foreign_business"},
        },
    ):
        assert (
            pg_client.post(
                "/company/tax/propose",
                headers=_headers(austrian_company),
                json=payload,
            ).status_code
            == 200
        )

    pg_db.expire_all()
    assert (pg_db.query(TaxCode).count(), pg_db.query(AuditEvent).count()) == before


def test_an_edited_rate_changes_what_is_proposed(
    pg_client, pg_db, austrian_company
):
    """The loop that matters: the user's edit is what the engine reads.

    Without this, every proposal test would pass against the shipped pack
    while the endpoint ignored the company's own rows entirely — which is
    exactly what `tax_service.engine_tables` exists to prevent.
    """
    company = austrian_company["company"]
    code = _code(pg_db, company, "AT-20")
    pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(austrian_company),
        json={"rate": "21"},
    )

    body = pg_client.post(
        "/company/tax/propose",
        headers=_headers(austrian_company),
        json={"buyer_country": "AT"},
    ).json()

    assert body["code"] == "AT-20"
    assert body["rate"] == "21.0000"
    assert "21 %" in " ".join(body["reasons"])
