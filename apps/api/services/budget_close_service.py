"""Closing a budget at the production's end, with a review (B1.1 part 3).

A budget can be closed only when it is LOCKED. Closed, it refuses cost
entries, still-to-spend and finishing lines until it is reopened
(`budget_service.refuse_if_closed`, called from the cost router's one write
gate).

**The check.** `close_check` lists what does not add up, each item with the
ids the screen needs to link to it:

* lines OVER their plan (the forecast final cost is above the planned cost);
* lines UNDER their plan and NOT marked finished — what has been spent is
  below the plan and nobody has said the line is done, so the forecast is
  still holding money that will probably never go out;
* lines marked finished that still have an order OPEN;
* every open order (ordered, not yet invoiced), and their total;
* costs NOT ON A LINE (booked on a section only), and their total.

Then the budget as a whole, in one of three plain states:

* ``clean`` — nothing in any of the lists above;
* ``within_estimate`` — something is listed, but the total forecast is at or
  below the total plan (money moved between lines);
* ``over_estimate`` — the total forecast is above the total plan.

**The digest.** The check's content is hashed. Closing over discrepancies
needs `acknowledge: true`, a note of at least one sentence, and the digest
of the check the person actually read; if anything changed since — an
invoice booked in another tab — the digest no longer matches and the close
is refused, writing nothing. The person is asked to look again rather than
approving a list they never saw.

**Nothing here computes a forecast.** Every amount is read off
`budget_tracking_service.tracking`, the one forecast; the comparisons
(`>`, `<`) decide which list a line belongs in and produce no amount.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..models.project import Budget, BudgetActual, BudgetCommitment
from . import audit
from .budget_service import (
    BUDGET_AUDITED,
    BudgetError,
    active_lines,
    active_sections,
    counts_in_totals,
)
from .budget_tracking_service import (
    STATUS_CANCELLED,
    STATUS_OPEN,
    actuals_of,
    commitments_of,
    open_amount,
    tracking,
)

ZERO = Decimal("0")

STATE_CLEAN = "clean"
STATE_WITHIN = "within_estimate"
STATE_OVER = "over_estimate"
CLOSE_STATES: tuple[str, ...] = (STATE_CLEAN, STATE_WITHIN, STATE_OVER)

BUDGET_CLOSED = "budget.closed"
BUDGET_REOPENED = "budget.reopened"

#: "A note of at least one sentence". Measured as words rather than
#: characters, so "ok" and "........" are refused and "Approved by the
#: client." is not. Recorded as a default in the B1.1 report.
MIN_NOTE_WORDS = 3


class CloseRefused(BudgetError):
    """The close was refused. The router answers 409 with the sentence."""


def _money(value: Decimal) -> str:
    return str(value)


def _line_item(line, row) -> dict:
    return {
        "line_id": str(line.id),
        "section_id": str(line.section_id),
        "description": line.description,
        "estimate_cost": _money(row.estimate_cost),
        "actual": _money(row.actual),
        "committed_open": _money(row.committed_open),
        "efc": _money(row.efc),
        "variance": _money(row.variance),
    }


def _entry_item(kind: str, row, amount: Decimal) -> dict:
    return {
        "kind": kind,
        "id": str(row.id),
        "section_id": str(row.section_id),
        "line_id": str(row.line_id) if row.line_id is not None else None,
        "description": row.description,
        "reference": row.reference,
        "amount": _money(amount),
    }


def digest_of(check: dict) -> str:
    """A stable hash of everything the check lists. Order-independent of
    dict ordering; list order is the check's own (by position)."""
    payload = {key: value for key, value in check.items() if key != "digest"}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def close_check(db: Session, budget: Budget) -> dict:
    """What does not add up, in plain lists, plus the state and the digest."""
    result = tracking(db, budget)
    sections = active_sections(db, budget)
    by_id = {section.id: section for section in sections}
    # Proposed and declined extras are in no total, so in no discrepancy.
    lines = [
        line
        for line in active_lines(db, [section.id for section in sections])
        if counts_in_totals(line, by_id.get(line.section_id))
    ]

    over: list[dict] = []
    under: list[dict] = []
    finished_open: list[dict] = []
    for line in lines:
        row = result.lines.get(str(line.id))
        if row is None:  # pragma: no cover - every active line is priced
            continue
        if row.over_budget:
            over.append(_line_item(line, row))
        elif row.actual < row.estimate_cost and not row.finished:
            under.append(_line_item(line, row))
        if row.finished and row.committed_open > ZERO:
            finished_open.append(_line_item(line, row))

    open_orders: list[dict] = []
    for row in commitments_of(db, budget, include_voided=False):
        if row.status != STATUS_OPEN:
            continue
        rest = open_amount(db, row)
        if rest > ZERO:
            open_orders.append(_entry_item("commitment", row, rest))

    unallocated: list[dict] = []
    for row in commitments_of(db, budget, include_voided=False):
        if row.line_id is None and row.status != STATUS_CANCELLED:
            unallocated.append(_entry_item("commitment", row, row.amount))
    for row in actuals_of(db, budget, include_voided=False):
        if row.line_id is None:
            unallocated.append(_entry_item("actual", row, row.amount))

    total = result.budget
    has_discrepancies = bool(over or under or finished_open or open_orders or unallocated)
    if not has_discrepancies:
        state = STATE_CLEAN
    elif total.efc > total.estimate_cost:
        state = STATE_OVER
    else:
        state = STATE_WITHIN

    check = {
        "budget_id": str(budget.id),
        "state": state,
        "estimate_cost": _money(total.estimate_cost),
        "efc": _money(total.efc),
        "variance": _money(total.variance),
        "lines_over": over,
        "lines_under_not_finished": under,
        "finished_with_open_commitment": finished_open,
        "open_commitments": open_orders,
        "open_commitments_total": _money(total.committed_open),
        "unallocated": unallocated,
        "unallocated_total": _money(total.unallocated),
    }
    check["digest"] = digest_of(check)
    return check


def _check_note(note: Optional[str]) -> str:
    text = (note or "").strip()
    if len(text.split()) < MIN_NOTE_WORDS:
        raise CloseRefused(
            "Closing over what does not add up needs a note of at least one "
            "sentence saying why it is fine — who approved it, or what "
            "happens to the difference."
        )
    return text


def close(
    db: Session,
    budget: Budget,
    *,
    acknowledge: bool,
    digest: Optional[str],
    note: Optional[str],
    actor: audit.Actor,
) -> dict:
    """Close the budget, or refuse and write nothing.

    Refused when the budget is not locked, already closed, or — when the
    check is not clean — without the acknowledgement, without a note, or
    with a digest that no longer matches the check as it stands now.
    """
    if budget.closed_at is not None:
        raise CloseRefused(f"{budget.name} is already closed.")
    if budget.locked_at is None:
        raise CloseRefused(
            f"{budget.name} is not locked. Lock it first: closing is the "
            f"production's end, and the plan it is closed against has to be "
            f"the approved one."
        )

    check = close_check(db, budget)
    text = (note or "").strip() or None
    if check["state"] != STATE_CLEAN:
        if not acknowledge:
            raise CloseRefused(
                "This budget has things that do not add up. Read the list, "
                "tick that you approve closing it as it is, and say why in "
                "the note."
            )
        text = _check_note(note)
        if not digest or digest != check["digest"]:
            raise CloseRefused(
                "The budget changed since the list was shown — something was "
                "booked or finished in the meantime. Look at the list again "
                "before closing it."
            )

    before = audit.snapshot(budget, BUDGET_AUDITED)
    budget.closed_at = datetime.now(timezone.utc)
    budget.closed_by = actor.user_id
    budget.close_note = text
    db.flush()
    after = audit.snapshot(budget, BUDGET_AUDITED)
    # What was approved, as it stood: the check is part of the record.
    after["close_check"] = check
    before["close_check"] = None
    audit.record(
        db,
        actor,
        budget,
        BUDGET_CLOSED,
        before,
        after,
        reason=text,
        fields=tuple(BUDGET_AUDITED) + ("close_check",),
    )
    return check


def reopen(db: Session, budget: Budget, *, reason: Optional[str], actor: audit.Actor) -> None:
    """Clear the closed state. A reason is required and audited."""
    if budget.closed_at is None:
        raise CloseRefused(f"{budget.name} is not closed, so there is nothing to reopen.")
    text = (reason or "").strip()
    if not text:
        raise CloseRefused("Reopening a closed budget needs a reason.")
    before = audit.snapshot(budget, BUDGET_AUDITED)
    budget.closed_at = None
    budget.closed_by = None
    budget.close_note = None
    db.flush()
    audit.record(
        db,
        actor,
        budget,
        BUDGET_REOPENED,
        before,
        audit.snapshot(budget, BUDGET_AUDITED),
        reason=text,
        fields=BUDGET_AUDITED,
    )
