"""The periodic tax-identifier re-check.

**Two tasks, not one, and the split is the point.** `run_due` selects what is
due and dispatches one `check_one` per identifier. A single task that checked
all 25 inline would hold a worker slot for up to 200 seconds (eight-second
timeout each), and any failure part-way through would leave the rest of the
batch unchecked with no record of why.

**`FOR UPDATE SKIP LOCKED` is in the selection**, so a second beat process —
and there will be one, during every deploy — steps over the rows the first
has taken rather than checking them again. Doubling the load on a register
that publishes concurrency limits is how an installation gets its address
blocked; `IP_BLOCKED` is a documented VIES fault.

**No internet is a supported configuration.** Every result is `unavailable`,
the previous verified state is kept until `max_check_age_days` runs out, and
nothing breaks. That is why the retry schedule backs off to the ordinary
interval instead of retrying every six hours for ever.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)

#: Both named explicitly. Celery routes on a task's NAME, and the wiring
#: test resolves routing through Celery's own router for exactly this
#: reason: a `module.*` glob never matches a task that has a name of its own.
RUN_DUE_NAME = "tax_id_checks.run_due"
CHECK_ONE_NAME = "tax_id_checks.check_one"


@shared_task(name=RUN_DUE_NAME)
def run_due(limit: int = 0) -> dict:
    """Dispatch a check for every identifier that is due. Hourly.

    Returns a summary rather than nothing, so the beat log says what
    happened — "selected 0" and "the task never ran" look identical
    otherwise, and telling them apart matters the first time somebody asks
    why a check did not happen.
    """
    from ..database import SessionLocal
    from ..services import tax_id_checks

    db = SessionLocal()
    try:
        due = tax_id_checks.due_identifiers(
            db, limit=limit or tax_id_checks.DEFAULT_BATCH
        )
        ids = [str(row.id) for row in due]
        # Commit before dispatching, to release the row locks. Holding them
        # across the dispatch would make the next run skip rows this one has
        # already handed off.
        db.commit()
    finally:
        db.close()

    for tax_id in ids:
        check_one.delay(tax_id)
    logger.info("tax_id_checks.run_due dispatched %s checks", len(ids))
    return {"dispatched": len(ids), "tax_id_ids": ids}


@shared_task(name=CHECK_ONE_NAME)
def check_one(tax_id_id: str, trigger_kind: str = "scheduled") -> dict:
    """Check one identifier and record the answer.

    No actor, so no audit row for the check itself: there is no user to
    attribute it to, and inventing one would put a person's name on
    something they did not do. A CHANGE still writes its event — that is
    about the identifier, not about who looked.
    """
    import uuid

    from ..database import SessionLocal
    from ..models.party import Party, PartyTaxId
    from ..services import tax_id_checks

    db = SessionLocal()
    try:
        tax_id = db.get(PartyTaxId, uuid.UUID(tax_id_id))
        if tax_id is None or not tax_id.is_active:
            # Retired or deleted between selection and execution. Not an
            # error: the schedule is allowed to be slightly stale.
            return {"skipped": tax_id_id, "reason": "gone or retired"}
        party = db.get(Party, tax_id.party_id)
        if party is None:  # pragma: no cover - FK makes this unreachable
            return {"skipped": tax_id_id, "reason": "no party"}

        result = tax_id_checks.run_check(db, tax_id=tax_id, party=party)
        trigger = (
            "retry" if (tax_id.unavailable_streak or 0) > 0 else trigger_kind
        )
        tax_id_checks.record_check(
            db,
            tax_id=tax_id,
            party=party,
            result=result,
            trigger_kind=trigger,
            actor=None,
        )
        db.commit()
        return {"tax_id_id": tax_id_id, "status": result.status}
    except Exception:
        db.rollback()
        # Logged and re-raised: Celery's own retry and error reporting are
        # better than a silent swallow, and a check that failed to RECORD is
        # a different problem from a registry that did not answer.
        logger.exception("tax_id_checks.check_one failed for %s", tax_id_id)
        raise
    finally:
        db.close()
