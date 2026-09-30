"""Per-role two-factor: required by a company role, with the switch OFF.

Every test in this file runs with `site_settings.require_2fa = False`. That is
the whole point — a rule that only shows up once the instance-wide switch is
on would be indistinguishable from the switch, and the switch already worked.

The seven cases are §3 of the P0b-1 prompt, and the shape worth keeping in
mind is that **issuing a credential and accepting one are different levers**:

  * `/auth/send-magic-code` is about which primary credentials this INSTANCE
    offers at all. Per-person gating there would refuse a role-required user
    the very code they need in order to sign in and enrol.
  * `_login_outcome` is about what a verified primary credential LEADS to, and
    that is where the per-person rule belongs.

Test (b) is the one that pins that difference down, and the corresponding
mutation — making `send_magic_code` consult `two_factor_required_for` — has to
fail it.
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
    password,
)


@pytest.fixture
def instance_2fa_off(pg_db):
    """The site-settings row with `require_2fa` explicitly False.

    An explicit row rather than relying on the absent-row default, so that a
    test asserting "the instance does not require this" is asserting about a
    configured instance and not about a missing table.
    """
    row = SiteSettings(org_name="FilmBill", require_2fa=False)
    pg_db.add(row)
    pg_db.commit()
    return row


@pytest.fixture
def world(pg_db, instance_2fa_off):
    make_superadmin(pg_db)
    company = make_company(pg_db, "Klientin OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    return {"company": company, "owner": owner}


def _login(client, user):
    return client.post(
        "/auth/login", json={"email": user.email, "password": password()}
    )


def _magic(client, user):
    return client.post(
        "/auth/send-magic-code", json={"email": user.email, "purpose": "login"}
    )


# ─── (a) a role grant turns forced enrolment on ─────────────────────────────


def test_a_tax_advisor_without_2fa_is_forced_into_setup_on_password_login(
    pg_client, pg_db, world
):
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)

    response = _login(pg_client, advisor)

    assert response.status_code == 200
    body = response.json()
    assert body["requires_2fa"] is True
    assert body["setup_required"] is True
    assert "access_token" not in body or body.get("access_token") is None
    assert "refresh_token" not in body or body.get("refresh_token") is None


def test_the_same_holds_for_a_magic_code_login(pg_client, pg_db, world, monkeypatch):
    """The other primary credential must conclude identically.

    `_login_outcome` is shared by both paths precisely so they cannot drift,
    and this is the assertion that says so — a second copy of the rule that
    forgot the role branch would pass every test above and fail this one.
    """
    advisor = make_user(pg_db, "advisor@example.com")
    grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)

    # Verify the code without going through Redis or a mailbox: this test is
    # about what a VERIFIED magic code leads to, not about delivery.
    monkeypatch.setattr(
        "apps.api.routers.auth.redis_verify_magic_code", lambda *a, **k: (True, None)
    )
    response = pg_client.post(
        "/auth/verify-magic-code",
        json={"email": advisor.email, "code": "123456", "purpose": "login"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["requires_2fa"] is True
    assert body["setup_required"] is True


# ─── (b) issuance is a separate lever ───────────────────────────────────────


def test_send_magic_code_still_works_for_a_role_required_user(
    pg_client, pg_db, world, monkeypatch
):
    """200, because the code is how they get in to enrol in the first place.

    If this ever turns into a 403, a tax advisor with no password has been
    locked out of the account they were just invited to — by the very rule
    that was supposed to protect it.
    """
    advisor = make_user(pg_db, "advisor@example.com", with_password=False)
    grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)

    monkeypatch.setattr("apps.api.routers.auth.store_magic_code", lambda *a, **k: None)
    response = _magic(pg_client, advisor)

    assert response.status_code == 200, response.text


# ─── (c) an unaffected user is untouched ────────────────────────────────────


def test_a_user_with_no_required_role_logs_in_normally(pg_client, pg_db, world):
    staffer = make_user(pg_db, "staffer@example.com")
    grant(pg_db, world["company"], staffer, CompanyRole.staff)

    body = _login(pg_client, staffer).json()

    assert body.get("requires_2fa") in (None, False)
    assert body["access_token"]


def test_a_user_in_no_company_at_all_logs_in_normally(pg_client, pg_db, world):
    nobody = make_user(pg_db, "nobody@example.com")

    body = _login(pg_client, nobody).json()

    assert body.get("requires_2fa") in (None, False)
    assert body["access_token"]


# ─── (d) an enrolled user is challenged regardless ──────────────────────────


def test_an_enrolled_user_is_challenged_whatever_their_role(pg_client, pg_db, world):
    """Turning a requirement off must never downgrade someone who chose 2FA."""
    enrolled = make_user(pg_db, "enrolled@example.com", two_factor_enabled=True)
    grant(pg_db, world["company"], enrolled, CompanyRole.staff)

    body = _login(pg_client, enrolled).json()

    assert body["requires_2fa"] is True
    assert body["setup_required"] is False


# ─── (e) the instance-wide switch is unchanged ──────────────────────────────


def test_instance_wide_on_still_closes_magic_codes_for_everyone(
    pg_client, pg_db, world, instance_2fa_off
):
    instance_2fa_off.require_2fa = True
    pg_db.commit()

    for user in (world["owner"], make_user(pg_db, "another@example.com")):
        assert _magic(pg_client, user).status_code == 403


# ─── (f) revoking the membership takes the requirement away ─────────────────


def test_revoking_the_membership_restores_a_normal_login(pg_client, pg_db, world):
    """The reason this is derived and not a column on `users`.

    A stored flag would have been written on the grant and would still be
    sitting there now, demanding a second factor from someone whose reason for
    needing one no longer exists.
    """
    advisor = make_user(pg_db, "advisor@example.com")
    record = grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)
    assert _login(pg_client, advisor).json()["requires_2fa"] is True

    record.revoked_at = datetime.now(timezone.utc)
    pg_db.commit()

    body = _login(pg_client, advisor).json()
    assert body.get("requires_2fa") in (None, False)
    assert body["access_token"]


def test_an_enrolled_user_stays_challenged_after_the_revocation(pg_client, pg_db, world):
    """"Unless enrolled" — the carve-out in the prompt's own wording."""
    advisor = make_user(pg_db, "advisor@example.com", two_factor_enabled=True)
    record = grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)
    record.revoked_at = datetime.now(timezone.utc)
    pg_db.commit()

    assert _login(pg_client, advisor).json()["requires_2fa"] is True


# ─── (g) /auth/me reports it ────────────────────────────────────────────────


def test_auth_me_reports_the_requirement_for_a_role_required_user(
    pg_client, pg_db, world
):
    """§206's field inherits the per-role rule with no frontend change.

    Confirmed here rather than assumed: `/auth/me` calls
    `two_factor_required_for`, which is the function P0b-1 changed, so the
    web's disabled "Turn off" button follows automatically — but "follows
    automatically" is exactly the kind of claim that is worth one request.
    """
    advisor = make_user(pg_db, "advisor@example.com", two_factor_enabled=True)
    grant(pg_db, world["company"], advisor, CompanyRole.tax_advisor)

    body = pg_client.get("/auth/me", headers=auth_headers(advisor)).json()

    assert body["two_factor_required"] is True


def test_auth_me_reports_no_requirement_for_an_ordinary_member(pg_client, pg_db, world):
    staffer = make_user(pg_db, "staffer@example.com", two_factor_enabled=True)
    grant(pg_db, world["company"], staffer, CompanyRole.staff)

    body = pg_client.get("/auth/me", headers=auth_headers(staffer)).json()

    assert body["two_factor_required"] is False


# ─── The configurable half: a company's own require_2fa_roles ───────────────


def test_a_company_can_require_2fa_of_any_role_it_names(pg_db, world):
    accountant = make_user(pg_db, "accountant@example.com")
    grant(pg_db, world["company"], accountant, CompanyRole.accountant)
    assert two_factor_required_for(pg_db, accountant) is False

    world["company"].require_2fa_roles = ["accountant"]
    pg_db.commit()

    assert two_factor_required_for(pg_db, accountant) is True


def test_the_requirement_follows_the_company_it_was_set_on(pg_db, world):
    """A role listed by company A does not bind a member of company B.

    The rule is per company, and the union is taken over the companies this
    person is actually in — not over every company on the installation.
    """
    other = make_company(pg_db, "Other GmbH", require_2fa_roles=["staff"])
    theirs = make_user(pg_db, "theirs@example.com")
    grant(pg_db, other, theirs, CompanyRole.staff)
    assert two_factor_required_for(pg_db, theirs) is True

    ours = make_user(pg_db, "ours@example.com")
    grant(pg_db, world["company"], ours, CompanyRole.staff)
    assert two_factor_required_for(pg_db, ours) is False


def test_an_expired_membership_carries_no_requirement(pg_db, world):
    advisor = make_user(pg_db, "expired@example.com")
    grant(
        pg_db,
        world["company"],
        advisor,
        CompanyRole.tax_advisor,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    assert two_factor_required_for(pg_db, advisor) is False


def test_a_stale_role_name_in_the_setting_does_not_break_login(pg_db, world):
    """A renamed enum value left behind in a company's list is ignored.

    Raising here would turn a settings leftover into a login outage for
    everyone in that company, which is a much worse failure than the setting
    being one entry short.
    """
    world["company"].require_2fa_roles = ["gaffer", "tax_advisor"]
    pg_db.commit()
    staffer = make_user(pg_db, "staffer@example.com")
    grant(pg_db, world["company"], staffer, CompanyRole.staff)

    assert two_factor_required_for(pg_db, staffer) is False
