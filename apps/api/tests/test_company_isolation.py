"""Cross-company isolation, for every company-scoped endpoint there is.

The case list is NOT hand-written. It is derived from the app's own OpenAPI
document, filtered to the `companies` tag, and every operation found there
must appear in `CASES` below — so adding an endpoint under that tag without
writing a case FAILS this file rather than quietly escaping it. That is the
one property that makes this suite still true in P4, when there are forty
endpoints instead of seven.

Two things are checked per scoped operation:

  1. sending `X-Company-Id: B` as a user who is only in A answers 404;
  2. naming one of B's rows in the path, while correctly scoped to A,
     answers 404 as well.

Both, because they fail independently. The first is the dependency
(`middleware/company.py`); the second is the query (`services/permissions.py::
scoped`), and an endpoint can pass the first while leaking on the second — a
membership id from another company looked up without a `company_id` filter is
found, and answering 403 or 200 for it confirms it exists.

404 and not 403 throughout: CLAUDE.md rule 6. A 403 says "this company exists
and you are not in it", which is a fact about a business relationship that
neither party published.
"""

import pytest
from fastapi.openapi.utils import get_openapi

from apps.api.models.company import CompanyRole

from .company_factories import (
    auth_headers,
    grant,
    make_bank_account,
    make_company,
    make_superadmin,
    make_user,
    unrelated_company_id,
)

#: The tag whose operations must every one be covered below.
SCOPED_TAG = "companies"

#: Marks an operation that takes no company context BY DESIGN, with the
#: reason. Listing your own companies and creating a new one are the only two
#: — in both, a company context is either the answer or does not exist yet.
#:
#: Spelled as an explicit entry rather than as an exclusion list somewhere
#: else, so that adding one is a visible act in the same table as the real
#: cases. A new endpoint cannot land here by omission.
NO_COMPANY_CONTEXT = "no-company-context"

#: How to exercise each operation.
#:
#: A path may contain `{membership_id}` or `{bank_account_id}`. Both are
#: substituted with the id of a row belonging to the **other** company, which
#: is what makes the second test below a real check rather than a request for
#: something that does not exist: a router that forgot its `company_id` filter
#: FINDS that row, and answering anything but 404 for it confirms it exists.
#:
#: Every placeholder a path can carry must appear in `OTHER_COMPANY_ROWS`
#: below, or the substitution silently leaves `{…}` in the URL and the
#: endpoint 404s for the wrong reason — a green test proving nothing. There is
#: an assertion for exactly that.
CASES: dict[str, object] = {
    "GET /companies": NO_COMPANY_CONTEXT,
    "POST /companies": NO_COMPANY_CONTEXT,
    "GET /company": {"body": None},
    "PATCH /company": {"body": {"trading_name": "Stolen"}},
    "GET /company/members": {"body": None},
    "POST /company/members": {
        "body": {"email": "someone-new@example.com", "role": "staff"}
    },
    "PATCH /company/members/{membership_id}": {"body": {"role": "admin"}},
    "DELETE /company/members/{membership_id}": {"body": None},
    "GET /company/bank-accounts": {"body": None},
    "POST /company/bank-accounts": {
        "body": {"iban": "AT61 1904 3002 3457 3201", "label": "Theirs"}
    },
    "PATCH /company/bank-accounts/{bank_account_id}": {"body": {"label": "Stolen"}},
    "DELETE /company/bank-accounts/{bank_account_id}": {"body": None},
}

#: Placeholder -> the key in the `two_companies` fixture holding a row that
#: belongs to company B.
OTHER_COMPANY_ROWS = {
    "{membership_id}": "membership_in_b",
    "{bank_account_id}": "bank_account_in_b",
}


def _scoped_operations() -> list[str]:
    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    found = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if SCOPED_TAG in (operation.get("tags") or []):
                found.append(f"{method.upper()} {path}")
    return sorted(found)


def test_every_company_endpoint_has_an_isolation_case(client):
    """The guard that keeps this file honest.

    Without it, the parametrised tests below would keep passing while a new
    unscoped endpoint sat beside them untested — which is the failure mode
    that makes a security suite worse than none, because it reads as coverage.
    """
    operations = set(_scoped_operations())
    missing = sorted(operations - set(CASES))
    stale = sorted(set(CASES) - operations)

    assert not missing, (
        f"company endpoints with no isolation case: {missing}. Add one to "
        f"CASES in this file — or, if it genuinely has no company context, "
        f"mark it NO_COMPANY_CONTEXT and say why."
    )
    assert not stale, f"CASES names operations that no longer exist: {stale}"


def test_the_case_list_is_not_empty(client):
    """A filter that matched nothing would make the file above pass silently."""
    assert len(_scoped_operations()) >= 10


#: Only the operations that DO take a company context get the two checks.
_SCOPED_CASES = sorted(
    name for name, case in CASES.items() if case is not NO_COMPANY_CONTEXT
)


@pytest.fixture
def two_companies(pg_db):
    """Ada is an owner of A and nothing else. Bo owns B.

    Ada is deliberately an OWNER of her own company — the most privileged
    company role there is — so that every 404 below is about the company
    boundary and not about a permission she happens to lack.
    """
    make_superadmin(pg_db)
    ada = make_user(pg_db, "ada@example.com")
    bo = make_user(pg_db, "bo@example.com")
    company_a = make_company(pg_db, "Company A")
    company_b = make_company(pg_db, "Company B")
    grant(pg_db, company_a, ada, CompanyRole.owner)
    bo_in_b = grant(pg_db, company_b, bo, CompanyRole.owner)
    # A row of B's that is NOT a membership, so the scoped lookups in the
    # bank-accounts router are exercised against a real neighbour rather than
    # against an id that names nothing.
    bank_in_b = make_bank_account(pg_db, company_b, iban="AT61 1904 3002 3457 3201")
    return {
        "ada": ada,
        "bo": bo,
        "a": company_a,
        "b": company_b,
        "membership_in_b": bo_in_b,
        "bank_account_in_b": bank_in_b,
    }


def _send(client, operation: str, headers: dict, case: dict, world: dict):
    method, path = operation.split(" ", 1)
    for placeholder, key in OTHER_COMPANY_ROWS.items():
        path = path.replace(placeholder, str(world[key].id))
    return client.request(method, path, headers=headers, json=case.get("body"))


def _targets_another_companys_row(operation: str) -> bool:
    return any(placeholder in operation for placeholder in OTHER_COMPANY_ROWS)


def test_every_placeholder_has_a_row_in_the_other_company(client):
    """The check that keeps the substitution honest.

    A path parameter with no entry in `OTHER_COMPANY_ROWS` leaves a literal
    `{bank_account_id}` in the URL. FastAPI then 404s it — for being an
    unparseable UUID, not for being another company's row — and the isolation
    test passes while proving nothing at all. This is the one failure mode of
    this file that would be completely silent.
    """
    import re

    placeholders = {
        match
        for operation in CASES
        for match in re.findall(r"\{[a-z_]+\}", operation)
    }
    missing = sorted(placeholders - set(OTHER_COMPANY_ROWS))
    assert not missing, (
        f"path parameters with no row in the other company: {missing}. Add one "
        f"to OTHER_COMPANY_ROWS and to the two_companies fixture, or the case "
        f"for that endpoint tests nothing."
    )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_pointing_the_header_at_another_company_is_404(
    pg_client, two_companies, operation
):
    world = two_companies
    response = _send(
        pg_client,
        operation,
        auth_headers(world["ada"], world["b"]),
        CASES[operation],
        world,
    )
    assert response.status_code == 404, (
        f"{operation} answered {response.status_code} for a company the caller "
        f"is not in: {response.text}"
    )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_naming_another_companys_row_while_correctly_scoped_is_404(
    pg_client, two_companies, operation
):
    """Scoped to A, reaching for a membership that belongs to B.

    For the operations with no `{membership_id}` this is the same request as
    a normal one and must SUCCEED — which is worth keeping in the same
    parametrisation rather than filtering out, because a scoping change that
    starts 404-ing legitimate traffic is a real regression and this is where
    it shows up.
    """
    world = two_companies
    case = CASES[operation]
    response = _send(
        pg_client,
        operation,
        auth_headers(world["ada"], world["a"]),
        case,
        world,
    )

    if _targets_another_companys_row(operation):
        assert response.status_code == 404, (
            f"{operation} reached a membership belonging to another company: "
            f"{response.status_code} {response.text}"
        )
    else:
        assert response.status_code < 400, (
            f"{operation} refused a legitimate request from a company owner: "
            f"{response.status_code} {response.text}"
        )


@pytest.mark.parametrize("operation", _SCOPED_CASES)
def test_a_company_that_does_not_exist_is_404(pg_client, two_companies, operation):
    """The same answer as a company that exists and is not yours.

    Asserted rather than assumed: if "unknown company" and "not your company"
    ever answered differently, the difference would be a membership oracle —
    ask about an id, and the status tells you whether it names a real
    business.
    """
    world = two_companies
    headers = auth_headers(world["ada"])
    headers["X-Company-Id"] = unrelated_company_id()
    response = _send(pg_client, operation, headers, CASES[operation], world)
    assert response.status_code == 404


def test_a_superadmin_is_not_a_member_of_every_company(pg_client, pg_db):
    """Installation authority is not company authority.

    The single most tempting shortcut in this whole layer, and the one that
    would make every other test in this file meaningless: a superadmin who
    implicitly reaches into any company turns the boundary into a suggestion.
    """
    root = make_superadmin(pg_db)
    company = make_company(pg_db, "Someone Else GmbH")

    response = pg_client.get("/company", headers=auth_headers(root, company))

    assert response.status_code == 404, (
        "a superadmin who is not a member read a company's record: "
        f"{response.text}"
    )


def test_a_member_of_one_company_sees_only_that_one_in_the_switcher(
    pg_client, two_companies
):
    world = two_companies
    response = pg_client.get("/companies", headers=auth_headers(world["ada"]))

    assert response.status_code == 200
    names = [entry["legal_name"] for entry in response.json()]
    assert names == ["Company A"]


def test_a_request_with_no_company_header_is_refused_but_says_why(
    pg_client, two_companies
):
    """400, not 404, and the difference is deliberate.

    A missing header is a broken client, not an authorisation question, and it
    reveals nothing about which companies exist. Answering 404 would send
    whoever is debugging it looking for a row that was never the problem.
    """
    world = two_companies
    response = pg_client.get("/company", headers=auth_headers(world["ada"]))

    assert response.status_code == 400
    assert "X-Company-Id" in response.json()["detail"]
