"""The permission matrix: every role against every endpoint.

Parametrised over the six roles rather than written as six test functions, so
that adding a role to `CompanyRole` without deciding what it may do fails
here — `EXPECTED` is checked for completeness against the enum before any
request is made.

The `tax_advisor` row is the one this file exists for. That role hands a
company's books to someone who does not work there (SCOPE §9.3b), and the
rule is that it reaches the archive and NOTHING else. There is no archive
endpoint yet — P5 builds it — so what that looks like today is: every
company endpoint answers 404, and `GET /companies` still lists the company,
because an advisor who cannot see whose archive they are in cannot do the job.
"""

import pytest

from apps.api.models.company import CompanyRole
from apps.api.services.permissions import (
    PERMISSIONS,
    TAX_ADVISOR_OPTIONAL,
    has_permission,
    permissions_for,
)

from .company_factories import auth_headers, grant, make_company, make_superadmin, make_user

#: role -> operation -> expected status.
#:
#: 404, never 403, for a member who lacks the permission — see
#: `middleware/company.py`. The one 403 in this table is `POST /companies`,
#: which is about the caller's INSTALLATION role and has no company to be coy
#: about.
EXPECTED: dict[CompanyRole, dict[str, int]] = {
    CompanyRole.owner: {
        "GET /company/bank-accounts": 200,
        "GET /company": 200,
        "PATCH /company": 200,
        "GET /company/members": 200,
    },
    CompanyRole.admin: {
        "GET /company/bank-accounts": 200,
        "GET /company": 200,
        "PATCH /company": 200,
        "GET /company/members": 200,
    },
    CompanyRole.accountant: {
        "GET /company/bank-accounts": 404,
        "GET /company": 200,
        "PATCH /company": 404,
        "GET /company/members": 404,
    },
    CompanyRole.producer: {
        "GET /company/bank-accounts": 404,
        "GET /company": 200,
        "PATCH /company": 404,
        "GET /company/members": 404,
    },
    CompanyRole.staff: {
        "GET /company/bank-accounts": 404,
        "GET /company": 200,
        "PATCH /company": 404,
        "GET /company/members": 404,
    },
    CompanyRole.tax_advisor: {
        # 404 like everything else. An external advisor reads the archive;
        # the company's bank details are not part of that and are exactly
        # what an invoice fraud would want.
        "GET /company/bank-accounts": 404,
        # Sees WHOSE archive this is, and nothing else about the company.
        "GET /company": 200,
        "PATCH /company": 404,
        "GET /company/members": 404,
    },
}

_BODIES = {"PATCH /company": {"trading_name": "Renamed"}}


def test_every_role_has_a_row(client):
    """A role added to the enum without a decision about it fails here."""
    missing = sorted(role.value for role in CompanyRole if role not in EXPECTED)
    assert not missing, f"roles with no expectations in this matrix: {missing}"


@pytest.mark.parametrize("role", list(CompanyRole), ids=lambda r: r.value)
def test_role_reaches_exactly_what_it_should(pg_client, pg_db, role):
    make_superadmin(pg_db)
    company = make_company(pg_db, "Matrix GmbH")
    # A second owner so that the matrix's own user can hold any role without
    # the "a company must keep an owner" guard interfering.
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)

    user = make_user(pg_db, f"member-{role.value}@example.com")
    grant(pg_db, company, user, role)
    headers = auth_headers(user, company)

    for operation, expected in EXPECTED[role].items():
        method, path = operation.split(" ", 1)
        response = pg_client.request(
            method, path, headers=headers, json=_BODIES.get(operation)
        )
        assert response.status_code == expected, (
            f"{role.value} got {response.status_code} on {operation}, expected "
            f"{expected}: {response.text}"
        )


def test_a_tax_advisor_still_sees_the_company_in_their_switcher(pg_client, pg_db):
    """Otherwise the archive they can open belongs to nobody they can name."""
    make_superadmin(pg_db)
    company = make_company(pg_db, "Klientin OG")
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, company, advisor, CompanyRole.tax_advisor)

    response = pg_client.get("/companies", headers=auth_headers(advisor))

    assert response.status_code == 200
    body = response.json()
    assert [entry["legal_name"] for entry in body] == ["Klientin OG"]
    assert body[0]["role"] == "tax_advisor"


def test_a_tax_advisors_permissions_are_the_archive_and_the_company_itself(pg_client, pg_db):
    """The list the web app gates its navigation on, asserted as served.

    Read off the API rather than off `permissions_for`, because the question
    is what a client is told — a correct table reached through a wrong
    serialisation is still a menu with the wrong entries in it.
    """
    make_superadmin(pg_db)
    company = make_company(pg_db, "Klientin OG")
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, company, advisor, CompanyRole.tax_advisor)

    body = pg_client.get("/companies", headers=auth_headers(advisor)).json()

    assert body[0]["permissions"] == [
        "archive.download",
        "archive.view",
        "company.view",
    ]


def test_the_optional_grant_needs_the_company_to_say_yes(pg_db):
    """`reports.view` for a tax advisor is off until a company opts in."""
    closed = make_company(pg_db, "Closed GmbH")
    open_company = make_company(pg_db, "Open GmbH", tax_advisor_reports=True)

    for permission in sorted(TAX_ADVISOR_OPTIONAL):
        assert not has_permission(permission, CompanyRole.tax_advisor, closed)
        assert has_permission(permission, CompanyRole.tax_advisor, open_company)

    # And the opt-in grants ONLY those two — it is not a general unlock.
    granted = set(permissions_for(CompanyRole.tax_advisor, open_company))
    assert granted == {
        "archive.download",
        "archive.view",
        "company.view",
        "exports.download",
        "reports.view",
    }


def test_an_unknown_permission_key_is_held_by_nobody():
    """A typo in a `require()` call must lock the endpoint, not open it."""
    for role in CompanyRole:
        assert not has_permission("company.setings.edit", role)
        assert not has_permission("", role)


def test_every_permission_the_prompt_named_exists():
    """The seeded keys later phases will import, written down once.

    Asserted so that a rename in `PERMISSIONS` cannot silently orphan the
    `require("archive.view")` a P5 router will be written against — an
    unknown key denies everyone, which is safe and completely silent.
    """
    required = {
        "company.settings.edit",
        "company.members.manage",
        "number_series.manage",
        "audit.view",
        "archive.view",
        "archive.download",
        "documents.finalize",
        "documents.revise",
        "ledger.view",
    }
    assert required <= set(PERMISSIONS)
