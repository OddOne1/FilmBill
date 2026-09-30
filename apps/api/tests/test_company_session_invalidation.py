"""A membership change ends the sessions it changed the meaning of.

`token_version` arrived with FreeFrame §199 for password and 2FA changes. P0b-1
adds the third reason: what a session is entitled to also changes when a
company grants, revokes or re-roles the person holding it.

The case that makes this non-optional is the 2FA one. Grant `tax_advisor` to
someone who is signed in and has no second factor, and without a bump their
existing access token keeps working for its full life and their refresh token
keeps minting new ones for a week — so the forced enrolment that the grant was
supposed to trigger is something they simply never encounter. The requirement
would be real on the login screen and absent in the session that matters.

Deactivation is deliberately NOT here: `get_current_user` re-reads `status` on
every request, so it was already handled, and a second mechanism for it would
be two things to keep in step.
"""

from datetime import datetime, timezone

import pytest

from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.services import company_service

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
    password,
    refresh_token_for,
)


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "Klientin OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def test_granting_a_role_rejects_the_access_token_that_predated_it(
    pg_client, pg_db, world
):
    user = make_user(pg_db, "user@example.com")
    grant(pg_db, world["company"], user, CompanyRole.staff)
    headers = auth_headers(user, world["company"])
    assert pg_client.get("/company", headers=headers).status_code == 200

    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={"email": "fresh@example.com", "role": "tax_advisor"},
    )
    assert response.status_code == 201

    # Now the same thing to the ALREADY-SIGNED-IN user.
    second = make_company(pg_db, "Second GmbH")
    grant(pg_db, second, world["owner"], CompanyRole.owner)
    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], second),
        json={"email": user.email, "role": "tax_advisor"},
    )
    assert response.status_code == 201, response.text

    assert pg_client.get("/company", headers=headers).status_code == 401


def test_granting_a_role_rejects_the_refresh_token_too(pg_client, pg_db, world):
    """The half that matters most.

    An access token expires in fifteen minutes on its own. A refresh token
    lives for a week and mints new access tokens the whole time, so leaving it
    valid would mean the grant takes effect a week late — or never, for
    someone who keeps a tab open.
    """
    user = make_user(pg_db, "user@example.com")
    grant(pg_db, world["company"], user, CompanyRole.staff)
    stale_refresh = refresh_token_for(user)

    second = make_company(pg_db, "Second GmbH")
    grant(pg_db, second, world["owner"], CompanyRole.owner)
    pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], second),
        json={"email": user.email, "role": "tax_advisor"},
    )

    response = pg_client.post("/auth/refresh", json={"refresh_token": stale_refresh})
    assert response.status_code == 401


def test_the_next_login_after_that_grant_hits_forced_enrolment(pg_client, pg_db, world):
    """The point of the bump, end to end.

    Kicking the session out only matters because of what the user meets when
    they come back — if that were still an ordinary login, the bump would be
    an inconvenience rather than a control.
    """
    user = make_user(pg_db, "user@example.com")
    second = make_company(pg_db, "Second GmbH")
    grant(pg_db, second, world["owner"], CompanyRole.owner)
    pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], second),
        json={"email": user.email, "role": "tax_advisor"},
    )

    body = pg_client.post(
        "/auth/login", json={"email": user.email, "password": password()}
    ).json()

    assert body["requires_2fa"] is True
    assert body["setup_required"] is True


def test_revoking_a_membership_rejects_the_open_session(pg_client, pg_db, world):
    """The acceptance walkthrough's step 4, as a test.

    An advisor signed in on another machine must land back at login on their
    next request, not keep browsing until their token happens to expire.
    """
    advisor = make_user(pg_db, "advisor@example.com")
    record = grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)
    headers = auth_headers(advisor, world["company"])
    assert pg_client.get("/company", headers=headers).status_code == 200

    response = pg_client.delete(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
    )
    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None

    assert pg_client.get("/company", headers=headers).status_code == 401


def test_changing_someones_role_rejects_their_open_session(pg_client, pg_db, world):
    user = make_user(pg_db, "user@example.com")
    record = grant(pg_db, world["company"], user, CompanyRole.staff)
    headers = auth_headers(user, world["company"])
    assert pg_client.get("/company", headers=headers).status_code == 200

    response = pg_client.patch(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
        json={"role": "tax_advisor"},
    )
    assert response.status_code == 200

    assert pg_client.get("/company", headers=headers).status_code == 401


def test_the_service_functions_bump_and_do_not_commit(pg_db, world):
    """Both halves of the contract these helpers exist to keep.

    The bump, because a caller cannot grant without it — that is the reason
    the function is named `..._and_bump` and the reason there is no plain
    `grant_membership` beside it. And the *absence* of a commit, because the
    membership and the user row have to land together: a commit inside here
    would put the grant on disk with the bump still pending.
    """
    user = make_user(pg_db, "user@example.com")
    before = user.token_version

    record = company_service.grant_membership_and_bump(
        pg_db,
        company=world["company"],
        user=user,
        role=CompanyRole.staff,
        granted_by=world["owner"],
    )
    assert user.token_version == before + 1
    assert pg_db.in_transaction()

    company_service.update_membership_and_bump(
        pg_db, record=record, user=user, role=CompanyRole.producer
    )
    assert user.token_version == before + 2

    company_service.revoke_membership_and_bump(pg_db, record=record, user=user)
    assert user.token_version == before + 3
    assert record.revoked_at is not None
