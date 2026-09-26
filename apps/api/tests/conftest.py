"""
Test configuration for the FilmBill API.

Uses a mock-based database approach because the models use PostgreSQL-specific
UUID types that are incompatible with SQLite. All DB interactions are mocked
so tests can run without a live database or S3.
"""
import os
import sys
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Set required environment variables BEFORE importing the app modules.
# This must happen before any import of apps.api.config or apps.api.main.
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/filmbill_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET", "filmbill-test")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY", "testkey")
os.environ.setdefault("S3_SECRET_KEY", "testsecret")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-for-tests-only")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3100")
os.environ.setdefault("GOTENBERG_URL", "http://localhost:3000")

# ── The suite gets its own Redis database ────────────────────────────────────
#
# Whatever REDIS_URL points at, the tests use database index 15 of that server
# and never the one the application is using. Run from a shell where Redis is
# unreachable this changes nothing; run inside `filmbill_api`, where it IS
# reachable, it is what stops the suite from reading and writing a live dev
# instance's keys — and, with the fixture below, what stops one test's rate
# limit from being another test's 429.
_TEST_REDIS_DB = 15


def _redis_url_for_tests(url: str) -> str:
    """Point `url` at the test database, keeping host, auth and options."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{_TEST_REDIS_DB}"))


os.environ["REDIS_URL"] = _redis_url_for_tests(os.environ["REDIS_URL"])


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Every test starts with an empty rate-limit window.

    This suite used to be green only because it ran where Redis was
    unreachable and both limiters fail open. Run where Redis answers, 30 of
    327 tests returned 429 instead of what they asserted —
    `test_setup_superadmin.py` alone fires 7 POSTs at
    `/setup/create-superadmin` against a cap of 3 per 600s, so the last four
    were testing the limiter, not the endpoint, and said so in a way nobody
    saw. That is CLAUDE.md 17b exactly: a harness kinder than production.

    The counters are deleted rather than the limiter disabled, so what runs
    in a test is the real `check_rate_limit` and the real
    `GlobalRateLimitMiddleware` — a test that DOES want to prove the cap
    (test_rate_limit.py) still can, and a 429 that appears from now on is a
    real one.

    Both key shapes are cleared: `rl:` from
    `redis_service.check_rate_limit`, and the `grl:` the global middleware
    spells out itself. Unreachable Redis is a no-op, which is what keeps this
    working in CI, where there is no Redis on the other end.
    """
    def _clear():
        try:
            from apps.api.services.redis_service import get_redis

            r = get_redis()
            keys = [k for pattern in ("rl:*", "grl:*") for k in r.scan_iter(pattern)]
            if keys:
                r.delete(*keys)
        except Exception:
            # No Redis here — then there are no counters to clear, and the
            # limiter is failing open anyway.
            pass

    _clear()
    yield
    _clear()


_FAKE_HASH = "$2b$12$fakehashfortestsonlythisisnotrealatall000000000000000"


def _make_user(
    user_id: uuid.UUID | None = None,
    email: str = "test@example.com",
    name: str = "Test User",
    password: str = "testpassword123",
) -> MagicMock:
    """Create a mock User object.

    We use a fake password hash so this function works even when the local
    bcrypt installation is incompatible with passlib.
    """
    from apps.api.models.user import UserStatus, UserGlobalRole

    u = MagicMock()
    u.id = user_id or uuid.uuid4()
    u.email = email
    u.name = name
    # Split explicitly. `name` is a computed property on the real model, and
    # UserResponse serialises first_name/last_name as well — a MagicMock left
    # on either one fails response validation with a 500 rather than
    # misbehaving visibly.
    first, _, last = name.partition(" ")
    u.first_name = first or None
    u.last_name = last or first
    # Store a fake hash — tests that need verify() must mock it themselves.
    u.password_hash = _FAKE_HASH
    u.status = UserStatus.active
    u.avatar_url = None
    u.created_at = datetime.now(timezone.utc)
    u.deleted_at = None
    # `superuser`, not `user`: unrestricted except on admin-only endpoints,
    # which is what a general-purpose fixture should be. `user` is the
    # restrictive bottom tier and belongs only in tests about that tier.
    u.role = UserGlobalRole.superuser
    u.email_verified = False
    u.preferences = {}
    # Every MagicMock attribute is truthy, so leaving these unset routes any
    # login through this fixture into the 2FA branch.
    u.two_factor_enabled = False
    u.totp_secret_encrypted = None
    u.backup_codes_hashed = None
    # Explicit for the same reason as the fields above, plus one of its own:
    # this is validated against Literal["totp", "email"] on the way out, so
    # leaving it unset fails serialisation rather than just misbehaving.
    u.two_factor_method = None
    # And `require_2fa`, which is not a User field at all: this suite's
    # mock_db returns ONE object from every `.first()`, so the site-settings
    # lookup inside /auth/login gets this same stub. Without it that lookup
    # reads a truthy MagicMock and every plain login is forced into 2FA
    # enrolment.
    u.require_2fa = False
    u.invite_token = None
    return u


def _make_mock_db() -> MagicMock:
    """Return a fresh mock Session."""
    db = MagicMock()
    db.query.return_value = db
    db.filter.return_value = db
    db.first.return_value = None
    db.all.return_value = []
    db.add.return_value = None
    db.flush.return_value = None
    db.commit.return_value = None
    db.refresh.return_value = None
    db.close.return_value = None
    return db


@pytest.fixture
def mock_db():
    """Provide a fresh mock DB session for each test."""
    return _make_mock_db()


@pytest.fixture
def client(mock_db):
    """Return a TestClient with mocked DB and S3."""
    with patch("apps.api.services.s3_service.ensure_bucket_exists"):
        with patch("apps.api.services.s3_service.get_s3_client", return_value=MagicMock()):
            from fastapi.testclient import TestClient
            from apps.api.main import app
            from apps.api.database import get_db

            app.dependency_overrides[get_db] = lambda: mock_db
            client = TestClient(app, raise_server_exceptions=False)
            yield client
            app.dependency_overrides.clear()


@pytest.fixture
def staged_2fa_setup():
    """In-memory stand-in for the Redis enrolment staging.

    A dict rather than a fakeredis: the three functions are the whole
    contract between /auth/2fa/setup and /auth/2fa/confirm-setup, and
    patching them keeps a test's assertions about what was staged readable
    as a dict rather than as a JSON blob under a key prefix.

    Yields the store itself, so a test can assert what setup staged and can
    plant a staged enrolment for a confirm it does not want to run setup
    for. Any test touching either endpoint needs this — without it the real
    functions reach for a Redis that is not there.
    """
    store = {}

    def _store(user_id, method, secret_encrypted):
        store[str(user_id)] = {"method": method, "secret": secret_encrypted}

    def _read(user_id):
        return store.get(str(user_id))

    def _clear(user_id):
        store.pop(str(user_id), None)

    with patch("apps.api.routers.auth.store_pending_2fa_setup", side_effect=_store), \
         patch("apps.api.routers.auth.read_pending_2fa_setup", side_effect=_read), \
         patch("apps.api.routers.auth.clear_pending_2fa_setup", side_effect=_clear):
        yield store


@pytest.fixture
def test_user(mock_db):
    """A mock user for use in auth-dependent tests."""
    return _make_user()


@pytest.fixture
def auth_headers(client, mock_db, test_user):
    """
    Simulate auth by:
    1. Patching get_user_by_email to return None on first call (no existing user)
       then the new user on subsequent calls.
    2. Letting the real hash/verify/JWT logic run.
    3. Returning Bearer headers.
    """
    from apps.api.services.auth_service import create_access_token, create_refresh_token
    # Directly generate a valid token for the test user
    token = create_access_token(str(test_user.id))

    # Make get_current_user resolve to test_user
    from apps.api.middleware.auth import get_current_user
    from apps.api.main import app

    app.dependency_overrides[get_current_user] = lambda: test_user
    yield {"Authorization": f"Bearer {token}"}
    # Cleanup: remove get_current_user override but keep get_db override
    app.dependency_overrides.pop(get_current_user, None)
