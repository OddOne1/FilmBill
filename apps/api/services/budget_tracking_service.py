"""Commitments, actuals, ETCs — the rules, the forecast, and the CSV.

Everything that decides something about a cost entry is here. The router does
lookups, permissions, response shapes and the commit.

Six things worth reading before changing this file.

**1. The forecast arithmetic is not here.** It is in
`billing/budget/tracking.py`, pure, and this module's job is to gather the
stored rows into that module's input and label its output. `exposure`,
`committed_open`, the `remaining` default rule and the forecast margin are
defined there and nowhere else (CLAUDE.md rule 2).

**2. The estimate side comes from the ONE calculation.**
`budget_service.calc_result` prices the budget, and `tracking` reads
`cost_net` per line and `subtotal` per section straight out of that result.
It does not re-derive a cost from `unit_cost × quantity × period × factor` —
a tracking view that priced the budget a second time could disagree with the
sheet by a cent and nobody could say which was right.

**3. A locked budget still takes cost entries.** This is the decision, not an
oversight: a lock freezes the ESTIMATE — sections, lines, globals,
adjustments — because the estimate is the baseline the actuals are measured
against. Commitments, actuals and ETCs are measurements OF that baseline, and
a lock that stopped them would mean the only budget worth tracking is one
nobody has approved. So the write handlers in `routers/budget_tracking.py`
deliberately do **not** call `budget_service.refuse_if_locked`, and
`test_budget_tracking_api.py` asserts it both ways: an estimate edit on a
locked budget is refused, an actual on the same budget is accepted.

**4. A commitment closes and reopens by itself.** When the non-voided actuals
linked to it reach its amount, `_settle` closes it; when one of them is
voided, `_settle` reopens it. Both write an audit event. The comparison is
`tracking.closes_commitment` — the same one `committed_open` makes — because
a second spelling of "is this order settled" is a second answer.

**5. The duplicate warning never blocks.** Creating an entry whose
`(payee, reference, amount)` matches a non-voided entry in the same budget
returns a `duplicates` list beside the created row. It is a warning and the
row is already written: a supplier really can invoice the same amount twice
in a month, and an import or a form that refused the second one would be
wrong in a way the user could not get past. The CSV import is the one place
a duplicate is *held back* rather than warned about, because there nobody is
looking at the row as it is written.

**6. Nothing is ever deleted.** `void` sets `voided_at` and `void_reason`
together — the service refuses one without the other — and a voided entry
stays in the ledger and leaves every number. A correction is a new entry
plus a void, which is why the amount is constrained positive in the schema
as well.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence


from sqlalchemy.orm import Session

from ..billing.budget import (
    Actual as TrackActual,
    BudgetInput,
    Commitment as TrackCommitment,
    LineInput,
    SectionInput,
    STATUS_CANCELLED,
    STATUS_CLOSED,
    STATUS_OPEN,
    closes_commitment,
    is_finished,
    track_budget,
)
from ..billing.budget.csv_actuals import (
    ACTION_CREATE,
    ACTION_DUPLICATE,
    ActualImportContext,
    ActualImportPlan,
    ActualImportRow,
    de_amount,
    de_date,
    duplicate_key,
    export_actuals_csv,
    plan_actuals_from_file,
)
from ..models.party import Party
from ..models.project import (
    Budget,
    BudgetActual,
    BudgetCommitment,
    BudgetLine,
    BudgetSection,
)
from . import audit
from .budget_service import (
    BudgetError,
    active_lines,
    active_sections,
    calc_result,
    counts_in_totals,
)

ZERO = Decimal("0")


class TrackingError(BudgetError):
    """A refusal a user can act on. The router answers 409 with the sentence."""


class TrackingNotFound(TrackingError):
    """Something the request named is not in this company. The router: 404.

    Its own type rather than a prose match, so the router can answer 404 for
    "a party from another company" and 409 for "that line is not in that
    section" without reading the sentence (CLAUDE.md rule 6: cross-company
    access returns 404, never 403 — a 403 would confirm the row exists).
    """


# ─── What an audit row records ──────────────────────────────────────────────

COMMITMENT_AUDITED: tuple[str, ...] = (
    "budget_id",
    "section_id",
    "line_id",
    "party_id",
    "payee_text",
    "description",
    "reference",
    "amount",
    "commitment_date",
    "status",
    "notes",
    "voided_at",
    "void_reason",
)
ACTUAL_AUDITED: tuple[str, ...] = (
    "budget_id",
    "section_id",
    "line_id",
    "commitment_id",
    "party_id",
    "payee_text",
    "description",
    "reference",
    "amount",
    "incurred_on",
    "paid_on",
    "notes",
    "voided_at",
    "void_reason",
)
LINE_ETC_AUDITED: tuple[str, ...] = ("etc",)

COMMITMENT_CREATED = "budget_commitment.created"
COMMITMENT_UPDATED = "budget_commitment.updated"
COMMITMENT_CLOSED = "budget_commitment.closed"
COMMITMENT_CANCELLED = "budget_commitment.cancelled"
COMMITMENT_VOIDED = "budget_commitment.voided"
#: The automatic reopen. A separate action from `updated` because "this order
#: is owed again" is a fact somebody reading the log is looking for, and it
#: would be invisible as a status diff among twelve other fields.
COMMITMENT_REOPENED = "budget_commitment.reopened"
ACTUAL_CREATED = "budget_actual.created"
ACTUAL_UPDATED = "budget_actual.updated"
ACTUAL_VOIDED = "budget_actual.voided"
LINE_ETC_SET = "budget_line.etc_set"
#: B1.1 "Mark finished". One line: its own event with before/after. A
#: section's worth: ONE event with counts and the ids, on the section.
LINE_FINISHED = "budget_line.finished"
LINE_REOPENED = "budget_line.reopened"
LINE_FINISHED_BULK = "budget_line.finished_bulk"
LINE_REOPENED_BULK = "budget_line.reopened_bulk"
ACTUALS_IMPORT_APPLIED = "budget.actuals_import_applied"


# ─── Validation ─────────────────────────────────────────────────────────────


def check_amount(amount: Optional[Decimal]) -> Decimal:
    """Positive, or a refusal naming why a negative row is not the answer."""
    if amount is None:
        raise TrackingError("This entry has no amount. Enter the amount.")
    if amount <= ZERO:
        raise TrackingError(
            f"the amount is {amount}, and an entry is always positive. To "
            f"correct one, add the right entry and void the wrong one — both "
            f"stay in the ledger and both are audited. A negative row would "
            f"make every total depend on which rows you happened to include."
        )
    return amount


def check_payee(party_id: Optional[uuid.UUID], payee_text: Optional[str]) -> None:
    """A party or free text, never both.

    Neither is also fine: a cost with no payee yet is a normal half-filled
    row. Both at once is not — two names for one payee is a row nobody can
    reconcile, and the screen would have to choose which to print.
    """
    if party_id is not None and (payee_text or "").strip():
        raise TrackingError(
            "an entry names a supplier either by picking a party or by typing "
            "a name, not both. Clear the typed name to use the party, or "
            "clear the party to use the name."
        )


def section_in_budget(
    db: Session, budget: Budget, section_id: uuid.UUID
) -> BudgetSection:
    row = (
        db.query(BudgetSection)
        .filter(
            BudgetSection.id == section_id,
            BudgetSection.budget_id == budget.id,
            BudgetSection.company_id == budget.company_id,
        )
        .first()
    )
    if row is None:
        raise TrackingNotFound("Section not found")
    return row


def line_in_section(
    db: Session, section: BudgetSection, line_id: Optional[uuid.UUID]
) -> Optional[BudgetLine]:
    """The line, checked to be IN that section. `None` for an unallocated cost.

    The two-table condition the migration deliberately does not enforce.
    Refused as a 409 and not a 404: the line exists, it is simply somewhere
    else in the sheet, and the fix is to change the section or the line.
    """
    if line_id is None:
        if section.is_extras:
            # B1.1: a cost in the Extras section belongs to one extra.
            raise TrackingError(
                "A cost in the Extras section is booked on the accepted extra "
                "it belongs to. Pick the extra."
            )
        return None
    row = (
        db.query(BudgetLine)
        .filter(
            BudgetLine.id == line_id,
            BudgetLine.company_id == section.company_id,
        )
        .first()
    )
    if row is None:
        raise TrackingNotFound("Budget line not found")
    if row.section_id != section.id:
        raise TrackingError(
            f"“{row.description}” is not in that section, and a cost is "
            f"booked on a line of the section it is charged to. Pick the "
            f"section the line is in, or pick a line from this one."
        )
    if not counts_in_totals(row, section):
        raise TrackingError(
            f"“{row.description}” is an extra the customer has not accepted "
            f"({row.extra_status}). Costs are booked on accepted extras only "
            f"— accept it first, or book the cost elsewhere."
        )
    return row


def line_has_live_costs(db: Session, line: BudgetLine) -> bool:
    """Any order (not cancelled) or invoice on this line that is not voided."""
    order = (
        db.query(BudgetCommitment.id)
        .filter(
            BudgetCommitment.line_id == line.id,
            BudgetCommitment.voided_at.is_(None),
            BudgetCommitment.status != STATUS_CANCELLED,
        )
        .first()
    )
    if order is not None:
        return True
    invoice = (
        db.query(BudgetActual.id)
        .filter(BudgetActual.line_id == line.id, BudgetActual.voided_at.is_(None))
        .first()
    )
    return invoice is not None


def party_in_company(
    db: Session, budget: Budget, party_id: Optional[uuid.UUID]
) -> Optional[Party]:
    """The party, checked to be this company's. 404 when it is not.

    404 and not 403, per CLAUDE.md rule 6: a 403 would confirm that the id
    names a real party in somebody else's company.
    """
    if party_id is None:
        return None
    row = (
        db.query(Party)
        .filter(Party.id == party_id, Party.company_id == budget.company_id)
        .first()
    )
    if row is None:
        raise TrackingNotFound("Party not found")
    return row


def commitment_in_budget(
    db: Session, budget: Budget, commitment_id: Optional[uuid.UUID]
) -> Optional[BudgetCommitment]:
    """The commitment, checked to be in THIS budget. `None` for no link.

    A commitment from another budget of the same project is the realistic
    mistake — two scenarios, "with the crane" and "without" — and linking an
    invoice across them would settle an order on a sheet nobody is tracking.
    """
    if commitment_id is None:
        return None
    row = (
        db.query(BudgetCommitment)
        .filter(
            BudgetCommitment.id == commitment_id,
            BudgetCommitment.company_id == budget.company_id,
        )
        .first()
    )
    if row is None:
        raise TrackingNotFound("Commitment not found")
    if row.budget_id != budget.id:
        raise TrackingError(
            "that commitment belongs to a different budget. An invoice can "
            "only settle an order on the same sheet — linking it across two "
            "scenarios of one project would reduce an open amount nobody is "
            "tracking."
        )
    if row.voided_at is not None:
        raise TrackingError(
            "that commitment has been voided, so there is nothing left to "
            "settle. Link the invoice to another order, or leave it "
            "unlinked — an unlinked actual still counts in full."
        )
    return row


def check_void_reason(reason: Optional[str]) -> str:
    """A void always says why. The two columns move together."""
    text = (reason or "").strip()
    if not text:
        raise TrackingError(
            "voiding an entry needs a reason — it stays in the ledger for "
            "ever and the reason is the only thing that explains it to "
            "whoever reads it next."
        )
    return text[:500]


# ─── Reading ────────────────────────────────────────────────────────────────


def commitments_of(
    db: Session, budget: Budget, *, include_voided: bool = True
) -> list[BudgetCommitment]:
    query = db.query(BudgetCommitment).filter(
        BudgetCommitment.budget_id == budget.id,
        BudgetCommitment.company_id == budget.company_id,
    )
    if not include_voided:
        query = query.filter(BudgetCommitment.voided_at.is_(None))
    return query.order_by(
        BudgetCommitment.commitment_date.desc(), BudgetCommitment.created_at.desc()
    ).all()


def actuals_of(
    db: Session, budget: Budget, *, include_voided: bool = True
) -> list[BudgetActual]:
    query = db.query(BudgetActual).filter(
        BudgetActual.budget_id == budget.id,
        BudgetActual.company_id == budget.company_id,
    )
    if not include_voided:
        query = query.filter(BudgetActual.voided_at.is_(None))
    return query.order_by(
        BudgetActual.incurred_on.desc(), BudgetActual.created_at.desc()
    ).all()


def filtered(
    query,
    model,
    *,
    section_id: Optional[uuid.UUID] = None,
    line_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    payee: Optional[str] = None,
    voided: Optional[bool] = None,
    date_column=None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
):
    """The filters every ledger list shares. One place, so they agree.

    `voided` is a tri-state on purpose: `None` is "both", which is what the
    ledger shows when "show voided" is ticked, and the default in the router
    is `False` — the numbers' view. A boolean with a default of False would
    have made "both" unreachable.

    `payee` searches the typed name only, not the party's. Searching across a
    join would need the parties table in every filter and the screen already
    has the party in its own dropdown.
    """
    if section_id is not None:
        query = query.filter(model.section_id == section_id)
    if line_id is not None:
        query = query.filter(model.line_id == line_id)
    if party_id is not None:
        query = query.filter(model.party_id == party_id)
    if payee and payee.strip():
        needle = f"%{payee.strip().lower()}%"
        from sqlalchemy import func as sa_func

        query = query.filter(sa_func.lower(model.payee_text).like(needle))
    if voided is True:
        query = query.filter(model.voided_at.isnot(None))
    elif voided is False:
        query = query.filter(model.voided_at.is_(None))
    if date_column is not None:
        if date_from is not None:
            query = query.filter(date_column >= date_from)
        if date_to is not None:
            query = query.filter(date_column <= date_to)
    return query


# ─── The forecast ───────────────────────────────────────────────────────────


def track_commitment_of(row: BudgetCommitment) -> TrackCommitment:
    return TrackCommitment(
        amount=row.amount,
        id=str(row.id),
        status=row.status,
        voided=row.voided_at is not None,
    )


def track_actual_of(row: BudgetActual) -> TrackActual:
    return TrackActual(
        amount=row.amount,
        id=str(row.id),
        voided=row.voided_at is not None,
        commitment_id=None if row.commitment_id is None else str(row.commitment_id),
    )


def open_amount(db: Session, row: BudgetCommitment) -> Decimal:
    """What is still owed on this ONE commitment, for its ledger row.

    Through `committed_open_of` and not a subtraction written here: the
    floor at zero and the status rules live there, and a second spelling of
    them would make a row disagree with the total above it.
    """
    from ..billing.budget import committed_open_of

    return committed_open_of(
        (track_commitment_of(row),),
        tuple(track_actual_of(entry) for entry in linked_actuals(db, row)),
    )


def tracking(db: Session, budget: Budget):
    """`TrackingResult` for every line, section and the budget.

    Three queries and one calculation: the engine prices the budget once
    (point 2 of the module docstring), the commitments and the actuals come
    back in one query each, and the arithmetic is the pure module's.

    Voided entries are passed IN rather than filtered out here, because
    `tracking.py` is the thing that decides what a void means — and a filter
    here would be a second answer to that, one that `committed_open` could
    not see.
    """
    result, _ = calc_result(db, budget)
    # B1.1: the forecast covers what the budget's document covers —
    # proposed and declined extras are not in it, and an Extras section
    # with no accepted extra is not either.
    all_sections = active_sections(db, budget)
    section_by_id = {section.id: section for section in all_sections}
    lines = [
        line
        for line in active_lines(db, [section.id for section in all_sections])
        if counts_in_totals(line, section_by_id.get(line.section_id))
    ]
    with_lines = {line.section_id for line in lines}
    sections = [
        section
        for section in all_sections
        if not section.is_extras or section.id in with_lines
    ]

    commitments = commitments_of(db, budget)
    actuals = actuals_of(db, budget)

    by_line_commitments: dict[uuid.UUID, list[BudgetCommitment]] = {}
    by_section_commitments: dict[uuid.UUID, list[BudgetCommitment]] = {}
    for row in commitments:
        if row.line_id is not None:
            by_line_commitments.setdefault(row.line_id, []).append(row)
        else:
            by_section_commitments.setdefault(row.section_id, []).append(row)

    by_line_actuals: dict[uuid.UUID, list[BudgetActual]] = {}
    by_section_actuals: dict[uuid.UUID, list[BudgetActual]] = {}
    for row in actuals:
        if row.line_id is not None:
            by_line_actuals.setdefault(row.line_id, []).append(row)
        else:
            by_section_actuals.setdefault(row.section_id, []).append(row)

    lines_by_section: dict[uuid.UUID, list[BudgetLine]] = {}
    for line in lines:
        lines_by_section.setdefault(line.section_id, []).append(line)

    children_by_parent: dict[Optional[uuid.UUID], list[BudgetSection]] = {}
    for section in sections:
        children_by_parent.setdefault(section.parent_id, []).append(section)

    # The engine's own numbers, indexed. `result.lines` is keyed by the line
    # id as a string; the groups need a walk.
    groups: dict[str, Any] = {}

    def walk(children) -> None:
        for child in children:
            if getattr(child, "type", None) == "group":
                groups[child.id] = child
                walk(child.children)

    walk(result.children)

    def section_input(section: BudgetSection) -> SectionInput:
        group = groups.get(str(section.id))
        own_lines = []
        for line in lines_by_section.get(section.id, []):
            priced = result.lines.get(str(line.id))
            own_lines.append(
                LineInput(
                    # `cost_net` is the engine's cost for this line — the
                    # estimate the forecast is measured against. A line the
                    # engine did not price (it cannot happen: every active
                    # line is in the input) would forecast against zero, so
                    # it falls back to zero rather than to the raw unit cost,
                    # which would be a second, wrong arithmetic.
                    estimate_cost=priced.cost_net if priced is not None else ZERO,
                    id=str(line.id),
                    commitments=tuple(
                        track_commitment_of(row)
                        for row in by_line_commitments.get(line.id, [])
                    ),
                    actuals=tuple(
                        track_actual_of(row)
                        for row in by_line_actuals.get(line.id, [])
                    ),
                    etc=line.etc,
                )
            )
        return SectionInput(
            id=str(section.id),
            lines=tuple(own_lines),
            children=tuple(
                section_input(child)
                for child in children_by_parent.get(section.id, [])
            ),
            commitments=tuple(
                track_commitment_of(row)
                for row in by_section_commitments.get(section.id, [])
            ),
            actuals=tuple(
                track_actual_of(row)
                for row in by_section_actuals.get(section.id, [])
            ),
            # The estimate-phase client price for this section, from the one
            # calculation. The forecast margin is measured against it and it
            # does not move when costs do.
            price=group.subtotal if group is not None else None,
        )

    return track_budget(
        BudgetInput(
            id=str(budget.id),
            sections=tuple(
                section_input(section)
                for section in children_by_parent.get(None, [])
            ),
            price=result.net,
        )
    )


# ─── Duplicates ─────────────────────────────────────────────────────────────


def duplicates_of(
    db: Session,
    budget: Budget,
    *,
    party_id: Optional[uuid.UUID],
    payee_text: Optional[str],
    reference: Optional[str],
    amount: Decimal,
    exclude_actual: Optional[uuid.UUID] = None,
    exclude_commitment: Optional[uuid.UUID] = None,
) -> list[dict]:
    """Non-voided entries in this budget with the same payee, reference and
    amount. **A warning, never a refusal** (point 5 of the module docstring).

    Both tables, because the realistic duplicate is an invoice entered twice
    and an invoice entered once as a commitment and once as an actual is the
    other half of the same mistake.

    The key is the CSV module's `duplicate_key`, so the form's warning and
    the import's held-back row fire on exactly the same condition.
    """
    payee = _payee_name(db, budget, party_id, payee_text)
    key = duplicate_key(payee, reference or "", amount)

    found: list[dict] = []
    for row in actuals_of(db, budget, include_voided=False):
        if exclude_actual is not None and row.id == exclude_actual:
            continue
        if _key_of(db, budget, row) == key:
            found.append(
                {
                    "kind": "actual",
                    "id": row.id,
                    "amount": row.amount,
                    "reference": row.reference,
                    "payee": _payee_name(db, budget, row.party_id, row.payee_text),
                    "date": row.incurred_on,
                    "description": row.description,
                }
            )
    for row in commitments_of(db, budget, include_voided=False):
        if exclude_commitment is not None and row.id == exclude_commitment:
            continue
        if row.status == STATUS_CANCELLED:
            continue
        if _key_of(db, budget, row) == key:
            found.append(
                {
                    "kind": "commitment",
                    "id": row.id,
                    "amount": row.amount,
                    "reference": row.reference,
                    "payee": _payee_name(db, budget, row.party_id, row.payee_text),
                    "date": row.commitment_date,
                    "description": row.description,
                }
            )
    return found


def _payee_name(
    db: Session,
    budget: Budget,
    party_id: Optional[uuid.UUID],
    payee_text: Optional[str],
) -> str:
    """The payee as one string: the party's name, or the typed text."""
    if party_id is not None:
        party = (
            db.query(Party)
            .filter(Party.id == party_id, Party.company_id == budget.company_id)
            .first()
        )
        if party is not None:
            return party.name
    return (payee_text or "").strip()


def _key_of(db: Session, budget: Budget, row) -> tuple:
    return duplicate_key(
        _payee_name(db, budget, row.party_id, row.payee_text),
        row.reference or "",
        row.amount,
    )


# ─── Commitments ────────────────────────────────────────────────────────────


def create_commitment(
    db: Session,
    membership,
    budget: Budget,
    *,
    actor: audit.Actor,
    section_id: uuid.UUID,
    line_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    payee_text: Optional[str] = None,
    description: Optional[str] = None,
    reference: Optional[str] = None,
    amount: Decimal,
    commitment_date: date,
    notes: Optional[str] = None,
) -> BudgetCommitment:
    """One order. No commit — the caller owns the transaction."""
    check_amount(amount)
    check_payee(party_id, payee_text)
    section = section_in_budget(db, budget, section_id)
    line = line_in_section(db, section, line_id)
    party_in_company(db, budget, party_id)

    row = BudgetCommitment(
        company_id=membership.company_id,
        budget_id=budget.id,
        section_id=section.id,
        line_id=None if line is None else line.id,
        party_id=party_id,
        payee_text=(payee_text or "").strip() or None,
        description=(description or "").strip() or None,
        reference=(reference or "").strip() or None,
        amount=amount,
        commitment_date=commitment_date,
        status=STATUS_OPEN,
        notes=notes,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor,
        row,
        COMMITMENT_CREATED,
        None,
        audit.snapshot(row, COMMITMENT_AUDITED),
        fields=COMMITMENT_AUDITED,
    )
    return row


def update_commitment(
    db: Session,
    budget: Budget,
    row: BudgetCommitment,
    *,
    actor: audit.Actor,
    changes: dict,
) -> BudgetCommitment:
    """Change an order. The status is not changed here — `close` and `cancel`
    are their own actions, so that "this order was withdrawn" is one audit
    event and not a status diff inside twelve other fields."""
    if row.voided_at is not None:
        raise TrackingError(
            "that commitment has been voided and cannot be changed. Add a "
            "new one — the voided row stays in the ledger with its reason."
        )
    before = audit.snapshot(row, COMMITMENT_AUDITED)

    if "amount" in changes:
        check_amount(changes["amount"])
    party_id = changes.get("party_id", row.party_id)
    payee_text = changes.get("payee_text", row.payee_text)
    check_payee(party_id, payee_text)
    if "party_id" in changes:
        party_in_company(db, budget, changes["party_id"])

    section = section_in_budget(db, budget, changes.get("section_id", row.section_id))
    if "section_id" in changes or "line_id" in changes:
        line = line_in_section(db, section, changes.get("line_id", row.line_id))
        row.section_id = section.id
        row.line_id = None if line is None else line.id

    for field_name in (
        "party_id",
        "payee_text",
        "description",
        "reference",
        "amount",
        "commitment_date",
        "notes",
    ):
        if field_name in changes:
            setattr(row, field_name, changes[field_name])

    db.flush()
    # The amount may have moved past or back behind what is invoiced against
    # it, so the status is re-derived rather than left where it was.
    _settle(db, row, actor=actor, automatic=True)
    audit.record(
        db,
        actor,
        row,
        COMMITMENT_UPDATED,
        before,
        audit.snapshot(row, COMMITMENT_AUDITED),
        fields=COMMITMENT_AUDITED,
    )
    return row


def linked_actuals(db: Session, row: BudgetCommitment) -> list[BudgetActual]:
    return (
        db.query(BudgetActual)
        .filter(
            BudgetActual.commitment_id == row.id,
            BudgetActual.company_id == row.company_id,
        )
        .all()
    )


def _settle(
    db: Session,
    row: BudgetCommitment,
    *,
    actor: audit.Actor,
    automatic: bool,
) -> None:
    """Close or reopen `row` according to the actuals linked to it.

    The condition is `tracking.closes_commitment`, the same comparison
    `committed_open` makes. A cancelled or voided commitment is left alone:
    cancelling is a decision and an invoice arriving afterwards must not
    quietly undo it.

    `automatic=True` is the only caller that writes the `reopened` event;
    the manual close and cancel write their own.
    """
    if row.voided_at is not None or row.status == STATUS_CANCELLED:
        return
    actuals = tuple(
        track_actual_of(entry) for entry in linked_actuals(db, row)
    )
    settled = closes_commitment(track_commitment_of(row), actuals)

    if settled and row.status == STATUS_OPEN:
        before = audit.snapshot(row, COMMITMENT_AUDITED)
        row.status = STATUS_CLOSED
        db.flush()
        audit.record(
            db,
            actor,
            row,
            COMMITMENT_CLOSED,
            before,
            audit.snapshot(row, COMMITMENT_AUDITED),
            reason="settled by the actuals linked to it",
            fields=COMMITMENT_AUDITED,
        )
    elif not settled and row.status == STATUS_CLOSED and automatic:
        before = audit.snapshot(row, COMMITMENT_AUDITED)
        row.status = STATUS_OPEN
        db.flush()
        audit.record(
            db,
            actor,
            row,
            COMMITMENT_REOPENED,
            before,
            audit.snapshot(row, COMMITMENT_AUDITED),
            reason="an actual that settled it was voided or changed",
            fields=COMMITMENT_AUDITED,
        )


def close_commitment(
    db: Session,
    row: BudgetCommitment,
    *,
    actor: audit.Actor,
    reason: Optional[str] = None,
) -> BudgetCommitment:
    """Close it by hand. A supplier who invoices 940 of 1 000 and stops.

    The remaining 60 leaves `committed_open` the moment this is recorded,
    which is the point: only the producer knows it is never coming.
    """
    if row.voided_at is not None:
        raise TrackingError("This order has been voided and cannot change. Add a new order instead.")
    if row.status == STATUS_CANCELLED:
        raise TrackingError(
            "that commitment was cancelled, which is not the same as closed. "
            "A cancelled order was withdrawn; a closed one was fulfilled. "
            "Add a new commitment if the order is back on."
        )
    before = audit.snapshot(row, COMMITMENT_AUDITED)
    row.status = STATUS_CLOSED
    db.flush()
    audit.record(
        db,
        actor,
        row,
        COMMITMENT_CLOSED,
        before,
        audit.snapshot(row, COMMITMENT_AUDITED),
        reason=(reason or "").strip() or "closed by hand",
        fields=COMMITMENT_AUDITED,
    )
    return row


def cancel_commitment(
    db: Session,
    row: BudgetCommitment,
    *,
    actor: audit.Actor,
    reason: Optional[str] = None,
) -> BudgetCommitment:
    """Withdraw it. It leaves `committed_gross` as well as `committed_open`.

    Refused once anything is invoiced against it: an order that has been
    part-invoiced was not withdrawn, it was fulfilled in part, and the honest
    record of that is a close.
    """
    if row.voided_at is not None:
        raise TrackingError("This order has been voided and cannot change. Add a new order instead.")
    live = [entry for entry in linked_actuals(db, row) if entry.voided_at is None]
    if live:
        raise TrackingError(
            f"{len(live)} actual(s) are booked against that commitment, so it "
            f"was not withdrawn — it was fulfilled in part. Close it instead: "
            f"that leaves what was ordered on the record and takes the rest "
            f"out of the open amount."
        )
    before = audit.snapshot(row, COMMITMENT_AUDITED)
    row.status = STATUS_CANCELLED
    db.flush()
    audit.record(
        db,
        actor,
        row,
        COMMITMENT_CANCELLED,
        before,
        audit.snapshot(row, COMMITMENT_AUDITED),
        reason=(reason or "").strip() or "cancelled by hand",
        fields=COMMITMENT_AUDITED,
    )
    return row


def void_commitment(
    db: Session, row: BudgetCommitment, *, actor: audit.Actor, reason: Optional[str]
) -> BudgetCommitment:
    """Take it out of every number. It stays in the ledger.

    Refused while live actuals point at it: voiding it would leave those
    invoices pointing at an order that is not there, and `committed_open`
    would stop being able to explain itself.
    """
    if row.voided_at is not None:
        raise TrackingError("This order is already voided. Nothing more to do.")
    text = check_void_reason(reason)
    live = [entry for entry in linked_actuals(db, row) if entry.voided_at is None]
    if live:
        raise TrackingError(
            f"{len(live)} actual(s) are linked to that commitment. Void or "
            f"unlink them first — an invoice pointing at a voided order is a "
            f"row whose open amount cannot be explained. If the order simply "
            f"ended early, close it instead."
        )
    before = audit.snapshot(row, COMMITMENT_AUDITED)
    row.voided_at = datetime.now(timezone.utc)
    row.void_reason = text
    db.flush()
    audit.record(
        db,
        actor,
        row,
        COMMITMENT_VOIDED,
        before,
        audit.snapshot(row, COMMITMENT_AUDITED),
        reason=text,
        fields=COMMITMENT_AUDITED,
    )
    return row


# ─── Actuals ────────────────────────────────────────────────────────────────


def create_actual(
    db: Session,
    membership,
    budget: Budget,
    *,
    actor: audit.Actor,
    section_id: uuid.UUID,
    line_id: Optional[uuid.UUID] = None,
    commitment_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    payee_text: Optional[str] = None,
    description: Optional[str] = None,
    reference: Optional[str] = None,
    amount: Decimal,
    incurred_on: date,
    paid_on: Optional[date] = None,
    notes: Optional[str] = None,
) -> BudgetActual:
    """One invoice or receipt. Closes its commitment if that settles it."""
    check_amount(amount)
    check_payee(party_id, payee_text)
    section = section_in_budget(db, budget, section_id)
    line = line_in_section(db, section, line_id)
    party_in_company(db, budget, party_id)
    commitment = commitment_in_budget(db, budget, commitment_id)

    row = BudgetActual(
        company_id=membership.company_id,
        budget_id=budget.id,
        section_id=section.id,
        line_id=None if line is None else line.id,
        commitment_id=None if commitment is None else commitment.id,
        party_id=party_id,
        payee_text=(payee_text or "").strip() or None,
        description=(description or "").strip() or None,
        reference=(reference or "").strip() or None,
        amount=amount,
        incurred_on=incurred_on,
        paid_on=paid_on,
        notes=notes,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        actor,
        row,
        ACTUAL_CREATED,
        None,
        audit.snapshot(row, ACTUAL_AUDITED),
        fields=ACTUAL_AUDITED,
    )
    if commitment is not None:
        _settle(db, commitment, actor=actor, automatic=True)
    return row


def update_actual(
    db: Session,
    budget: Budget,
    row: BudgetActual,
    *,
    actor: audit.Actor,
    changes: dict,
) -> BudgetActual:
    """Change an invoice. Re-settles both the old and the new commitment.

    Both, because moving an invoice from one order to another leaves the
    first one owed again — and a reopen that only looked at the new link
    would leave the old order closed on money nobody is going to spend.
    """
    if row.voided_at is not None:
        raise TrackingError(
            "that actual has been voided and cannot be changed. Add a new "
            "one — the voided row stays in the ledger with its reason."
        )
    before = audit.snapshot(row, ACTUAL_AUDITED)
    previous_commitment_id = row.commitment_id

    if "amount" in changes:
        check_amount(changes["amount"])
    party_id = changes.get("party_id", row.party_id)
    payee_text = changes.get("payee_text", row.payee_text)
    check_payee(party_id, payee_text)
    if "party_id" in changes:
        party_in_company(db, budget, changes["party_id"])
    if "commitment_id" in changes:
        commitment_in_budget(db, budget, changes["commitment_id"])

    section = section_in_budget(db, budget, changes.get("section_id", row.section_id))
    if "section_id" in changes or "line_id" in changes:
        line = line_in_section(db, section, changes.get("line_id", row.line_id))
        row.section_id = section.id
        row.line_id = None if line is None else line.id

    for field_name in (
        "commitment_id",
        "party_id",
        "payee_text",
        "description",
        "reference",
        "amount",
        "incurred_on",
        "paid_on",
        "notes",
    ):
        if field_name in changes:
            setattr(row, field_name, changes[field_name])

    db.flush()
    audit.record(
        db,
        actor,
        row,
        ACTUAL_UPDATED,
        before,
        audit.snapshot(row, ACTUAL_AUDITED),
        fields=ACTUAL_AUDITED,
    )
    _resettle(db, budget, {previous_commitment_id, row.commitment_id}, actor=actor)
    return row


def void_actual(
    db: Session, budget: Budget, row: BudgetActual, *, actor: audit.Actor, reason: Optional[str]
) -> BudgetActual:
    """Take it out of every number, and reopen its commitment if it had one.

    The reopen is the half of this that is easy to forget, and it is the
    difference between a 400 that is owed and a 400 that nobody is tracking.
    """
    if row.voided_at is not None:
        raise TrackingError("This invoice is already voided. Nothing more to do.")
    text = check_void_reason(reason)
    before = audit.snapshot(row, ACTUAL_AUDITED)
    row.voided_at = datetime.now(timezone.utc)
    row.void_reason = text
    db.flush()
    audit.record(
        db,
        actor,
        row,
        ACTUAL_VOIDED,
        before,
        audit.snapshot(row, ACTUAL_AUDITED),
        reason=text,
        fields=ACTUAL_AUDITED,
    )
    _resettle(db, budget, {row.commitment_id}, actor=actor)
    return row


def _resettle(
    db: Session, budget: Budget, commitment_ids: set, *, actor: audit.Actor
) -> None:
    for commitment_id in commitment_ids:
        if commitment_id is None:
            continue
        row = (
            db.query(BudgetCommitment)
            .filter(
                BudgetCommitment.id == commitment_id,
                BudgetCommitment.company_id == budget.company_id,
            )
            .first()
        )
        if row is not None:
            _settle(db, row, actor=actor, automatic=True)


# ─── The ETC ────────────────────────────────────────────────────────────────


def set_etc(
    db: Session,
    line: BudgetLine,
    value: Optional[Decimal],
    *,
    actor: audit.Actor,
) -> BudgetLine:
    """Set or clear a line's estimate-to-complete.

    `None` clears it and the default rule takes over; `Decimal("0")` means
    "this line is finished" and is a different statement. A negative ETC is
    refused: it would make the line forecast less than has already been
    spent on it.
    """
    if value is not None and value < ZERO:
        raise TrackingError(
            f"an estimate to complete is {value}, and what is left to spend "
            f"cannot be negative. Set it to 0 to say the line is finished, "
            f"or clear it to go back to the default rule."
        )
    before = audit.snapshot(line, LINE_ETC_AUDITED)
    line.etc = value
    db.flush()
    audit.record(
        db,
        actor,
        line,
        LINE_ETC_SET,
        before,
        audit.snapshot(line, LINE_ETC_AUDITED),
        reason=(
            "cleared — back to the default rule"
            if value is None
            else f"estimate to complete set to {value}"
        ),
        fields=LINE_ETC_AUDITED,
    )
    return line


# ─── Mark finished (B1.1) ───────────────────────────────────────────────────

#: What "finished" is stored as: an ETC of exactly zero (SCOPE D83). Not a
#: flag of its own — a second column for the same statement would be a
#: second answer to "is this line done", and the two could disagree.
FINISHED_ETC = Decimal("0.00")


def _open_on(result, line: BudgetLine) -> Decimal:
    """The line's "ordered, not yet invoiced", from the one forecast.

    Read off `tracking(...)` rather than recomputed here: what is still owed
    on a line is `committed_open_of`'s rule and only that module spells it.
    """
    found = result.lines.get(str(line.id))
    return found.committed_open if found is not None else ZERO


def _lines_under(db: Session, budget: Budget, section: BudgetSection) -> list[BudgetLine]:
    """A section's own active lines and its sub-sections' — "all lines in
    this section" means what the section row's total covers."""
    sections = active_sections(db, budget)
    wanted = {section.id}
    wanted.update(row.id for row in sections if row.parent_id == section.id)
    by_id = {row.id: row for row in sections}
    return [
        line
        for line in active_lines(db, list(wanted))
        if counts_in_totals(line, by_id.get(line.section_id))
    ]


def finish_line(
    db: Session,
    budget: Budget,
    line: BudgetLine,
    *,
    confirm_open_commitment: bool,
    actor: audit.Actor,
) -> BudgetLine:
    """Mark one line finished: still-to-spend becomes 0.00.

    A line with an order still open asks first: finishing it says "nothing
    more will be spent" while a supplier is still owed money for it, which
    is sometimes true (the order will be cancelled) and is never something
    to do by accident. The refusal names the amount.
    """
    if is_finished(line.etc):
        raise TrackingError(f"{line.description} is already marked finished.")
    open_amount_ = _open_on(tracking(db, budget), line)
    if open_amount_ > ZERO and not confirm_open_commitment:
        raise TrackingError(
            f"{line.description} still has {open_amount_} ordered and not yet "
            f"invoiced. Finishing it says nothing more will be spent on it. "
            f"Confirm to finish it anyway, or wait for the invoice."
        )
    before = audit.snapshot(line, LINE_ETC_AUDITED)
    line.etc = FINISHED_ETC
    db.flush()
    audit.record(
        db,
        actor,
        line,
        LINE_FINISHED,
        before,
        audit.snapshot(line, LINE_ETC_AUDITED),
        reason=(
            f"marked finished with {open_amount_} still ordered, confirmed"
            if open_amount_ > ZERO
            else "marked finished"
        ),
        fields=LINE_ETC_AUDITED,
    )
    return line


def reopen_line(db: Session, line: BudgetLine, *, actor: audit.Actor) -> BudgetLine:
    """Undo "Mark finished": still-to-spend goes back to being worked out."""
    if not is_finished(line.etc):
        raise TrackingError(
            f"{line.description} is not marked finished, so there is nothing "
            f"to reopen."
        )
    before = audit.snapshot(line, LINE_ETC_AUDITED)
    line.etc = None
    db.flush()
    audit.record(
        db,
        actor,
        line,
        LINE_REOPENED,
        before,
        audit.snapshot(line, LINE_ETC_AUDITED),
        reason="reopened — still to spend is worked out again",
        fields=LINE_ETC_AUDITED,
    )
    return line


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def finish_section(
    db: Session, budget: Budget, section: BudgetSection, *, actor: audit.Actor
) -> dict:
    """Finish every line in a section, except those with an order still open.

    The skip is the point of the rule: a bulk action nobody reads line by
    line must not hide money that is still owed. Skipped lines are listed
    with their open amount so the screen can say which and why; each can
    still be finished one by one, where the confirmation is asked.

    ONE audit event, on the section, with counts and ids.
    """
    result = tracking(db, budget)
    changed: list[BudgetLine] = []
    already: list[BudgetLine] = []
    skipped: list[tuple[BudgetLine, Decimal]] = []
    for line in _lines_under(db, budget, section):
        if is_finished(line.etc):
            already.append(line)
            continue
        open_amount_ = _open_on(result, line)
        if open_amount_ > ZERO:
            skipped.append((line, open_amount_))
            continue
        changed.append(line)

    for line in changed:
        line.etc = FINISHED_ETC
    db.flush()

    message = f"{_plural(len(changed), 'line')} marked finished."
    if skipped:
        message += (
            f" {_plural(len(skipped), 'line')} skipped: still ordered, not invoiced."
        )
    audit.record(
        db,
        actor,
        section,
        LINE_FINISHED_BULK,
        None,
        {
            "finished": len(changed),
            "already_finished": len(already),
            "skipped_open_commitment": len(skipped),
            "finished_line_ids": [str(line.id) for line in changed],
            "skipped_line_ids": [str(line.id) for line, _ in skipped],
        },
        reason=message,
    )
    return {
        "section_id": section.id,
        "changed": [line.id for line in changed],
        "already": [line.id for line in already],
        "skipped": [
            {
                "line_id": line.id,
                "description": line.description,
                "committed_open": amount,
            }
            for line, amount in skipped
        ],
        "message": message,
    }


def reopen_section(
    db: Session, budget: Budget, section: BudgetSection, *, actor: audit.Actor
) -> dict:
    """Reopen every finished line in a section. ONE audit event, on the section.

    Only FINISHED lines move. A line whose still-to-spend was typed by hand
    (a non-zero amount) is not finished and keeps what was typed.
    """
    changed: list[BudgetLine] = []
    already: list[BudgetLine] = []
    for line in _lines_under(db, budget, section):
        if is_finished(line.etc):
            changed.append(line)
        else:
            already.append(line)
    for line in changed:
        line.etc = None
    db.flush()
    message = f"{_plural(len(changed), 'line')} reopened."
    audit.record(
        db,
        actor,
        section,
        LINE_REOPENED_BULK,
        None,
        {
            "reopened": len(changed),
            "not_finished": len(already),
            "reopened_line_ids": [str(line.id) for line in changed],
        },
        reason=message,
    )
    return {
        "section_id": section.id,
        "changed": [line.id for line in changed],
        "already": [line.id for line in already],
        "skipped": [],
        "message": message,
    }


# ─── CSV ────────────────────────────────────────────────────────────────────


def export_rows(db: Session, budget: Budget) -> list[dict]:
    """The budget's non-voided actuals, in the import's own columns.

    Voided entries are left out: the file exists to be changed and sent
    back, and a voided row in it would read as a cost to re-import. The
    ledger is where a void is visible.
    """
    sections = {row.id: row for row in active_sections(db, budget)}
    lines = {
        row.id: row for row in active_lines(db, list(sections))
    }
    rows: list[dict] = []
    for row in sorted(
        actuals_of(db, budget, include_voided=False),
        key=lambda entry: (entry.incurred_on, entry.created_at),
    ):
        section = sections.get(row.section_id)
        line = lines.get(row.line_id) if row.line_id is not None else None
        rows.append(
            {
                "date": de_date(row.incurred_on),
                "description": row.description or "",
                "payee": _payee_name(db, budget, row.party_id, row.payee_text),
                "reference": row.reference or "",
                "amount": de_amount(row.amount),
                "section_code": (section.code or "") if section is not None else "",
                "line_description": line.description if line is not None else "",
                "paid_on": de_date(row.paid_on),
            }
        )
    return rows


def export_file(db: Session, budget: Budget) -> bytes:
    return export_actuals_csv(export_rows(db, budget))


def import_context(db: Session, budget: Budget) -> ActualImportContext:
    """What already exists, as plain data for the pure planner."""
    sections = active_sections(db, budget)
    sections_by_code: dict[str, str] = {}
    sections_by_name: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    for section in sections:
        if section.code:
            sections_by_code.setdefault(section.code.strip().lower(), str(section.id))
        name = section.name.strip().lower()
        if name in sections_by_name:
            ambiguous_names.add(name)
        sections_by_name.setdefault(name, str(section.id))

    lines_by_key: dict[tuple, str] = {}
    seen: set[tuple] = set()
    ambiguous_lines: set[tuple] = set()
    for line in active_lines(db, [section.id for section in sections]):
        key = (str(line.section_id), line.description.strip().lower())
        if key in seen:
            ambiguous_lines.add(key)
        seen.add(key)
        lines_by_key.setdefault(key, str(line.id))

    parties_by_name: dict[str, str] = {}
    ambiguous_parties: set[str] = set()
    for party in (
        db.query(Party)
        .filter(Party.company_id == budget.company_id, Party.is_active.is_(True))
        .all()
    ):
        for candidate in (party.name, party.trading_name):
            if not candidate:
                continue
            name = candidate.strip().lower()
            if name in parties_by_name and parties_by_name[name] != str(party.id):
                ambiguous_parties.add(name)
            parties_by_name.setdefault(name, str(party.id))

    existing: dict[tuple, str] = {}
    for row in actuals_of(db, budget, include_voided=False):
        existing.setdefault(_key_of(db, budget, row), str(row.id))

    return ActualImportContext(
        sections_by_code=sections_by_code,
        sections_by_name={
            name: section_id
            for name, section_id in sections_by_name.items()
            if name not in ambiguous_names
        },
        ambiguous_section_names=frozenset(ambiguous_names),
        lines_by_key=lines_by_key,
        ambiguous_line_keys=frozenset(ambiguous_lines),
        parties_by_name={
            name: party_id
            for name, party_id in parties_by_name.items()
            if name not in ambiguous_parties
        },
        ambiguous_party_names=frozenset(ambiguous_parties),
        existing_by_key=existing,
    )


def preview_import(db: Session, budget: Budget, payload) -> ActualImportPlan:
    """What the file would do. Writes nothing."""
    return plan_actuals_from_file(payload, import_context(db, budget))


def apply_import(
    db: Session,
    membership,
    budget: Budget,
    payload,
    *,
    actor: audit.Actor,
    expected_digest: str,
    include_duplicates: Sequence[int] = (),
) -> tuple[ActualImportPlan, int]:
    """Apply exactly what the preview showed. `(plan, created)`. No commit.

    `expected_digest` is the digest the preview returned and it is required:
    an apply without it would apply whatever the file plans NOW, which is
    the one thing the plan→apply split exists to rule out.

    `include_duplicates` is the line numbers the user ticked. A duplicate row
    that is not in it is **skipped** — the preview said it would be, and an
    import that wrote it anyway would be applying something other than what
    was shown.

    Error rows are skipped rather than failing the file, for A1's reason: one
    typo in row 40 of a 200-row sheet must not waste the other 199, and the
    response says which was which.
    """
    plan = preview_import(db, budget, payload)
    if plan.digest != expected_digest:
        raise TrackingError(
            f"this file no longer does what the preview showed (the preview "
            f"was {expected_digest}, the file now plans {plan.digest}). "
            f"Something changed in between — a cost entered by hand, a line "
            f"renamed, a section added. Run the preview again and check it "
            f"before applying."
        )

    ticked = set(include_duplicates or ())
    created = 0
    for row in plan.rows:
        if row.action == ACTION_CREATE or (
            row.action == ACTION_DUPLICATE and row.line_number in ticked
        ):
            _create_from_row(db, membership, budget, row, actor=actor)
            created += 1

    audit.record(
        db,
        actor,
        budget,
        ACTUALS_IMPORT_APPLIED,
        after={
            "created": created,
            "planned": len(plan.creates),
            "duplicates_held_back": len(plan.duplicates) - len(ticked & {
                row.line_number for row in plan.duplicates
            }),
            "duplicates_imported": len(
                ticked & {row.line_number for row in plan.duplicates}
            ),
            "skipped_with_errors": len(plan.errors),
            "digest": plan.digest,
            "delimiter": plan.delimiter,
        },
    )
    db.flush()
    return plan, created


def _create_from_row(
    db: Session,
    membership,
    budget: Budget,
    row: ActualImportRow,
    *,
    actor: audit.Actor,
) -> BudgetActual:
    """One planned row, written. Goes through `create_actual`.

    Through the service function and not a bare INSERT, so an imported cost
    is validated, audited and settles its commitment exactly like a typed
    one. An import with its own write path is how two kinds of row end up in
    one table.
    """
    values = row.values
    return create_actual(
        db,
        membership,
        budget,
        actor=actor,
        section_id=uuid.UUID(row.section_id),
        line_id=None if row.line_id is None else uuid.UUID(row.line_id),
        party_id=None if values.party_id is None else uuid.UUID(values.party_id),
        payee_text=values.payee_text or None,
        description=values.description or None,
        reference=values.reference or None,
        amount=values.amount,
        incurred_on=values.incurred_on,
        paid_on=values.paid_on,
    )
