from celery import Celery
from kombu import Queue
from kombu.exceptions import OperationalError

try:
    from ..config import settings
except ImportError:
    from config import settings

celery_app = Celery(
    "filmbill",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "apps.api.tasks.email_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=5,
    broker_pool_limit=0,  # Disable connection pooling in web process to avoid stale connections
    # A task is acked when it FINISHES, not when it is received.
    #
    # Celery's default acks a task the moment a worker picks it up. Every
    # deploy restarts the workers, so a job in flight was already acked and
    # simply vanished: nothing requeued it, and because the process was
    # killed rather than raising, the except/finally that would have
    # recorded the failure never ran either. Inherited from FreeFrame, where
    # that cost a production incident with no trace of it anywhere.
    #
    # reject_on_worker_lost completes the pair: without it a task whose
    # worker dies is marked failed rather than redelivered.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # With acks_late, prefetched-but-unstarted tasks are also redelivered on
    # a restart. Fetching one at a time keeps that set to the task actually
    # running.
    worker_prefetch_multiplier=1,
    # Define queues.
    #
    # NOTHING CONSUMES "default". docker-compose starts consumers for
    # `email_high,email_low` (email_worker) and `documents` (worker) only,
    # so a task with no route here is queued and then sits there forever,
    # silently. That exact bug hit FreeFrame four separate times; the
    # invariant is pinned by tests/test_celery_wiring.py.
    task_queues=(
        Queue("default"),
        Queue("email_high"),  # Magic codes, invites - immediate
        Queue("email_low"),   # Everything else that is mail
        # PDF rendering, e-invoice XML, archive packaging from P2 on. Its
        # own worker container because a long render must not sit in front
        # of a login code.
        Queue("documents"),
    ),
    task_default_queue="default",
    # Route tasks to queues.
    #
    # Both forms where a task declares an explicit `name=`: Celery routes on
    # the task's NAME, so a module glob cannot match a task named
    # "build_document_pdf". The glob then covers anything added to that
    # module later without a name of its own.
    task_routes={
        "apps.api.tasks.email_tasks.send_magic_code_email": {"queue": "email_high"},
        "apps.api.tasks.email_tasks.send_invite_email": {"queue": "email_high"},
        # Both immediate and both security-relevant: a backup-address
        # code somebody is waiting on, and a notice that an account was
        # changed. email_low is for things that can arrive in a minute.
        "apps.api.tasks.email_tasks.send_backup_email_code_email": {"queue": "email_high"},
        "apps.api.tasks.email_tasks.send_security_notice_email": {"queue": "email_high"},
        "apps.api.tasks.email_tasks.*": {"queue": "email_low"},
    },
    # Rate limiting for email queues (SES limits)
    task_annotations={
        "apps.api.tasks.email_tasks.*": {"rate_limit": "10/s"},  # 10 emails per second
    },
)

# No scheduled tasks in P0a. Dunning runs, recurring invoices and the
# archive sweeps arrive with the features that need them (SCOPE P6);
# tests/test_celery_wiring.py asserts that whatever lands here is routed to
# a queue some container actually consumes.
celery_app.conf.beat_schedule = {}


import threading
import logging

_task_logger = logging.getLogger("celery.dispatch")


def _task_label(task):
    """A name for the log line that cannot itself throw.

    `task.name` only exists on a registered Celery task. When the thing
    handed to send_task_safe is NOT one -- which is exactly the case worth
    logging about -- reading `.name` raises inside the error handler, so the
    real failure is replaced by a second traceback from the code trying to
    report it. That happened for real: a decorator that had drifted onto the
    wrong function left process_asset a plain function, and the resulting
    AttributeError was reported only as a crash in this logger.
    """
    return getattr(task, "name", None) or getattr(task, "__name__", None) or repr(task)


def _dispatch_task(task, args, kwargs):
    """Actually send the task to Celery broker (runs in background thread)."""
    try:
        task.delay(*args, **kwargs)
    except (OperationalError, ConnectionError, OSError):
        try:
            with celery_app.producer_or_acquire() as producer:
                task.apply_async(args=args, kwargs=kwargs, producer=producer)
        except Exception:
            _task_logger.warning(
                "Failed to dispatch task %s after retry", _task_label(task), exc_info=True
            )
    except Exception:
        # exc_info because the label alone does not say WHY. "Failed to
        # dispatch task process_asset" with no traceback is what turned a
        # one-line bug into a production outage nobody could see the cause
        # of; the AttributeError underneath names it immediately.
        _task_logger.warning(
            "Failed to dispatch task %s", _task_label(task), exc_info=True
        )


def send_task_safe(task, *args, **kwargs):
    """Send a Celery task in a background thread so it never blocks the API response.

    Broker connections can take seconds (especially with pool_limit=0).
    This ensures the API returns immediately while the task is dispatched async.
    """
    thread = threading.Thread(
        target=_dispatch_task,
        args=(task, args, kwargs),
        daemon=True,
    )
    thread.start()

# Scheduled tasks knowingly left on the unconsumed `default` queue.
#
# Empty, and it must stay a deliberate, reviewed set rather than a place to
# park a broken wiring. tests/test_celery_wiring.py asserts every OTHER
# scheduled task reaches a consumed queue and pins this set exactly, so a
# name cannot be added quietly — which is how the same bug survived four
# separate discoveries in FreeFrame.
KNOWN_UNROUTED: set[str] = set()
