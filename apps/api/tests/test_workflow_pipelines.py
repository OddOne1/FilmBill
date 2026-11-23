"""Every pipeline in a workflow runs under `pipefail`.

**Why this file is allowed to read a file, given rule 11.** Rule 11 forbids a
test that greps *source text* as a stand-in for behaviour — asserting that
`engine.py` contains the word "rounding" instead of checking what it rounds.
This is the other thing: the workflow file IS the artifact under test. There
is no behaviour behind it to reach for, because the behaviour belongs to
GitHub's runner, and the runner is not here. So this parses the YAML and
asserts on its structure, never on comments or formatting.

**What it is about.** GitHub's documented shells (github/docs,
`data/reusables/actions/supported-shells.md`):

    shell: unspecified  ->  bash -e {0}
    shell: bash         ->  bash --noprofile --norc -eo pipefail {0}

with the table's own note: "this runs a different command to when `bash` is
specified explicitly". The default has **no pipefail**, so

    python -m pytest ... | tee output.txt

reports `tee`'s exit status. A failing suite leaves a green step. That is not
hypothetical — it hid two failing tests on main from 11afc1d until A1, and
the only thing still looking was a floor on the passed count, which 2267 of
2269 clears without noticing (instance 19 in
`docs/engineering/test-harness-traps.md`).

The guard is deliberately about **every** pipeline and not about `tee`:
`pytest | tail`, `pytest > out | grep`, anything. The next one will not be
`tee`.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: A shell whose documented invocation sets pipefail. Keyed on the basename so
#: a custom `shell: /usr/bin/bash` counts too.
PIPEFAIL_SHELLS = frozenset({"bash"})


def workflow_files() -> list[pathlib.Path]:
    return sorted(
        path
        for path in WORKFLOW_DIR.iterdir()
        if path.suffix in {".yml", ".yaml"}
    )


def test_there_are_workflows_to_check():
    """The guard below is a loop; an empty loop passes.

    A positive marker rather than the absence of a failure (rule 17b): if the
    directory moves or the suffix filter stops matching, every assertion in
    this file would still be green on nothing at all.
    """
    found = workflow_files()
    assert found, f"no workflow files under {WORKFLOW_DIR}"
    names = {path.name for path in found}
    assert "ci.yml" in names, names


def has_pipeline(line: str) -> bool:
    """True when `line` contains a real shell pipeline.

    Quote-aware on purpose, because `grep -oE '[0-9]+ passed|skipped'` and
    `a || b` both contain a `|` and neither is a pipeline. A naive `"|" in
    line` would flag half the file and the guard would be switched off within
    a week.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False

    in_single = in_double = False
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\":
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "|" and not in_single and not in_double:
            # `||` is logical or, in either position.
            if stripped[index + 1 : index + 2] == "|":
                index += 2
                continue
            if index and stripped[index - 1] == "|":
                index += 1
                continue
            return True
        index += 1
    return False


def script_has_pipeline(script: str) -> bool:
    """`has_pipeline` for a whole `run:` block.

    Its own function because `has_pipeline` is per-LINE — it treats a leading
    `#` as a comment, so handing it a multi-line block whose first line is a
    comment answers False for the whole step.
    """
    return any(has_pipeline(line) for line in script.splitlines())


def shell_for(workflow: dict, job: dict, step: dict) -> str | None:
    """The shell this step runs under, by GitHub's precedence.

    Step, then the job's `defaults.run.shell`, then the workflow's — which is
    the order the documentation gives: "a default setting defined in a job
    will override a default setting that has the same name defined in a
    workflow".
    """
    for source in (
        step.get("shell"),
        job.get("defaults", {}).get("run", {}).get("shell"),
        workflow.get("defaults", {}).get("run", {}).get("shell"),
    ):
        if source:
            return str(source)
    return None


def pipefail_in_effect(shell: str | None, script: str) -> bool:
    """Either the shell sets it, or the script does.

    Both count. A step that says `set -o pipefail` itself is correct whatever
    happens to the workflow default, which is exactly why the per-step lines
    in `ci.yml` are kept alongside the default.
    """
    if shell:
        if pathlib.PurePath(shell.split()[0]).name in PIPEFAIL_SHELLS:
            return True
        if "pipefail" in shell:  # a custom shell string
            return True
    return "set -o pipefail" in script or "set -eo pipefail" in script


def piped_steps(path: pathlib.Path) -> list[tuple[str, str, str]]:
    """`(job, step name, the offending line)` for every unguarded pipeline."""
    workflow = yaml.safe_load(path.read_text()) or {}
    offenders: list[tuple[str, str, str]] = []

    for job_name, job in (workflow.get("jobs") or {}).items():
        for position, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if not script:
                continue
            shell = shell_for(workflow, job, step)
            if pipefail_in_effect(shell, script):
                continue
            for line in script.splitlines():
                if has_pipeline(line):
                    offenders.append(
                        (
                            job_name,
                            step.get("name") or f"step #{position + 1}",
                            line.strip(),
                        )
                    )
    return offenders


@pytest.mark.parametrize(
    "path", workflow_files(), ids=lambda path: path.name
)
def test_no_run_step_pipes_without_pipefail(path):
    """A pipeline with no pipefail reports the LAST command's exit status.

    So `pytest | tee` is green when pytest is red. Fix it by giving the
    workflow (or the job, or the step) `shell: bash`, or by putting
    `set -o pipefail` at the top of the step.
    """
    offenders = piped_steps(path)
    assert not offenders, "\n".join(
        f"{path.name}: job {job!r}, step {step!r} pipes without pipefail:\n"
        f"    {line}"
        for job, step, line in offenders
    )


def test_ci_declares_an_explicit_bash_so_pipefail_is_on():
    """The workflow-level default, asserted as a value and not as a comment.

    `shell: bash` is what buys `-eo pipefail`; leaving `shell:` out gets
    `bash -e {0}` instead, and the two differ in exactly the way that cost
    this project a year of green runs over two failing tests.
    """
    workflow = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text())
    # `.get` rather than `[...]`: a missing block is the thing most likely to
    # happen here, and `KeyError: 'defaults'` is a worse thing to read at
    # 2 a.m. than a sentence saying what to put back.
    shell = workflow.get("defaults", {}).get("run", {}).get("shell")
    assert shell == "bash", (
        "ci.yml needs a workflow-level `defaults: run: shell: bash`; found "
        f"{shell!r}. Without the explicit shell GitHub runs `bash -e {{0}}` "
        "and every pipeline in the file loses pipefail."
    )


def test_the_pytest_steps_also_set_pipefail_themselves():
    """Belt and braces, and the braces are the point.

    A job may define its own `defaults` and override the workflow's; GitHub
    documents that precedence. If that ever happens to `api-tests`, these
    `set -o pipefail` lines are what keeps the suite's exit code real, so
    they are not redundant with the default and must not be "tidied away".
    """
    workflow = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text())
    piped_pytest = [
        step
        for step in workflow["jobs"]["api-tests"]["steps"]
        if "pytest" in (step.get("run") or "")
        and script_has_pipeline(step["run"])
    ]
    assert len(piped_pytest) == 2, [step.get("name") for step in piped_pytest]
    for step in piped_pytest:
        assert "set -o pipefail" in step["run"], step.get("name")


def test_a_counted_pipeline_is_guarded_against_matching_nothing():
    """The bug the fix above introduced, caught here.

    With pipefail on, a `grep` that matches nothing fails its pipeline, and a
    failing pipeline inside `$( )` aborts the whole step under `-e` — with no
    message. For `SKIPPED=$(grep ... skipped ...)` the healthy run is the one
    that dies, because finding no skips is the good outcome. Hence `|| true`
    on every counted pipeline.
    """
    workflow = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text())
    counted: list[str] = []
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for line in (step.get("run") or "").splitlines():
                bare = line.strip()
                # An assignment whose value is a command substitution
                # containing a pipeline.
                if "=$(" in bare and has_pipeline(bare):
                    counted.append(bare)

    assert counted, "no counted pipelines found — has the shape changed?"
    unguarded = [line for line in counted if "|| true" not in line]
    assert not unguarded, (
        "these abort the step when the grep matches nothing:\n"
        + "\n".join(f"    {line}" for line in unguarded)
    )
