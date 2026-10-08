"""The image-reference parser, which is what the weekly guard depends on.

`scripts/image_references.py` exists because MinIO withdrew `minio/minio` and
`minio/mc` from Docker Hub while both were pinned in two compose files, and
nothing noticed for weeks: every machine that ran the stack had the layers
cached already. A green smoke test on a warm Docker cache says nothing about a
fresh host.

A guard is only as good as its parser. One that silently matched nothing would
report "every image reference resolves" forever — the same false green, one
level up — so these tests feed it the shapes it has to survive, and the last
one asserts it still finds this repository's own references.

No registry is contacted here. `resolves()` runs `docker manifest inspect` and
is the workflow's job; what is tested is the reading.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))

from image_references import (  # noqa: E402
    collect,
    is_checkable,
    is_local_build,
    references_from_compose,
    references_from_dockerfile,
)


# ─── compose ────────────────────────────────────────────────────────────────


def test_a_plain_image_with_a_tag():
    assert references_from_compose(
        "services:\n  db:\n    image: postgres:15-alpine\n"
    ) == ["postgres:15-alpine"]


def test_an_image_with_no_tag():
    """Legal, and worth finding: an untagged reference means `:latest`, which
    is the thing that moves under you."""
    assert references_from_compose("    image: redis\n") == ["redis"]


def test_a_registry_host_and_a_port():
    assert references_from_compose("    image: quay.io/minio/minio:latest\n") == [
        "quay.io/minio/minio:latest"
    ]
    assert references_from_compose("    image: registry.local:5000/thing:1\n") == [
        "registry.local:5000/thing:1"
    ]


def test_a_digest_pin():
    reference = "postgres@sha256:" + "a" * 64
    assert references_from_compose(f"    image: {reference}\n") == [reference]


def test_quoted_and_trailing_comment():
    assert references_from_compose('    image: "redis:7-alpine"  # pinned\n') == [
        "redis:7-alpine"
    ]


def test_the_word_image_elsewhere_is_not_a_reference():
    """`image` appears in comments and in command strings. Matching those
    would make the check fail on things that are not references at all —
    and a check that cries wolf gets switched off."""
    text = (
        "services:\n"
        "  api:\n"
        "    # the image: below is built, not pulled\n"
        "    build: .\n"
        "    command: echo 'image: not-a-reference'\n"
    )
    assert references_from_compose(text) == []


def test_a_variable_reference_is_returned_but_not_checkable():
    """Returned rather than dropped, and flagged.

    A compose file whose image name comes from the environment is a compose
    file whose image nobody can verify, and silence there is the same failure
    this whole script exists to prevent.
    """
    found = references_from_compose("    image: ${REGISTRY}/api:${TAG}\n")

    assert found == ["${REGISTRY}/api:${TAG}"]
    assert not is_checkable(found[0])


def test_a_concrete_reference_is_checkable():
    assert is_checkable("postgres:15-alpine")


# ─── Dockerfile ─────────────────────────────────────────────────────────────


def test_a_single_from():
    assert references_from_dockerfile("FROM python:3.11-slim\nRUN true\n") == [
        "python:3.11-slim"
    ]


def test_multi_stage_returns_base_images_and_not_stage_names():
    """`FROM deps` refers to an earlier stage, not to an image called "deps".
    Asking a registry about it would fail every run."""
    text = (
        "FROM node:20-alpine AS deps\n"
        "FROM node:20-alpine AS builder\n"
        "COPY --from=deps /app /app\n"
        "FROM builder AS runner\n"
    )
    assert references_from_dockerfile(text) == ["node:20-alpine", "node:20-alpine"]


def test_a_build_arg_base_image_is_resolved_from_its_default():
    text = "ARG BASE=python:3.11-slim\nFROM ${BASE}\n"
    assert references_from_dockerfile(text) == ["python:3.11-slim"]


def test_a_build_arg_with_no_default_stays_unresolved_and_is_flagged():
    """Honest: the real value comes from the build command and this file
    cannot know it, so it is reported rather than guessed at."""
    found = references_from_dockerfile("ARG BASE\nFROM ${BASE}\n")

    assert found == ["${BASE}"]
    assert not is_checkable(found[0])


def test_a_platform_flag_is_not_mistaken_for_the_image():
    assert references_from_dockerfile(
        "FROM --platform=linux/amd64 python:3.11-slim AS base\n"
    ) == ["python:3.11-slim"]


def test_scratch_is_not_an_image():
    assert references_from_dockerfile("FROM scratch\n") == []


def test_lowercase_from_is_still_a_from():
    assert references_from_dockerfile("from python:3.11-slim\n") == ["python:3.11-slim"]


# ─── locally built tags ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reference",
    ["filmbill-v2-api:test", "filmbill-v2-web:test", "filmbill-api:latest"],
)
def test_our_own_build_tags_are_skipped(reference):
    """They exist in no registry — `scripts/server-test-build.sh` builds them
    locally. Checking them would fail every run and teach everyone to ignore
    the result."""
    assert is_local_build(reference)


@pytest.mark.parametrize(
    "reference",
    ["postgres:15-alpine", "pgsty/minio:RELEASE.2026-08-04T00-00-00Z", "traefik:v2.11"],
)
def test_real_images_are_not_skipped(reference):
    assert not is_local_build(reference)


# ─── against this repository ────────────────────────────────────────────────


def test_it_finds_this_repositorys_own_references():
    """The guard against a parser that quietly matches nothing.

    Without this, a regex that stopped working would report "every image
    reference resolves" forever — a false green one level up from the one
    that caused all this.
    """
    found = collect()

    assert len(found) >= 8, f"only found {sorted(found)}"
    assert "postgres:15-alpine" in found
    assert "python:3.11-slim" in found
    # The replacement, in both stacks that use object storage.
    assert "pgsty/minio:RELEASE.2026-08-04T00-00-00Z" in found
    assert set(found["pgsty/minio:RELEASE.2026-08-04T00-00-00Z"]) == {
        "docker-compose.dev.yml",
        "docker-compose.server-test.yml",
    }


def test_the_withdrawn_minio_images_are_gone_from_this_repository():
    """The actual regression. Not a grep over source text — this is the
    parser's own view of what the stacks will try to pull."""
    found = collect()

    assert not [r for r in found if r.startswith("minio/")], (
        f"a withdrawn minio/* image is referenced again: "
        f"{[r for r in found if r.startswith('minio/')]}"
    )


# ─── telling "gone" from "would not answer" ─────────────────────────────────
#
# `docker manifest inspect` exits 1 for BOTH, so the distinction lives entirely
# in stderr. Getting it wrong in the lenient direction turns a withdrawn image
# into a pass; getting it wrong in the strict direction reports every image as
# broken whenever Docker Hub throttles, and a guard that cries wolf gets
# switched off.
#
# `subprocess.run` is doubled here, and the thing under test is the
# CLASSIFICATION rather than the subprocess — so what the double returns is the
# real recorded stderr from the real commands, captured on 2026-10-07:
#
#   throttled  `toomanyrequests: You have reached your unauthenticated pull
#               rate limit. https://www.docker.com/increase-rate-limit`
#   withdrawn  `errors: denied: requested access to the resource is denied
#               unauthorized: authentication required`
#
# The withdrawn case is additionally checked against the live registry below,
# so the strings are not the only evidence (CLAUDE.md 17b).

import subprocess as _subprocess  # noqa: E402
from unittest.mock import patch  # noqa: E402

import image_references as _ir  # noqa: E402

THROTTLED = (
    "toomanyrequests: You have reached your unauthenticated pull rate limit. "
    "https://www.docker.com/increase-rate-limit"
)
WITHDRAWN = (
    "errors:\ndenied: requested access to the resource is denied\n"
    "unauthorized: authentication required"
)


def _fake_run(stderr: str, returncode: int = 1):
    def run(*args, **kwargs):
        return _subprocess.CompletedProcess(
            args=args[0], returncode=returncode, stdout="", stderr=stderr
        )

    return run


def test_a_registry_that_answers_no_is_MISSING():
    with patch.object(_ir.subprocess, "run", _fake_run(WITHDRAWN)):
        verdict, detail = _ir.classify("minio/minio:gone", retries=1)

    assert verdict == _ir.MISSING
    assert "denied" in detail


def test_a_rate_limited_registry_is_INCONCLUSIVE_not_MISSING():
    """The one that matters. Reported as MISSING, this would name every image
    in the repository as withdrawn the first time Docker Hub throttles CI."""
    with patch.object(_ir.subprocess, "run", _fake_run(THROTTLED)):
        verdict, _ = _ir.classify("redis:7-alpine", retries=1)

    assert verdict == _ir.INCONCLUSIVE


def test_a_rate_limited_registry_is_retried():
    """Because it is usually transient, and the guard has to be believable
    when it finally does say MISSING."""
    calls = {"n": 0}

    def run(*args, **kwargs):
        calls["n"] += 1
        return _subprocess.CompletedProcess(args[0], 1, "", THROTTLED)

    with patch.object(_ir.subprocess, "run", run), patch.object(_ir.time, "sleep"):
        _ir.classify("redis:7-alpine", retries=3)

    assert calls["n"] == 3


def test_a_definite_no_is_NOT_retried():
    """Waiting changes nothing about a withdrawn image, and three lookups per
    reference would make a full check needlessly slow."""
    calls = {"n": 0}

    def run(*args, **kwargs):
        calls["n"] += 1
        return _subprocess.CompletedProcess(args[0], 1, "", WITHDRAWN)

    with patch.object(_ir.subprocess, "run", run), patch.object(_ir.time, "sleep"):
        _ir.classify("minio/minio:gone", retries=3)

    assert calls["n"] == 1


def test_success_is_OK():
    with patch.object(_ir.subprocess, "run", _fake_run("", returncode=0)):
        assert _ir.classify("postgres:15-alpine", retries=1)[0] == _ir.OK


@pytest.mark.parametrize(
    "stderr",
    [
        "dial tcp: lookup registry-1.docker.io: no such host",
        "net/http: TLS handshake timeout",
        "Get https://registry-1.docker.io/v2/: connection refused",
    ],
)
def test_network_failures_are_INCONCLUSIVE_too(stderr):
    """A DNS failure or a dropped TLS handshake is no evidence about the
    image either — same reasoning as the rate limit."""
    with patch.object(_ir.subprocess, "run", _fake_run(stderr)):
        assert _ir.classify("redis:7-alpine", retries=1)[0] == _ir.INCONCLUSIVE


def test_the_withdrawn_minio_image_really_classifies_as_MISSING_live():
    """The same assertion against the real registry, so the recorded strings
    above are not the only evidence that the classifier works (17b).

    Skips where Docker is unavailable, and treats a rate-limited answer as a
    skip rather than a failure — this test is about the verdict for an image
    that is genuinely gone, and a throttled run cannot speak to it.
    """
    try:
        available = (
            _subprocess.run(
                ["docker", "version"],
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        # No `docker` BINARY at all, which is the case inside the API
        # container and in the api-tests CI job. FileNotFoundError, not a
        # non-zero exit — caught explicitly because an uncaught one here reads
        # as a failure of the classifier rather than an absent tool.
        available = False
    if not available:
        pytest.skip(
            "no docker CLI here — this one asks a real registry. Runs on the "
            "Mac and in the image-references workflow."
        )

    verdict, detail = _ir.classify("minio/minio:RELEASE.2024-09-13T20-26-02Z", retries=1)
    if verdict == _ir.INCONCLUSIVE:
        pytest.skip(f"registry would not answer (rate limit?): {detail[:120]}")

    assert verdict == _ir.MISSING, (
        "minio/minio is withdrawn from Docker Hub and must classify as MISSING; "
        f"got {verdict} ({detail[:160]})"
    )
