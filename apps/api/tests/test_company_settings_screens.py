"""What the company settings screens write, seen from the API.

Three groups, and the middle one is the reason this file exists rather than
a handful of additions to `test_company_members.py`:

* **General** — the fields a company puts on its own invoices.
* **Security** — `require_2fa_roles`, asserted END TO END. P0b-1 proved
  `two_factor_required_for` reads the column; this proves that saving the
  Security screen is what puts a value in it, and that the consequence — a
  producer forced into enrolment at their next login — actually follows. The
  unit test underneath could pass while the PATCH wrote nothing.
* **Accounting** — the selectors persist and change nothing. "Changes
  nothing" is the assertion that is easy to skip and is exactly what P6 will
  rely on when it starts reading them.
"""

import pytest

from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
    password,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


# ─── General ────────────────────────────────────────────────────────────────


def test_the_general_screen_saves_every_field_it_shows(pg_client, pg_db, world):
    """All of them at once, because a PATCH that drops one field silently is
    the failure mode of a wide form — and the field it drops is whichever one
    nobody checked."""
    payload = {
        "legal_name": "YON Studio OG",
        "trading_name": "YON",
        "legal_form": "OG",
        "register_number": "FN 123456a",
        "register_court": "Handelsgericht Wien",
        "vat_id": "ATU12345678",
        "tax_number": "12/345/6789",
        "address_street": "Praterstraße 1",
        "address_zip": "1020",
        "address_city": "Wien",
        "address_country": "at",
        "email": "office@example.com",
        "phone": "+43 1 234 5678",
        "website": "https://example.com",
        "default_currency": "eur",
        "default_language": "de",
        "fiscal_year_start_month": 1,
        "timezone": "Europe/Vienna",
    }

    response = pg_client.patch("/company", headers=_headers(world), json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    for field, value in payload.items():
        expected = value.upper() if field in {"address_country", "default_currency"} else value
        assert body[field] == expected, f"{field} came back as {body[field]!r}"


def test_a_partial_save_does_not_blank_the_rest(pg_client, world):
    """`exclude_unset`, asserted through the API.

    The failure this prevents is a person editing the phone number and losing
    their VAT ID — which they would not notice until an invoice went out
    without it.
    """
    pg_client.patch(
        "/company",
        headers=_headers(world),
        json={"vat_id": "ATU12345678", "address_city": "Wien"},
    )

    response = pg_client.patch(
        "/company", headers=_headers(world), json={"phone": "+43 1 234 5678"}
    )

    body = response.json()
    assert body["vat_id"] == "ATU12345678"
    assert body["address_city"] == "Wien"


def test_a_country_code_is_stored_upper_case(pg_client, world):
    """One spelling, because it is the key a region pack is looked up by."""
    body = pg_client.patch(
        "/company", headers=_headers(world), json={"address_country": "at"}
    ).json()

    assert body["address_country"] == "AT"


def test_an_accountant_cannot_save_the_general_screen(pg_client, pg_db, world):
    accountant = make_user(pg_db, "accountant@example.com")
    grant(pg_db, world["company"], accountant, CompanyRole.accountant)

    response = pg_client.patch(
        "/company",
        headers=auth_headers(accountant, world["company"]),
        json={"legal_name": "Mine now"},
    )

    assert response.status_code == 404


# ─── Security — end to end ──────────────────────────────────────────────────


def test_saving_the_security_screen_makes_a_producer_need_2fa(pg_client, pg_db, world):
    """§4.6, the whole chain in one test.

    Save the screen → the column changes → `/auth/me` reports the requirement
    for a producer → that producer's next login forces enrolment. Each link
    has a unit test underneath it; this is the one that would catch a PATCH
    that validated the roles and then forgot to write them.
    """
    producer = make_user(pg_db, "producer@example.com")
    grant(pg_db, world["company"], producer, CompanyRole.producer)

    before = pg_client.get("/auth/me", headers=auth_headers(producer)).json()
    assert before["two_factor_required"] is False

    saved = pg_client.patch(
        "/company", headers=_headers(world), json={"require_2fa_roles": ["producer"]}
    )
    assert saved.status_code == 200
    assert saved.json()["require_2fa_roles"] == ["producer"]

    after = pg_client.get("/auth/me", headers=auth_headers(producer)).json()
    assert after["two_factor_required"] is True

    login = pg_client.post(
        "/auth/login", json={"email": producer.email, "password": password()}
    ).json()
    assert login["requires_2fa"] is True
    assert login["setup_required"] is True


def test_removing_the_role_again_lifts_the_requirement(pg_client, pg_db, world):
    """The way back out.

    A screen that can only ever add a requirement is a screen that turns a
    mis-click into a permanent obligation for everybody holding that role.
    """
    producer = make_user(pg_db, "producer@example.com")
    grant(pg_db, world["company"], producer, CompanyRole.producer)
    pg_client.patch(
        "/company", headers=_headers(world), json={"require_2fa_roles": ["producer"]}
    )

    pg_client.patch("/company", headers=_headers(world), json={"require_2fa_roles": []})

    body = pg_client.get("/auth/me", headers=auth_headers(producer)).json()
    assert body["two_factor_required"] is False


def test_the_requirement_does_not_reach_a_different_role(pg_client, pg_db, world):
    staffer = make_user(pg_db, "staffer@example.com")
    grant(pg_db, world["company"], staffer, CompanyRole.staff)
    pg_client.patch(
        "/company", headers=_headers(world), json={"require_2fa_roles": ["producer"]}
    )

    body = pg_client.get("/auth/me", headers=auth_headers(staffer)).json()
    assert body["two_factor_required"] is False


def test_an_unknown_role_is_refused_rather_than_stored(pg_client, world):
    """A value nothing can ever match would be a setting that silently does
    nothing — which looks identical, on screen, to one that works."""
    response = pg_client.patch(
        "/company", headers=_headers(world), json={"require_2fa_roles": ["gaffer"]}
    )

    assert response.status_code == 422


def test_the_tax_advisor_reports_toggle_widens_only_the_two_keys(
    pg_client, pg_db, world
):
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)

    closed = pg_client.get("/companies", headers=auth_headers(advisor)).json()
    assert "reports.view" not in closed[0]["permissions"]

    pg_client.patch(
        "/company", headers=_headers(world), json={"tax_advisor_reports": True}
    )

    opened = pg_client.get("/companies", headers=auth_headers(advisor)).json()
    assert set(opened[0]["permissions"]) - set(closed[0]["permissions"]) == {
        "reports.view",
        "exports.download",
    }


# ─── Accounting — stored, and inert ─────────────────────────────────────────

ACCOUNTING = {
    "bookkeeping_mode": "double_entry",
    "vat_timing": "ist",
    "kleinunternehmer": True,
    "chart_of_accounts_template": "at_skr03",
    "export_format": "bmd_csv",
    "archive_date_basis": "payment_date",
    "month_approval_enabled": True,
}


def test_the_accounting_selectors_persist(pg_client, pg_db, world):
    response = pg_client.patch("/company", headers=_headers(world), json=ACCOUNTING)

    assert response.status_code == 200, response.text
    reread = pg_client.get("/company", headers=_headers(world)).json()
    for field, value in ACCOUNTING.items():
        assert reread[field] == value, f"{field} did not persist"


def test_the_defaults_are_the_ordinary_austrian_case(pg_client, world):
    """What a company gets before anybody opens the screen.

    Asserted so that changing a default is a deliberate act with a failing
    test attached, rather than something that quietly reclassifies every
    company created after the change.
    """
    body = pg_client.get("/company", headers=_headers(world)).json()

    assert body["bookkeeping_mode"] == "ear"
    assert body["vat_timing"] == "soll"
    assert body["kleinunternehmer"] is False
    assert body["archive_date_basis"] == "invoice_date"
    assert body["month_approval_enabled"] is False
    # NULL, not a first option: "nobody has chosen" must stay distinguishable
    # from "chose the first one", because the real lists come from region
    # packs that do not exist yet.
    assert body["chart_of_accounts_template"] is None
    assert body["export_format"] is None


@pytest.mark.parametrize(
    "field,bad",
    [
        ("bookkeeping_mode", "cash_basis"),
        ("vat_timing", "quarterly"),
        ("archive_date_basis", "due_date"),
    ],
)
def test_an_invalid_selector_value_is_refused(pg_client, world, field, bad):
    """The three with a fixed vocabulary are `Literal`s, so the API refuses a
    value the UI could never offer — and the generated TypeScript carries the
    same three options rather than `string`."""
    response = pg_client.patch("/company", headers=_headers(world), json={field: bad})

    assert response.status_code == 422


def test_storing_the_selectors_changes_nothing_else(pg_client, pg_db, world):
    """SCOPE §10 (D13): these are answers, not behaviour, until P6.

    Worth an explicit test because "it does nothing" is the property a future
    change breaks silently. If wiring one of these up makes this fail, that is
    the test doing its job — update it in the same commit that makes the
    selector mean something.
    """
    producer = make_user(pg_db, "producer@example.com")
    grant(pg_db, world["company"], producer, CompanyRole.producer)

    before_company = pg_client.get("/company", headers=_headers(world)).json()
    before_me = pg_client.get("/auth/me", headers=auth_headers(producer)).json()
    before_permissions = pg_client.get(
        "/companies", headers=auth_headers(producer)
    ).json()[0]["permissions"]

    pg_client.patch("/company", headers=_headers(world), json=ACCOUNTING)

    after_company = pg_client.get("/company", headers=_headers(world)).json()
    after_me = pg_client.get("/auth/me", headers=auth_headers(producer)).json()
    after_permissions = pg_client.get(
        "/companies", headers=auth_headers(producer)
    ).json()[0]["permissions"]

    # Nothing outside the seven selectors moved.
    changed = {
        field
        for field in after_company
        if after_company[field] != before_company.get(field)
    }
    assert changed == set(ACCOUNTING), f"unexpected changes: {changed - set(ACCOUNTING)}"
    assert after_me == before_me
    assert after_permissions == before_permissions


# ─── Creating a company through the API the screen uses ─────────────────────


def test_creating_a_company_returns_the_accounting_defaults(pg_client, pg_db):
    """The create screen posts here and then switches to the new company, so
    the response has to be the full record rather than just an id."""
    root = make_superadmin(pg_db)

    response = pg_client.post(
        "/companies",
        headers=auth_headers(root),
        json={"legal_name": "Zweite GmbH", "address_country": "AT"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["bookkeeping_mode"] == "ear"
    assert body["vat_timing"] == "soll"
    assert body["archived_at"] is None
