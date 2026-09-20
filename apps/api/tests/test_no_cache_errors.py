"""Error responses are never cacheable.

Inherited from FreeFrame, where an `/api/api/...` typo produced a 404 the
browser then replayed from DISK CACHE for four hours:

    404, Cache-Control: max-age=14400, Content-Length: 22

22 bytes is exactly `{"detail":"Not Found"}` — FastAPI's unmatched-route
404, which sets no Cache-Control at all. 14400 seconds is four hours, which
is Cloudflare's default Browser Cache TTL, applied to responses that reach
the edge without a caching policy of their own. An origin policy overrides
that default, so setting one settles it for the edge and the browser
together.

Why it matters beyond tidiness: once a client has hit a broken URL, fixing
the bug server-side changes nothing for that client until the cache expires,
because the browser stops asking. A cached error is a cached lie about the
current state of the server.

Every assertion here drives the real app through a TestClient and reads the
response headers. FreeFrame's version of this file checked the middleware's
SOURCE TEXT instead, which CLAUDE.md rule 11 forbids: the module docstring
above names the very headers being asserted on, so a grep-based check passes
on prose alone.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response


@pytest.fixture
def app_with_probes(client):
    """The real app, plus three throwaway routes that return known shapes.

    Added to the live app rather than to a fresh FastAPI instance, because
    what is being tested is this app's middleware stack in its real order.
    They are removed again afterwards.
    """
    from apps.api.main import app

    @app.get("/__probe/ok")
    def _ok():
        return Response(content="fine", media_type="text/plain",
                        headers={"Cache-Control": "max-age=31536000"})

    @app.get("/__probe/cacheable-error")
    def _cacheable_error():
        # An error path that has inherited a cacheable policy — the case
        # where "set the header only if absent" would silently do nothing.
        return Response(content="nope", status_code=404, media_type="text/plain",
                        headers={"Cache-Control": "max-age=14400"})

    @app.get("/__probe/boom")
    def _boom():
        raise HTTPException(status_code=500, detail="boom")

    added = [r for r in app.routes if getattr(r, "path", "").startswith("/__probe/")]
    yield client
    for route in added:
        app.routes.remove(route)


def test_an_unmatched_route_404_is_not_cacheable(app_with_probes):
    """The exact response that started this: FastAPI's own unmatched-route
    404, which sets no Cache-Control of its own."""
    resp = app_with_probes.get("/no-such-endpoint-anywhere")

    assert resp.status_code == 404
    assert resp.headers["Cache-Control"] == "no-store"


def test_no_store_not_merely_no_cache(app_with_probes):
    """no-cache still permits a stored copy that is revalidated, and there
    is nothing worth storing about an error."""
    resp = app_with_probes.get("/no-such-endpoint-anywhere")

    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Pragma"] == "no-cache"


def test_a_500_is_not_cacheable(app_with_probes):
    resp = app_with_probes.get("/__probe/boom")

    assert resp.status_code == 500
    assert resp.headers["Cache-Control"] == "no-store"


def test_an_already_cacheable_error_is_overwritten(app_with_probes):
    """Set, not defaulted. An error path that inherited a cacheable policy
    from its success path would keep it otherwise — which is the shape the
    original bug had."""
    resp = app_with_probes.get("/__probe/cacheable-error")

    assert resp.status_code == 404
    assert resp.headers["Cache-Control"] == "no-store"


def test_success_caching_policy_is_left_alone(app_with_probes):
    """A deliberately long-cached success response must survive untouched —
    the middleware is about errors, not about caching in general."""
    resp = app_with_probes.get("/__probe/ok")

    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "max-age=31536000"
    assert "Pragma" not in resp.headers


def test_a_rate_limited_429_is_not_cacheable(client, mock_db):
    """The reason this middleware is registered OUTERMOST rather than
    anywhere else in main.py.

    Starlette's add_middleware inserts at the FRONT of the stack, so the
    last one added runs outermost. Registered any earlier, this would sit
    INSIDE the rate limiter and never see the 429 it returns — a cached 429
    locks a client out for the cache lifetime, long after the window has
    passed. Driven through the real limiter rather than asserted about
    registration order, because the order is only interesting for what it
    produces.
    """
    with patch("apps.api.middleware.rate_limit.check_rate_limit",
               return_value=(False, 30)):
        resp = client.post("/auth/send-magic-code", json={"email": "u@example.com"})

    assert resp.status_code == 429
    assert resp.headers["Cache-Control"] == "no-store"


def test_a_setup_guard_short_circuit_is_not_cacheable(client, mock_db):
    """The other thing an inner registration would miss: the setup guard
    answers 503 before any route is reached.

    A cached "not set up yet" would survive the setup that fixes it, which
    is the same class of lie as the cached 404.
    """
    from apps.api.middleware import setup_guard

    empty_db = MagicMock()
    empty_db.query.return_value = empty_db
    empty_db.filter.return_value = empty_db
    empty_db.first.return_value = None  # no superadmin exists yet

    with patch.object(setup_guard, "_setup_complete", False), \
         patch("apps.api.database.SessionLocal", return_value=empty_db):
        resp = client.get("/auth/me")

    assert resp.status_code == 503
    assert resp.json()["needs_setup"] is True
    assert resp.headers["Cache-Control"] == "no-store"
