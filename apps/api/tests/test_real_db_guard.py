"""A test that needs Postgres and does not get it must not pass quietly.

The family this closes is the one CLAUDE.md's harness rule is about: a check
that reported green because it never ran. P0b-0's rate limiter failed open
where Redis was unreachable; P0b-2's `SetupGuardMiddleware` read its answer
off the ambient database and the all-green configuration was the one where
the middleware never executed; `test_smtp_security_modes` passed because no
environment it ran in set the variable it was about. A `pytest.skip` is the
same shape with a label on it — and the label is only ever read by whoever
goes looking.

So: **locally a missing database skips, in CI it fails.** Locally a skip is
right; somebody running the suite on a laptop with no stack up should not be
told their change is broken. In CI it is not right: the runner is configured
on purpose, a missing service is a misconfiguration, and a company-scoping
test that skipped is a cross-company leak nobody checked for.

These tests need no database themselves — they are about the guard — which is
the point. A guard that could only be exercised by removing the database
could not be exercised at all.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from _pytest.outcomes import Failed, Skipped

from . import conftest

REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeItem:
    """Enough of a pytest item for `pytest_collection_modifyitems`."""

    def __init__(self, *fixturenames: str):
        self.fixturenames = list(fixturenames)
        self.markers: list[str] = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


# ─── The guard itself ───────────────────────────────────────────────────────


def test_without_ci_a_missing_database_skips():
    """With a reason that names the resource and says how to supply it."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CI", None)
        with pytest.raises(Skipped) as caught:
            conftest._no_postgres("nothing answered on port 5432")

    message = str(caught.value)
    assert "did NOT" in message or "not run" in message.lower()
    assert "docker compose" in message, "the skip must say how to get a database"
    assert "nothing answered on port 5432" in message


def test_with_ci_set_a_missing_database_fails():
    """The whole point. `CI` is set by GitHub Actions on every runner."""
    with patch.dict(os.environ, {"CI": "true"}):
        with pytest.raises(Failed) as caught:
            conftest._no_postgres("nothing answered on port 5432")

    message = str(caught.value)
    assert "misconfiguration" in message
    assert "nothing answered on port 5432" in message


def test_the_guard_does_not_read_ci_as_a_string():
    """`CI=false` is still "a CI set this", and GitHub never sets it to that.

    Pinned because the tempting `if os.environ.get("CI") == "true"` would make
    the guard silent on any runner that spells it `1`, and silence is the
    failure mode this whole file exists to remove.
    """
    with patch.dict(os.environ, {"CI": "1"}):
        with pytest.raises(Failed):
            conftest._no_postgres("no database")


def test_an_empty_ci_variable_is_treated_as_not_ci():
    """An exported-but-empty `CI=` is how a shell says "unset" in practice."""
    with patch.dict(os.environ, {"CI": ""}):
        with pytest.raises(Skipped):
            conftest._no_postgres("no database")


# ─── The marker that makes the count possible ───────────────────────────────


@pytest.mark.parametrize("fixture", ["pg_engine", "pg_db", "pg_client"])
def test_a_test_requesting_a_real_database_fixture_is_marked(fixture):
    item = _FakeItem(fixture, "world")
    conftest.pytest_collection_modifyitems([item])
    assert item.markers == [conftest.REAL_DB_MARKER]


def test_a_test_on_the_mock_session_is_not_marked():
    """Otherwise `-m realdb` would count the mock-session tests too, and the
    number CI asserts would stop meaning anything."""
    item = _FakeItem("client", "mock_db", "auth_headers")
    conftest.pytest_collection_modifyitems([item])
    assert item.markers == []


def test_the_marker_really_selects_the_real_database_tests():
    """Through the real pytest, not through the function above.

    The unit tests prove the function; this proves the wiring — that the hook
    is named correctly, that the marker is registered, and that `-m realdb`
    therefore selects something. A collection-only run, so no database is
    needed and nothing is executed.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "apps/api/tests",
            "-m",
            conftest.REAL_DB_MARKER,
            "--collect-only",
            "-q",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "PytestUnknownMarkWarning" not in result.stdout + result.stderr, (
        "the realdb marker is not registered in pytest.ini"
    )

    selected = [
        line for line in result.stdout.splitlines() if "::test_" in line
    ]
    assert len(selected) >= 40, (
        f"-m realdb selected only {len(selected)} tests; the hook or the "
        f"fixture names have drifted:\n{result.stdout[-2000:]}"
    )
    # Two files that must be in it, one per reason a real database is needed:
    # uniqueness (a constraint a mock cannot have) and scoping (a WHERE a
    # mock applies for every filter and for none).
    joined = "\n".join(selected)
    assert "test_company_bank_account_iban_unique.py" in joined
    assert "test_company_isolation.py" in joined
