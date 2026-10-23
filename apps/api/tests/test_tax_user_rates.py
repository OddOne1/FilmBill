"""Adding your own rates and no-tax cases, against a real PostgreSQL.

Mathias, 2026-10-23: *"this app is for users all over Europe: one or several
reduced rates, maybe several standard rates; the number is up to the user …
there must also be the option of no tax."* So the region pack is a starting
point and these are the two ways a company goes on from there.

**The boundary under test is the EN 16931 category.** A user invents rates,
names and wording; a user never invents a category. A new rate is always `S`
— a reduced rate is a rated rate, just lower — and a no-tax case picks one of
six. The reason is not tidiness: the category is what an e-invoice is
validated against at the recipient, so a category nobody recognises is a
document that bounces weeks later, in somebody else's system, with no useful
message. Several tests below exist only to make that impossible to reach by
typing.

The other theme is **nothing is ever deleted**. A class any item has been
labelled with explains that item's tax, and a document line snapshots the
code it used, so retiring is a flag — and a rate's class and code carry that
flag together, because one of the two switched off is a state nothing on the
screen can explain.
"""

from decimal import Decimal

import pytest

from apps.api.models.audit import AuditEvent
from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.tax import TaxClass, TaxCode
from apps.api.services import tax_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)


@pytest.fixture
def seeded(pg_db):
    """An Austrian company with its pack, and one owner."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG", address_country="AT")
    owner = make_user(pg_db, "rates-owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    tax_service.seed_tax_configuration(pg_db, company=company)
    pg_db.commit()
    return {"company": company, "owner": owner}


@pytest.fixture
def country_less(pg_db):
    """A company with no country at all — the dev and server-test shape."""
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "FilmBill")
    owner = make_user(pg_db, "bare-rates@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _codes(pg_db, company) -> dict:
    return {
        row.code: row
        for row in pg_db.query(TaxCode).filter(TaxCode.company_id == company.id)
    }


def _classes(pg_db, company) -> dict:
    return {
        row.key: row
        for row in pg_db.query(TaxClass).filter(TaxClass.company_id == company.id)
    }


def _events(pg_db, action: str) -> list[AuditEvent]:
    return (
        pg_db.query(AuditEvent)
        .filter(AuditEvent.action == action)
        .order_by(AuditEvent.created_at)
        .all()
    )


# ─── Adding a rate ──────────────────────────────────────────────────────────


def test_adding_a_rate_creates_a_class_and_an_s_code_together(
    pg_client, pg_db, seeded
):
    """Both halves, in one request, with the code generated from the rate."""
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Super-reduced", "rate": "4.5"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tax_code"]["code"] == "AT-4.5"
    assert body["tax_code"]["rate"] == "4.5000"
    assert body["tax_code"]["category"] == "S"
    assert body["tax_code"]["name"] == "Super-reduced"
    assert body["tax_code"]["is_active"] is True
    # NULL origin: the company made this, and the seeder treats it as none of
    # its business.
    assert body["tax_code"]["origin_pack"] is None

    assert body["tax_class"]["key"] == "custom_1"
    assert body["tax_class"]["label"] == "Super-reduced"
    assert body["tax_class"]["code"] == "AT-4.5"
    assert body["tax_class"]["rate"] == "4.5000"
    assert body["tax_class"]["is_active"] is True
    assert body["tax_class"]["is_custom"] is True

    stored = _classes(pg_db, seeded["company"])["custom_1"]
    assert stored.code_id == _codes(pg_db, seeded["company"])["AT-4.5"].id


def test_the_generated_code_uses_the_company_country_and_the_rate(
    pg_client, pg_db, seeded
):
    for rate, expected in (("21", "AT-21"), ("9.5", "AT-9.5"), ("2.1", "AT-2.1")):
        response = pg_client.post(
            "/company/tax/rates",
            headers=_headers(seeded),
            json={"label": f"Rate {rate}", "rate": rate},
        )
        assert response.status_code == 201, response.text
        assert response.json()["tax_code"]["code"] == expected


def test_a_company_without_a_country_still_gets_a_usable_code(
    pg_client, country_less
):
    """`TAX-` rather than a blank prefix or a crash.

    A company with no country has no tax configuration at all, but it can
    still be handed a rate by somebody who knows what they are doing, and the
    code has to be something an advisor can read.
    """
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(country_less),
        json={"label": "Standard", "rate": "20"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["tax_code"]["code"] == "TAX-20"


def test_a_colliding_generated_code_gets_a_suffix(pg_client, seeded):
    """Two rates at the same percentage is a legitimate configuration — a
    reduced rate and a special scheme at the same number — so the second one
    is given a suffix rather than refused."""
    first = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Thirteen A", "rate": "13"},
    )
    second = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Thirteen B", "rate": "13"},
    )

    # `AT-13` is already the pack's, so the first custom one is already a
    # suffixed code.
    assert first.json()["tax_code"]["code"] == "AT-13-2"
    assert second.json()["tax_code"]["code"] == "AT-13-3"


def test_a_user_chosen_code_is_accepted_and_upper_cased(pg_client, seeded):
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "House rate", "rate": "7", "code": "haus-7"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["tax_code"]["code"] == "HAUS-7"


@pytest.mark.parametrize(
    "code",
    [
        "has space",
        "-starts-with-a-hyphen",
        "WAY-TOO-LONG-FOR-THE-COLUMN-X",
        "slash/es",
        "ümlaut",
        "",
    ],
)
def test_an_unusable_code_is_refused_with_a_readable_message(
    pg_client, seeded, code
):
    """This string is printed on invoices and read by the advisor's software,
    so a space or a slash is a support request waiting to happen."""
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Nope", "rate": "7", "code": code},
    )

    assert response.status_code == 422, response.text


def test_a_duplicate_user_chosen_code_is_refused_by_name(pg_client, seeded):
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Clash", "rate": "7", "code": "AT-20"},
    )

    assert response.status_code == 422, response.text
    assert "AT-20" in response.json()["detail"]


@pytest.mark.parametrize("rate", ["0", "-5", "100", "140", "0.0000"])
def test_a_rate_outside_the_bounds_is_refused(pg_client, seeded, rate):
    """Above 0 because an unset rate is something only a pack ships; below
    100 because a percentage of 100 or more is a typo in every real tax
    system, and catching it here is cheaper than on an invoice."""
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Out of range", "rate": rate},
    )

    assert response.status_code == 422, response.text


def test_a_rate_with_four_decimals_is_kept_exactly(pg_client, pg_db, seeded):
    """Rule 1. A rate is a Decimal from the request to the column."""
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Odd", "rate": "8.1667"},
    )

    assert response.json()["tax_code"]["rate"] == "8.1667"
    assert _codes(pg_db, seeded["company"])["AT-8.1667"].rate == Decimal("8.1667")


def test_a_rate_needs_a_name(pg_client, seeded):
    response = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "   ", "rate": "7"},
    )
    assert response.status_code == 422, response.text


def test_the_class_keys_count_up_and_skip_nothing(pg_client, pg_db, seeded):
    """`custom_1`, `custom_2`, `custom_3` — and the next one after a retire
    does not reuse a key, because an item may still be labelled with it."""
    for index in range(1, 4):
        response = pg_client.post(
            "/company/tax/rates",
            headers=_headers(seeded),
            json={"label": f"Rate {index}", "rate": str(index + 1)},
        )
        assert response.json()["tax_class"]["key"] == f"custom_{index}"

    retired = _classes(pg_db, seeded["company"])["custom_2"]
    pg_client.patch(
        f"/company/tax/classes/{retired.id}",
        headers=_headers(seeded),
        json={"is_active": False},
    )
    again = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "After a retire", "rate": "6"},
    )
    assert again.json()["tax_class"]["key"] == "custom_4"


def test_a_failed_rate_creation_leaves_neither_half_behind(
    pg_client, pg_db, seeded
):
    """One transaction. A class with no code is a class every item in it
    resolves nothing from."""
    before = (
        len(_codes(pg_db, seeded["company"])),
        len(_classes(pg_db, seeded["company"])),
    )

    assert (
        pg_client.post(
            "/company/tax/rates",
            headers=_headers(seeded),
            json={"label": "Doomed", "rate": "7", "code": "AT-20"},
        ).status_code
        == 422
    )

    pg_db.expire_all()
    assert (
        len(_codes(pg_db, seeded["company"])),
        len(_classes(pg_db, seeded["company"])),
    ) == before


def test_a_company_is_capped_at_thirty_codes(pg_client, pg_db, seeded):
    """A guard against a runaway script, not a product limit.

    `engine_tables` loads every code on every proposal — including each
    keystroke of the Test-a-case screen — so the cap keeps that a query
    returning tens of rows. The message says what to do instead.
    """
    start = len(_codes(pg_db, seeded["company"]))
    assert start < tax_service.MAX_CODES_PER_COMPANY

    for index in range(tax_service.MAX_CODES_PER_COMPANY - start):
        response = pg_client.post(
            "/company/tax/rates",
            headers=_headers(seeded),
            json={"label": f"Filler {index}", "rate": "7", "code": f"F{index}"},
        )
        assert response.status_code == 201, response.text

    refused = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "One too many", "rate": "7", "code": "LAST"},
    )

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert str(tax_service.MAX_CODES_PER_COMPANY) in detail
    assert "Deactivate one" in detail


# ─── Adding a no-tax case ───────────────────────────────────────────────────


def test_adding_a_no_tax_case_creates_a_rate_zero_code_and_no_class(
    pg_client, pg_db, seeded
):
    response = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={
            "label": "Exempt – training",
            "category": "E",
            "legal_notes": {"de": "Steuerfrei: Bildungsleistung.", "en": "Exempt: training."},
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "AT-E"
    assert body["rate"] == "0.0000"
    assert body["category"] == "E"
    assert body["name"] == "Exempt – training"
    assert body["legal_notes"]["de"] == "Steuerfrei: Bildungsleistung."
    # The user wrote it, so it is as unreviewed as anything FilmBill ships.
    assert body["notes_reviewed"] is False
    assert body["origin_pack"] is None

    # No class: a no-tax case is a property of a situation, not of an item.
    assert "AT-E" not in {
        row.code
        for row in pg_db.query(TaxCode)
        .join(TaxClass, TaxClass.code_id == TaxCode.id)
        .filter(TaxCode.company_id == seeded["company"].id)
    }


@pytest.mark.parametrize("category", ["Z", "E", "AE", "K", "G", "O"])
def test_every_one_of_the_six_categories_is_accepted(
    pg_client, seeded, category
):
    response = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": f"Case {category}", "category": category},
    )

    assert response.status_code == 201, response.text
    assert response.json()["category"] == category
    assert response.json()["rate"] == "0.0000"


@pytest.mark.parametrize("category", ["S", "L", "M", "X", "", "e"])
def test_a_category_outside_the_six_is_refused(pg_client, seeded, category):
    """`S` is absent because a rated code is a RATE, added the other way;
    `L` and `M` carry real rates and need a region pack, not a dropdown; the
    rest do not exist."""
    response = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "Nope", "category": category},
    )

    assert response.status_code == 422, response.text


def test_the_six_categories_are_served_with_a_line_in_both_languages(
    pg_client, seeded
):
    """Served rather than written into the web app, so the sentence a user
    chooses from lives beside the definition it explains."""
    response = pg_client.get(
        "/company/tax/no-tax-categories", headers=_headers(seeded)
    )

    assert response.status_code == 200, response.text
    options = {row["category"]: row["labels"] for row in response.json()}
    assert set(options) == {"Z", "E", "AE", "K", "G", "O"}
    for category, labels in options.items():
        assert set(labels) == {"en", "de"}, category
        for text in labels.values():
            assert len(text) > 20 and text.endswith("."), category


def test_the_served_categories_are_exactly_what_the_endpoint_accepts(
    pg_client, seeded
):
    """The property that matters: a dialog cannot offer a seventh.

    Driven through both endpoints rather than by comparing two literals — a
    text comparison would pass while one of them was never reached.
    """
    served = {
        row["category"]
        for row in pg_client.get(
            "/company/tax/no-tax-categories", headers=_headers(seeded)
        ).json()
    }
    for category in served:
        response = pg_client.post(
            "/company/tax/no-tax-cases",
            headers=_headers(seeded),
            json={"label": f"Offered {category}", "category": category},
        )
        assert response.status_code == 201, (category, response.text)


def test_a_blank_note_is_dropped_rather_than_stored(pg_client, seeded):
    """An empty string prints as nothing and reads as present."""
    response = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={
            "label": "Half noted",
            "category": "O",
            "legal_notes": {"de": "   ", "en": "Not taxable here."},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["legal_notes"] == {"en": "Not taxable here."}


def test_a_no_tax_case_code_defaults_to_country_and_category(pg_client, seeded):
    first = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "First", "category": "O"},
    )
    second = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "Second", "category": "O"},
    )

    assert first.json()["code"] == "AT-O"
    assert second.json()["code"] == "AT-O-2"


# ─── Editing and retiring ───────────────────────────────────────────────────


def test_a_class_label_can_be_changed_and_the_key_cannot(
    pg_client, pg_db, seeded
):
    """The key is what an ITEM stores, so renaming it would silently re-tax
    every item in that class."""
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Typo", "rate": "7"},
    ).json()
    class_id = created["tax_class"]["id"]

    renamed = pg_client.patch(
        f"/company/tax/classes/{class_id}",
        headers=_headers(seeded),
        json={"label": "Spelled right"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["label"] == "Spelled right"
    assert renamed.json()["key"] == "custom_1"

    refused = pg_client.patch(
        f"/company/tax/classes/{class_id}",
        headers=_headers(seeded),
        json={"key": "something_else"},
    )
    assert refused.status_code == 422, refused.text
    assert "key" in refused.text


def test_retiring_a_rate_switches_the_class_and_its_code_together(
    pg_client, pg_db, seeded
):
    """A live code behind a dead class is a state nothing can explain."""
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Short lived", "rate": "3"},
    ).json()

    response = pg_client.patch(
        f"/company/tax/classes/{created['tax_class']['id']}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False
    assert response.json()["code_is_active"] is False
    pg_db.expire_all()
    assert _codes(pg_db, seeded["company"])["AT-3"].is_active is False


def test_reactivating_a_rate_always_works(pg_client, pg_db, seeded):
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Back again", "rate": "3"},
    ).json()
    class_id = created["tax_class"]["id"]
    pg_client.patch(
        f"/company/tax/classes/{class_id}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    response = pg_client.patch(
        f"/company/tax/classes/{class_id}",
        headers=_headers(seeded),
        json={"is_active": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True
    assert response.json()["code_is_active"] is True


def test_switching_only_the_code_of_a_rate_is_refused_and_says_where_to_go(
    pg_client, pg_db, seeded
):
    """The pair's flag moves together, so there is one place to move it."""
    code = _codes(pg_db, seeded["company"])["AT-20"]

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "/company/tax/classes/" in detail
    assert "Normalsteuersatz" in detail
    pg_db.expire_all()
    assert _codes(pg_db, seeded["company"])["AT-20"].is_active is True


def test_a_no_tax_case_is_retired_through_the_code_endpoint(
    pg_client, pg_db, seeded
):
    """It has no class, so there is nothing else to switch."""
    created = pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "Briefly", "category": "E"},
    ).json()

    response = pg_client.patch(
        f"/company/tax/codes/{created['id']}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is False


def test_a_rate_edit_says_it_applies_from_now_on(pg_client, pg_db, seeded):
    """Rates change by law, so the edit is allowed — and the consequence is
    stated by the SERVER rather than by the screen, because it is a statement
    about what the system does (rule 3)."""
    code = _codes(pg_db, seeded["company"])["AT-20"]

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"rate": "21"},
    )

    assert response.status_code == 200, response.text
    notices = response.json()["notices"]
    assert len(notices) == 1
    assert "from now on" in notices[0]
    assert "already been issued" in notices[0]


def test_an_edit_that_is_not_a_rate_change_carries_no_notice(
    pg_client, pg_db, seeded
):
    code = _codes(pg_db, seeded["company"])["AT-20"]

    response = pg_client.patch(
        f"/company/tax/codes/{code.id}",
        headers=_headers(seeded),
        json={"name": "Renamed"},
    )

    assert response.json()["notices"] == []


def test_a_read_never_carries_a_notice(pg_client, seeded):
    """`notices` is for what the server wants said about a WRITE."""
    for row in pg_client.get(
        "/company/tax/codes", headers=_headers(seeded)
    ).json():
        assert row["notices"] == []


def test_there_is_no_delete_endpoint_for_a_code_or_a_class(pg_client, seeded):
    """Asserted on the OpenAPI document, which is the API's own statement of
    what exists.

    Nothing is ever deleted (Mathias, 2026-10-23), and the way to keep that
    true is for there to be no route — not for a handler to refuse.
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    tax_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if path.startswith("/company/tax")
    }
    assert tax_paths, "no tax paths at all — this test stopped covering them"
    for path, methods in tax_paths.items():
        assert "delete" not in methods, f"{path} has a DELETE"


# ─── Permissions ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role, expected",
    [
        (CompanyRole.owner, 201),
        (CompanyRole.admin, 201),
        (CompanyRole.accountant, 404),
        (CompanyRole.producer, 404),
        (CompanyRole.staff, 404),
        (CompanyRole.tax_advisor, 404),
    ],
)
def test_only_owner_and_admin_may_add_a_rate(
    pg_client, pg_db, seeded, role, expected
):
    """Same key as the rest of Settings → Company, and 404 on refusal
    (rule 6). Adding a rate changes what every future invoice can carry."""
    company = seeded["company"]
    member = make_user(pg_db, f"adder-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()

    response = pg_client.post(
        "/company/tax/rates",
        headers=auth_headers(member, company),
        json={"label": f"By {role.value}", "rate": "7"},
    )

    assert response.status_code == expected, response.text


@pytest.mark.parametrize("role", list(CompanyRole))
def test_every_role_may_read_the_no_tax_categories(pg_client, pg_db, seeded, role):
    company = seeded["company"]
    member = make_user(pg_db, f"reader-cat-{role.value}@example.com")
    grant(pg_db, company, member, role)
    pg_db.commit()

    response = pg_client.get(
        "/company/tax/no-tax-categories",
        headers=auth_headers(member, company),
    )
    assert response.status_code == 200, response.text


def test_a_class_of_another_company_is_404(pg_client, pg_db, seeded):
    other = make_company(pg_db, "Somebody Else GmbH", address_country="DE")
    tax_service.seed_tax_configuration(pg_db, company=other)
    pg_db.commit()
    theirs = (
        pg_db.query(TaxClass)
        .filter(TaxClass.company_id == other.id, TaxClass.key == "standard")
        .one()
    )

    response = pg_client.patch(
        f"/company/tax/classes/{theirs.id}",
        headers=_headers(seeded),
        json={"label": "Stolen"},
    )

    assert response.status_code == 404, response.text
    pg_db.expire_all()
    assert (
        pg_db.query(TaxClass).filter(TaxClass.id == theirs.id).one().label
        != "Stolen"
    )


# ─── Audit ──────────────────────────────────────────────────────────────────


def test_adding_a_rate_writes_a_created_event_for_each_half(
    pg_client, pg_db, seeded
):
    pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Audited", "rate": "4.5"},
    )

    code_events = _events(pg_db, "tax_code.created")
    class_events = _events(pg_db, "tax_class.created")
    assert len(code_events) == 1 and len(class_events) == 1

    # `[old, new]` — see `services/audit.diff`. A create has no old value.
    assert code_events[0].changes["code"] == [None, "AT-4.5"]
    assert code_events[0].changes["rate"] == [None, "4.5000"]
    assert code_events[0].changes["category"] == [None, "S"]
    assert code_events[0].entity_type == "tax_codes"
    assert code_events[0].company_id == seeded["company"].id
    assert code_events[0].user_id == seeded["owner"].id

    assert class_events[0].changes["key"] == [None, "custom_1"]
    assert class_events[0].changes["label"] == [None, "Audited"]
    assert class_events[0].entity_type == "tax_classes"


def test_adding_a_no_tax_case_writes_one_created_event(pg_client, pg_db, seeded):
    pg_client.post(
        "/company/tax/no-tax-cases",
        headers=_headers(seeded),
        json={"label": "Exempt – training", "category": "E"},
    )

    events = _events(pg_db, "tax_code.created")
    assert len(events) == 1
    assert events[0].changes["category"] == [None, "E"]
    assert events[0].changes["rate"] == [None, "0.0000"]
    assert _events(pg_db, "tax_class.created") == []


def test_renaming_a_rate_writes_a_class_updated_event(pg_client, pg_db, seeded):
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Before", "rate": "3"},
    ).json()

    pg_client.patch(
        f"/company/tax/classes/{created['tax_class']['id']}",
        headers=_headers(seeded),
        json={"label": "After"},
    )

    events = _events(pg_db, "tax_class.updated")
    assert len(events) == 1
    assert events[0].changes["label"] == ["Before", "After"]


def test_retiring_a_rate_is_audited_on_both_the_class_and_the_code(
    pg_client, pg_db, seeded
):
    """Two rows for one request, because they answer two different questions:
    "why is this rate gone from the picker" and "why is AT-3 inactive"."""
    created = pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Doomed", "rate": "3"},
    ).json()

    pg_client.patch(
        f"/company/tax/classes/{created['tax_class']['id']}",
        headers=_headers(seeded),
        json={"is_active": False},
    )

    class_event = _events(pg_db, "tax_class.updated")[-1]
    code_event = _events(pg_db, "tax_code.updated")[-1]
    assert class_event.changes["is_active"] == [True, False]
    assert code_event.changes["is_active"] == [True, False]
    assert code_event.entity_type == "tax_codes"


def test_a_refused_creation_leaves_no_audit_row(pg_client, pg_db, seeded):
    """The event is written in the caller's transaction, before the commit."""
    assert (
        pg_client.post(
            "/company/tax/rates",
            headers=_headers(seeded),
            json={"label": "Doomed", "rate": "0"},
        ).status_code
        == 422
    )

    assert _events(pg_db, "tax_code.created") == []
    assert _events(pg_db, "tax_class.created") == []


def test_the_created_events_are_visible_in_the_audit_log(
    pg_client, pg_db, seeded
):
    """Acceptance item 6, end to end."""
    pg_client.post(
        "/company/tax/rates",
        headers=_headers(seeded),
        json={"label": "Super-reduced", "rate": "4.5"},
    )

    for action in ("tax_code.created", "tax_class.created"):
        response = pg_client.get(
            f"/company/audit-events?action={action}", headers=_headers(seeded)
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1, action
        assert body["events"][0]["action"] == action
