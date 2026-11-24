"""The Budget sheet's endpoints.

Every handler takes its context from `require("budgets.view")` or
`require("budgets.edit")`. **Staff have neither** — the sheet exposes cost
rates and margins, and a crew-safe view is a later slice's work — so a staff
user gets 403 from every endpoint in this file and therefore never sees a
`cost` or a `margin` field at all. `test_budgets_api.py` asserts that by
walking every endpoint as a staff user and searching the whole response body
for either word.

**No DELETE route, anywhere.** A section, a line, a global and an adjustment
are all retired by `POST .../remove`, which sets `is_active = False` and
writes an audit event. The absence is asserted on the OpenAPI document.

**Nothing here computes money** (rule 2). `GET /budgets/{id}/calculated`
builds the engine's input from the stored rows and returns the engine's own
output; the handlers that write a row return the row, not a total. A screen
that wants a number asks for the calculated view again.

**The lock is checked in one place.** Every write handler calls
`budget_service.refuse_if_locked` before it touches anything, which is why
that function and not a flag is the thing the mutation test removes.
"""

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..billing.calc import PERIOD_UNITS
from ..billing.catalog.kinds import ITEM_KINDS, KIND_OTHER
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.catalog import Item
from ..models.project import (
    Budget,
    BudgetAdjustment,
    BudgetGlobal,
    BudgetLine,
    BudgetSection,
    Project,
    day_type_of_source,
    PROJECT_DAY_TYPES,
)
from ..schemas.budget import (
    BudgetAdjustmentCreate,
    BudgetAdjustmentResponse,
    BudgetAdjustmentUpdate,
    BudgetCalculatedResponse,
    BudgetCreate,
    BudgetDuplicateRequest,
    BudgetGlobalCreate,
    BudgetGlobalResponse,
    BudgetGlobalUpdate,
    BudgetImportPreviewResponse,
    BudgetImportResultResponse,
    BudgetImportRowResponse,
    BudgetLineCreate,
    BudgetLineResponse,
    BudgetLineUpdate,
    BudgetLockRequest,
    BudgetResponse,
    BudgetSectionCreate,
    BudgetSectionResponse,
    BudgetSectionUpdate,
    BudgetSheetResponse,
    BudgetUpdate,
    CatalogDifference,
)
from ..services import audit, budget_service
from ..services.permissions import scoped

router = APIRouter(tags=["budgets"])


# ─── Lookups and refusals ───────────────────────────────────────────────────


def _project_or_404(
    db: Session, membership: Membership, project_id: uuid.UUID
) -> Project:
    row = (
        scoped(db.query(Project), membership)
        .filter(Project.id == project_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return row


def _budget_or_404(
    db: Session, membership: Membership, budget_id: uuid.UUID
) -> Budget:
    row = (
        scoped(db.query(Budget), membership)
        .filter(Budget.id == budget_id, Budget.is_active.is_(True))
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found"
        )
    return row


def _section_or_404(
    db: Session, membership: Membership, budget: Budget, section_id: uuid.UUID
) -> BudgetSection:
    row = (
        scoped(db.query(BudgetSection), membership)
        .filter(
            BudgetSection.id == section_id,
            BudgetSection.budget_id == budget.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Section not found"
        )
    return row


def _line_or_404(
    db: Session, membership: Membership, budget: Budget, line_id: uuid.UUID
) -> BudgetLine:
    row = (
        scoped(db.query(BudgetLine), membership)
        .join(BudgetSection, BudgetSection.id == BudgetLine.section_id)
        .filter(BudgetLine.id == line_id, BudgetSection.budget_id == budget.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Line not found"
        )
    return row


def _global_or_404(
    db: Session, membership: Membership, budget: Budget, global_id: uuid.UUID
) -> BudgetGlobal:
    row = (
        scoped(db.query(BudgetGlobal), membership)
        .filter(BudgetGlobal.id == global_id, BudgetGlobal.budget_id == budget.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Global not found"
        )
    return row


def _adjustment_or_404(
    db: Session, membership: Membership, budget: Budget, adjustment_id: uuid.UUID
) -> BudgetAdjustment:
    row = (
        scoped(db.query(BudgetAdjustment), membership)
        .filter(
            BudgetAdjustment.id == adjustment_id,
            BudgetAdjustment.budget_id == budget.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Adjustment not found"
        )
    return row


def _refuse(error: Exception, code: int = status.HTTP_409_CONFLICT):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _refuse_budget_error(error: budget_service.BudgetError):
    """A service refusal, mapped to the status code that fits it.

    A `RefError` is a 422: the request named something that is not there, and
    the fix is in the request. Everything else is a 409: the request was
    well-formed and the state refused it.
    """
    if isinstance(error, budget_service.RefError):
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    _refuse(error)


def _editable(db: Session, budget: Budget) -> Budget:
    """Take the lock check and the row lock together.

    Every write goes through here: the lock refusal first, then
    `SELECT … FOR UPDATE` on the budget so that two people adding a line at
    the same moment cannot compute the same position.
    """
    try:
        budget_service.refuse_if_locked(budget)
    except budget_service.BudgetError as error:
        _refuse(error)
    return budget_service.lock_for_write(db, budget)


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Response building ──────────────────────────────────────────────────────


def _budget_response(db: Session, budget: Budget) -> BudgetResponse:
    sections = budget_service.active_sections(db, budget)
    lines = budget_service.active_lines(db, [row.id for row in sections])
    return BudgetResponse(
        id=budget.id,
        project_id=budget.project_id,
        name=budget.name,
        currency=budget.currency,
        notes=budget.notes,
        is_primary=budget.is_primary,
        is_active=budget.is_active,
        locked_at=budget.locked_at,
        is_locked=budget.locked_at is not None,
        section_count=len(sections),
        line_count=len(lines),
        created_at=budget.created_at,
        updated_at=budget.updated_at,
    )


def _line_response(db: Session, line: BudgetLine) -> BudgetLineResponse:
    difference = budget_service.catalog_difference(db, line)
    return BudgetLineResponse(
        id=line.id,
        section_id=line.section_id,
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
        is_active=line.is_active,
        catalog_difference=(
            CatalogDifference(**difference) if difference is not None else None
        ),
    )


def _global_response(
    db: Session, budget: Budget, row: BudgetGlobal
) -> BudgetGlobalResponse:
    computed = budget_service.computed_value(db, budget, row.source)
    return BudgetGlobalResponse(
        id=row.id,
        budget_id=row.budget_id,
        key=row.key,
        label=row.label,
        # The counted value when there is one, so the panel can never show a
        # stale number even if nothing has recalculated yet.
        value=row.value if computed is None else computed,
        unit=row.unit,
        source=row.source,
        is_computed=computed is not None,
        referenced_by=len(budget_service.lines_referencing(db, budget, row.key)),
        is_active=row.is_active,
    )


def _sheet(db: Session, budget: Budget) -> BudgetSheetResponse:
    sections = budget_service.active_sections(db, budget)
    lines = budget_service.active_lines(db, [row.id for row in sections])
    return BudgetSheetResponse(
        budget=_budget_response(db, budget),
        sections=[BudgetSectionResponse.model_validate(row) for row in sections],
        lines=[_line_response(db, row) for row in lines],
        globals=[
            _global_response(db, budget, row)
            for row in budget_service.active_globals(db, budget)
        ],
        adjustments=[
            BudgetAdjustmentResponse.model_validate(row)
            for row in budget_service.active_adjustments(db, budget)
        ],
    )


# ─── A project's budgets ────────────────────────────────────────────────────


@router.get(
    "/projects/{project_id}/budgets", response_model=list[BudgetResponse]
)
def list_budgets(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """Several per project is normal — "with the crane", "without"."""
    project = _project_or_404(db, membership, project_id)
    rows = (
        scoped(db.query(Budget), membership)
        .filter(Budget.project_id == project.id, Budget.is_active.is_(True))
        .order_by(Budget.is_primary.desc(), Budget.created_at)
        .all()
    )
    return [_budget_response(db, row) for row in rows]


@router.post(
    "/projects/{project_id}/budgets",
    response_model=BudgetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_budget(
    project_id: uuid.UUID,
    body: BudgetCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    project = _project_or_404(db, membership, project_id)
    company_currency = membership.company.default_currency
    if body.currency is not None and body.currency.upper() != company_currency:
        _refuse(
            budget_service.BudgetError(
                f"a budget is kept in the company currency, which is "
                f"{company_currency}. A2 writes no other — a budget that said "
                f"{body.currency.upper()} and was calculated in "
                f"{company_currency} would be worse than this refusal."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    budget = Budget(
        company_id=membership.company_id,
        project_id=project.id,
        name=body.name.strip(),
        currency=company_currency,
        notes=body.notes,
        is_primary=False,
        is_active=True,
    )
    db.add(budget)
    db.flush()
    if body.is_primary:
        budget_service.set_primary(db, membership, budget)

    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_CREATED,
        None,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


@router.post(
    "/projects/{project_id}/budgets/example",
    response_model=BudgetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_example_budget(
    project_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """The S1 budget from SCOPE §12, so the arithmetic can be checked by eye.

    Its own endpoint rather than a flag on the create, because it is a
    different act: "give me something to look at" on an empty project. It is
    labelled as an example in its own notes — a budget that looked real and
    was not is the kind of thing that ends up quoted to a client.
    """
    project = _project_or_404(db, membership, project_id)
    budget = budget_service.create_example_budget(db, membership, project)
    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_CREATED,
        None,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        reason="Created from SCOPE §12's S1 example",
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


# ─── One budget ─────────────────────────────────────────────────────────────


@router.get("/budgets/{budget_id}", response_model=BudgetSheetResponse)
def read_budget(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """The whole editable sheet: sections, lines, globals, adjustments.

    The NUMBERS are not in here — `GET .../calculated` has them. Two
    requests, because the sheet's rows are what an edit PATCHes and the
    totals are what an edit invalidates, and a single response would make
    "which of these did my edit change" unanswerable.
    """
    budget = _budget_or_404(db, membership, budget_id)
    budget_service.sync_computed_globals(db, budget)
    db.commit()
    return _sheet(db, budget)


@router.patch("/budgets/{budget_id}", response_model=BudgetResponse)
def update_budget(
    budget_id: uuid.UUID,
    body: BudgetUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Name and notes. Allowed while locked — the lock stops the numbers."""
    budget = _budget_or_404(db, membership, budget_id)
    before = audit.snapshot(budget, budget_service.BUDGET_AUDITED)
    for field_name, value in body.model_dump(exclude_unset=True).items():
        setattr(budget, field_name, value)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_UPDATED,
        before,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


@router.get(
    "/budgets/{budget_id}/calculated", response_model=BudgetCalculatedResponse
)
def calculated(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """Every number the sheet and the top sheet show, from the one engine.

    Refs are resolved to plain numbers here, BEFORE the engine is called
    (D70): a dangling one is a 422 naming the key rather than a line priced
    at one day.
    """
    budget = _budget_or_404(db, membership, budget_id)
    budget_service.sync_computed_globals(db, budget)
    db.commit()
    try:
        answer = budget_service.calculated(db, budget)
    except budget_service.BudgetError as error:
        _refuse_budget_error(error)
    return BudgetCalculatedResponse(
        budget_id=budget.id,
        currency=budget.currency,
        result=answer["result"],
        top_sheet=answer["top_sheet"],
        factors=answer["factors"],
    )


@router.post("/budgets/{budget_id}/lock", response_model=BudgetResponse)
def lock_budget(
    budget_id: uuid.UUID,
    body: BudgetLockRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _budget_or_404(db, membership, budget_id)
    before = audit.snapshot(budget, budget_service.BUDGET_AUDITED)
    budget_service.lock(db, budget)
    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_LOCKED,
        before,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        reason=body.reason,
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


@router.post("/budgets/{budget_id}/unlock", response_model=BudgetResponse)
def unlock_budget(
    budget_id: uuid.UUID,
    body: BudgetLockRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Unlocking is an ordinary audited action, not an escape hatch.

    The point of the lock is that a locked budget cannot be edited by
    ACCIDENT, not that it cannot be edited — and the audit row is what makes
    the difference visible afterwards.
    """
    budget = _budget_or_404(db, membership, budget_id)
    before = audit.snapshot(budget, budget_service.BUDGET_AUDITED)
    budget_service.unlock(db, budget)
    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_UNLOCKED,
        before,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        reason=body.reason,
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


@router.post("/budgets/{budget_id}/set-primary", response_model=BudgetResponse)
def set_primary(
    budget_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _budget_or_404(db, membership, budget_id)
    before = audit.snapshot(budget, budget_service.BUDGET_AUDITED)
    budget_service.set_primary(db, membership, budget)
    audit.record(
        db,
        _actor(request, membership),
        budget,
        budget_service.BUDGET_SET_PRIMARY,
        before,
        audit.snapshot(budget, budget_service.BUDGET_AUDITED),
        fields=budget_service.BUDGET_AUDITED,
    )
    db.commit()
    db.refresh(budget)
    return _budget_response(db, budget)


@router.post(
    "/budgets/{budget_id}/duplicate",
    response_model=BudgetResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_budget(
    budget_id: uuid.UUID,
    body: BudgetDuplicateRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """A scenario from a scenario. Numerically identical, and unlocked."""
    budget = _budget_or_404(db, membership, budget_id)
    copy = budget_service.duplicate(db, membership, budget, name=body.name)
    audit.record(
        db,
        _actor(request, membership),
        copy,
        budget_service.BUDGET_DUPLICATED,
        None,
        {
            "copied_from": str(budget.id),
            "copied_from_name": budget.name,
            "name": copy.name,
        },
    )
    db.commit()
    db.refresh(copy)
    return _budget_response(db, copy)


# ─── Sections ───────────────────────────────────────────────────────────────


@router.post(
    "/budgets/{budget_id}/sections",
    response_model=BudgetSectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_section(
    budget_id: uuid.UUID,
    body: BudgetSectionCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    parent = (
        _section_or_404(db, membership, budget, body.parent_id)
        if body.parent_id is not None
        else None
    )
    try:
        budget_service.check_parent_section(db, budget, parent)
    except budget_service.BudgetError as error:
        _refuse(error)

    row = BudgetSection(
        company_id=membership.company_id,
        budget_id=budget.id,
        code=(body.code or None),
        name=body.name.strip(),
        parent_id=parent.id if parent is not None else None,
        position=(
            body.position
            if body.position is not None
            else budget_service.next_section_position(
                db, budget, parent.id if parent is not None else None
            )
        ),
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.SECTION_CREATED,
        None,
        audit.snapshot(row, budget_service.SECTION_AUDITED),
        fields=budget_service.SECTION_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetSectionResponse.model_validate(row)


@router.patch(
    "/budgets/{budget_id}/sections/{section_id}",
    response_model=BudgetSectionResponse,
)
def update_section(
    budget_id: uuid.UUID,
    section_id: uuid.UUID,
    body: BudgetSectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _section_or_404(db, membership, budget, section_id)
    changes = body.model_dump(exclude_unset=True)

    if "parent_id" in changes:
        parent_id = changes["parent_id"]
        if parent_id == row.id:
            _refuse(
                budget_service.BudgetError(
                    f"{row.name!r} cannot be its own parent."
                )
            )
        parent = (
            _section_or_404(db, membership, budget, parent_id)
            if parent_id is not None
            else None
        )
        try:
            budget_service.check_parent_section(db, budget, parent)
        except budget_service.BudgetError as error:
            _refuse(error)
        # And the section being moved must not itself have children, or the
        # result would be three levels deep by another route.
        has_children = (
            db.query(func.count(BudgetSection.id))
            .filter(
                BudgetSection.parent_id == row.id,
                BudgetSection.is_active.is_(True),
            )
            .scalar()
        )
        if parent is not None and has_children:
            _refuse(
                budget_service.BudgetError(
                    f"{row.name!r} has {has_children} sub-section(s) of its "
                    f"own, so moving it inside {parent.name!r} would make "
                    f"them a third level — and the calculation engine nests "
                    f"groups two deep (SCOPE §5.4). Move those out first."
                )
            )

    before = audit.snapshot(row, budget_service.SECTION_AUDITED)
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.SECTION_UPDATED,
        before,
        audit.snapshot(row, budget_service.SECTION_AUDITED),
        fields=budget_service.SECTION_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetSectionResponse.model_validate(row)


@router.post(
    "/budgets/{budget_id}/sections/{section_id}/remove",
    response_model=BudgetSectionResponse,
)
def remove_section(
    budget_id: uuid.UUID,
    section_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Take a section out of the sheet. Refused while it still holds lines.

    Refused rather than cascading, because a section that quietly took its
    lines with it would change the total by an amount nobody asked about.
    The sentence says how many lines and what to do with them.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _section_or_404(db, membership, budget, section_id)

    lines = len(budget_service.active_lines(db, [row.id]))
    children = (
        db.query(func.count(BudgetSection.id))
        .filter(
            BudgetSection.parent_id == row.id,
            BudgetSection.is_active.is_(True),
        )
        .scalar()
    )
    if lines or children:
        _refuse(
            budget_service.BudgetError(
                f"{row.name!r} still holds {lines} line(s) and {children} "
                f"sub-section(s). Move or remove those first — a section that "
                f"took its lines with it would change the total by an amount "
                f"nobody asked about."
            )
        )

    before = audit.snapshot(row, budget_service.SECTION_AUDITED)
    row.is_active = False
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.SECTION_REMOVED,
        before,
        audit.snapshot(row, budget_service.SECTION_AUDITED),
        fields=budget_service.SECTION_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetSectionResponse.model_validate(row)


# ─── Lines ──────────────────────────────────────────────────────────────────


def _check_kind(value: Optional[str]) -> None:
    if value is not None and value not in ITEM_KINDS:
        _refuse(
            budget_service.BudgetError(
                f"{value!r} is not an item kind. The catalog's are "
                f"{', '.join(sorted(ITEM_KINDS))}."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.post(
    "/budgets/{budget_id}/lines",
    response_model=BudgetLineResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_line(
    budget_id: uuid.UUID,
    body: BudgetLineCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """A line, from the catalog or free-text.

    **The catalog's rates are COPIED here and never read again.** With an
    `item_id` and no prices, the item's rates for the line's period unit are
    copied onto the line; with prices, the prices given win, because somebody
    who typed a number meant it.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    section = _section_or_404(db, membership, budget, body.section_id)
    _check_kind(body.kind)

    defaults: dict = {}
    if body.item_id is not None:
        item = (
            scoped(db.query(Item), membership)
            .filter(Item.id == body.item_id)
            .first()
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
            )
        defaults = budget_service.line_defaults_from_item(
            db, membership, item, period_unit=body.period_unit
        )

    given = body.model_dump(exclude_unset=True)
    period_unit = (
        given.get("period_unit") or defaults.get("period_unit") or "day"
    )
    if period_unit not in PERIOD_UNITS:
        _refuse(
            budget_service.BudgetError(
                f"{period_unit!r} is not a period unit. The engine's are "
                f"{', '.join(sorted(PERIOD_UNITS))}."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    row = BudgetLine(
        company_id=membership.company_id,
        section_id=section.id,
        position=(
            body.position
            if body.position is not None
            else budget_service.next_line_position(db, section)
        ),
        item_id=body.item_id,
        description=(
            (body.description or "").strip()
            or defaults.get("description")
            or ""
        ),
        kind=given.get("kind") or defaults.get("kind") or KIND_OTHER,
        quantity=body.quantity,
        quantity_ref=body.quantity_ref,
        period_value=body.period_value,
        period_ref=body.period_ref,
        period_unit=period_unit,
        factor=body.factor,
        factor_source=(
            given.get("factor_source")
            or defaults.get("factor_source")
            or "manual"
        ),
        unit_price=(
            given.get("unit_price")
            if given.get("unit_price") is not None
            else defaults.get("unit_price", Decimal("0"))
        ),
        unit_cost=(
            given.get("unit_cost")
            if given.get("unit_cost") is not None
            else defaults.get("unit_cost", Decimal("0"))
        ),
        discount_mode=body.discount_mode,
        discount_value=body.discount_value,
        tax_ref=(
            given.get("tax_ref")
            if given.get("tax_ref") is not None
            else defaults.get("tax_ref", "")
        ),
        tags=list(body.tags or []),
        allow_negative=body.allow_negative,
        notes=body.notes,
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.LINE_CREATED,
        None,
        audit.snapshot(row, budget_service.LINE_AUDITED),
        fields=budget_service.LINE_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _line_response(db, row)


@router.patch(
    "/budgets/{budget_id}/lines/{line_id}", response_model=BudgetLineResponse
)
def update_line(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    body: BudgetLineUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """One cell, or a drag to another section — the same endpoint.

    The sheet's cell editing and its drag-and-drop are the same act to the
    person doing them. The audit action is `budget_line.moved` when the
    section or the position changed and `budget_line.updated` otherwise, so
    the log still distinguishes them.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _line_or_404(db, membership, budget, line_id)
    changes = body.model_dump(exclude_unset=True)
    clear = set(changes.pop("clear", []) or [])
    _check_kind(changes.get("kind"))

    if "section_id" in changes:
        target = _section_or_404(db, membership, budget, changes["section_id"])
        changes["section_id"] = target.id
        if "position" not in changes:
            changes["position"] = budget_service.next_line_position(db, target)

    if "period_unit" in changes and changes["period_unit"] not in PERIOD_UNITS:
        _refuse(
            budget_service.BudgetError(
                f"{changes['period_unit']!r} is not a period unit."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    moved = "section_id" in changes or "position" in changes
    before = audit.snapshot(row, budget_service.LINE_AUDITED)
    for field_name, value in changes.items():
        setattr(row, field_name, value)

    for field_name in clear:
        if field_name == "discount":
            row.discount_mode = None
            row.discount_value = None
        elif field_name == "item_id":
            # The line keeps its copied rates and becomes free text. What it
            # loses is the catalog comparison, which is the only thing
            # `item_id` was still doing.
            row.item_id = None
            row.factor_source = "manual"
        elif field_name == "tax_ref":
            row.tax_ref = ""
        else:
            setattr(row, field_name, None)

    if row.discount_mode is not None and row.discount_value is None:
        _refuse(
            budget_service.BudgetError(
                "a discount needs both a mode and a value. Send both, or "
                "clear the discount."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if row.factor_source == "catalog" and row.item_id is None:
        _refuse(
            budget_service.BudgetError(
                "a factor can only come from the catalog on a line that has "
                "an item — there is no table to look it up in otherwise. Set "
                "the factor by hand."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.LINE_MOVED if moved else budget_service.LINE_UPDATED,
        before,
        audit.snapshot(row, budget_service.LINE_AUDITED),
        fields=budget_service.LINE_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _line_response(db, row)


@router.post(
    "/budgets/{budget_id}/lines/{line_id}/duplicate",
    response_model=BudgetLineResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_line(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """"Another one of those" — the second most common act on a sheet."""
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _line_or_404(db, membership, budget, line_id)
    section = _section_or_404(db, membership, budget, row.section_id)

    copy = BudgetLine(
        company_id=membership.company_id,
        section_id=row.section_id,
        position=budget_service.next_line_position(db, section),
        item_id=row.item_id,
        description=row.description,
        kind=row.kind,
        quantity=row.quantity,
        quantity_ref=row.quantity_ref,
        period_value=row.period_value,
        period_ref=row.period_ref,
        period_unit=row.period_unit,
        factor=row.factor,
        factor_source=row.factor_source,
        unit_price=row.unit_price,
        unit_cost=row.unit_cost,
        discount_mode=row.discount_mode,
        discount_value=row.discount_value,
        tax_ref=row.tax_ref,
        tags=list(row.tags or []),
        allow_negative=row.allow_negative,
        notes=row.notes,
        is_active=True,
    )
    db.add(copy)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        copy,
        budget_service.LINE_CREATED,
        None,
        audit.snapshot(copy, budget_service.LINE_AUDITED),
        reason=f"Duplicated from {row.id}",
        fields=budget_service.LINE_AUDITED,
    )
    db.commit()
    db.refresh(copy)
    return _line_response(db, copy)


@router.post(
    "/budgets/{budget_id}/lines/{line_id}/remove",
    response_model=BudgetLineResponse,
)
def remove_line(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _line_or_404(db, membership, budget, line_id)
    before = audit.snapshot(row, budget_service.LINE_AUDITED)
    row.is_active = False
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.LINE_REMOVED,
        before,
        audit.snapshot(row, budget_service.LINE_AUDITED),
        fields=budget_service.LINE_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _line_response(db, row)


# ─── Globals ────────────────────────────────────────────────────────────────


def _check_source(source: str) -> None:
    if source == "manual":
        return
    day_type = day_type_of_source(source)
    if day_type is None or day_type not in PROJECT_DAY_TYPES:
        _refuse(
            budget_service.BudgetError(
                f"{source!r} is not a global source. A global is either "
                f"`manual` or counted from the project's days as "
                f"`days:<type>`, with a type from "
                f"{', '.join(PROJECT_DAY_TYPES)}. There is no expression "
                f"language here on purpose (SCOPE §16 D70): a formula field "
                f"would be a second programming language inside a budget."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


@router.post(
    "/budgets/{budget_id}/globals",
    response_model=BudgetGlobalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_global(
    budget_id: uuid.UUID,
    body: BudgetGlobalCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    _check_source(body.source)
    key = body.key.strip().lower()

    existing = (
        db.query(BudgetGlobal)
        .filter(BudgetGlobal.budget_id == budget.id, BudgetGlobal.key == key)
        .first()
    )
    if existing is not None:
        if existing.is_active:
            _refuse(
                budget_service.BudgetError(
                    f"this budget already has a global called {key!r}. Edit "
                    f"that one — two globals with one key would make a line's "
                    f"ref ambiguous."
                )
            )
        # Reactivating rather than inserting a second row: the key is unique
        # per budget in the database, so a removed global is the row that
        # comes back.
        before = audit.snapshot(existing, budget_service.GLOBAL_AUDITED)
        existing.is_active = True
        existing.label = body.label.strip()
        existing.unit = body.unit
        existing.source = body.source
        existing.value = body.value
        db.flush()
        audit.record(
            db,
            _actor(request, membership),
            existing,
            budget_service.GLOBAL_UPDATED,
            before,
            audit.snapshot(existing, budget_service.GLOBAL_AUDITED),
            fields=budget_service.GLOBAL_AUDITED,
        )
        db.commit()
        db.refresh(existing)
        return _global_response(db, budget, existing)

    computed = budget_service.computed_value(db, budget, body.source)
    row = BudgetGlobal(
        company_id=membership.company_id,
        budget_id=budget.id,
        key=key,
        label=body.label.strip(),
        value=body.value if computed is None else computed,
        unit=body.unit,
        source=body.source,
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.GLOBAL_CREATED,
        None,
        audit.snapshot(row, budget_service.GLOBAL_AUDITED),
        fields=budget_service.GLOBAL_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _global_response(db, budget, row)


@router.patch(
    "/budgets/{budget_id}/globals/{global_id}",
    response_model=BudgetGlobalResponse,
)
def update_global(
    budget_id: uuid.UUID,
    global_id: uuid.UUID,
    body: BudgetGlobalUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """A manual global's value, or any global's label.

    A COMPUTED global's value is refused: it is the project's day count, and
    a typed-over one would disagree with the Days tab the moment anybody
    looked at both.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _global_or_404(db, membership, budget, global_id)
    changes = body.model_dump(exclude_unset=True)

    if "value" in changes and day_type_of_source(row.source) is not None:
        _refuse(
            budget_service.BudgetError(
                f"{row.key} is counted from the project's "
                f"{day_type_of_source(row.source)} days and cannot be typed "
                f"over. Add or remove a day in the Days tab and this follows."
            )
        )

    before = audit.snapshot(row, budget_service.GLOBAL_AUDITED)
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.GLOBAL_UPDATED,
        before,
        audit.snapshot(row, budget_service.GLOBAL_AUDITED),
        fields=budget_service.GLOBAL_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _global_response(db, budget, row)


@router.post(
    "/budgets/{budget_id}/globals/{global_id}/remove",
    response_model=BudgetGlobalResponse,
)
def remove_global(
    budget_id: uuid.UUID,
    global_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Refused while a line still points at it, with the lines named.

    Removing it silently would turn every line that used it into a 422 on
    the next calculation, and the person who hit that would have no way to
    know what had been taken away.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _global_or_404(db, membership, budget, global_id)
    try:
        budget_service.refuse_removing_referenced_global(db, budget, row)
    except budget_service.BudgetError as error:
        _refuse(error)

    before = audit.snapshot(row, budget_service.GLOBAL_AUDITED)
    row.is_active = False
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.GLOBAL_REMOVED,
        before,
        audit.snapshot(row, budget_service.GLOBAL_AUDITED),
        fields=budget_service.GLOBAL_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _global_response(db, budget, row)


# ─── Adjustments ────────────────────────────────────────────────────────────


@router.post(
    "/budgets/{budget_id}/adjustments",
    response_model=BudgetAdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_adjustment(
    budget_id: uuid.UUID,
    body: BudgetAdjustmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """A discount or surcharge, on a section or on the whole budget.

    **It moves the price and never the cost** — the engine's behaviour, not a
    choice made here. A contingency or a production fee that has to raise the
    COST side is an ordinary line of kind `fee` instead (D78).
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    section = (
        _section_or_404(db, membership, budget, body.section_id)
        if body.section_id is not None
        else None
    )
    if body.base == "kinds" and not body.kinds:
        _refuse(
            budget_service.BudgetError(
                "an adjustment based on kinds needs at least one kind — with "
                "none it would apply to nothing, which is not what anybody "
                "means by it."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    row = BudgetAdjustment(
        company_id=membership.company_id,
        budget_id=budget.id,
        section_id=section.id if section is not None else None,
        position=(
            body.position
            if body.position is not None
            else budget_service.next_adjustment_position(db, budget)
        ),
        label=body.label.strip(),
        type=body.type,
        mode=body.mode,
        value=body.value,
        base=body.base,
        kinds=list(body.kinds or []),
        is_active=True,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.ADJUSTMENT_CREATED,
        None,
        audit.snapshot(row, budget_service.ADJUSTMENT_AUDITED),
        fields=budget_service.ADJUSTMENT_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetAdjustmentResponse.model_validate(row)


@router.patch(
    "/budgets/{budget_id}/adjustments/{adjustment_id}",
    response_model=BudgetAdjustmentResponse,
)
def update_adjustment(
    budget_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    body: BudgetAdjustmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _adjustment_or_404(db, membership, budget, adjustment_id)
    changes = body.model_dump(exclude_unset=True)

    before = audit.snapshot(row, budget_service.ADJUSTMENT_AUDITED)
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    if row.base == "kinds" and not (row.kinds or []):
        _refuse(
            budget_service.BudgetError(
                "an adjustment based on kinds needs at least one kind."
            ),
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.ADJUSTMENT_UPDATED,
        before,
        audit.snapshot(row, budget_service.ADJUSTMENT_AUDITED),
        fields=budget_service.ADJUSTMENT_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetAdjustmentResponse.model_validate(row)


@router.post(
    "/budgets/{budget_id}/adjustments/{adjustment_id}/remove",
    response_model=BudgetAdjustmentResponse,
)
def remove_adjustment(
    budget_id: uuid.UUID,
    adjustment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    row = _adjustment_or_404(db, membership, budget, adjustment_id)
    before = audit.snapshot(row, budget_service.ADJUSTMENT_AUDITED)
    row.is_active = False
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        row,
        budget_service.ADJUSTMENT_REMOVED,
        before,
        audit.snapshot(row, budget_service.ADJUSTMENT_AUDITED),
        fields=budget_service.ADJUSTMENT_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return BudgetAdjustmentResponse.model_validate(row)


# ─── CSV ────────────────────────────────────────────────────────────────────


@router.get("/budgets/{budget_id}/export.csv")
def export_budget_csv(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """The sheet as a CSV, with the engine's own price, cost and margin.

    `;` and a UTF-8 BOM, like the catalog's export and for the same reason:
    Excel on a German-language Windows opens that correctly by
    double-clicking, and a comma-separated UTF-8 file without a BOM arrives
    as one column of mojibake.
    """
    budget = _budget_or_404(db, membership, budget_id)
    budget_service.sync_computed_globals(db, budget)
    db.commit()
    try:
        payload = budget_service.export_file(db, budget)
    except budget_service.BudgetError as error:
        _refuse_budget_error(error)

    name = f"budget-{budget.name}".replace(" ", "-").replace("/", "-")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


def _import_rows(plan) -> list[BudgetImportRowResponse]:
    return [
        BudgetImportRowResponse(
            line_number=row.line_number,
            action=row.action,
            message=row.message,
            section_code=row.section_code,
            description=row.description,
        )
        for row in plan.rows
    ]


@router.post(
    "/budgets/{budget_id}/import/preview",
    response_model=BudgetImportPreviewResponse,
)
def preview_import(
    budget_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """What the file would do. Writes nothing.

    A1's plan-then-apply pattern: the preview returns a `digest` of exactly
    what it plans, the apply sends it back, and an apply whose plan no longer
    matches is refused. Applying something other than what was shown is the
    one thing an import must not do.
    """
    budget = _budget_or_404(db, membership, budget_id)
    try:
        plan = budget_service.preview_import(db, budget, file.file.read())
    except budget_service.BudgetError as error:
        _refuse_budget_error(error)
    return BudgetImportPreviewResponse(
        rows=_import_rows(plan),
        creates=len(plan.creates),
        updates=len(plan.updates),
        errors=len(plan.errors),
        digest=plan.digest,
        ignored_columns=list(plan.ignored_columns),
        delimiter=plan.delimiter,
    )


@router.post(
    "/budgets/{budget_id}/import", response_model=BudgetImportResultResponse
)
def apply_import(
    budget_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    #: Required. An apply without the digest of the preview somebody read
    #: would apply whatever the file plans NOW, which is the one thing the
    #: plan→apply split exists to rule out.
    expected_digest: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Apply exactly what the preview showed. Never deletes.

    A row naming an unknown section code is an ERROR and not an invention: a
    typo would otherwise become a permanent section nobody meant to create.
    """
    budget = _editable(db, _budget_or_404(db, membership, budget_id))
    try:
        plan = budget_service.apply_import(
            db,
            membership,
            budget,
            file.file.read(),
            actor=_actor(request, membership),
            expected_digest=expected_digest,
        )
    except budget_service.BudgetError as error:
        _refuse_budget_error(error)
    db.commit()
    return BudgetImportResultResponse(
        rows=_import_rows(plan),
        creates=len(plan.creates),
        updates=len(plan.updates),
        errors=len(plan.errors),
        digest=plan.digest,
        ignored_columns=list(plan.ignored_columns),
        delimiter=plan.delimiter,
        applied=True,
    )
