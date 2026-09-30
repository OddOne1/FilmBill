"""Real rows for the real-database company tests.

Not fixtures, because every test here needs a different *shape* of world —
two companies and one member, or one company and six roles — and a fixture
per shape would be more code than the shapes. These are plain functions over
the `pg_db` session.

Everything committed, always. The middleware that runs before a route opens
its OWN session (see `conftest.pg_db`), so a row left uncommitted in the test's
session is a row the account gate and the setup guard cannot see, and the
request fails for a reason that has nothing to do with what is being tested.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from apps.api.models.company import Company, CompanyMembership, CompanyRole
from apps.api.models.user import User, UserGlobalRole, UserStatus
from apps.api.services.auth_service import (
    create_access_token,
    create_refresh_token,
    hash_password,
)

#: One bcrypt hash, computed once per process rather than per user. bcrypt is
#: deliberately slow and the permission matrix creates six users; hashing the
#: same password six times per test is ten seconds of nothing.
_PASSWORD = "Rehearsal-Dinner-Truck-42"
_PASSWORD_HASH: Optional[str] = None


def password() -> str:
    return _PASSWORD


def _hash() -> str:
    global _PASSWORD_HASH
    if _PASSWORD_HASH is None:
        _PASSWORD_HASH = hash_password(_PASSWORD)
    return _PASSWORD_HASH


def make_user(
    db,
    email: str,
    *,
    global_role: UserGlobalRole = UserGlobalRole.user,
    with_password: bool = True,
    two_factor_enabled: bool = False,
    status: UserStatus = UserStatus.active,
) -> User:
    """An account that can actually make a request.

    `backup_email_verified_at` is set on every one of them. The account gate
    (middleware/account_gate.py) answers 403 to every non-/auth route while a
    backup address is outstanding, so a user without it would 403 on every
    company endpoint and each of these tests would pass for entirely the wrong
    reason.
    """
    user = User(
        email=email,
        first_name="Test",
        last_name=email.split("@")[0],
        password_hash=_hash() if with_password else None,
        status=status,
        role=global_role,
        email_verified=True,
        two_factor_enabled=two_factor_enabled,
        two_factor_method="totp" if two_factor_enabled else None,
        totp_secret_encrypted="not-a-real-secret" if two_factor_enabled else None,
        backup_email=f"backup+{email}",
        backup_email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_superadmin(db, email: str = "installation-root@example.com") -> User:
    """The installation administrator.

    Most of these tests need one to exist even when it is not the caller,
    because `SetupGuardMiddleware` answers 503 to everything until there is
    a superadmin — which would make every assertion below read "503".
    """
    return make_user(db, email, global_role=UserGlobalRole.superadmin)


def make_company(db, legal_name: str, **fields) -> Company:
    company = Company(legal_name=legal_name, **fields)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def grant(
    db,
    company: Company,
    user: User,
    role: CompanyRole,
    *,
    expires_at: Optional[datetime] = None,
    revoked_at: Optional[datetime] = None,
) -> CompanyMembership:
    """A membership row, written directly.

    Directly rather than through `POST /company/members`, deliberately: these
    tests are about what a membership DOES, and building the world through
    the endpoint under test would mean a bug in the endpoint could hide a bug
    in the scoping. `test_company_members_endpoint` covers the endpoint
    itself.

    Note that this does NOT bump `token_version` — the service function does
    that, and `test_company_session_invalidation.py` checks it there.
    """
    record = CompanyMembership(
        company_id=company.id,
        user_id=user.id,
        role=role,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def auth_headers(user: User, company: Optional[Company] = None) -> dict:
    """Bearer token for `user`, plus the active company header when given.

    The token carries the user's CURRENT `token_version`. A test that changes
    entitlements and then expects the old token to be rejected must therefore
    take its headers BEFORE the change — which is exactly the sequence
    `test_company_session_invalidation.py` asserts.
    """
    headers = {
        "Authorization": f"Bearer {create_access_token(str(user.id), user.token_version or 0)}"
    }
    if company is not None:
        headers["X-Company-Id"] = str(company.id)
    return headers


def refresh_token_for(user: User) -> str:
    return create_refresh_token(str(user.id), user.token_version or 0)


def unrelated_company_id() -> str:
    """An id that names nothing. For the "does not exist" half of 404."""
    return str(uuid.uuid4())
