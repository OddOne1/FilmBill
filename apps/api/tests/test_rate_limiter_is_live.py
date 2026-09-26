"""The rate limiter is actually running, and the reset fixture does not blunt it.

`test_rate_limit.py` mocks `get_redis`, so it proves the ARITHMETIC of
`check_rate_limit` and nothing about whether the limiter is wired into a
request. That gap is how the suite came to be green for the wrong reason: run
where Redis is unreachable, both limiters fail open, and 30 tests that were
quietly receiving 429 went back to passing without anyone changing the code
they were meant to cover.

`conftest.reset_rate_limiter` clears the counters between tests so each one
starts with a fresh window. The obvious risk with a fixture like that is that
it stops being a reset and becomes an off switch — nobody would notice,
because everything would simply pass. This file is the guard: it asserts the
cap still bites inside a single test, and that the next test is nevertheless
clean.

Skipped where Redis is unreachable. That is CI today, and skipping is honest
there: with no Redis there is no limiter to observe, and a test that passed
anyway would be repeating the original lie.
"""
import pytest

#: `/setup/create-superadmin` is declared `rate_limit("create_superadmin", 3, 600)`.
CREATE_SUPERADMIN_CAP = 3


def _redis_or_skip():
    try:
        from apps.api.services.redis_service import get_redis

        get_redis().ping()
    except Exception as exc:  # pragma: no cover - depends on the environment
        pytest.skip(f"no Redis on the other end, so no limiter to observe: {exc}")


@pytest.fixture
def live_redis():
    _redis_or_skip()


def _post(client):
    """A request that the limiter sees and that never reaches the database.

    The body is deliberately invalid: the rate-limit dependency resolves
    before validation answers, so a limited request is a 429 and an unlimited
    one is a 422. Neither outcome touches the mocked session, which keeps this
    about the limiter.
    """
    return client.post("/setup/create-superadmin", json={})


class TestTheCapStillBites:
    def test_the_request_after_the_cap_is_refused(self, client, live_redis):
        codes = [_post(client).status_code for _ in range(CREATE_SUPERADMIN_CAP + 1)]

        assert codes[:CREATE_SUPERADMIN_CAP] == [422] * CREATE_SUPERADMIN_CAP, codes
        assert codes[-1] == 429, (
            f"the {CREATE_SUPERADMIN_CAP + 1}th request was {codes[-1]}, not 429 — "
            "the limiter is failing open, and every rate-limit assertion in this "
            "suite is worthless"
        )

    def test_the_refusal_says_how_long_to_wait(self, client, live_redis):
        for _ in range(CREATE_SUPERADMIN_CAP):
            _post(client)
        refused = _post(client)

        assert refused.status_code == 429
        assert refused.headers.get("Retry-After"), "no Retry-After on a 429"
        assert "Too many requests" in refused.json()["detail"]


class TestTheResetIsAResetAndNotAnOffSwitch:
    """These two run in file order; the first exhausts the window, the second
    asserts it came back. If the fixture stopped clearing, the second fails —
    which is the whole point of splitting them."""

    def test_first_exhausts_the_window(self, client, live_redis):
        for _ in range(CREATE_SUPERADMIN_CAP + 1):
            _post(client)

        assert _post(client).status_code == 429

    def test_then_the_next_test_starts_clean(self, client, live_redis):
        assert _post(client).status_code == 422, (
            "this test inherited the previous one's exhausted window — "
            "conftest.reset_rate_limiter is not clearing the counters"
        )
