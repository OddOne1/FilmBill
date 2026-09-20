"""The public API surface is exactly what P0a is supposed to expose.

This is the acceptance check for "nothing media-related survived", and it is
deliberately NOT a grep for media words. A grep over source files passes on a
comment and fails on an unrelated variable name; FreeFrame's own history has
four separate false passes from checks of that shape (CLAUDE.md rule 11).

What is checked instead is the API the app actually serves, read from
`app.openapi()`:

  1. the set of operation TAGS is exactly the kept set, and
  2. every route, method by method, matches a committed snapshot.

Both halves are needed. Routers for notifications, site-settings and
email-settings declare NO prefix — their paths are spelled out per route — so
a prefix- or tag-only check would miss a whole router coming back. And the
tag check alone would miss a media endpoint re-added under a kept tag.

To change the surface on purpose: run this file's `__main__` (see the bottom)
to regenerate the snapshot, and say in the commit why the API grew.
"""

from pathlib import Path

from fastapi.openapi.utils import get_openapi

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "openapi_paths.txt"

#: Every tag this API is allowed to expose in P0a.
#:
#: Derived from what survived the port, not from a wish list. Two notes for
#: whoever compares this against the P0a prompt, which anticipated a slightly
#: different set:
#:
#:   * `branding` is absent. FreeFrame's `branding` router was entirely
#:     PROJECT branding and watermarking — both media. The Branding *page*,
#:     which SCOPE §1 keeps 1:1, is powered by `/site-settings`, and that tag
#:     is here.
#:   * `me` is absent. FreeFrame's `me` router served `/me/assets` and
#:     `/me/folders` and nothing else. `/me/notifications` lives on, under
#:     the `notifications` tag, exactly where it was.
#:   * `files` is new: the object proxy that replaces `hls_proxy`, keeping
#:     avatars and brand images reachable without making the bucket
#:     browser-facing (CLAUDE.md rule 16).
EXPECTED_TAGS = {
    "admin",
    "auth",           # includes every /auth/2fa/* endpoint
    "email-settings",
    "events",
    "files",
    "health",
    "notifications",
    "setup",
    "site-settings",
    "users",
}


def _schema():
    from apps.api.main import app

    return get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )


def _operations(schema) -> list:
    """Sorted "METHOD /path" lines — one per operation.

    Method-level rather than path-level: a new DELETE on a path that already
    has a GET is a real widening of the API that a path-only list would not
    notice.
    """
    lines = []
    for path, operations in schema["paths"].items():
        for method in operations:
            lines.append(f"{method.upper()} {path}")
    return sorted(lines)


def _tags(schema) -> set:
    found = set()
    for operations in schema["paths"].values():
        for operation in operations.values():
            found.update(operation.get("tags", []))
    return found


def test_the_tag_set_is_exactly_the_kept_set(client):
    tags = _tags(_schema())

    assert tags == EXPECTED_TAGS, (
        f"unexpected: {sorted(tags - EXPECTED_TAGS)}; "
        f"missing: {sorted(EXPECTED_TAGS - tags)}"
    )


def test_every_route_matches_the_committed_snapshot(client):
    """The allowlist proper.

    A router registered by accident — or re-registered after being dropped —
    shows up here as an unexpected block of paths, whatever tag it carries.
    """
    assert SNAPSHOT.exists(), f"missing snapshot: {SNAPSHOT}"

    expected = [l for l in SNAPSHOT.read_text().splitlines()
                if l.strip() and not l.startswith("#")]
    actual = _operations(_schema())

    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    assert not added and not removed, (
        f"API surface changed.\n  added:   {added}\n  removed: {removed}\n"
        f"If this is intended, regenerate {SNAPSHOT.name} and say why in the "
        f"commit."
    )


def test_the_snapshot_is_not_empty(client):
    """A truncated or unwritten snapshot would make the check above pass for
    an app that serves nothing."""
    actual = _operations(_schema())

    assert len(actual) > 30, f"only {len(actual)} operations — did the app load?"


def test_no_route_is_left_untagged(client):
    """An untagged operation is invisible to the tag allowlist above, which
    would let a whole router in unnoticed."""
    untagged = [
        f"{method.upper()} {path}"
        for path, operations in _schema()["paths"].items()
        for method, operation in operations.items()
        if not operation.get("tags")
    ]

    assert not untagged, f"operations with no tag: {untagged}"


if __name__ == "__main__":
    # Regenerate the snapshot. Deliberately a separate, manual action: if the
    # test rewrote it on failure, it would ratify every accidental change.
    import os
    from unittest.mock import MagicMock, patch

    os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/x")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("JWT_SECRET", "regenerate-snapshot-only")

    with patch("apps.api.services.s3_service.ensure_bucket_exists"), \
         patch("apps.api.services.s3_service.get_s3_client", return_value=MagicMock()):
        lines = _operations(_schema())

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(
        "# The complete FilmBill API surface, one line per operation.\n"
        "# Regenerate with: python -m apps.api.tests.test_openapi_surface\n"
        "# See that file for why this is a snapshot rather than a grep.\n"
        + "\n".join(lines)
        + "\n"
    )
    print(f"wrote {len(lines)} operations to {SNAPSHOT}")
