"""`SetupGuardMiddleware`, tested on purpose for the first time.

Nothing exercised this before P0b-2. It was met only by accident — when the
database the suite happened to be pointed at was empty, the guard's query
raised, it failed open, and every test passed *because the guard did not
run*. Point the same suite at a dev database with tables and no superadmin and
258 tests failed with 503, from the same commit, in the same hour.

That is why `conftest.client` now satisfies the guard explicitly, and why this
file exists: the fixture is deliberately kinder than production in exactly one
respect, so the thing it stands in for is made real here (CLAUDE.md 17b).

The middleware is used as it ships. What varies is the database underneath it
— which is the whole of its logic.
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.api.middleware import setup_guard


@pytest.fixture
def guard_client(mock_db):
    """A TestClient with the guard's cache CLEARED, so it really runs.

    The opposite of `conftest.client`, deliberately. `_setup_complete` is a
    process global that short-circuits the guard once it has ever seen a
    superadmin, so a test of the guard has to reset it — and restore it, or
    every test after this file would inherit a cleared cache.
    """
    previous = setup_guard._setup_complete
    setup_guard._setup_complete = False

    import apps.api.database as database

    previous_session_local = database.SessionLocal
    database.SessionLocal = lambda: mock_db

    with patch("apps.api.services.s3_service.ensure_bucket_exists"), patch(
        "apps.api.services.s3_service.get_s3_client", return_value=MagicMock()
    ):
        from fastapi.testclient import TestClient

        from apps.api.database import get_db
        from apps.api.main import app

        app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.clear()

    database.SessionLocal = previous_session_local
    setup_guard._setup_complete = previous


def _no_superadmin(mock_db):
    mock_db.first.return_value = None


def _a_superadmin_exists(mock_db):
    mock_db.first.return_value = MagicMock()


class TestAnInstanceThatIsNotSetUp:
    def test_a_protected_route_is_refused_with_503(self, guard_client, mock_db):
        _no_superadmin(mock_db)

        response = guard_client.get("/auth/me")

        assert response.status_code == 503
        assert response.json()["needs_setup"] is True

    def test_the_message_says_what_to_do(self, guard_client, mock_db):
        """503 alone reads as an outage. It is not one — it is an instance
        nobody has finished installing, and the body has to say so or the
        operator goes looking at logs."""
        _no_superadmin(mock_db)

        detail = guard_client.get("/auth/me").json()["detail"]

        assert "not set up" in detail.lower()

    @pytest.mark.parametrize(
        "path",
        ["/setup/status", "/health", "/site-settings", "/openapi.json"],
    )
    def test_the_paths_setup_itself_needs_stay_open(
        self, guard_client, mock_db, path
    ):
        """Otherwise the guard locks out the flow that would satisfy it.

        `/site-settings` is in the list because the setup and login screens
        render their branding from it before anyone has an account, and
        `/health` because it is what tells the operator the container is
        alive while the instance is deliberately refusing everything else.
        """
        _no_superadmin(mock_db)

        assert guard_client.get(path).status_code != 503


class TestAnInstanceThatIsSetUp:
    def test_the_guard_lets_everything_through(self, guard_client, mock_db):
        _a_superadmin_exists(mock_db)

        assert guard_client.get("/auth/me").status_code != 503

    def test_the_answer_is_cached_after_the_first_success(
        self, guard_client, mock_db
    ):
        """The cache is the reason this middleware costs nothing per request
        — and the reason a test of it has to clear the flag deliberately.

        Asserted because the cache is also a trap: it means "the database says
        there is no superadmin" stops being consulted forever, so an instance
        whose database is later emptied keeps serving. That is the intended
        trade and it should be a visible one.
        """
        _a_superadmin_exists(mock_db)
        assert guard_client.get("/auth/me").status_code != 503

        _no_superadmin(mock_db)

        assert guard_client.get("/auth/me").status_code != 503
        assert setup_guard._setup_complete is True


class TestAnUnreachableDatabase:
    def test_the_guard_fails_open(self, guard_client, mock_db):
        """Deliberate, and worth pinning precisely because it is what made
        this middleware invisible for three phases.

        A database that cannot be reached is an outage, and answering "please
        complete initial setup" would blame the operator's install for it. The
        route behind the guard fails on its own database access and says
        something truthful instead.
        """
        mock_db.query.side_effect = RuntimeError("connection refused")

        response = guard_client.get("/health")

        assert response.status_code != 503

    def test_failing_open_does_not_mark_setup_complete(self, guard_client, mock_db):
        """A failed check must not be cached as a pass.

        Otherwise one blip while the database is restarting would switch the
        guard off for the life of the process — and a genuinely un-set-up
        instance would then serve its way through the rest of its uptime.
        """
        mock_db.query.side_effect = RuntimeError("connection refused")

        guard_client.get("/auth/me")

        assert setup_guard._setup_complete is False
