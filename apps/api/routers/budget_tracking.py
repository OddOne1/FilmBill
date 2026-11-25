"""Cost planning endpoints: commitments, actuals, ETCs and the forecast.

Every handler takes its context from `require("budgets.view")` or
`require("budgets.edit")`. **No new permission keys** — a cost entry is part
of the budget and exposes the same cost rates and margins the sheet does, so
there is nothing a person could be allowed to see here and not there. Staff
have neither key and therefore get 403 from every endpoint in this file;
`test_budget_tracking_api.py` asserts that by walking every endpoint as a
staff user and searching the whole response body for a cost word.

**No DELETE route, anywhere.** An entry is retired by
`POST .../void` with a reason, which sets `voided_at` and writes an audit
event; the entry stays in the ledger and leaves every number. The absence is
asserted on the OpenAPI document rather than on a handler, which is the shape
`routers/budgets.py`, `routers/projects.py` and `routers/catalog.py` all use.

**A locked budget still takes cost entries**, and this is the one place the
difference from `routers/budgets.py` matters: the write handlers here do
**not** call `_editable`. A lock freezes the estimate because the estimate is
the baseline the actuals are measured against — see
`services/budget_tracking_service.py`, point 3. The budget row is still
locked `FOR UPDATE` on a write, for the reason A2's `lock_for_write`
gives.

**Nothing here computes money** (rule 2). `GET /budgets/{id}/tracking`
returns `billing/budget/tracking.py`'s output for every line, section and the
budget, and the handlers that write a row return the row. A screen that wants
a number asks for the tracking view again.
"""

import uuid
from datetime import date
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
from sqlalchemy.orm import Session

from ..billing.catalog.csv_import import ImportFormatError
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.party import Party
from ..models.project import (
    Budget,
    BudgetActual,
    BudgetCommitment,
    BudgetLine,
    BudgetSection,
)
from ..schemas.budget_tracking import (
    ActualImportPreviewResponse,
    ActualImportResultResponse,
    ActualImportRowResponse,
    BudgetActualCreate,
    BudgetActualCreated,
    BudgetActualResponse,
    BudgetActualUpdate,
    BudgetCommitmentCreate,
    BudgetCommitmentCreated,
    BudgetCommitmentResponse,
    BudgetCommitmentUpdate,
    BudgetLedgerResponse,
    BudgetTrackingResponse,
    BulkFinishResponse,
    CloseRequest,
    CostEntryDuplicate,
    EtcRequest,
    FinishRequest,
    LineTrackingResponse,
    RollupTrackingResponse,
    VoidRequest,
)
from ..services import audit, budget_service, budget_tracking_service as tracking_service
from ..services.permissions import scoped

#: The `budgets` tag, not one of its own: a cost entry is part of the
#: budget, and `test_openapi_surface.py` keeps the tag set closed on purpose.
router = APIRouter(tags=["budgets"])


# ─── Lookups and refusals ───────────────────────────────────────────────────


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


def _commitment_or_404(
    db: Session, membership: Membership, budget: Budget, entry_id: uuid.UUID
) -> BudgetCommitment:
    row = (
        scoped(db.query(BudgetCommitment), membership)
        .filter(
            BudgetCommitment.id == entry_id,
            BudgetCommitment.budget_id == budget.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Commitment not found"
        )
    return row


def _actual_or_404(
    db: Session, membership: Membership, budget: Budget, entry_id: uuid.UUID
) -> BudgetActual:
    row = (
        scoped(db.query(BudgetActual), membership)
        .filter(BudgetActual.id == entry_id, BudgetActual.budget_id == budget.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Actual not found"
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget line not found"
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
            BudgetSection.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Budget section not found"
        )
    return row


def _refuse(error: Exception, code: int = status.HTTP_409_CONFLICT):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _refuse_tracking_error(error: budget_service.BudgetError):
    """A service refusal, mapped to the code that fits it.

    `TrackingNotFound` is a 404: the request named something that is not in
    this company, and a 403 would confirm that the row exists somewhere
    (CLAUDE.md rule 6). Everything else is a 409 — the request was
    well-formed and the state refused it.
    """
    if isinstance(error, tracking_service.TrackingNotFound):
        _refuse(error, status.HTTP_404_NOT_FOUND)
    _refuse(error)


def _writable(db: Session, budget: Budget) -> Budget:
    """`SELECT … FOR UPDATE` on the budget row, then the CLOSED check — and
    **no lock check**.

    Deliberately not `budgets._editable`: a locked budget accepts cost
    entries, because a locked ESTIMATE is the baseline they are measured
    against. See the module docstring. A CLOSED budget does not (B1.1): the
    production has ended, and every write in this router — orders,
    invoices, still to spend, finishing lines, the import — goes through
    here, so this is the one place that refusal lives. The row lock comes
    first so a close and a cost entry arriving together cannot both pass.
    """
    row = budget_service.lock_for_write(db, budget)
    try:
        budget_service.refuse_if_closed(row)
    except budget_service.BudgetError as error:
        _refuse(error)
    return row


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Response building ──────────────────────────────────────────────────────


class _Names:
    """Section codes, section names, line descriptions and party names.

    Built once per request and handed to every row, so a ledger of 300
    entries is four queries rather than nine hundred.
    """

    def __init__(self, db: Session, budget: Budget) -> None:
        self.sections = {
            row.id: row for row in budget_service.active_sections(db, budget)
        }
        self.lines = {
            row.id: row
            for row in budget_service.active_lines(db, list(self.sections))
        }
        self.parties = {
            row.id: row
            for row in db.query(Party).filter(Party.company_id == budget.company_id).all()
        }

    def payee(self, row) -> Optional[str]:
        if row.party_id is not None:
            party = self.parties.get(row.party_id)
            if party is not None:
                return party.name
        return row.payee_text

    def common(self, row) -> dict:
        section = self.sections.get(row.section_id)
        line = self.lines.get(row.line_id) if row.line_id is not None else None
        return {
            "section_name": section.name if section is not None else None,
            "section_code": section.code if section is not None else None,
            "line_description": line.description if line is not None else None,
            "payee": self.payee(row),
            "is_voided": row.voided_at is not None,
        }


def _commitment_response(
    db: Session, row: BudgetCommitment, names: _Names
) -> BudgetCommitmentResponse:
    linked = tracking_service.linked_actuals(db, row)
    return BudgetCommitmentResponse(
        **{
            name: getattr(row, name)
            for name in (
                "id",
                "budget_id",
                "section_id",
                "line_id",
                "party_id",
                "payee_text",
                "description",
                "reference",
                "amount",
                "notes",
                "voided_at",
                "void_reason",
                "created_at",
                "updated_at",
                "status",
                "commitment_date",
            )
        },
        **names.common(row),
        # The pure module's, through the service. Never a subtraction here.
        open_amount=tracking_service.open_amount(db, row),
        actual_count=len([entry for entry in linked if entry.voided_at is None]),
    )


def _actual_response(
    db: Session, row: BudgetActual, names: _Names
) -> BudgetActualResponse:
    reference = None
    if row.commitment_id is not None:
        commitment = (
            db.query(BudgetCommitment)
            .filter(BudgetCommitment.id == row.commitment_id)
            .first()
        )
        reference = commitment.reference if commitment is not None else None
    return BudgetActualResponse(
        **{
            name: getattr(row, name)
            for name in (
                "id",
                "budget_id",
                "section_id",
                "line_id",
                "party_id",
                "payee_text",
                "description",
                "reference",
                "amount",
                "notes",
                "voided_at",
                "void_reason",
                "created_at",
                "updated_at",
                "commitment_id",
                "incurred_on",
                "paid_on",
            )
        },
        **names.common(row),
        commitment_reference=reference,
    )


def _warnings(found: list[dict]) -> list[CostEntryDuplicate]:
    return [CostEntryDuplicate(**row) for row in found]


# ─── Commitments ────────────────────────────────────────────────────────────


@router.get(
    "/budgets/{budget_id}/commitments",
    response_model=list[BudgetCommitmentResponse],
)
def list_commitments(
    budget_id: uuid.UUID,
    section_id: Optional[uuid.UUID] = None,
    line_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    payee: Optional[str] = Query(default=None, max_length=255),
    entry_status: Optional[str] = Query(
        default=None, alias="status", description="open | closed | cancelled"
    ),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    voided: bool = Query(
        default=False, description="Show only voided entries."
    ),
    include_voided: bool = Query(
        default=False, description="Show voided entries alongside live ones."
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    budget = _budget_or_404(db, membership, budget_id)
    query = scoped(db.query(BudgetCommitment), membership).filter(
        BudgetCommitment.budget_id == budget.id
    )
    query = tracking_service.filtered(
        query,
        BudgetCommitment,
        section_id=section_id,
        line_id=line_id,
        party_id=party_id,
        payee=payee,
        voided=None if include_voided else voided,
        date_column=BudgetCommitment.commitment_date,
        date_from=date_from,
        date_to=date_to,
    )
    if entry_status:
        query = query.filter(BudgetCommitment.status == entry_status)
    rows = query.order_by(
        BudgetCommitment.commitment_date.desc(), BudgetCommitment.created_at.desc()
    ).all()
    names = _Names(db, budget)
    return [_commitment_response(db, row, names) for row in rows]


@router.post(
    "/budgets/{budget_id}/commitments",
    response_model=BudgetCommitmentCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_commitment(
    budget_id: uuid.UUID,
    body: BudgetCommitmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """One order. The response carries any duplicate warning beside it."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    try:
        row = tracking_service.create_commitment(
            db,
            membership,
            budget,
            actor=_actor(request, membership),
            section_id=body.section_id,
            line_id=body.line_id,
            party_id=body.party_id,
            payee_text=body.payee_text,
            description=body.description,
            reference=body.reference,
            amount=body.amount,
            commitment_date=body.commitment_date,
            notes=body.notes,
        )
        found = tracking_service.duplicates_of(
            db,
            budget,
            party_id=body.party_id,
            payee_text=body.payee_text,
            reference=body.reference,
            amount=body.amount,
            exclude_commitment=row.id,
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    names = _Names(db, budget)
    return BudgetCommitmentCreated(
        commitment=_commitment_response(db, row, names),
        duplicates=_warnings(found),
    )


@router.patch(
    "/budgets/{budget_id}/commitments/{commitment_id}",
    response_model=BudgetCommitmentResponse,
)
def update_commitment(
    budget_id: uuid.UUID,
    commitment_id: uuid.UUID,
    body: BudgetCommitmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _commitment_or_404(db, membership, budget, commitment_id)
    try:
        tracking_service.update_commitment(
            db,
            budget,
            row,
            actor=_actor(request, membership),
            changes=body.model_dump(exclude_unset=True),
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _commitment_response(db, row, _Names(db, budget))


@router.post(
    "/budgets/{budget_id}/commitments/{commitment_id}/close",
    response_model=BudgetCommitmentResponse,
)
def close_commitment(
    budget_id: uuid.UUID,
    commitment_id: uuid.UUID,
    request: Request,
    body: CloseRequest = CloseRequest(),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Settled, although the invoices do not add up to it.

    A supplier who invoices 940 against a 1 000 order and stops: the
    remaining 60 leaves the open amount the moment this is recorded, and
    only the producer knows it is never coming.
    """
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _commitment_or_404(db, membership, budget, commitment_id)
    try:
        tracking_service.close_commitment(
            db, row, actor=_actor(request, membership), reason=body.reason
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _commitment_response(db, row, _Names(db, budget))


@router.post(
    "/budgets/{budget_id}/commitments/{commitment_id}/cancel",
    response_model=BudgetCommitmentResponse,
)
def cancel_commitment(
    budget_id: uuid.UUID,
    commitment_id: uuid.UUID,
    request: Request,
    body: CloseRequest = CloseRequest(),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Withdrawn. It leaves `committed_gross` as well as the open amount."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _commitment_or_404(db, membership, budget, commitment_id)
    try:
        tracking_service.cancel_commitment(
            db, row, actor=_actor(request, membership), reason=body.reason
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _commitment_response(db, row, _Names(db, budget))


@router.post(
    "/budgets/{budget_id}/commitments/{commitment_id}/void",
    response_model=BudgetCommitmentResponse,
)
def void_commitment(
    budget_id: uuid.UUID,
    commitment_id: uuid.UUID,
    body: VoidRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Out of every number, still in the ledger. The reason is required."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _commitment_or_404(db, membership, budget, commitment_id)
    try:
        tracking_service.void_commitment(
            db, row, actor=_actor(request, membership), reason=body.reason
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _commitment_response(db, row, _Names(db, budget))


# ─── Actuals ────────────────────────────────────────────────────────────────


@router.get(
    "/budgets/{budget_id}/actuals", response_model=list[BudgetActualResponse]
)
def list_actuals(
    budget_id: uuid.UUID,
    section_id: Optional[uuid.UUID] = None,
    line_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    commitment_id: Optional[uuid.UUID] = None,
    payee: Optional[str] = Query(default=None, max_length=255),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    voided: bool = Query(
        default=False, description="Show only voided entries."
    ),
    include_voided: bool = Query(
        default=False, description="Show voided entries alongside live ones."
    ),
    unpaid: bool = Query(
        default=False, description="Only entries with no `paid_on`."
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    budget = _budget_or_404(db, membership, budget_id)
    query = scoped(db.query(BudgetActual), membership).filter(
        BudgetActual.budget_id == budget.id
    )
    query = tracking_service.filtered(
        query,
        BudgetActual,
        section_id=section_id,
        line_id=line_id,
        party_id=party_id,
        payee=payee,
        voided=None if include_voided else voided,
        date_column=BudgetActual.incurred_on,
        date_from=date_from,
        date_to=date_to,
    )
    if commitment_id is not None:
        query = query.filter(BudgetActual.commitment_id == commitment_id)
    if unpaid:
        query = query.filter(BudgetActual.paid_on.is_(None))
    rows = query.order_by(
        BudgetActual.incurred_on.desc(), BudgetActual.created_at.desc()
    ).all()
    names = _Names(db, budget)
    return [_actual_response(db, row, names) for row in rows]


@router.post(
    "/budgets/{budget_id}/actuals",
    response_model=BudgetActualCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_actual(
    budget_id: uuid.UUID,
    body: BudgetActualCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """One invoice or receipt. Closes its commitment if that settles it."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    try:
        row = tracking_service.create_actual(
            db,
            membership,
            budget,
            actor=_actor(request, membership),
            section_id=body.section_id,
            line_id=body.line_id,
            commitment_id=body.commitment_id,
            party_id=body.party_id,
            payee_text=body.payee_text,
            description=body.description,
            reference=body.reference,
            amount=body.amount,
            incurred_on=body.incurred_on,
            paid_on=body.paid_on,
            notes=body.notes,
        )
        found = tracking_service.duplicates_of(
            db,
            budget,
            party_id=body.party_id,
            payee_text=body.payee_text,
            reference=body.reference,
            amount=body.amount,
            exclude_actual=row.id,
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    names = _Names(db, budget)
    return BudgetActualCreated(
        actual=_actual_response(db, row, names), duplicates=_warnings(found)
    )


@router.patch(
    "/budgets/{budget_id}/actuals/{actual_id}", response_model=BudgetActualResponse
)
def update_actual(
    budget_id: uuid.UUID,
    actual_id: uuid.UUID,
    body: BudgetActualUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _actual_or_404(db, membership, budget, actual_id)
    try:
        tracking_service.update_actual(
            db,
            budget,
            row,
            actor=_actor(request, membership),
            changes=body.model_dump(exclude_unset=True),
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _actual_response(db, row, _Names(db, budget))


@router.post(
    "/budgets/{budget_id}/actuals/{actual_id}/void",
    response_model=BudgetActualResponse,
)
def void_actual(
    budget_id: uuid.UUID,
    actual_id: uuid.UUID,
    body: VoidRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Out of every number, still in the ledger — and its commitment reopens."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    row = _actual_or_404(db, membership, budget, actual_id)
    try:
        tracking_service.void_actual(
            db, budget, row, actor=_actor(request, membership), reason=body.reason
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return _actual_response(db, row, _Names(db, budget))


# ─── The duplicate check ────────────────────────────────────────────────────


@router.get(
    "/budgets/{budget_id}/duplicates", response_model=list[CostEntryDuplicate]
)
def check_duplicates(
    budget_id: uuid.UUID,
    amount: Decimal = Query(..., gt=0),
    party_id: Optional[uuid.UUID] = None,
    payee_text: Optional[str] = Query(default=None, max_length=255),
    reference: Optional[str] = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """Entries that look like the one about to be saved. Writes nothing.

    What the Add dialogs ask BEFORE they save, so the warning is in front of
    the person while they can still change their mind. It is the same
    comparison the create endpoints run afterwards (`duplicates_of`, keyed by
    `csv_actuals.duplicate_key`), so the warning before and the warning
    after cannot disagree — and it still never blocks: the dialog offers
    "save anyway".
    """
    budget = _budget_or_404(db, membership, budget_id)
    try:
        tracking_service.party_in_company(db, budget, party_id)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    return _warnings(
        tracking_service.duplicates_of(
            db,
            budget,
            party_id=party_id,
            payee_text=payee_text,
            reference=reference,
            amount=amount,
        )
    )


# ─── The ledger ─────────────────────────────────────────────────────────────


@router.get("/budgets/{budget_id}/ledger", response_model=BudgetLedgerResponse)
def read_ledger(
    budget_id: uuid.UUID,
    section_id: Optional[uuid.UUID] = None,
    line_id: Optional[uuid.UUID] = None,
    party_id: Optional[uuid.UUID] = None,
    payee: Optional[str] = Query(default=None, max_length=255),
    entry_status: Optional[str] = Query(default=None, alias="status"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    include_voided: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """Both kinds in one request, each in its own array.

    What the Costs tab reads. Two arrays rather than one merged list because
    the two have different dates and a merged list would have to invent a
    third column to sort on.
    """
    budget = _budget_or_404(db, membership, budget_id)
    names = _Names(db, budget)

    commitments = tracking_service.filtered(
        scoped(db.query(BudgetCommitment), membership).filter(
            BudgetCommitment.budget_id == budget.id
        ),
        BudgetCommitment,
        section_id=section_id,
        line_id=line_id,
        party_id=party_id,
        payee=payee,
        voided=None if include_voided else False,
        date_column=BudgetCommitment.commitment_date,
        date_from=date_from,
        date_to=date_to,
    )
    if entry_status:
        commitments = commitments.filter(BudgetCommitment.status == entry_status)

    actuals = tracking_service.filtered(
        scoped(db.query(BudgetActual), membership).filter(
            BudgetActual.budget_id == budget.id
        ),
        BudgetActual,
        section_id=section_id,
        line_id=line_id,
        party_id=party_id,
        payee=payee,
        voided=None if include_voided else False,
        date_column=BudgetActual.incurred_on,
        date_from=date_from,
        date_to=date_to,
    )

    return BudgetLedgerResponse(
        commitments=[
            _commitment_response(db, row, names)
            for row in commitments.order_by(
                BudgetCommitment.commitment_date.desc(),
                BudgetCommitment.created_at.desc(),
            ).all()
        ],
        actuals=[
            _actual_response(db, row, names)
            for row in actuals.order_by(
                BudgetActual.incurred_on.desc(), BudgetActual.created_at.desc()
            ).all()
        ],
    )


# ─── The ETC ────────────────────────────────────────────────────────────────


@router.put(
    "/budgets/{budget_id}/lines/{line_id}/etc",
    response_model=LineTrackingResponse,
)
def set_etc(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    body: EtcRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Set or clear what is still to be spent on this line.

    `null` clears it and the default rule takes over; `"0.00"` says the line
    is finished. The response is the line's forecast AFTER the change, so the
    drawer does not need a second request to show what moved.

    Allowed on a locked budget: an ETC is a measurement of the estimate, not
    a change to it.
    """
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    line = _line_or_404(db, membership, budget, line_id)
    try:
        tracking_service.set_etc(
            db, line, body.etc, actor=_actor(request, membership)
        )
        result = tracking_service.tracking(db, budget)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    found = result.lines.get(str(line.id))
    if found is None:  # pragma: no cover - an active line is always priced
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This line does not count in the budget's totals, so it has "
                "no forecast. Pick a line on the sheet, or an accepted extra."
            ),
        )
    return LineTrackingResponse(**vars(found))


# ─── Mark finished (B1.1) ───────────────────────────────────────────────────


def _line_forecast(db: Session, budget: Budget, line: BudgetLine) -> LineTrackingResponse:
    result = tracking_service.tracking(db, budget)
    found = result.lines.get(str(line.id))
    if found is None:  # pragma: no cover - an active line is always priced
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This line does not count in the budget's totals, so it has "
                "no forecast. Pick a line on the sheet, or an accepted extra."
            ),
        )
    return LineTrackingResponse(**vars(found))


@router.post(
    "/budgets/{budget_id}/lines/{line_id}/finish",
    response_model=LineTrackingResponse,
)
def finish_line(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    body: FinishRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Mark a line finished: nothing more is expected, still to spend 0.00.

    Allowed on a locked budget (D85). A line with an order still open is
    refused unless `confirm_open_commitment` is true. The response is the
    line's forecast after the change.
    """
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    line = _line_or_404(db, membership, budget, line_id)
    try:
        tracking_service.finish_line(
            db,
            budget,
            line,
            confirm_open_commitment=body.confirm_open_commitment,
            actor=_actor(request, membership),
        )
        answer = _line_forecast(db, budget, line)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return answer


@router.post(
    "/budgets/{budget_id}/lines/{line_id}/reopen",
    response_model=LineTrackingResponse,
)
def reopen_line(
    budget_id: uuid.UUID,
    line_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Undo "Mark finished": still to spend is worked out again."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    line = _line_or_404(db, membership, budget, line_id)
    try:
        tracking_service.reopen_line(db, line, actor=_actor(request, membership))
        answer = _line_forecast(db, budget, line)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return answer


@router.post(
    "/budgets/{budget_id}/sections/{section_id}/finish",
    response_model=BulkFinishResponse,
)
def finish_section(
    budget_id: uuid.UUID,
    section_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Finish all lines in a section, skipping any with an order still open.

    The skipped lines come back by name with their open amount. One audit
    event, `budget_line.finished_bulk`, with the counts.
    """
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    section = _section_or_404(db, membership, budget, section_id)
    try:
        answer = tracking_service.finish_section(
            db, budget, section, actor=_actor(request, membership)
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return BulkFinishResponse(**answer)


@router.post(
    "/budgets/{budget_id}/sections/{section_id}/reopen",
    response_model=BulkFinishResponse,
)
def reopen_section(
    budget_id: uuid.UUID,
    section_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Reopen every finished line in a section. One audit event."""
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    section = _section_or_404(db, membership, budget, section_id)
    try:
        answer = tracking_service.reopen_section(
            db, budget, section, actor=_actor(request, membership)
        )
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return BulkFinishResponse(**answer)


# ─── The forecast ───────────────────────────────────────────────────────────


@router.get("/budgets/{budget_id}/tracking", response_model=BudgetTrackingResponse)
def read_tracking(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """Committed, actual, EFC and variance for every line, section and total.

    The web app renders this and computes none of it (rule 2). The estimate
    side is the SAME calculation `GET .../calculated` returns — one engine
    call, through `budget_service.calc_result` — so the two views cannot
    disagree by a cent.
    """
    budget = _budget_or_404(db, membership, budget_id)
    budget_service.sync_computed_globals(db, budget)
    db.commit()
    try:
        result = tracking_service.tracking(db, budget)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    return BudgetTrackingResponse(
        budget=RollupTrackingResponse(**vars(result.budget)),
        lines={
            line_id: LineTrackingResponse(**vars(row))
            for line_id, row in result.lines.items()
        },
        sections={
            section_id: RollupTrackingResponse(**vars(row))
            for section_id, row in result.sections.items()
        },
    )


# ─── The actuals CSV ────────────────────────────────────────────────────────


@router.get("/budgets/{budget_id}/actuals/export.csv")
def export_actuals_csv(
    budget_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.view")),
):
    """The non-voided actuals, in the import's own columns.

    `;` and a UTF-8 BOM, like every other export here and for the same
    reason: Excel on a German-language Windows opens that correctly by
    double-clicking.
    """
    budget = _budget_or_404(db, membership, budget_id)
    payload = tracking_service.export_file(db, budget)
    name = f"actuals-{budget.name}".replace(" ", "-").replace("/", "-")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


def _import_rows(plan) -> list[ActualImportRowResponse]:
    return [
        ActualImportRowResponse(
            line_number=row.line_number,
            action=row.action,
            message=row.message,
            section_code=row.section_code,
            description=row.description,
            duplicate_of=(
                uuid.UUID(row.duplicate_of) if row.duplicate_of else None
            ),
        )
        for row in plan.rows
    ]


@router.post(
    "/budgets/{budget_id}/actuals/import/preview",
    response_model=ActualImportPreviewResponse,
)
def preview_actuals_import(
    budget_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """What the file would do. Writes nothing.

    A1's plan-then-apply pattern: the preview returns a `digest` of exactly
    what it plans, the apply sends it back, and an apply whose plan no longer
    matches is refused.
    """
    budget = _budget_or_404(db, membership, budget_id)
    try:
        plan = tracking_service.preview_import(db, budget, file.file.read())
    except ImportFormatError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    return ActualImportPreviewResponse(
        rows=_import_rows(plan),
        creates=len(plan.creates),
        duplicates=len(plan.duplicates),
        errors=len(plan.errors),
        digest=plan.digest,
        ignored_columns=list(plan.ignored_columns),
        delimiter=plan.delimiter,
    )


@router.post(
    "/budgets/{budget_id}/actuals/import",
    response_model=ActualImportResultResponse,
)
def apply_actuals_import(
    budget_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    #: Required. An apply without the digest of the preview somebody read
    #: would apply whatever the file plans NOW.
    expected_digest: str = Query(..., min_length=1),
    #: The line numbers of the duplicate rows the user ticked. Anything not
    #: in here that the preview marked `duplicate` is skipped.
    include_duplicates: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("budgets.edit")),
):
    """Apply exactly what the preview showed. Only ever creates.

    Never updates and never voids: a row that looks like an existing entry
    is held back, and an entry the file does not mention is left alone. An
    import that could rewrite what a shoot cost would make a stale export a
    weapon.
    """
    budget = _writable(db, _budget_or_404(db, membership, budget_id))
    try:
        plan, created = tracking_service.apply_import(
            db,
            membership,
            budget,
            file.file.read(),
            actor=_actor(request, membership),
            expected_digest=expected_digest,
            include_duplicates=include_duplicates,
        )
    except ImportFormatError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)
    except budget_service.BudgetError as error:
        _refuse_tracking_error(error)
    db.commit()
    return ActualImportResultResponse(
        rows=_import_rows(plan),
        creates=len(plan.creates),
        duplicates=len(plan.duplicates),
        errors=len(plan.errors),
        digest=plan.digest,
        ignored_columns=list(plan.ignored_columns),
        delimiter=plan.delimiter,
        applied=True,
        created=created,
    )
