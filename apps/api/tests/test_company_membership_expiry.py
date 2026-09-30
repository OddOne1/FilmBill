"""An expired membership is no membership at all.

The column exists for the tax advisor: engaged for the annual accounts, not
forever. That makes the expiry a security boundary rather than a convenience,
and a boundary that is only checked in the listing — where it is easy to
remember — while the *dependency* still lets requests through is a boundary
that does not exist.

So this file checks the same expiry at four separate places: the dependency,
the switcher, the 2FA policy, and the row's own `is_active`. They share one
implementation (`CompanyMembership.is_active`), which is the point; the tests
are what stops a future edit from inlining three of them differently.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apps.api.models.company import CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.services.site_settings_service import two_factor_required_for

from .company_factories import (
    auth_headers,
    grant,
    make_company,
    make_superadmin,
    make_user,
)

YESTERDAY = lambda: datetime.now(timezone.utc) - timedelta(days=1)  # noqa: E731
NEXT_MONTH = lambda: datetime.now(timezone.utc) + timedelta(days=30)  # noqa: E731


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="FilmBill", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "Klientin OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


def test_an_expired_membership_gets_the_same_404_as_none_at_all(
    pg_client, pg_db, world
):
    advisor = make_user(pg_db, "advisor@example.com")
    grant(
        pg_db,
        world["company"],
        advisor,
        CompanyRole.tax_advisor,
        expires_at=YESTERDAY(),
    )

    response = pg_client.get("/company", headers=auth_headers(advisor, world["company"]))

    assert response.status_code == 404
    assert response.json()["detail"] == "Not found"


def test_a_membership_that_has_not_expired_yet_works(pg_client, pg_db, world):
    """The other half. A test that only checked the refusal would pass on an
    implementation that refused everybody."""
    advisor = make_user(pg_db, "advisor@example.com")
    grant(
        pg_db,
        world["company"],
        advisor,
        CompanyRole.tax_advisor,
        expires_at=NEXT_MONTH(),
    )

    response = pg_client.get("/company", headers=auth_headers(advisor, world["company"]))

    assert response.status_code == 200


def test_an_expired_membership_is_not_in_the_switcher(pg_client, pg_db, world):
    advisor = make_user(pg_db, "advisor@example.com")
    grant(
        pg_db, world["company"], advisor, CompanyRole.tax_advisor, expires_at=YESTERDAY()
    )

    body = pg_client.get("/companies", headers=auth_headers(advisor)).json()

    assert body == []


def test_an_expired_membership_carries_no_2fa_requirement(pg_db, world):
    """Otherwise a lapsed advisor is stuck with a requirement and no access —
    told to enrol for a company they can no longer open."""
    advisor = make_user(pg_db, "advisor@example.com")
    grant(
        pg_db, world["company"], advisor, CompanyRole.tax_advisor, expires_at=YESTERDAY()
    )

    assert two_factor_required_for(pg_db, advisor) is False


def test_setting_an_expiry_in_the_past_takes_effect_immediately(
    pg_client, pg_db, world
):
    """Backdating an expiry is a valid way to end access now.

    Worth its own test because the PATCH path writes the column while the
    reads compare it, and an implementation that only evaluated the expiry at
    grant time would pass everything above and fail here.
    """
    advisor = make_user(pg_db, "advisor@example.com")
    record = grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)
    headers = auth_headers(advisor, world["company"])
    assert pg_client.get("/company", headers=headers).status_code == 200

    response = pg_client.patch(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
        json={"expires_at": YESTERDAY().isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # The role change bumped the token too, so this asks for a fresh one and
    # then finds the membership gone — 401 from the stale token, 404 from a
    # new one. Both are "no longer in", and the second is the one this test
    # is about.
    pg_db.refresh(advisor)
    assert pg_client.get(
        "/company", headers=auth_headers(advisor, world["company"])
    ).status_code == 404


def test_omitting_expires_at_does_not_clear_it(pg_client, pg_db, world):
    """`model_fields_set`, asserted through the API.

    The failure this prevents is silent and permanent: an owner edits an
    advisor's ROLE, the expiry quietly becomes NULL, and a grant that was
    meant to lapse in thirty days now lasts forever with nothing on screen to
    say so.
    """
    advisor = make_user(pg_db, "advisor@example.com")
    expiry = NEXT_MONTH()
    record = grant(
        pg_db, world["company"], advisor, CompanyRole.tax_advisor, expires_at=expiry
    )

    response = pg_client.patch(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
        json={"role": "accountant"},
    )

    assert response.status_code == 200
    assert response.json()["expires_at"] is not None


def test_sending_expires_at_as_null_does_clear_it(pg_client, pg_db, world):
    advisor = make_user(pg_db, "advisor@example.com")
    record = grant(
        pg_db,
        world["company"],
        advisor,
        CompanyRole.tax_advisor,
        expires_at=NEXT_MONTH(),
    )

    response = pg_client.patch(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
        json={"expires_at": None},
    )

    assert response.status_code == 200
    assert response.json()["expires_at"] is None


def test_a_naive_expiry_does_not_500_the_dependency(pg_db, world):
    """A hand-edited or fixture-written row with no timezone.

    The column is `DateTime(timezone=True)`, so Postgres hands back an aware
    value — but comparing a naive one with an aware `now()` raises TypeError,
    and that would turn an authorisation question into a 500. Read as UTC,
    same as `site_settings_service.passwordless_window_closed` settled for the
    same trap.
    """
    from apps.api.models.company import CompanyMembership

    record = CompanyMembership(
        company_id=world["company"].id,
        user_id=world["owner"].id,
        role=CompanyRole.staff,
        expires_at=datetime.utcnow() - timedelta(days=1),
    )

    assert record.is_active() is False
