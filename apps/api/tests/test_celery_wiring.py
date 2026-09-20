"""The Celery topology holds together.

Inherited from FreeFrame, where the SAME bug was found four separate times:
a task that was dispatched, accepted by the broker, and then never executed
by anything, silently, for months. Two mechanisms produce it --

  * A task routed (or defaulted) onto a queue no container consumes. The
    message is accepted and sits in the list forever. FreeFrame's compose
    started consumers for three queues and `task_default_queue` was a fourth
    that nobody served, so every unrouted task vanished.
  * `beat_schedule` naming a task by STRING. A name matching no registered
    task is not an error anywhere: beat dispatches it and no worker answers.

Both are invisible to unit tests of the task functions themselves, which
pass perfectly while the task is never called.

**Where FilmBill differs, and why these still matter.** P0a has two tasks
and an empty beat schedule, so several of these checks have little to chew
on today. They are kept, and each one that could pass vacuously carries an
explicit teeth check — a known-bad input it must still reject. The checks
exist for the day P2 adds document rendering on its own queue, which is
exactly when this bug class returns.

Routing is resolved by CELERY'S OWN ROUTER rather than by re-implementing
the match against the source, because getting that re-implementation subtly
wrong is how the bug hid: a task declared `name="build_document_pdf"` is
matched on that string, and a `apps.api.tasks.document_tasks.*` glob never
sees it.
"""

import ast
import re
from pathlib import Path

import pytest

TASK_DECORATORS = ("celery_app.task", "shared_task", "app.task")

API = Path(__file__).resolve().parents[1]
REPO = API.parents[1]
CELERY_APP = API / "tasks" / "celery_app.py"
COMPOSE = REPO / "docker-compose.prod.yml"
DEV_COMPOSE = REPO / "docker-compose.dev.yml"


@pytest.fixture(scope="module")
def celery_app():
    """The real app, with its task modules imported so they register."""
    from apps.api.tasks.celery_app import celery_app as app
    import apps.api.tasks.email_tasks  # noqa: F401  (registers its tasks)

    return app


def _queue_for(celery_app, task_name: str) -> str:
    """The queue Celery itself would publish this task name to."""
    return celery_app.amqp.router.route({}, task_name)["queue"].name


def _our_task_names(celery_app) -> list:
    return sorted(n for n in celery_app.tasks if n.startswith("apps.api.")
                  or not n.startswith("celery."))


def _consumed_queues(compose: Path) -> set:
    """Queues actually served by a `celery ... worker -Q ...` in a compose file."""
    queues = set()
    for m in re.finditer(r"celery -A [\w.]+ worker -Q ([\w,${}:-]+)", compose.read_text()):
        queues.update(q for q in m.group(1).split(",") if q and "$" not in q)
    return queues


def _decorated_tasks() -> dict:
    """{function name -> (module file, decorator sources, ast node)}."""
    out = {}
    for path in (API / "tasks").glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
                continue
            decs = [ast.unparse(d) for d in node.decorator_list]
            if any(any(k in d for k in TASK_DECORATORS) for d in decs):
                out[node.name] = (path.name, decs, node)
    return out


# ── the queue that nobody serves ────────────────────────────────────────────

def test_every_task_reaches_a_consumed_queue(celery_app):
    """The core invariant, and the one with the body count."""
    consumed = _consumed_queues(COMPOSE)
    assert consumed, "parsed no worker -Q flags out of docker-compose.prod.yml"

    stranded = {}
    checked = 0
    for name in _our_task_names(celery_app):
        checked += 1
        queue = _queue_for(celery_app, name)
        if queue not in consumed:
            stranded[name] = queue

    assert checked, "resolved no tasks at all — the check has nothing to catch"
    assert not stranded, (
        f"task(s) routed to a queue no container consumes: {stranded}; "
        f"consumed queues are {sorted(consumed)}"
    )


def test_the_stranding_check_has_teeth(celery_app):
    """A green check above must mean "nothing is stranded", not "nothing was
    looked at".

    An unrouted task really does land on `default`, and `default` really is
    unconsumed — so the previous test would fail on one. Without this, the
    day someone adds a `default` consumer "to be safe", the real check goes
    permanently green and stops meaning anything.
    """
    unrouted = _queue_for(celery_app, "apps.api.tasks.not_a_module.not_a_task")

    assert unrouted == celery_app.conf.task_default_queue == "default"
    assert "default" not in _consumed_queues(COMPOSE), (
        "something now consumes `default`, which makes the stranding check "
        "unable to fail — route tasks explicitly instead"
    )


def test_dev_and_prod_consume_the_same_queues():
    """A task that works in dev and vanishes in production is the worst
    version of this bug, because the dev run is what gives confidence."""
    assert _consumed_queues(DEV_COMPOSE) == _consumed_queues(COMPOSE)


def test_every_declared_queue_is_consumed_or_is_the_default(celery_app):
    """A queue declared and served by nobody is a trap for the next person
    routing a task to it."""
    declared = {q.name for q in celery_app.conf.task_queues}
    consumed = _consumed_queues(COMPOSE)

    orphaned = declared - consumed - {"default"}
    assert not orphaned, (
        f"queue(s) declared but consumed by no container: {sorted(orphaned)}"
    )


# ── beat ────────────────────────────────────────────────────────────────────

def test_p0a_schedules_nothing_and_says_so(celery_app):
    """P0a has no scheduled work. Pinned, so adding an entry means facing the
    two checks below in the same change rather than a year later."""
    assert celery_app.conf.beat_schedule == {}


def test_every_scheduled_task_exists_and_reaches_a_worker(celery_app):
    """The check that failed to exist in FreeFrame until three jobs had
    already been silently dead in production.

    Vacuous while the schedule is empty — `test_p0a_schedules_nothing` is
    what makes that honest — and load-bearing from the first beat entry on.
    """
    consumed = _consumed_queues(COMPOSE)
    registered = set(celery_app.tasks)

    for entry, config in celery_app.conf.beat_schedule.items():
        name = config["task"]
        assert name in registered, (
            f"beat entry {entry!r} names task {name!r}, which nothing defines"
        )
        queue = _queue_for(celery_app, name)
        assert queue in consumed, (
            f"beat entry {entry!r} routes to {queue!r}, which no worker "
            f"consumes (consumed: {sorted(consumed)})"
        )


def test_the_unrouted_exemption_list_is_empty(celery_app):
    """The escape hatch, pinned shut.

    FreeFrame's equivalent check SKIPPED anything on an exemption list, which
    is how one job sat broken through three separate discoveries of its own
    bug class: the check was green the whole time, because the bug was on the
    list. Adding a name here means editing this test — saying out loud in a
    diff that a scheduled task is knowingly not running.
    """
    from apps.api.tasks.celery_app import KNOWN_UNROUTED

    assert KNOWN_UNROUTED == set(), (
        f"KNOWN_UNROUTED is {KNOWN_UNROUTED}. Adding a name means a scheduled "
        f"task is being knowingly left broken; say why in the diff."
    )


# ── delivery guarantees ─────────────────────────────────────────────────────

def test_late_acks_are_enabled(celery_app):
    """Without these two, a killed worker's task is dropped, not redelivered.

    Every deploy restarts the workers, so this is not an edge case: it is
    what happens to whatever is in flight at deploy time.
    """
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


def test_prefetch_is_one(celery_app):
    """The other half of acks_late: with a larger prefetch, tasks a worker
    has claimed but not started are also redelivered on a restart, widening
    the window of duplicated work for no throughput this app needs."""
    assert celery_app.conf.worker_prefetch_multiplier == 1


# ── decorator placement (structural, via AST) ───────────────────────────────

def test_no_task_decorator_sits_on_a_private_helper():
    """A decorator applies to whatever `def` follows it.

    Inserting a helper between the decorator and its intended task silently
    moves the registration onto the helper and leaves the real task a plain
    function with no `.delay()`. Nothing raises at import time; it fails at
    the first dispatch, in a background thread, in production. That is a real
    FreeFrame outage.

    A leading underscore is this codebase's marker for "called directly, not
    dispatched", so a decorated one is the signature of exactly that drift.
    """
    private = {
        name: mod for name, (mod, _decs, _node) in _decorated_tasks().items()
        if name.startswith("_")
    }
    assert not private, f"private helper(s) registered as Celery tasks: {private}"


def test_bound_tasks_take_self_first():
    """`bind=True` injects the task instance as the first positional arg.

    A bound task whose first parameter is not `self` is either mis-decorated
    or will be called with every argument shifted by one.
    """
    wrong = {}
    for name, (mod, decs, node) in _decorated_tasks().items():
        if not any("bind=True" in d for d in decs):
            continue
        first = node.args.args[0].arg if node.args.args else None
        if first != "self":
            wrong[name] = f"{mod}: first param is {first!r}"
    assert not wrong, f"bind=True task(s) not taking self first: {wrong}"


def test_send_task_safe_targets_are_registered_tasks(celery_app):
    """Dispatching a plain function raises AttributeError at runtime only.

    send_task_safe calls `.delay()`, which a plain function does not have —
    and it runs in a daemon thread, so the failure surfaces as a stray
    traceback rather than a failed request. Checking the call sites is the
    only place this is cheap to catch.
    """
    tasks = set(_decorated_tasks())
    bad = {}
    for folder in ("routers", "tasks", "services"):
        d = API / folder
        if not d.is_dir():
            continue
        for path in d.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "id", None) != "send_task_safe" or not node.args:
                    continue
                target = node.args[0]
                # Only resolvable bare names; anything else is out of reach
                # of a static check and is left alone rather than guessed at.
                if isinstance(target, ast.Name) and target.id not in tasks:
                    bad[f"{path.name}:{node.lineno}"] = target.id
    assert not bad, f"send_task_safe called with non-task target(s): {bad}"


def test_dispatching_a_non_task_is_logged_and_not_a_second_traceback():
    """The logger must never be the thing that crashes.

    `task.name` exists only on a registered task, so reading it inside the
    handler that fires *because* the target was not one replaces the real
    error with a second traceback from the reporting code. That is what
    turned a FreeFrame outage into two stacked tracebacks with the actual
    AttributeError buried.

    Driven for real: a plain function is dispatched, and the assertion is
    that the call returns, the thread finishes, and a warning naming the
    function was logged.
    """
    from apps.api.tasks.celery_app import send_task_safe

    def not_a_task():  # no .delay()
        raise AssertionError("must never be called")

    import logging

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("celery.dispatch")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        send_task_safe(not_a_task)
        # send_task_safe dispatches on a daemon thread; give it a moment.
        import time

        for _ in range(100):
            if records:
                break
            time.sleep(0.01)
    finally:
        logger.removeHandler(handler)

    assert records, "dispatching a non-task logged nothing at all"
    message = records[0].getMessage()
    assert "not_a_task" in message, (
        f"the log line does not name the offending target: {message!r}"
    )
    assert records[0].exc_info, (
        "no traceback attached — the log names the task without saying why"
    )


# ── compose ─────────────────────────────────────────────────────────────────

def test_celery_services_do_not_inherit_the_api_healthcheck():
    """Every Celery service must override it, or it is permanently unhealthy.

    The image's HEALTHCHECK curls localhost:8000/health, which no Celery
    process serves. In FreeFrame all four Celery services were `(unhealthy)`
    from the day they were added, which is why nobody noticed one of them
    actually being gone.
    """
    text = COMPOSE.read_text()
    # A service block runs to the next top-level service key: a line with
    # EXACTLY two spaces of indent then a name. Matching "\n  " alone would
    # stop at the block's own first child, which is indented four.
    for service in ("worker:", "email_worker:", "beat:"):
        start = text.index(f"\n  {service}")
        nxt = re.search(r"\n  [A-Za-z_][\w-]*:", text[start + 3:])
        block = text[start:start + 3 + nxt.start()] if nxt else text[start:]
        assert "healthcheck:" in block, f"{service} has no healthcheck override"
        # The COMMAND, not the block: the block also carries the comment
        # explaining what is being overridden, which names the API's own
        # probe. Asserting over the whole block matches that prose instead.
        cmd = re.search(r"healthcheck:\s*\n\s*test:\s*(.+)", block)
        assert cmd, f"{service} healthcheck has no test command"
        assert "localhost:8000" not in cmd.group(1), (
            f"{service} healthcheck probes the API port"
        )
