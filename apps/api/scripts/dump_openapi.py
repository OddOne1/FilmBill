"""Write the OpenAPI document to stdout, or to a file.

    python -m apps.api.scripts.dump_openapi > openapi.json
    python -m apps.api.scripts.dump_openapi --out apps/web/openapi.json

`pnpm gen:api` runs this and feeds the result to `openapi-typescript`, which
is the whole reason it exists: generating the web app's types must not need a
running API, a database, Redis or S3. A codegen step that needs the stack up
is a codegen step that gets skipped, and CLAUDE.md rule 10 only works if CI
can regenerate on every pull request and compare.

**It builds the schema by importing the app, not by fetching `/openapi.json`.**
Those can differ — `DISABLE_DOCS` turns the served document off entirely in
production — and the thing the web app must be typed against is the routes the
code declares, not whatever a particular deployment chose to publish.

Placeholder environment is supplied below for the settings that are required
but irrelevant here. Nothing is connected to: `Settings` only needs the values
to exist, the S3 client is patched out because `main` imports it at module
scope, and the lifespan that would create a bucket never runs on import.
"""

import argparse
import json
import os
import sys
from unittest.mock import MagicMock, patch

#: Set BEFORE importing anything under apps.api — `config.Settings` reads the
#: environment at import time and raises on a missing required field.
#: `setdefault`, so a real environment (a developer with a .env, CI with its
#: service containers) still wins.
_PLACEHOLDERS = {
    "DATABASE_URL": "postgresql://openapi:openapi@localhost:5432/openapi",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET": "dump-openapi-only-never-used-to-sign-anything",
    "S3_BUCKET": "openapi",
    "S3_ENDPOINT": "http://localhost:9000",
    "S3_ACCESS_KEY": "openapi",
    "S3_SECRET_KEY": "openapi",
    "S3_REGION": "us-east-1",
    "FRONTEND_URL": "http://localhost:3100",
    "GOTENBERG_URL": "http://localhost:3000",
    # The document must be identical whether or not this deployment serves
    # /docs, so the flag is forced off rather than inherited. Left inherited,
    # a developer with DISABLE_DOCS=true in their .env would regenerate a
    # different file than CI and the drift check would fail for nobody's
    # mistake.
    "DISABLE_DOCS": "false",
}


def build_schema() -> dict:
    for key, value in _PLACEHOLDERS.items():
        if key == "DISABLE_DOCS":
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    from fastapi.openapi.utils import get_openapi

    with patch("apps.api.services.s3_service.ensure_bucket_exists"), patch(
        "apps.api.services.s3_service.get_s3_client", return_value=MagicMock()
    ):
        from apps.api.main import app

        return get_openapi(title=app.title, version=app.version, routes=app.routes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args()

    # `sort_keys` and a fixed indent, so the bytes depend only on the routes.
    # Without it the file would differ between runs on dict ordering alone and
    # CI's `git diff --exit-code` would fail at random, which is the fastest
    # way to teach everyone to ignore it.
    text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"

    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
