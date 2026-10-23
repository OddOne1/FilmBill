"""`apps/api/billing/` holds no database and no web framework. Enforced.

SCOPE principle P1 puts the calculation engine in its own package, and the
whole value of that is lost the moment one function in it takes a `Session`:
after that, every test of a tax decision needs a database, the fixtures grow,
and the golden-fixture table — the only form in which a tax rule is reviewable
by somebody who is not a programmer — stops being possible.

**The subject is what the package IMPORTS when it is imported**, so the first
test imports it in a fresh interpreter and asks that interpreter what it
loaded. Not a grep: a grep over source text has produced four false passes in
this project (CLAUDE.md rule 11), and in this particular case a grep cannot
see `importlib.import_module("sqlalchemy")` at all while the interpreter can.

The AST scan below it is a second, narrower check that exists for its error
MESSAGE — it names the file and the line — and it runs after the real one, so
a failure here is always accompanied by the behavioural failure above.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BILLING = REPO_ROOT / "apps" / "api" / "billing"

#: What must not be reachable from `apps.api.billing`.
#:
#: `sqlalchemy` and `fastapi` are the boundary itself. `apps.api.models` is
#: listed separately because importing it is how the boundary would be
#: crossed in practice — a function that "just needs the TaxCode class" —
#: and it imports SQLAlchemy transitively, so it would be caught anyway, with
#: a much worse message.
FORBIDDEN_TOP_LEVEL = ("sqlalchemy", "fastapi", "starlette", "alembic")
FORBIDDEN_MODULES = FORBIDDEN_TOP_LEVEL + ("apps.api.models", "apps.api.database")


def test_importing_the_billing_package_loads_no_orm_and_no_web_framework():
    """The real check: a fresh interpreter, and what it actually loaded.

    A subprocess because this one cannot be answered in-process — by the time
    pytest runs, SQLAlchemy and FastAPI are loaded by half the suite, and
    `"sqlalchemy" in sys.modules` would be True no matter what this package
    does.
    """
    program = (
        "import sys\n"
        # Both packages, because the boundary is the PACKAGE's and a second
        # one added later must not be exempt by having been forgotten here.
        "import apps.api.billing.parties as parties\n"
        "import apps.api.billing.tax as tax\n"
        # Touch EVERY exported name, so this is not merely a check on what
        # the __init__ statements do: an import inside a function body is
        # reached as well. Written against `__all__` rather than a named
        # function so it keeps covering the whole surface as the package grows.
        "for module in (tax, parties):\n"
        "    assert module.__all__, f'{module.__name__} exports nothing'\n"
        "    for name in module.__all__:\n"
        "        getattr(module, name)\n"
        "loaded = sorted(\n"
        "    name for name in sys.modules\n"
        f"    if name.split('.')[0] in {FORBIDDEN_TOP_LEVEL!r}\n"
        ")\n"
        "print('|'.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"apps.api.billing.tax does not import cleanly on its own:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    leaked = [name for name in result.stdout.strip().split("|") if name]
    assert not leaked, (
        "importing apps.api.billing.tax pulled in "
        f"{', '.join(leaked)}. The billing package is pure Python over plain "
        "data — see its docstring. Move whatever needs a session into "
        "apps/api/services/ and pass the data in."
    )


def test_the_import_probe_would_notice_a_violation():
    """The guard on the test above: prove the probe can fail.

    Without this, a typo in the program string — or an `apps.api.billing.tax`
    that silently fails to import — would leave the test passing on an empty
    module list, which is exactly the shape of a check satisfied by the
    absence of its own subject (CLAUDE.md 17b, case 2).
    """
    program = (
        "import sys\n"
        "import apps.api.models  # the thing the boundary forbids\n"
        "loaded = sorted(\n"
        "    name for name in sys.modules\n"
        f"    if name.split('.')[0] in {FORBIDDEN_TOP_LEVEL!r}\n"
        ")\n"
        "print('|'.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "sqlalchemy" in result.stdout, (
        "the probe reported no SQLAlchemy for a module that definitely "
        f"imports it, so it cannot be trusted when it reports none:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _python_files() -> list[Path]:
    return sorted(BILLING.rglob("*.py"))


def test_the_package_has_files_to_check():
    """A glob that matched nothing would make the scan below vacuous."""
    files = _python_files()
    assert len(files) >= 7, f"only found {files} under {BILLING}"


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """`(line, dotted name)` for every import in `path`, relatives resolved."""
    tree = ast.parse(path.read_text(), filename=str(path))
    package = path.relative_to(REPO_ROOT).with_suffix("").parts
    found: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # `from ..models import X` inside apps/api/billing/tax/x.py
                # is apps.api.models. Walk up `level` packages from the file's
                # own package.
                base = list(package[: -node.level])
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            found.append((node.lineno, target))
    return found


def test_no_file_in_billing_names_a_forbidden_module():
    """The narrow check, kept for its message.

    Reports `billing/tax/engine.py:12 imports sqlalchemy` rather than a list
    of module names with no file attached — which is the difference between a
    failure somebody can act on and one they have to bisect.
    """
    offences = []
    for path in _python_files():
        for line, module in _imported_modules(path):
            if any(
                module == forbidden or module.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_MODULES
            ):
                offences.append(
                    f"{path.relative_to(REPO_ROOT)}:{line} imports {module}"
                )

    assert not offences, (
        "the billing package must stay free of the ORM and the web "
        "framework:\n" + "\n".join(offences)
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("import sqlalchemy", "sqlalchemy"),
        ("from sqlalchemy.orm import Session", "sqlalchemy.orm"),
        ("from ...models.tax import TaxCode", "apps.api.models.tax"),
        ("from ..tax.engine import propose_tax", "apps.api.billing.tax.engine"),
    ],
)
def test_the_resolver_resolves_what_it_claims_to(tmp_path, source, expected):
    """The scan's own arithmetic, checked.

    `node.level` counting is the kind of off-by-one that fails silently: one
    too many and every relative import resolves to something harmless, the
    scan reports nothing and reads as coverage. These cases are written from
    the position of a real file in the package.
    """
    path = BILLING / "tax" / "probe_for_the_resolver.py"
    path.write_text(source + "\n")
    try:
        assert (1, expected) in _imported_modules(path)
    finally:
        path.unlink()
