"""The Budget: its rules, its engine input, and its CSV.

Everything that decides something about a budget is here. The router does
lookups, permissions, response shapes and the commit.

Five things worth reading before changing this file.

**1. The engine input is built here and nowhere else**, and it goes through
`services/calc_mapping.py` — the same two functions `POST /calc/preview`
uses. One mapping means a budget line and a quote line come back with the
same fields and the same rounding; two would mean the sheet and the
calculator disagreeing by a cent and nobody able to say which was right
(CLAUDE.md rule 2).

**2. Refs are resolved BEFORE the engine is called.** A line's
`quantity_ref` or `period_ref` names a `budget_globals.key`; `resolve`
turns it into a number, and the engine is handed numbers. The engine never
sees a name, has no evaluator, and has no hook through which one could be
added (D70). A ref that names nothing is a 422 naming the key — never a
silent 1, which would price a line at one day and look like an answer.

**3. A `days:<type>` global is COUNTED from the project's active production
days** every time the budget is read or calculated. Not cached, not
triggered: a shoot day added in the Days tab has to move the budget, and a
cached value plus a trigger is two places for that to be wrong. The count
is one indexed query per such global and a budget has one or two.

**4. Rates are copied from the catalog at insertion and never written back.**
`item_id` stays on the line so the API can report "this differs from the
catalog now"; nothing re-prices a line from the price list. A budget that
silently re-priced itself when somebody edited an item would be a budget
nobody could compare with last week's.

**5. Adjustments move the price and never the cost.** That is the engine's
behaviour (see `billing/calc/engine._apply_adjustments`) and it is not
changed here. A contingency or a production fee that has to raise the COST
side is modelled as an ordinary line of kind `fee`, which is what SCOPE §5.4
already offers — "a surcharge like a production fee can alternatively be a
normal `fee` line".
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..billing.budget import (
    ACTION_CREATE,
    ACTION_UPDATE,
    BudgetImportContext,
    BudgetImportPlan,
    BudgetImportRow,
    de_amount,
    de_quantity,
    export_csv,
    plan_from_file,
)
from ..billing.calc import PERIOD_UNITS, percent_share
from ..core.rounding import quantize_qty
from ..billing.catalog import FactorError, explain_factor
from ..billing.catalog.kinds import ITEM_KINDS, KIND_OTHER
from ..models.catalog import Item, RATE_BILL, RATE_COST, RentalFactor
from ..models.project import (
    EXTRA_ACCEPTED,
    MAX_SECTION_DEPTH,
    Budget,
    BudgetAdjustment,
    BudgetGlobal,
    BudgetLine,
    BudgetSection,
    Project,
    day_type_of_source,
)
from ..models.tax import TaxClass, TaxCode
from . import audit
from .calc_mapping import CalcError, calculate_document, response_from_result
from .project_service import count_days_of_type

ZERO = Decimal("0")
ONE = Decimal("1")

#: How far apart new positions are placed. A gap, so that dragging a row
#: between two others is one UPDATE rather than a renumbering of the whole
#: section — and so that a client which does its own arithmetic on
#: positions has somewhere to put the result.
POSITION_STEP = 10


class BudgetError(ValueError):
    """A refusal a user can act on. The router answers 409 with the sentence."""


class BudgetLocked(BudgetError):
    """This budget is frozen. Still a 409, with its own type so the router
    can say so without matching on prose."""


class RefError(BudgetError):
    """A ref names a global that is not there. The router answers 422."""


# ─── What an audit row records ──────────────────────────────────────────────

BUDGET_AUDITED: tuple[str, ...] = (
    "name",
    "currency",
    "notes",
    "is_primary",
    "is_active",
    "locked_at",
    "closed_at",
    "closed_by",
    "close_note",
)
SECTION_AUDITED: tuple[str, ...] = (
    "code",
    "name",
    "parent_id",
    "position",
    "is_active",
)
LINE_AUDITED: tuple[str, ...] = (
    "section_id",
    "position",
    "item_id",
    "description",
    "kind",
    "quantity",
    "quantity_ref",
    "period_value",
    "period_ref",
    "period_unit",
    "factor",
    "factor_source",
    "unit_price",
    "unit_cost",
    "discount_mode",
    "discount_value",
    "tax_ref",
    "tags",
    "allow_negative",
    "notes",
    "is_active",
    "extra_status",
    "extra_decided_on",
    "extra_note",
)
GLOBAL_AUDITED: tuple[str, ...] = (
    "key",
    "label",
    "value",
    "unit",
    "source",
    "is_active",
)
ADJUSTMENT_AUDITED: tuple[str, ...] = (
    "section_id",
    "position",
    "label",
    "type",
    "mode",
    "value",
    "base",
    "kinds",
    "is_active",
)

BUDGET_CREATED = "budget.created"
BUDGET_UPDATED = "budget.updated"
BUDGET_DUPLICATED = "budget.duplicated"
BUDGET_LOCKED = "budget.locked"
BUDGET_UNLOCKED = "budget.unlocked"
BUDGET_SET_PRIMARY = "budget.set_primary"
BUDGET_IMPORT_APPLIED = "budget.import_applied"
SECTION_CREATED = "budget_section.created"
SECTION_UPDATED = "budget_section.updated"
SECTION_REMOVED = "budget_section.removed"
LINE_CREATED = "budget_line.created"
LINE_UPDATED = "budget_line.updated"
LINE_REMOVED = "budget_line.removed"
LINE_MOVED = "budget_line.moved"
GLOBAL_CREATED = "budget_global.created"
GLOBAL_UPDATED = "budget_global.updated"
GLOBAL_REMOVED = "budget_global.removed"
ADJUSTMENT_CREATED = "budget_adjustment.created"
ADJUSTMENT_UPDATED = "budget_adjustment.updated"
ADJUSTMENT_REMOVED = "budget_adjustment.removed"


# ─── The lock ───────────────────────────────────────────────────────────────


def refuse_if_locked(budget: Budget) -> None:
    """A locked budget takes no structural edit. 409, with a sentence.

    Which edits: lines, sections, globals and adjustments — everything that
    changes a number. The budget's own NAME and notes stay editable, because
    "Estimate v3 (approved 24.11.)" is exactly the kind of thing somebody
    wants to write on a frozen sheet.
    """
    if budget.locked_at is not None:
        raise BudgetLocked(
            f"{budget.name} was locked on "
            f"{budget.locked_at.date().isoformat()} and its lines, sections, "
            f"globals and adjustments cannot be changed. Unlock it first — "
            f"unlocking is recorded in the audit log, which is the point of "
            f"the lock."
        )


class BudgetClosed(BudgetError):
    """This budget is closed (B1.1): the production has ended. A 409."""


def refuse_if_closed(
    budget: Budget,
    *,
    what: str = "orders, invoices, still to spend and finishing lines are refused",
) -> None:
    """A closed budget takes no cost entry, no still-to-spend, no finish.

    Separate from `refuse_if_locked` because the two freeze different
    things: a lock freezes the PLAN and leaves costs open (D85); closing is
    the production's end and freezes the costs too. A closed budget is
    always locked, so every estimate edit is refused by the lock already.
    """
    if budget.closed_at is not None:
        raise BudgetClosed(
            f"{budget.name} was closed on {budget.closed_at.date().isoformat()}, "
            f"so {what}. Reopen it first — reopening asks for a "
            f"reason and is recorded in the audit log."
        )


def lock_for_write(db: Session, budget: Budget) -> Budget:
    """`SELECT … FOR UPDATE` on the budget row. Returns it.

    Taken before any insert that computes a `position`, so two people adding
    a line at the same moment cannot both read "the highest position is 40"
    and both write 50. Serialising on the BUDGET row rather than on the
    section is deliberate: a budget is edited by one person at a time in
    practice, the critical section is one INSERT long, and locking per
    section would leave two sections of one budget able to produce the same
    position for a line that is later moved between them.
    """
    row = (
        db.query(Budget)
        .filter(Budget.id == budget.id)
        .with_for_update()
        .first()
    )
    return row if row is not None else budget


def _next_position(db: Session, model, *filters) -> int:
    highest = (
        db.query(func.coalesce(func.max(model.position), 0)).filter(*filters).scalar()
    )
    return int(highest or 0) + POSITION_STEP


def next_section_position(
    db: Session, budget: Budget, parent_id: Optional[uuid.UUID]
) -> int:
    return _next_position(
        db,
        BudgetSection,
        BudgetSection.budget_id == budget.id,
        BudgetSection.parent_id.is_(None)
        if parent_id is None
        else BudgetSection.parent_id == parent_id,
    )


def next_line_position(db: Session, section: BudgetSection) -> int:
    return _next_position(db, BudgetLine, BudgetLine.section_id == section.id)


def next_adjustment_position(db: Session, budget: Budget) -> int:
    return _next_position(db, BudgetAdjustment, BudgetAdjustment.budget_id == budget.id)


# ─── Sections ───────────────────────────────────────────────────────────────


def check_parent_section(
    db: Session, budget: Budget, parent: Optional[BudgetSection]
) -> None:
    """Two levels of section, no more — the engine's group limit (§5.4).

    The refusal names the engine's limit rather than a number out of
    nowhere, because the person reading it is dragging a section into another
    one and the only useful answer is "that is as deep as it goes, and here
    is why".
    """
    if parent is None:
        return
    if parent.budget_id != budget.id:
        raise BudgetError("that section belongs to a different budget.")
    if parent.parent_id is not None:
        raise BudgetError(
            f"{parent.name!r} is already inside a section, and a budget "
            f"nests sections {MAX_SECTION_DEPTH} deep at most — the "
            f"calculation engine nests groups {MAX_SECTION_DEPTH} deep "
            f"(SCOPE §5.4), so a third level could be stored and never "
            f"calculated. Put these lines directly in {parent.name!r}, or "
            f"move the section up a level."
        )


def active_sections(db: Session, budget: Budget) -> list[BudgetSection]:
    return (
        db.query(BudgetSection)
        .filter(
            BudgetSection.budget_id == budget.id,
            BudgetSection.is_active.is_(True),
        )
        .order_by(BudgetSection.position, BudgetSection.code)
        .all()
    )


def active_lines(db: Session, section_ids: Sequence[uuid.UUID]) -> list[BudgetLine]:
    if not section_ids:
        return []
    return (
        db.query(BudgetLine)
        .filter(
            BudgetLine.section_id.in_(list(section_ids)),
            BudgetLine.is_active.is_(True),
        )
        .order_by(BudgetLine.position, BudgetLine.description)
        .all()
    )


def active_globals(db: Session, budget: Budget) -> list[BudgetGlobal]:
    return (
        db.query(BudgetGlobal)
        .filter(
            BudgetGlobal.budget_id == budget.id,
            BudgetGlobal.is_active.is_(True),
        )
        .order_by(BudgetGlobal.key)
        .all()
    )


def active_adjustments(db: Session, budget: Budget) -> list[BudgetAdjustment]:
    return (
        db.query(BudgetAdjustment)
        .filter(
            BudgetAdjustment.budget_id == budget.id,
            BudgetAdjustment.is_active.is_(True),
        )
        .order_by(BudgetAdjustment.position, BudgetAdjustment.label)
        .all()
    )


# ─── Globals ────────────────────────────────────────────────────────────────


def computed_value(db: Session, budget: Budget, source: str) -> Optional[Decimal]:
    """A `days:<type>` global's value, counted now. `None` for `manual`."""
    day_type = day_type_of_source(source)
    if day_type is None:
        return None
    # Quantized to the quantity scale, so a counted global serialises like
    # every other quantity in the system — `"3.0000"`, not `"3"`. A screen
    # that formatted one field differently from its neighbour because of
    # where the number came from would be a screen nobody trusts.
    return quantize_qty(Decimal(count_days_of_type(db, budget.project_id, day_type)))


def sync_computed_globals(db: Session, budget: Budget) -> list[BudgetGlobal]:
    """Bring every `days:<type>` global's stored value up to date.

    The stored value is a CACHE of the count, kept so that a read of the
    globals panel is one query and so that the CSV export has a number to
    print. The authority is the day count, which is why this runs before
    every calculation and every read rather than on a trigger: a trigger
    would be a second place for "how many shoot days are there" to be
    answered, and the two would disagree the first time a day was added by
    anything but the Days tab.

    Returns the globals it changed. No commit — the caller owns it.
    """
    changed: list[BudgetGlobal] = []
    for row in active_globals(db, budget):
        value = computed_value(db, budget, row.source)
        if value is None:
            continue
        if row.value != value:
            row.value = value
            changed.append(row)
    if changed:
        db.flush()
    return changed


def global_values(db: Session, budget: Budget) -> dict[str, Decimal]:
    """`key → value` for every active global, computed ones counted now."""
    values: dict[str, Decimal] = {}
    for row in active_globals(db, budget):
        value = computed_value(db, budget, row.source)
        values[row.key] = row.value if value is None else value
    return values


def lines_referencing(
    db: Session, budget: Budget, key: str
) -> list[BudgetLine]:
    """Active lines whose `quantity_ref` or `period_ref` names `key`.

    What makes "a global a line uses cannot be removed" a refusal that names
    the lines. Removing one silently would turn every line that used it into
    a 422 on the next calculation, and the person who hit that would have no
    way to know what had been taken away.
    """
    section_ids = [section.id for section in active_sections(db, budget)]
    if not section_ids:
        return []
    return (
        db.query(BudgetLine)
        .filter(
            BudgetLine.section_id.in_(section_ids),
            BudgetLine.is_active.is_(True),
            (BudgetLine.quantity_ref == key) | (BudgetLine.period_ref == key),
        )
        .order_by(BudgetLine.description)
        .all()
    )


def refuse_removing_referenced_global(
    db: Session, budget: Budget, row: BudgetGlobal
) -> None:
    users = lines_referencing(db, budget, row.key)
    if not users:
        return
    listed = ", ".join(f"“{line.description}”" for line in users)
    raise BudgetError(
        f"{row.key} is used by {len(users)} line(s): {listed}. Point those "
        f"at another global or type a number into them first — a line whose "
        f"ref no longer resolves cannot be priced at all."
    )


# ─── The engine input ───────────────────────────────────────────────────────


def _resolve(
    value: Decimal,
    ref: Optional[str],
    values: Mapping[str, Decimal],
    *,
    what: str,
    line: BudgetLine,
) -> Decimal:
    """The number a field contributes: the global it names, or its own value.

    **Before the engine, always.** A dangling ref is a refusal and not a
    fallback: answering 1 would price the line at one day and look exactly
    like a correct answer.
    """
    if not ref:
        return value
    if ref not in values:
        raise RefError(
            f"the {what} of “{line.description}” points at the global "
            f"{ref!r}, and this budget has no active global with that key. "
            f"Add it in the Globals panel, or type a number into the line."
        )
    return values[ref]


def factor_table(db: Session, item: Item) -> list[tuple]:
    rows = (
        db.query(RentalFactor)
        .filter(RentalFactor.group_id == item.rental_factor_group_id)
        .order_by(RentalFactor.days)
        .all()
    )
    return [(row.days, row.factor) for row in rows]


def _catalog_factor(
    db: Session, line: BudgetLine, item: Optional[Item], period_value: Decimal
) -> tuple[Decimal, Optional[dict]]:
    """`(factor, explanation)` for a line whose factor comes from the catalog.

    The lookup is A1's `explain_factor`, so the rule is the one the Rental
    factors screen prints and the one D71 records as `[Guessing]` — there is
    no second implementation here. The explanation travels with the number
    because "2.8" is not an answer somebody can disagree with and "2.8, from
    the 5-day row, because the table has no 6" is.
    """
    if item is None or item.rental_factor_group_id is None:
        return line.factor, None
    table = factor_table(db, item)
    if period_value != period_value.to_integral_value():
        raise BudgetError(
            f"“{line.description}” takes its factor from the catalog and runs "
            f"for {period_value} {line.period_unit}s. A factor table is keyed "
            f"by whole days — a half-day rental is a different unit, not a "
            f"fractional lookup. Set the factor by hand, or make the period a "
            f"whole number."
        )
    try:
        factor, matched, rule = explain_factor(table, int(period_value))
    except FactorError as error:
        raise BudgetError(
            f"“{line.description}”: {error}"
        ) from None
    return factor, {
        "line_id": str(line.id),
        "days": int(period_value),
        "factor": factor,
        "matched_days": matched,
        "rule": rule,
        "group_name": _group_name(db, item),
    }


def _group_name(db: Session, item: Item) -> Optional[str]:
    from ..models.catalog import RentalFactorGroup

    group = (
        db.query(RentalFactorGroup)
        .filter(RentalFactorGroup.id == item.rental_factor_group_id)
        .first()
    )
    return group.name if group is not None else None


def _line_payload(
    db: Session,
    line: BudgetLine,
    values: Mapping[str, Decimal],
    items: Mapping[uuid.UUID, Item],
    explanations: list,
) -> dict:
    quantity = _resolve(
        line.quantity, line.quantity_ref, values, what="quantity", line=line
    )
    period_value = _resolve(
        line.period_value, line.period_ref, values, what="period", line=line
    )
    factor = line.factor
    if line.factor_source == "catalog":
        factor, explanation = _catalog_factor(
            db, line, items.get(line.item_id), period_value
        )
        if explanation is not None:
            explanations.append(explanation)

    payload: dict[str, Any] = {
        "type": "line",
        "id": str(line.id),
        "label": line.description,
        "quantity": quantity,
        "period_value": period_value,
        "period_unit": line.period_unit,
        "factor": factor,
        "unit_price": line.unit_price,
        "unit_cost": line.unit_cost,
        "tax_ref": line.tax_ref or "",
        "kind": line.kind or "",
        "tags": list(line.tags or []),
        "allow_negative": bool(line.allow_negative),
    }
    if line.discount_mode:
        payload["discount"] = {
            "mode": line.discount_mode,
            "value": line.discount_value if line.discount_value is not None else ZERO,
        }
    return payload


def _adjustment_payload(row: BudgetAdjustment) -> dict:
    payload: dict[str, Any] = {
        "label": row.label,
        "type": row.type,
        "mode": row.mode,
        "value": row.value,
        "base": row.base,
    }
    if row.base == "kinds":
        payload["kinds"] = list(row.kinds or [])
    return payload


#: Which lines an engine document is built from (B1.1 extras).
#: `budget` — everything except proposed and declined extras: the budget's
#: totals, its tax grouping and the tracking baseline. `baseline` — nothing
#: from the Extras section at all: the quote as it was locked.
SCOPE_BUDGET = "budget"
SCOPE_BASELINE = "baseline"


def counts_in_totals(line: BudgetLine, section: Optional[BudgetSection]) -> bool:
    """Does this line enter the budget's totals? Every ordinary line does;
    an extra only once it is ACCEPTED. The one place the rule is spelled."""
    if section is not None and section.is_extras:
        return line.extra_status == EXTRA_ACCEPTED
    return True


def engine_input(
    db: Session, budget: Budget, *, scope: str = SCOPE_BUDGET
) -> tuple[dict, list]:
    """`(document data, factor explanations)` — the engine's own input shape.

    Built from the stored rows and handed to `services/calc_mapping`, which
    is the same parse `POST /calc/preview` uses. Children are ordered: a
    section's own LINES first, then its sub-sections, each by `position`.
    One order, stated here, so the top sheet and the CSV and the screen all
    read the same way.

    `scope` (B1.1): extras are ordinary lines in the reserved Extras
    section, so they need no calculation path of their own — they are simply
    left OUT of the document when they must not count. Proposed and declined
    extras never count; `baseline` leaves the whole Extras section out. An
    Extras section with nothing left in it is left out too, so the baseline
    and the budget are the same document until an extra is accepted.
    """
    values = global_values(db, budget)
    all_sections = active_sections(db, budget)
    section_by_id = {section.id: section for section in all_sections}
    lines = [
        line
        for line in active_lines(db, [section.id for section in all_sections])
        if counts_in_totals(line, section_by_id.get(line.section_id))
        and not (scope == SCOPE_BASELINE and section_by_id[line.section_id].is_extras)
    ]
    with_lines = {line.section_id for line in lines}
    sections = [
        section
        for section in all_sections
        if not section.is_extras or section.id in with_lines
    ]
    adjustments = active_adjustments(db, budget)

    item_ids = {line.item_id for line in lines if line.item_id is not None}
    items: dict[uuid.UUID, Item] = {}
    if item_ids:
        items = {
            item.id: item
            for item in db.query(Item).filter(Item.id.in_(list(item_ids))).all()
        }

    lines_by_section: dict[uuid.UUID, list[BudgetLine]] = {}
    for line in lines:
        lines_by_section.setdefault(line.section_id, []).append(line)

    children_by_parent: dict[Optional[uuid.UUID], list[BudgetSection]] = {}
    for section in sections:
        children_by_parent.setdefault(section.parent_id, []).append(section)

    adjustments_by_section: dict[Optional[uuid.UUID], list[BudgetAdjustment]] = {}
    for row in adjustments:
        adjustments_by_section.setdefault(row.section_id, []).append(row)

    explanations: list = []

    def group_for(section: BudgetSection) -> dict:
        children: list[dict] = [
            _line_payload(db, line, values, items, explanations)
            for line in lines_by_section.get(section.id, [])
        ]
        children.extend(
            group_for(child) for child in children_by_parent.get(section.id, [])
        )
        return {
            "type": "group",
            "id": str(section.id),
            "title": section.name,
            "description": "",
            "children": children,
            "adjustments": [
                _adjustment_payload(row)
                for row in adjustments_by_section.get(section.id, [])
            ],
        }

    data = {
        "id": str(budget.id),
        "title": budget.name,
        "children": [group_for(section) for section in children_by_parent.get(None, [])],
        "adjustments": [
            _adjustment_payload(row) for row in adjustments_by_section.get(None, [])
        ],
    }
    return data, explanations


def calc_result(db: Session, budget: Budget, *, scope: str = SCOPE_BUDGET) -> tuple:
    """`(CalcResult, factor explanations)` — the engine's own value objects.

    The one place a budget is priced. `calculated` below turns this into the
    wire shape; B1's `services/budget_tracking_service.py` takes the
    `Decimal`s out of it for the estimate side of the forecast. Two callers,
    one calculation: a tracking view that priced the budget a second time
    could show an estimate cost that differed from the sheet's by a cent, and
    nobody could say which was right (CLAUDE.md rule 2).

    A `CalcError` — a third nesting level that got into the database somehow,
    a duplicate id — comes out as a `BudgetError` with the engine's own
    sentence.
    """
    data, explanations = engine_input(db, budget, scope=scope)
    try:
        return calculate_document(data), explanations
    except CalcError as error:
        raise BudgetError(str(error)) from None


def extras_section(db: Session, budget: Budget) -> Optional[BudgetSection]:
    """The budget's one Extras section, or None before the first extra."""
    return (
        db.query(BudgetSection)
        .filter(
            BudgetSection.budget_id == budget.id,
            BudgetSection.is_extras.is_(True),
            BudgetSection.is_active.is_(True),
        )
        .first()
    )


def extra_lines(db: Session, budget: Budget) -> list[BudgetLine]:
    section = extras_section(db, budget)
    return active_lines(db, [section.id]) if section is not None else []


def extras_own_result(db: Session, budget: Budget):
    """Each extra's OWN price, cost and margin, whatever its status.

    One engine call over a document holding only the extras, with no
    adjustments — what each extra is on its own, which is what the customer
    is told. A proposed or declined extra has no number in the budget's
    document (it is left out), so this is where its figures come from.
    `None` when there are no extras.
    """
    lines = extra_lines(db, budget)
    if not lines:
        return None
    values = global_values(db, budget)
    item_ids = {line.item_id for line in lines if line.item_id is not None}
    items = (
        {item.id: item for item in db.query(Item).filter(Item.id.in_(list(item_ids))).all()}
        if item_ids
        else {}
    )
    explanations: list = []
    data = {
        "id": f"{budget.id}-extras",
        "title": "Extras",
        "children": [
            _line_payload(db, line, values, items, explanations) for line in lines
        ],
        "adjustments": [],
    }
    try:
        return calculate_document(data)
    except CalcError as error:
        raise BudgetError(str(error)) from None


def _totals_of(result) -> dict:
    return {
        "net": result.net,
        "cost": result.cost,
        "margin": result.margin,
        "margin_percent": result.margin_percent,
        "net_by_tax_ref": dict(result.net_by_tax_ref),
    }


def calculated(db: Session, budget: Budget) -> dict:
    """Every number the sheet and the top sheet need. Computes nothing itself.

    The arithmetic is the engine's, the shares are the engine's
    `percent_share`, and this function's whole job is to assemble the input
    and label the output.
    """
    result, explanations = calc_result(db, budget)
    baseline, _ = calc_result(db, budget, scope=SCOPE_BASELINE)
    response = response_from_result(result)

    sections = {str(section.id): section for section in active_sections(db, budget)}
    top_sheet = []
    for child in result.children:
        section = sections.get(child.id)
        top_sheet.append(
            {
                "section_id": child.id,
                "code": section.code if section is not None else None,
                "name": child.title,
                "price": child.subtotal,
                "cost": child.cost_subtotal,
                "margin": child.margin,
                "margin_percent": child.margin_percent,
                # Share of the sections' total, i.e. of the subtotal BEFORE
                # the budget's own adjustments — so the shares add up to
                # 100 % and the fee is visible as its own row in the totals
                # rather than diluted across the sections it is charged on.
                "share_of_subtotal": percent_share(child.subtotal, result.subtotal),
            }
        )

    own = extras_own_result(db, budget)
    extras = []
    for line in extra_lines(db, budget):
        priced = own.lines.get(str(line.id)) if own is not None else None
        extras.append(
            {
                "line_id": line.id,
                "description": line.description,
                "status": line.extra_status,
                "decided_on": line.extra_decided_on,
                "note": line.extra_note,
                "price": priced.net if priced is not None else ZERO,
                "cost": priced.cost_net if priced is not None else ZERO,
                "margin": priced.margin if priced is not None else ZERO,
                "margin_percent": priced.margin_percent if priced is not None else None,
            }
        )

    return {
        "result": response,
        "top_sheet": top_sheet,
        "factors": explanations,
        # B1.1: the quote as locked, and the budget with accepted extras.
        # Two engine runs over two documents; nothing is added up here.
        "totals": {
            "baseline": _totals_of(baseline),
            "with_extras": _totals_of(result),
        },
        "extras": extras,
    }


# ─── Lines from the catalog ─────────────────────────────────────────────────


def rate_for(db: Session, item: Item, *, rate_type: str, period_unit: str):
    from ..models.catalog import ItemRate

    row = (
        db.query(ItemRate)
        .filter(
            ItemRate.item_id == item.id,
            ItemRate.rate_type == rate_type,
            ItemRate.period_unit == period_unit,
        )
        .first()
    )
    return row.amount if row is not None else None


def tax_ref_for_item(db: Session, membership, item: Item) -> str:
    """The item's default tax class, as the CODE string the engine groups by.

    For grouping only — a budget computes no VAT (D72). The code rather than
    the class key, because that is what a quote's lines will carry in slice
    B and a budget that grouped by a different key could not be compared
    with the invoices that came out of it.
    """
    if not item.default_tax_class:
        return ""
    row = (
        db.query(TaxCode)
        .join(TaxClass, TaxClass.code_id == TaxCode.id)
        .filter(
            TaxClass.company_id == membership.company_id,
            TaxClass.key == item.default_tax_class,
        )
        .first()
    )
    return row.code if row is not None else ""


def line_defaults_from_item(
    db: Session, membership, item: Item, *, period_unit: Optional[str] = None
) -> dict:
    """What a new line takes from an item. **Copied, then independent.**

    The unit is the item's own unit's period unit when it has one, because
    that is the period its rates are per — taking `day` by default would
    copy a per-piece rate onto a line charged per day.
    """
    from ..models.catalog import Unit

    unit_period = period_unit
    if unit_period is None and item.unit_id is not None:
        unit = db.query(Unit).filter(Unit.id == item.unit_id).first()
        unit_period = unit.period_unit if unit is not None else None
    unit_period = unit_period or "day"

    return {
        "item_id": item.id,
        "description": item.description_customer or item.name_de,
        "kind": item.kind or KIND_OTHER,
        "period_unit": unit_period,
        "unit_price": rate_for(db, item, rate_type=RATE_BILL, period_unit=unit_period)
        or ZERO,
        "unit_cost": rate_for(db, item, rate_type=RATE_COST, period_unit=unit_period)
        or ZERO,
        "tax_ref": tax_ref_for_item(db, membership, item),
        "factor_source": "catalog" if item.rental_factor_group_id else "manual",
    }


def catalog_difference(db: Session, line: BudgetLine) -> Optional[dict]:
    """How the line's rates differ from the catalog's TODAY, or `None`.

    Reported and never acted on (point 4 of the module docstring). A line
    with no item has nothing to differ from.
    """
    if line.item_id is None:
        return None
    item = db.query(Item).filter(Item.id == line.item_id).first()
    if item is None:
        return None
    bill = rate_for(db, item, rate_type=RATE_BILL, period_unit=line.period_unit)
    cost = rate_for(db, item, rate_type=RATE_COST, period_unit=line.period_unit)
    differs = (bill is not None and bill != line.unit_price) or (
        cost is not None and cost != line.unit_cost
    )
    if not differs:
        return None
    return {
        "catalog_unit_price": bill,
        "catalog_unit_cost": cost,
        "item_name": item.name_de,
    }


# ─── Duplicating ────────────────────────────────────────────────────────────


def duplicate(
    db: Session,
    membership,
    budget: Budget,
    *,
    name: Optional[str] = None,
) -> Budget:
    """A copy of `budget`: sections, lines, globals, adjustments. Unlocked.

    Numerically identical, which is the property the test asserts by
    calculating both and comparing. It is identical because every number is
    copied and every ref is copied by KEY — the copy has its own globals
    with the same keys, so a line pointing at `shoot_days` points at the
    copy's `shoot_days` and the two budgets stay independent afterwards.

    The copy is **unlocked** whatever the original was: a lock says "this
    estimate is approved", and that is a statement about the original and
    not about a scenario somebody is about to change.

    **The baseline only** (B1.1): no Extras section and no extras (they
    were agreed with a client about THIS production), no still-to-spend,
    and not closed.
    """
    copy = Budget(
        company_id=membership.company_id,
        project_id=budget.project_id,
        name=name or f"{budget.name} (copy)",
        currency=budget.currency,
        notes=budget.notes,
        # Never primary: two primaries would make "the project's budget" a
        # question with two answers, and a copy is a scenario until somebody
        # says otherwise.
        is_primary=False,
        is_active=True,
        locked_at=None,
    )
    db.add(copy)
    db.flush()

    for row in active_globals(db, budget):
        db.add(
            BudgetGlobal(
                company_id=membership.company_id,
                budget_id=copy.id,
                key=row.key,
                label=row.label,
                value=row.value,
                unit=row.unit,
                source=row.source,
                is_active=True,
            )
        )

    section_map: dict[uuid.UUID, uuid.UUID] = {}
    sections = [row for row in active_sections(db, budget) if not row.is_extras]
    # Parents first, so a child's `parent_id` can be mapped.
    for section in sorted(sections, key=lambda row: (row.parent_id is not None, row.position)):
        new_section = BudgetSection(
            company_id=membership.company_id,
            budget_id=copy.id,
            code=section.code,
            name=section.name,
            parent_id=section_map.get(section.parent_id)
            if section.parent_id is not None
            else None,
            position=section.position,
            is_active=True,
        )
        db.add(new_section)
        db.flush()
        section_map[section.id] = new_section.id

    for line in active_lines(db, [section.id for section in sections]):
        db.add(
            BudgetLine(
                company_id=membership.company_id,
                section_id=section_map[line.section_id],
                position=line.position,
                item_id=line.item_id,
                description=line.description,
                kind=line.kind,
                quantity=line.quantity,
                quantity_ref=line.quantity_ref,
                period_value=line.period_value,
                period_ref=line.period_ref,
                period_unit=line.period_unit,
                factor=line.factor,
                factor_source=line.factor_source,
                unit_price=line.unit_price,
                unit_cost=line.unit_cost,
                discount_mode=line.discount_mode,
                discount_value=line.discount_value,
                tax_ref=line.tax_ref,
                tags=list(line.tags or []),
                allow_negative=line.allow_negative,
                notes=line.notes,
                is_active=True,
            )
        )

    for row in active_adjustments(db, budget):
        db.add(
            BudgetAdjustment(
                company_id=membership.company_id,
                budget_id=copy.id,
                section_id=section_map.get(row.section_id)
                if row.section_id is not None
                else None,
                position=row.position,
                label=row.label,
                type=row.type,
                mode=row.mode,
                value=row.value,
                base=row.base,
                kinds=list(row.kinds or []),
                is_active=True,
            )
        )

    db.flush()
    return copy


# ─── The example budget (SCOPE §12 S1) ──────────────────────────────────────

#: The S1 quote, as `tests/fixtures/calc/s1-quote.json` has it, with the
#: section codes a budget needs. The fixture is the contract — net 12 517.20,
#: cost 5 380.00, margin 7 137.20 — and this is the same data in the shape a
#: budget stores. `test_budgets_api.py` calculates the example and compares
#: it against the fixture file itself rather than against these literals, so
#: the two cannot drift.
#:
#: The unit costs are the fixture's and are NOT from SCOPE: DoP 600/day,
#: gaffer 420/day, camera and lenses owned outright, Studio A sub-rented at
#: 1 100/day, the taxi rebilled at what it cost.
EXAMPLE_NAME = "Example (S1)"
EXAMPLE_SECTIONS: tuple[tuple[str, str, tuple], ...] = (
    (
        "1100",
        "Crew",
        (
            ("DoP", "labor", "1", "3", "day", "850.00", "600.00"),
            ("Gaffer", "labor", "1", "3", "day", "550.00", "420.00"),
        ),
    ),
    (
        "1200",
        "Equipment",
        (
            ("Alexa 35", "equipment_rental", "1", "3", "day", "1200.00", "0.00"),
            ("Lens set", "equipment_rental", "2", "3", "day", "180.00", "0.00"),
        ),
    ),
    (
        "1300",
        "Studio",
        (("Studio A", "space_rental", "1", "2", "day", "1400.00", "1100.00"),),
    ),
    (
        "1400",
        "Travel",
        (("Taxi", "travel", "1", "1", "flat", "120.00", "120.00"),),
    ),
)
#: The 5 % discount on Crew and the 8 % production fee on the whole budget.
EXAMPLE_CREW_DISCOUNT = ("Crew discount", "discount", "percent", "5")
EXAMPLE_FEE = ("Production fee", "surcharge", "percent", "8")


def standard_tax_ref(db: Session, company_id) -> str:
    """The company's `standard` tax class's code, or "" when it has none."""
    standard = (
        db.query(TaxCode)
        .join(TaxClass, TaxClass.code_id == TaxCode.id)
        .filter(
            TaxClass.company_id == company_id,
            TaxClass.key == "standard",
        )
        .first()
    )
    return standard.code if standard is not None else ""


def create_example_budget(db: Session, membership, project: Project) -> Budget:
    """The S1 budget, so the totals can be checked by eye.

    Labelled as an example in its own notes, because a budget that looks
    real and is not is the kind of thing that ends up quoted to a client.

    It also gets the two `days:<type>` globals, unused by its own lines:
    acceptance step 3 is "point the Alexa line's days at `shoot_days` and
    watch the numbers follow", and a global that has to be created first
    makes that step about creating a global.
    """
    tax_ref = standard_tax_ref(db, membership.company_id)

    budget = Budget(
        company_id=membership.company_id,
        project_id=project.id,
        name=EXAMPLE_NAME,
        currency=getattr(membership.company, "default_currency", None) or "EUR",
        notes=(
            "An EXAMPLE, created by FilmBill from SCOPE §12's S1 scenario so "
            "the arithmetic can be checked by eye: net 12,517.20 with Crew "
            "3,990.00 (after a 5 % discount), Equipment 4,680.00, Studio "
            "2,800.00, Travel 120.00 and an 8 % production fee. The unit "
            "costs are illustrative. Delete the lines and put real ones in, "
            "or duplicate it as a starting point."
        ),
        is_primary=False,
        is_active=True,
    )
    db.add(budget)
    db.flush()

    for key, label, day_type in (
        ("shoot_days", "Shoot days", "shoot"),
        ("prep_days", "Prep days", "prep"),
    ):
        db.add(
            BudgetGlobal(
                company_id=membership.company_id,
                budget_id=budget.id,
                key=key,
                label=label,
                value=Decimal(count_days_of_type(db, project.id, day_type)),
                unit="days",
                source=f"days:{day_type}",
                is_active=True,
            )
        )

    crew_id: Optional[uuid.UUID] = None
    for index, (code, name, lines) in enumerate(EXAMPLE_SECTIONS):
        section = BudgetSection(
            company_id=membership.company_id,
            budget_id=budget.id,
            code=code,
            name=name,
            position=(index + 1) * POSITION_STEP,
            is_active=True,
        )
        db.add(section)
        db.flush()
        if code == "1100":
            crew_id = section.id
        for offset, (
            description,
            kind,
            quantity,
            period_value,
            period_unit,
            unit_price,
            unit_cost,
        ) in enumerate(lines):
            db.add(
                BudgetLine(
                    company_id=membership.company_id,
                    section_id=section.id,
                    position=(offset + 1) * POSITION_STEP,
                    description=description,
                    kind=kind,
                    quantity=Decimal(quantity),
                    period_value=Decimal(period_value),
                    period_unit=period_unit,
                    factor=ONE,
                    factor_source="manual",
                    unit_price=Decimal(unit_price),
                    unit_cost=Decimal(unit_cost),
                    tax_ref=tax_ref,
                    tags=[],
                    is_active=True,
                )
            )

    label, type_, mode, value = EXAMPLE_CREW_DISCOUNT
    db.add(
        BudgetAdjustment(
            company_id=membership.company_id,
            budget_id=budget.id,
            section_id=crew_id,
            position=POSITION_STEP,
            label=label,
            type=type_,
            mode=mode,
            value=Decimal(value),
            base="subtotal",
            kinds=[],
            is_active=True,
        )
    )
    label, type_, mode, value = EXAMPLE_FEE
    db.add(
        BudgetAdjustment(
            company_id=membership.company_id,
            budget_id=budget.id,
            section_id=None,
            position=2 * POSITION_STEP,
            label=label,
            type=type_,
            mode=mode,
            value=Decimal(value),
            base="subtotal",
            kinds=[],
            is_active=True,
        )
    )
    db.flush()
    return budget


# ─── CSV out ────────────────────────────────────────────────────────────────


def export_rows(db: Session, budget: Budget) -> list[dict]:
    """One dict per active line, with the engine's own price, cost and margin.

    The numbers come from `calculated` and are written through as the strings
    the engine produced — nothing here formats or re-rounds an amount
    (rule 2).
    """
    answer = calculated(db, budget)
    by_id: dict[str, Any] = {}

    def walk(children):
        for child in children:
            if getattr(child, "type", None) == "group":
                walk(child.children)
            else:
                by_id[child.id] = child

    walk(answer["result"].children)

    sections = {section.id: section for section in active_sections(db, budget)}
    values = global_values(db, budget)
    # An extra's figures are its OWN (B1.1): a proposed or declined one is
    # not in the budget's document at all, and an accepted one's line
    # figures are the same either way.
    own = extras_own_result(db, budget)
    rows: list[dict] = []
    for line in active_lines(db, list(sections)):
        section = sections.get(line.section_id)
        is_extra = section is not None and section.is_extras
        computed = (
            (own.lines.get(str(line.id)) if own is not None else None)
            if is_extra
            else by_id.get(str(line.id))
        )
        # A line that follows a global is written with the number it is
        # CHARGED at (the global's value) and the key in its own column, so
        # the file says what the sheet says and reads back as a ref.
        quantity = values.get(line.quantity_ref, line.quantity) if line.quantity_ref else line.quantity
        period = values.get(line.period_ref, line.period_value) if line.period_ref else line.period_value
        rows.append(
            {
                "section_code": (section.code or "") if section is not None else "",
                "section_name": section.name if section is not None else "",
                "description": line.description,
                "kind": line.kind,
                "quantity": de_quantity(quantity),
                "quantity_global": line.quantity_ref or "",
                "period_value": de_quantity(period),
                "period_global": line.period_ref or "",
                "period_unit": line.period_unit,
                "factor": de_quantity(line.factor),
                "unit_price": de_amount(line.unit_price),
                "unit_cost": de_amount(line.unit_cost),
                "discount_mode": line.discount_mode or "",
                "discount_value": de_quantity(line.discount_value),
                "price": de_amount(computed.net) if computed is not None else "",
                "cost": de_amount(computed.cost_net) if computed is not None else "",
                "margin": de_amount(computed.margin) if computed is not None else "",
                "extra_status": line.extra_status or "",
            }
        )
    return rows


def export_file(db: Session, budget: Budget) -> bytes:
    return export_csv(export_rows(db, budget))


# ─── CSV in ─────────────────────────────────────────────────────────────────


def import_context(db: Session, budget: Budget) -> BudgetImportContext:
    """What already exists in this budget, as plain data for the pure planner."""
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
    line_refs: dict[str, tuple[str, str]] = {}
    seen: set[tuple] = set()
    ambiguous: set[tuple] = set()
    for line in active_lines(db, [section.id for section in sections]):
        key = (str(line.section_id), line.description.strip().lower())
        if key in seen:
            ambiguous.add(key)
        seen.add(key)
        lines_by_key.setdefault(key, str(line.id))
        line_refs[str(line.id)] = (line.quantity_ref or "", line.period_ref or "")

    return BudgetImportContext(
        line_refs=line_refs,
        sections_by_code=sections_by_code,
        sections_by_name={
            name: section_id
            for name, section_id in sections_by_name.items()
            if name not in ambiguous_names
        },
        ambiguous_section_names=frozenset(ambiguous_names),
        lines_by_key=lines_by_key,
        extras_sections=frozenset(str(row.id) for row in sections if row.is_extras),
        global_keys=frozenset(row.key for row in active_globals(db, budget)),
        ambiguous_keys=frozenset(ambiguous),
        kinds=frozenset(ITEM_KINDS),
        period_units=frozenset(PERIOD_UNITS),
    )


def preview_import(db: Session, budget: Budget, payload) -> BudgetImportPlan:
    """What the file would do. Writes nothing."""
    return plan_from_file(payload, import_context(db, budget))


def apply_import(
    db: Session,
    membership,
    budget: Budget,
    payload,
    *,
    actor: audit.Actor,
    expected_digest: str,
) -> BudgetImportPlan:
    """Apply the plan this file produces. No commit — the caller owns it.

    `expected_digest` is the digest the preview returned, and it is
    required. When the freshly planned import does not match it, **nothing
    is written** and the
    refusal names both digests: the budget changed between the preview
    somebody read and the apply they pressed, and applying something other
    than what was shown is the one thing an import must not do.

    Error rows are skipped rather than failing the whole file, for A1's
    reason: one typo in row 40 of a 200-row sheet must not waste the other
    199, and the response says which was which.
    """
    plan = preview_import(db, budget, payload)

    if plan.digest != expected_digest:
        raise BudgetError(
            f"this file no longer does what the preview showed (the preview "
            f"was {expected_digest}, the file now plans {plan.digest}). "
            f"Something in the budget changed in between — a line renamed, a "
            f"section added. Run the preview again and check it before "
            f"applying."
        )

    for row in plan.rows:
        if row.action == ACTION_CREATE:
            _create_from_row(db, membership, budget, row, actor=actor)
        elif row.action == ACTION_UPDATE:
            _update_from_row(db, row, actor=actor)

    audit.record(
        db,
        actor,
        budget,
        BUDGET_IMPORT_APPLIED,
        after={
            "created": len(plan.creates),
            "updated": len(plan.updates),
            "skipped_with_errors": len(plan.errors),
            "digest": plan.digest,
            "delimiter": plan.delimiter,
        },
    )
    db.flush()
    return plan


def _create_from_row(
    db: Session,
    membership,
    budget: Budget,
    row: BudgetImportRow,
    *,
    actor: audit.Actor,
) -> BudgetLine:
    values = row.values
    section = (
        db.query(BudgetSection)
        .filter(BudgetSection.id == uuid.UUID(row.section_id))
        .first()
    )
    line = BudgetLine(
        company_id=membership.company_id,
        section_id=section.id,
        position=next_line_position(db, section),
        description=values.description,
        kind=values.kind or KIND_OTHER,
        quantity=values.quantity if values.quantity is not None else ONE,
        quantity_ref=values.quantity_ref or None,
        period_value=values.period_value if values.period_value is not None else ONE,
        period_ref=values.period_ref or None,
        period_unit=values.period_unit or "day",
        factor=values.factor if values.factor is not None else ONE,
        factor_source="manual",
        unit_price=values.unit_price if values.unit_price is not None else ZERO,
        unit_cost=values.unit_cost if values.unit_cost is not None else ZERO,
        discount_mode=values.discount_mode,
        discount_value=values.discount_value,
        tax_ref="",
        tags=[],
        notes=values.notes,
        is_active=True,
    )
    db.add(line)
    db.flush()
    audit.record(
        db,
        actor,
        line,
        LINE_CREATED,
        None,
        audit.snapshot(line, LINE_AUDITED),
        fields=LINE_AUDITED,
    )
    return line


def _update_from_row(db: Session, row: BudgetImportRow, *, actor: audit.Actor) -> None:
    """Apply one update row. **A blank column keeps the stored value.**

    The alternative — a blank meaning zero — would turn a partial export
    into a way to zero out every column it did not carry.
    """
    values = row.values
    line = db.query(BudgetLine).filter(BudgetLine.id == uuid.UUID(row.line_id)).first()
    if line is None:  # pragma: no cover - the plan resolved it one statement ago
        return
    before = audit.snapshot(line, LINE_AUDITED)
    # Refs first: a present ref column decides whether the number column
    # applies at all. A line that follows a global keeps its stored number
    # untouched (it is the fallback if the ref is later cleared); the file's
    # number for it is the global's value, written by the export to be read.
    skip: set[str] = set()
    for ref_field, value_field in (("quantity_ref", "quantity"), ("period_ref", "period_value")):
        ref = getattr(values, ref_field)
        if ref is None:
            if getattr(line, ref_field):
                skip.add(value_field)
            continue
        setattr(line, ref_field, ref or None)
        if ref:
            skip.add(value_field)
    for field_name in (
        "kind",
        "quantity",
        "period_value",
        "period_unit",
        "factor",
        "unit_price",
        "unit_cost",
        "notes",
    ):
        if field_name in skip:
            continue
        value = getattr(values, field_name)
        if value is not None:
            setattr(line, field_name, value)
    if values.discount_mode is not None:
        line.discount_mode = values.discount_mode
        line.discount_value = values.discount_value
    db.flush()
    audit.record(
        db,
        actor,
        line,
        LINE_UPDATED,
        before,
        audit.snapshot(line, LINE_AUDITED),
        fields=LINE_AUDITED,
    )


# ─── Locking ────────────────────────────────────────────────────────────────


def lock(db: Session, budget: Budget) -> None:
    budget.locked_at = datetime.now(timezone.utc)
    db.flush()


def unlock(db: Session, budget: Budget) -> None:
    refuse_if_closed(budget, what="it cannot be unlocked")
    budget.locked_at = None
    db.flush()


def set_primary(db: Session, membership, budget: Budget) -> None:
    """Make this the project's primary budget, and the others not.

    One statement for the others rather than a loop, and both inside the
    caller's transaction: two primaries would make "the project's budget" a
    question with two answers, and a screen showing whichever came back
    first.
    """
    db.query(Budget).filter(
        Budget.company_id == membership.company_id,
        Budget.project_id == budget.project_id,
        Budget.id != budget.id,
        Budget.is_primary.is_(True),
    ).update({"is_primary": False}, synchronize_session="fetch")
    budget.is_primary = True
    db.flush()
