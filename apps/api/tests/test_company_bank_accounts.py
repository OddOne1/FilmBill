"""Bank accounts: the IBAN check, and the one-default invariant.

Two things are worth a file of their own here.

**The IBAN check is structural and offline.** `core/iban.py` calls nothing, so
it can never be the reason a settings form hangs — and mod-97 catches the
errors a person entering an account off a letterhead actually makes. What it
does NOT prove is that the account exists, and nothing in this file pretends
otherwise.

**"Exactly one default" is an invariant, not a convention.** A company with
accounts and no default is a company whose next invoice has no account number
on it. The client cannot maintain it — demote-then-promote is two requests and
the gap between them is the bug — so it is enforced server-side, in one
transaction, and these tests read the database afterwards rather than
believing the response.
"""

import pytest

from apps.api.models.company import CompanyBankAccount, CompanyRole
from apps.api.models.site_settings import SiteSettings

from .company_factories import (
    auth_headers,
    grant,
    make_bank_account,
    make_company,
    make_superadmin,
    make_user,
)

#: Real, well-formed IBANs with correct check digits. Austrian and German
#: documentation examples, not accounts belonging to anybody.
VALID_AT = "AT61 1904 3002 3457 3201"
VALID_DE = "DE89 3704 0044 0532 0130 00"


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


def _defaults(pg_db, company):
    return (
        pg_db.query(CompanyBankAccount)
        .filter(
            CompanyBankAccount.company_id == company.id,
            CompanyBankAccount.is_default.is_(True),
        )
        .all()
    )


# ─── The IBAN ───────────────────────────────────────────────────────────────


def test_a_valid_iban_is_accepted_and_stored_without_spaces(pg_client, pg_db, world):
    """Stored compact, served both ways.

    Normalising on the way in is what stops one row holding the spaced form
    and another the compact one — after which every equality check is quietly
    wrong for half the table.
    """
    response = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "Production"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["iban"] == "AT611904300234573201"
    assert body["iban_formatted"] == "AT61 1904 3002 3457 3201"


def test_a_wrong_check_digit_is_refused_with_a_readable_reason(
    pg_client, pg_db, world
):
    """One transposed character — the error people actually make."""
    response = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": "AT61 1904 3002 3457 3202"},
    )

    assert response.status_code == 422
    assert "check digits" in response.text
    assert pg_db.query(CompanyBankAccount).count() == 0


@pytest.mark.parametrize(
    "bad,because",
    [
        ("AT61 1904 3002 3457 320", "an AT IBAN is 20 characters"),
        ("1234 5678 9012 3456", "no country prefix"),
        ("ATXX 1904 3002 3457 3201", "check digits are not digits"),
        ("", "empty"),
        ("   ", "whitespace only"),
        ("AT61-1904-3002-3457-3201", "hyphens are not IBAN characters"),
    ],
)
def test_malformed_ibans_are_refused(pg_client, world, bad, because):
    response = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": bad}
    )
    assert response.status_code == 422, f"accepted {bad!r} ({because})"


def test_a_country_this_registry_does_not_know_passes_on_the_checksum_alone(pg_db):
    """A self-hosted install somewhere `IBAN_LENGTHS` has not heard of must
    still be able to enter its own bank account.

    Asserted at the unit, because there is no way to express "a country code
    that will never be added" through the API without inventing one — and an
    invented code with valid check digits is exactly what this needs.
    """
    from apps.api.core.iban import IBAN_LENGTHS, validate_iban

    assert "ZZ" not in IBAN_LENGTHS
    # ZZ00 + 16 zeros, with check digits computed to satisfy mod-97.
    candidate = None
    for check in range(10, 100):
        trial = f"ZZ{check}0000000000000000"
        try:
            validate_iban(trial)
        except Exception:
            continue
        candidate = trial
        break

    assert candidate is not None, "no ZZ IBAN satisfied mod-97 — check the algorithm"


# ─── Exactly one default ────────────────────────────────────────────────────


def test_the_first_account_becomes_the_default_even_unasked(pg_client, pg_db, world):
    """Nothing downstream has an answer for "accounts, but no default"."""
    response = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "is_default": False},
    )

    assert response.status_code == 201
    assert response.json()["is_default"] is True


def test_promoting_a_second_account_demotes_the_first(pg_client, pg_db, world):
    """The mutation target. Read from the database, not from the response —
    a handler that returned `is_default: true` while writing nothing would
    satisfy an assertion on its own output."""
    first = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    ).json()
    second = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_DE}
    ).json()
    assert first["is_default"] is True and second["is_default"] is False

    response = pg_client.patch(
        f"/company/bank-accounts/{second['id']}",
        headers=_headers(world),
        json={"is_default": True},
    )

    assert response.status_code == 200
    pg_db.expire_all()
    defaults = _defaults(pg_db, world["company"])
    assert len(defaults) == 1, f"{len(defaults)} defaults after promoting one"
    assert str(defaults[0].id) == second["id"]


def test_creating_an_account_as_the_default_demotes_the_previous_one(
    pg_client, pg_db, world
):
    """The other way in. Two code paths reach `set_default_bank_account`, and
    a test of only the PATCH would leave the POST free to write a second
    default."""
    pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    )
    pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_DE, "is_default": True},
    )

    pg_db.expire_all()
    assert len(_defaults(pg_db, world["company"])) == 1


def test_the_default_cannot_simply_be_demoted(pg_client, world):
    """Refused, with the action that does work named in the message."""
    account = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    ).json()
    pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_DE}
    )

    response = pg_client.patch(
        f"/company/bank-accounts/{account['id']}",
        headers=_headers(world),
        json={"is_default": False},
    )

    assert response.status_code == 400
    assert "default" in response.json()["detail"]


def test_the_default_cannot_be_deleted_while_another_account_exists(
    pg_client, world
):
    account = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    ).json()
    pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_DE}
    )

    response = pg_client.delete(
        f"/company/bank-accounts/{account['id']}", headers=_headers(world)
    )

    assert response.status_code == 400


def test_the_last_account_can_be_deleted(pg_client, pg_db, world):
    """A company with no accounts at all is fine — it is a company that has
    not told us where to be paid yet."""
    account = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    ).json()

    response = pg_client.delete(
        f"/company/bank-accounts/{account['id']}", headers=_headers(world)
    )

    assert response.status_code == 204
    assert pg_db.query(CompanyBankAccount).count() == 0


def test_the_listing_puts_the_default_first(pg_client, world):
    pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "First"},
    )
    pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_DE, "label": "Second", "is_default": True},
    )

    body = pg_client.get("/company/bank-accounts", headers=_headers(world)).json()

    assert [entry["label"] for entry in body] == ["Second", "First"]


# ─── Scoping, beyond the parametrised isolation file ────────────────────────


def test_the_default_of_another_company_is_not_demoted(pg_client, pg_db, world):
    """The scoping bug this endpoint could plausibly have.

    `set_default_bank_account` issues a bulk UPDATE over "the other rows". If
    that UPDATE forgot its `company_id`, it would quietly clear the default
    flag on every other company on the installation — no error anywhere, and
    nobody notices until an invoice renders with no bank account on it.
    """
    other = make_company(pg_db, "Other GmbH")
    theirs = make_bank_account(pg_db, other, iban=VALID_DE, is_default=True)

    pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "is_default": True},
    )

    pg_db.refresh(theirs)
    assert theirs.is_default is True, (
        "creating a default in one company cleared another company's default"
    )


def test_an_accountant_cannot_read_the_bank_accounts(pg_client, pg_db, world):
    """Gated on `company.settings.edit`, not `company.view`, for READS too.

    An IBAN is the detail a convincing invoice fraud needs; there is no reason
    every member of a production company can read one out of a settings
    screen. Asserted so the stricter choice cannot be loosened by accident.
    """
    accountant = make_user(pg_db, "accountant@example.com")
    grant(pg_db, world["company"], accountant, CompanyRole.accountant)

    response = pg_client.get(
        "/company/bank-accounts",
        headers=auth_headers(accountant, world["company"]),
    )

    assert response.status_code == 404
