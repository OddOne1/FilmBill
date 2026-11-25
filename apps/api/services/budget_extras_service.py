"""Extras: charges agreed after the quote (B1.1 part 4).

An extra is how the price changes after the lock. It is **a budget line in
the budget's one reserved Extras section**, not a new calculation path: the
engine prices it like any line, the tax grouping groups it like any line,
and costs can be booked on it like any line once it is accepted.

What makes it an extra is its status:

* `proposed` — the customer has been told and has not answered. Listed, and
  out of every total.
* `accepted` — it counts: in the budget's totals, its tax grouping and the
  tracking baseline (`budget_service.counts_in_totals`). `extra_decided_on`
  is the day.
* `declined` — out of every total again. A withdrawn extra is declined with
  a note; nothing is deleted.

**The lock.** The Extras section stays editable on a LOCKED budget — that is
its whole purpose — while every other estimate edit is still refused by
`budget_service.refuse_if_locked`. That is why these writes have their own
gate (`writable_for_extras`) and do not go through the sheet's `_editable`:
the sheet's gate stays exactly as strict as it was. Extras do need the budget
NOT closed: a closed production takes no new charges until it is reopened.
"""

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..billing.calc import PERIOD_UNITS
from ..billing.catalog.kinds import ITEM_KINDS, KIND_OTHER
from ..models.catalog import Item
from ..models.project import (
    EXTRA_ACCEPTED,
    EXTRA_DECLINED,
    EXTRA_PROPOSED,
    Budget,
    BudgetLine,
    BudgetSection,
)
from . import audit
from .budget_service import (
    LINE_AUDITED,
    BudgetError,
    extras_section,
    line_defaults_from_item,
    lock_for_write,
    next_line_position,
    next_section_position,
    refuse_if_closed,
    standard_tax_ref,
)

EXTRAS_SECTION_NAME = "Extras"

EXTRA_PROPOSED_EVENT = "budget_extra.proposed"
EXTRA_UPDATED_EVENT = "budget_extra.updated"
EXTRA_ACCEPTED_EVENT = "budget_extra.accepted"
EXTRA_DECLINED_EVENT = "budget_extra.declined"
SECTION_CREATED = "budget_section.created"

#: The sentence every refusal to touch the Extras section through the sheet
#: ends with.
MANAGED_ON_THE_TAB = "Extras are managed on the Extras tab."


class ExtraError(BudgetError):
    """A refusal about an extra. 409, or 422 where the router says so."""


def writable_for_extras(db: Session, budget: Budget) -> Budget:
    """The row lock and the CLOSED check — and deliberately no LOCK check."""
    row = lock_for_write(db, budget)
    refuse_if_closed(
        row, what="extras cannot be proposed, changed, accepted or declined"
    )
    return row


def ensure_extras_section(
    db: Session, membership, budget: Budget, *, actor: audit.Actor
) -> BudgetSection:
    """The Extras section, created on first use. Top level, after the rest."""
    section = extras_section(db, budget)
    if section is not None:
        return section
    section = BudgetSection(
        company_id=membership.company_id,
        budget_id=budget.id,
        code=None,
        name=EXTRAS_SECTION_NAME,
        parent_id=None,
        position=next_section_position(db, budget, None) + 1000,
        is_active=True,
        is_extras=True,
    )
    db.add(section)
    db.flush()
    audit.record(
        db,
        actor,
        section,
        SECTION_CREATED,
        None,
        {"name": section.name, "is_extras": True},
        reason="the Extras section, created with the first extra",
    )
    return section


def _check_kind_and_unit(kind: Optional[str], period_unit: Optional[str]) -> None:
    if kind is not None and kind not in ITEM_KINDS:
        raise ExtraError(f"{kind!r} is not an item kind.")
    if period_unit is not None and period_unit not in PERIOD_UNITS:
        raise ExtraError(f"{period_unit!r} is not a period unit.")


def propose(
    db: Session, membership, budget: Budget, body, *, item: Optional[Item], actor: audit.Actor
) -> BudgetLine:
    """A new extra, `proposed`. Its rates come from the catalog item when
    there is one and no price was typed — copied, like any line's."""
    _check_kind_and_unit(body.kind, body.period_unit)
    section = ensure_extras_section(db, membership, budget, actor=actor)
    defaults: dict = {}
    if item is not None:
        defaults = line_defaults_from_item(
            db, membership, item, period_unit=body.period_unit
        )
    given = body.model_dump(exclude_unset=True)
    row = BudgetLine(
        company_id=membership.company_id,
        section_id=section.id,
        position=next_line_position(db, section),
        item_id=body.item_id,
        description=(body.description or "").strip() or defaults.get("description") or "",
        kind=given.get("kind") or defaults.get("kind") or KIND_OTHER,
        quantity=body.quantity,
        period_value=body.period_value,
        period_unit=given.get("period_unit") or defaults.get("period_unit") or "flat",
        factor=body.factor,
        factor_source=defaults.get("factor_source") or "manual",
        unit_price=(
            given["unit_price"]
            if given.get("unit_price") is not None
            else defaults.get("unit_price", Decimal("0"))
        ),
        unit_cost=(
            given["unit_cost"]
            if given.get("unit_cost") is not None
            else defaults.get("unit_cost", Decimal("0"))
        ),
        # An extra is taxed like the quote's standard lines unless the item
        # or the request says otherwise (a default, recorded in the report).
        tax_ref=(
            given["tax_ref"]
            if given.get("tax_ref") is not None
            else defaults.get("tax_ref") or standard_tax_ref(db, membership.company_id)
        ),
        tags=[],
        allow_negative=False,
        is_active=True,
        extra_status=EXTRA_PROPOSED,
        extra_note=(body.note or "").strip() or None,
        apply_production_fee=body.apply_production_fee,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor,
        row,
        EXTRA_PROPOSED_EVENT,
        None,
        audit.snapshot(row, LINE_AUDITED),
        fields=LINE_AUDITED,
    )
    return row


def update(db: Session, row: BudgetLine, body, *, actor: audit.Actor) -> BudgetLine:
    """Change a proposed or accepted extra. Audited with before and after —
    for an accepted one that IS the record of a price changed after the
    customer agreed."""
    if row.extra_status == EXTRA_DECLINED:
        raise ExtraError(
            f"“{row.description}” was declined and stays as it was declined. "
            f"Propose a new extra instead."
        )
    changes = body.model_dump(exclude_unset=True)
    _check_kind_and_unit(changes.get("kind"), changes.get("period_unit"))
    before = audit.snapshot(row, LINE_AUDITED)
    for name, value in changes.items():
        if name == "note":
            row.extra_note = (value or "").strip() or None
        elif value is not None:
            setattr(row, name, value)
    db.flush()
    audit.record(
        db,
        actor,
        row,
        EXTRA_UPDATED_EVENT,
        before,
        audit.snapshot(row, LINE_AUDITED),
        reason=(
            "changed after the customer accepted it"
            if row.extra_status == EXTRA_ACCEPTED
            else None
        ),
        fields=LINE_AUDITED,
    )
    return row


def _decide(
    db: Session,
    row: BudgetLine,
    status: str,
    event: str,
    *,
    decided_on: Optional[date],
    note: Optional[str],
    actor: audit.Actor,
) -> BudgetLine:
    before = audit.snapshot(row, LINE_AUDITED)
    row.extra_status = status
    row.extra_decided_on = decided_on or date.today()
    if note is not None and note.strip():
        row.extra_note = note.strip()
    db.flush()
    audit.record(
        db,
        actor,
        row,
        event,
        before,
        audit.snapshot(row, LINE_AUDITED),
        reason=(note or "").strip() or None,
        fields=LINE_AUDITED,
    )
    return row


def accept(db: Session, row: BudgetLine, *, decided_on, note, actor) -> BudgetLine:
    if row.extra_status == EXTRA_ACCEPTED:
        raise ExtraError(f"“{row.description}” is already accepted.")
    return _decide(
        db, row, EXTRA_ACCEPTED, EXTRA_ACCEPTED_EVENT,
        decided_on=decided_on, note=note, actor=actor,
    )


def decline(db: Session, row: BudgetLine, *, decided_on, note, actor) -> BudgetLine:
    """Decline — or withdraw — an extra. Needs a note: what was agreed.

    An accepted extra with costs already booked on it cannot be declined:
    the costs would be left on a line that no longer counts.
    """
    if row.extra_status == EXTRA_DECLINED:
        raise ExtraError(f"“{row.description}” is already declined.")
    if not (note or "").strip():
        raise ExtraError(
            "Declining an extra needs a note — what the customer said, or why "
            "it was withdrawn."
        )
    from .budget_tracking_service import line_has_live_costs

    if line_has_live_costs(db, row):
        raise ExtraError(
            f"“{row.description}” has orders or invoices booked on it. Void "
            f"or move them first — a declined extra is out of every total, "
            f"and its costs would be left on a line that no longer counts."
        )
    return _decide(
        db, row, EXTRA_DECLINED, EXTRA_DECLINED_EVENT,
        decided_on=decided_on, note=note, actor=actor,
    )
