"""The Members screen's endpoints, and the invite path they share.

`POST /company/members` is the one endpoint in P0b-1 that writes to two tables
and queues a job, so it gets its own file. What is checked here beyond the
happy path:

  * a new address goes through `invite_service`, i.e. the SAME invite the
    admin screen sends — not a second token format, expiry rule and email to
    keep in step;
  * the user row and the membership row land together, so an invited account
    cannot exist without the reason it was invited;
  * the mail is queued AFTER the commit, because the email worker is another
    process and would otherwise read a token the database does not have yet;
  * a company can never be left with no owner, because that state has no way
    out through the API.
"""

from datetime import datetime, timedelta, timezone

import pytest

from apps.api.models.company import CompanyMembership, CompanyRole
from apps.api.models.site_settings import SiteSettings
from apps.api.models.user import User, UserStatus

from .company_factories import auth_headers, grant, make_company, make_superadmin, make_user


@pytest.fixture
def world(pg_db):
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    make_superadmin(pg_db)
    company = make_company(pg_db, "YON Studio OG")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    pg_db.commit()
    return {"company": company, "owner": owner}


@pytest.fixture
def queued(monkeypatch):
    """Capture what would have gone to the email worker.

    Patched at `invite_service.send_task_safe` — the module that decides to
    send — rather than at Celery, so the assertion is about FilmBill's own
    call and not about a broker being reachable.
    """
    calls = []
    monkeypatch.setattr(
        "apps.api.services.invite_service.send_task_safe",
        lambda *args, **kwargs: calls.append(args),
    )
    return calls


def test_adding_an_unknown_address_invites_them_and_grants_the_role(
    pg_client, pg_db, world, queued
):
    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={
            "email": "advisor@example.com",
            "role": "tax_advisor",
            "name": "Anna Berger",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "advisor@example.com"
    assert body["role"] == "tax_advisor"
    assert body["status"] == UserStatus.pending_invite.value
    assert body["expires_at"] is not None
    assert body["is_active"] is True

    invited = pg_db.query(User).filter(User.email == "advisor@example.com").one()
    assert invited.invite_token, "no invite token — the invite path did not run"
    assert invited.status == UserStatus.pending_invite
    assert len(queued) == 1, "the invite email was not queued"
    # send_invite_email(to, inviter_name, org_name, invite_url, expiry_days)
    assert queued[0][1] == "advisor@example.com"
    assert invited.invite_token in queued[0][4]


def test_the_invite_link_is_queued_only_after_the_rows_are_committed(
    pg_client, pg_db, world, monkeypatch
):
    """The ordering bug this would otherwise have.

    The worker is a different process on a different session. Queue the task
    inside the transaction and it can be picked up before the commit lands,
    and the invitee follows a link to a token that is not in the database yet.

    Checked by reading the database FROM THE TASK — a second session, exactly
    like the worker's — at the moment the task is queued.
    """
    seen = {}

    def _capture(*args, **kwargs):
        from apps.api.database import SessionLocal

        other_session = SessionLocal()
        try:
            seen["visible"] = (
                other_session.query(User)
                .filter(User.email == "advisor@example.com")
                .first()
                is not None
            )
        finally:
            other_session.close()

    monkeypatch.setattr("apps.api.services.invite_service.send_task_safe", _capture)

    pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={"email": "advisor@example.com", "role": "tax_advisor"},
    )

    assert seen.get("visible") is True, (
        "the invite mail was queued before the commit — the worker could "
        "follow a link to a token that is not in the database"
    )


def test_adding_an_existing_account_grants_without_inviting(
    pg_client, pg_db, world, queued
):
    existing = make_user(pg_db, "colleague@example.com")

    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={"email": existing.email, "role": "producer"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == UserStatus.active.value
    assert queued == [], "an existing account was sent an invite link"


def test_adding_the_same_person_twice_is_refused(pg_client, pg_db, world, queued):
    existing = make_user(pg_db, "colleague@example.com")
    grant(pg_db, world["company"], existing, CompanyRole.staff)

    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={"email": existing.email, "role": "producer"},
    )

    assert response.status_code == 400
    assert "already a member" in response.json()["detail"]


def test_someone_revoked_can_be_added_back(pg_client, pg_db, world, queued):
    """Revocation is not a ban. A freelancer returns for the next production."""
    person = make_user(pg_db, "freelancer@example.com")
    grant(
        pg_db,
        world["company"],
        person,
        CompanyRole.producer,
        revoked_at=datetime.now(timezone.utc),
    )

    response = pg_client.post(
        "/company/members",
        headers=auth_headers(world["owner"], world["company"]),
        json={"email": person.email, "role": "producer"},
    )

    assert response.status_code == 201
    rows = (
        pg_db.query(CompanyMembership)
        .filter(CompanyMembership.user_id == person.id)
        .all()
    )
    assert len(rows) == 2, "the old row was overwritten instead of kept"


def test_an_admin_cannot_mint_an_owner(pg_client, pg_db, world):
    """Otherwise `company.members.manage` is a one-request path to everything,
    which is not what the two roles are meant to differ by."""
    admin = make_user(pg_db, "admin@example.com")
    grant(pg_db, world["company"], admin, CompanyRole.admin)

    response = pg_client.post(
        "/company/members",
        headers=auth_headers(admin, world["company"]),
        json={"email": "new-owner@example.com", "role": "owner"},
    )

    assert response.status_code == 403


def test_the_last_owner_cannot_be_revoked(pg_client, pg_db, world):
    """The resulting state has no way out through the API.

    `company.members.manage` belongs to owners and admins, and an admin
    cannot promote anyone to owner — so a company with no owner would need
    someone with SQL access to repair.
    """
    record = (
        pg_db.query(CompanyMembership)
        .filter(CompanyMembership.user_id == world["owner"].id)
        .one()
    )

    response = pg_client.delete(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
    )

    assert response.status_code == 400
    assert "at least one owner" in response.json()["detail"]


def test_an_owner_can_step_down_when_there_is_another(pg_client, pg_db, world):
    second = make_user(pg_db, "second@example.com")
    grant(pg_db, world["company"], second, CompanyRole.owner)
    record = (
        pg_db.query(CompanyMembership)
        .filter(CompanyMembership.user_id == world["owner"].id)
        .one()
    )

    response = pg_client.delete(
        f"/company/members/{record.id}",
        headers=auth_headers(world["owner"], world["company"]),
    )

    assert response.status_code == 200


def test_revoking_twice_is_idempotent(pg_client, pg_db, world):
    """Two clicks on a slow connection are not a conflict to report."""
    person = make_user(pg_db, "freelancer@example.com")
    record = grant(pg_db, world["company"], person, CompanyRole.producer)
    headers = auth_headers(world["owner"], world["company"])

    first = pg_client.delete(f"/company/members/{record.id}", headers=headers)
    second = pg_client.delete(f"/company/members/{record.id}", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["revoked_at"] == second.json()["revoked_at"]


def test_the_listing_hides_revoked_rows_unless_asked(pg_client, pg_db, world):
    person = make_user(pg_db, "freelancer@example.com")
    grant(
        pg_db,
        world["company"],
        person,
        CompanyRole.producer,
        revoked_at=datetime.now(timezone.utc),
    )
    headers = auth_headers(world["owner"], world["company"])

    default = pg_client.get("/company/members", headers=headers).json()
    everything = pg_client.get(
        "/company/members?include_revoked=true", headers=headers
    ).json()

    assert [entry["email"] for entry in default] == ["owner@example.com"]
    assert len(everything) == 2


def test_creating_a_company_makes_the_creator_its_owner(pg_client, pg_db):
    root = make_superadmin(pg_db)

    response = pg_client.post(
        "/companies",
        headers=auth_headers(root),
        json={
            "legal_name": "YON Studio OG",
            "address_country": "at",
            "default_currency": "eur",
            "default_language": "de",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # Upper-cased on the way in, so a region pack is looked up by one spelling.
    assert body["address_country"] == "AT"
    assert body["default_currency"] == "EUR"

    listed = pg_client.get("/companies", headers=auth_headers(root)).json()
    assert listed[0]["role"] == "owner"


def test_a_non_superadmin_cannot_create_a_company(pg_client, pg_db):
    make_superadmin(pg_db)
    ordinary = make_user(pg_db, "ordinary@example.com")

    response = pg_client.post(
        "/companies", headers=auth_headers(ordinary), json={"legal_name": "Mine GmbH"}
    )

    assert response.status_code == 403


def test_setup_creates_the_first_company_with_the_superadmin_as_owner(pg_client, pg_db):
    """§1: an install is usable from its first login, not from a step nobody
    documented."""
    response = pg_client.post(
        "/setup/create-superadmin",
        json={
            "email": "first@example.com",
            "first_name": "Mathias",
            "last_name": "Sonnleitner",
            "password": "Rehearsal-Dinner-Truck-42",
        },
    )

    assert response.status_code == 201, response.text
    root = pg_db.query(User).filter(User.email == "first@example.com").one()
    membership = (
        pg_db.query(CompanyMembership)
        .filter(CompanyMembership.user_id == root.id)
        .one()
    )
    assert membership.role == CompanyRole.owner
