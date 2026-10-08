#!/usr/bin/env python3
"""Every container image this repository names, and whether it still exists.

Run `--list` to print the references, `--check` to resolve each one against
its registry. `--check` is what `.github/workflows/image-references.yml` runs
weekly.

**Why this exists.** MinIO withdrew `minio/minio` and `minio/mc` from Docker
Hub. Both were pinned in two compose files, and nothing noticed for weeks
because every machine that ran the stack already had the layers cached — a
green smoke test on a warm Docker cache says nothing about a fresh host. This
is the check that was missing: it asks the registry, not the local daemon.

**Locally built tags are skipped.** `filmbill-v2-api:test` and friends are
built by `scripts/server-test-build.sh` and exist in no registry; asking about
them would fail every run. They are matched by prefix (see `LOCAL_PREFIXES`),
and the prefix is deliberately narrow so a real image called something similar
is still checked.

Parsing is by hand rather than through `docker compose config`, on purpose:
that command needs every variable the file interpolates to be set, which makes
the check depend on a populated `.env` that CI does not have. A reference
containing an unexpanded `${VAR}` is reported as unresolvable rather than
silently skipped — see `is_checkable`.
"""

# `str | None` in an annotation is evaluated at runtime before 3.10, and the
# Mac ships python3.9 while CI and the container are 3.11. This makes the
# annotations lazy so the script runs on both — it is a repo-wide tool, not
# an API module.
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Tags built locally and pushed nowhere. Prefix match, kept narrow.
LOCAL_PREFIXES = ("filmbill-v2-", "filmbill-")

#: `image: foo/bar:tag`, with optional quotes. Anchored to the compose key so
#: the word "image" inside a comment or a command string is not a reference.
_COMPOSE_IMAGE = re.compile(r"^\s*image:\s*[\"']?([^\s\"'#]+)[\"']?", re.M)

#: `FROM ref`, optionally `AS stage`, optionally `--platform=…`. Case-
#: insensitive because `from` is legal Dockerfile syntax.
_DOCKERFILE_FROM = re.compile(
    r"^\s*FROM\s+(?:--[a-z-]+(?:=\S+)?\s+)*([^\s]+)", re.M | re.I
)

#: `ARG FOO=default` — needed to resolve `FROM ${FOO}`, which is how a
#: parameterised base image is written.
_DOCKERFILE_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", re.M)

#: `${VAR}` or `$VAR` left in a reference after substitution.
_UNEXPANDED = re.compile(r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*")


def is_local_build(reference: str) -> bool:
    """Whether this is a tag we build ourselves and never push."""
    return reference.startswith(LOCAL_PREFIXES)


def is_checkable(reference: str) -> bool:
    """Whether a registry could be asked about this reference at all.

    False for a leftover `${VAR}`: that is not a reference, it is a template
    the parser could not finish. Reported separately rather than skipped,
    because a compose file whose image name depends on an environment variable
    is a compose file whose image nobody can verify.
    """
    return not _UNEXPANDED.search(reference)


def _strip_stage(reference: str, stages: set) -> str | None:
    """`FROM builder` refers to an earlier stage, not an image. Returns None."""
    return None if reference in stages else reference


def references_from_compose(text: str) -> list:
    return [m.group(1) for m in _COMPOSE_IMAGE.finditer(text)]


def references_from_dockerfile(text: str) -> list:
    """Base images only — multi-stage `FROM <earlier stage>` is not an image.

    `ARG` defaults are substituted so `FROM ${BASE}` resolves to whatever the
    Dockerfile itself declares. An `ARG` with no default stays unexpanded and
    is reported by `is_checkable`, which is the honest answer: the real value
    comes from the build command and this file cannot know it.
    """
    args = {m.group(1): m.group(2) for m in _DOCKERFILE_ARG.finditer(text)}

    # Stage names first, so a later `FROM deps` is recognised as a stage.
    stages = {
        m.group(1).lower()
        for m in re.finditer(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", text, re.M | re.I)
    }

    found = []
    for match in _DOCKERFILE_FROM.finditer(text):
        reference = match.group(1)
        for name, value in args.items():
            reference = reference.replace(f"${{{name}}}", value).replace(
                f"${name}", value
            )
        reference = _strip_stage(reference, stages)
        if reference and reference.lower() != "scratch":
            found.append(reference)
    return found


def collect(root: pathlib.Path = REPO_ROOT) -> dict:
    """Every reference in the repository, mapped to the files naming it."""
    where: dict = {}

    def note(reference: str, path: pathlib.Path) -> None:
        # Deduplicated: a multi-stage Dockerfile names its base image once per
        # stage, and listing the same file three times reads like three
        # problems.
        files = where.setdefault(reference, [])
        name = str(path.relative_to(root))
        if name not in files:
            files.append(name)

    for path in sorted(root.glob("docker-compose*.yml")):
        for reference in references_from_compose(path.read_text()):
            note(reference, path)

    for path in sorted(root.rglob("Dockerfile*")):
        if "node_modules" in path.parts:
            continue
        for reference in references_from_dockerfile(path.read_text()):
            note(reference, path)

    return where


#: Verdicts from asking a registry about a reference.
OK = "ok"
MISSING = "missing"
INCONCLUSIVE = "inconclusive"

#: Stderr fragments that mean "the registry would not answer", as opposed to
#: "the registry answered no".
#:
#: This distinction is the difference between a guard and a nuisance, and it is
#: NOT visible in the exit code — `docker manifest inspect` exits 1 for both.
#: Measured on 2026-10-07 while writing this:
#:
#:   throttled   `toomanyrequests: You have reached your unauthenticated pull
#:                rate limit.`                       ← no evidence either way
#:   withdrawn   `denied: requested access to the resource is denied` /
#:               `unauthorized: authentication required`  ← definitely gone
#:
#: Running `--check` a few times in a row is enough to hit the first one, at
#: which point a naive exit-code check reports EVERY image as broken. A guard
#: that cries wolf gets switched off, and then it is worth less than nothing.
_RATE_LIMITED = (
    "toomanyrequests",
    "rate limit",
    "timeout",
    "temporary failure",
    "no such host",
    "connection refused",
    "i/o timeout",
    "tls handshake",
)

#: How hard to retry a registry that would not answer.
_RETRIES = 3
_BACKOFF = (5, 15)


def classify(reference: str, retries: int = _RETRIES) -> tuple:
    """Ask the registry: (verdict, detail). Pulls no layers.

    Retries while the answer is "would not answer", because that is the common
    transient and the whole point is to be believable when it says MISSING.
    """
    detail = ""
    for attempt in range(1, max(1, retries) + 1):
        result = subprocess.run(
            ["docker", "manifest", "inspect", reference],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            return OK, ""
        detail = " ".join((result.stderr or "").split())
        lowered = detail.lower()
        if not any(fragment in lowered for fragment in _RATE_LIMITED):
            # The registry answered, and the answer is no.
            return MISSING, detail
        if attempt < retries:
            time.sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])

    return INCONCLUSIVE, detail


def resolves(reference: str) -> bool:
    """Kept for callers that only want a yes/no. INCONCLUSIVE counts as no."""
    return classify(reference)[0] == OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="print references")
    group.add_argument("--check", action="store_true", help="resolve each one")
    args = parser.parse_args()

    found = collect()
    if not found:
        print("No image references found — the parser is broken.", file=sys.stderr)
        return 1

    if args.list:
        for reference, files in sorted(found.items()):
            kind = "local" if is_local_build(reference) else "registry"
            print(f"{kind:9} {reference}  ({', '.join(files)})")
        return 0

    missing, unexpanded, inconclusive = [], [], []
    for reference, files in sorted(found.items()):
        if is_local_build(reference):
            print(f"skip   {reference}  (built locally)")
            continue
        if not is_checkable(reference):
            unexpanded.append((reference, files, "unexpanded variable"))
            print(f"UNRESOLVED {reference}  ({', '.join(files)})")
            continue
        verdict, detail = classify(reference)
        if verdict == OK:
            print(f"ok     {reference}")
        elif verdict == MISSING:
            missing.append((reference, files, detail))
            print(f"MISSING {reference}  ({', '.join(files)})")
        else:
            inconclusive.append((reference, files, detail))
            print(f"?????? {reference}  ({', '.join(files)}) — registry would not answer")

    if missing or unexpanded:
        print(file=sys.stderr)
        print(
            "These image references do not resolve. A pinned tag that has been "
            "withdrawn still works on any machine with the layers cached, which "
            "is how this went unnoticed for weeks — so fix them now rather than "
            "on the next fresh host:",
            file=sys.stderr,
        )
        for reference, files, detail in missing + unexpanded:
            print(f"  {reference}  in {', '.join(files)}", file=sys.stderr)
            if detail:
                print(f"      {detail}", file=sys.stderr)

    if inconclusive:
        # Failing rather than passing, deliberately. A rate-limited answer is
        # no evidence either way, and the two costs are not symmetric: a false
        # red on a weekly job costs somebody a glance and a re-run, while a
        # false green is the bug that started all this and it hid for weeks.
        # The message says whose fault it is so the glance is short.
        print(file=sys.stderr)
        print(
            "These could NOT be determined — the registry refused to answer, "
            "usually Docker Hub's anonymous rate limit. This is not a withdrawn "
            "image; re-run the workflow (`workflow_dispatch`) or wait. It is "
            "reported as a failure rather than a pass because an undetermined "
            "check that reads as green is how a withdrawn image hides:",
            file=sys.stderr,
        )
        for reference, files, detail in inconclusive:
            print(f"  {reference}  in {', '.join(files)}", file=sys.stderr)
            if detail:
                print(f"      {detail}", file=sys.stderr)

    if missing or unexpanded or inconclusive:
        return 1

    print("\nEvery image reference resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
