"""Settings → Company → Tax.

Five endpoints on the singular `/company/...` path — the active company comes
from `X-Company-Id` and from nowhere else (`middleware/company.py`) — and
every handler takes its context from `require(...)`, so there is no way to get
the context without the check.

**Nothing here decides any tax.** Every answer comes from
`billing/tax/engine.propose_tax` through `services/tax_service.propose`,
which is the single engine (CLAUDE.md rule 2). `POST /company/tax/propose`
exists for exactly that reason: the "Test a case" screen needs to show a
proposal while somebody is still picking options, and the alternative — a
rule evaluator in TypeScript — is two implementations of the thing that
decides what goes on an invoice.

**Permissions.** No new keys. Reads take `company.view`, which every member
including the tax advisor holds: a rate is not a secret, it is printed on
every document the company sends, and an accountant who cannot see the tax
table cannot check a document against it. Writes take
`company.settings.edit`, the same key as the rest of Settings → Company.

**Rules are read-only here.** SCOPE §6.3 makes editing them a later step; they
are per company from day one so that a company CAN edit them when that step
arrives, rather than being stuck with a shipped list.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..billing.tax import Override, PackError, treatment_label
from ..billing.tax.vocabulary import validate_rate_for_category
from ..core.money import quantize_qty
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.tax import TaxClass, TaxCode, TaxRule
from ..schemas.tax import (
    TaxClassResponse,
    TaxCodeResponse,
    TaxCodeUpdate,
    TaxProposalResponse,
    TaxProposeRequest,
    TaxRuleResponse,
    TaxWarningResponse,
    TaxWouldHaveAppliedResponse,
)
from ..services import audit, tax_service
from ..services.permissions import scoped

router = APIRouter(tags=["tax"])


#: What an audit row records about a tax code.
#:
#: `rate` and `is_active` first because they are what changes an invoice.
#: `legal_notes` is in it because the sentence printed beside the amount is
#: part of the document's legal content, and `notes_reviewed` because "who
#: said this wording was right" is exactly the question an audit log is read
#: to answer. `code` and `category` are NOT here — they cannot change.
TAX_CODE_AUDITED = (
    "rate",
    "is_active",
    "name",
    "legal_notes",
    "notes_reviewed",
    "revenue_account",
)


def _code_or_404(db: Session, membership: Membership, tax_code_id: uuid.UUID) -> TaxCode:
    """One of this company's codes, or 404.

    Through `scoped()`, so another company's id is not found rather than
    found-and-refused (rule 6). The same 404 covers "does not exist" and
    "belongs to somebody else", because answering differently would confirm
    the row exists.
    """
    row = (
        scoped(db.query(TaxCode), membership)
        .filter(TaxCode.id == tax_code_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tax code not found"
        )
    return row


def _class_response(row: TaxClass, code: TaxCode) -> TaxClassResponse:
    return TaxClassResponse(
        id=row.id,
        key=row.key,
        label=row.label,
        code_id=row.code_id,
        code=code.code,
        rate=code.rate,
        category=code.category,
        code_is_active=bool(code.is_active),
        position=row.position,
        origin_pack=row.origin_pack,
    )


def _override(request) -> Optional[Override]:
    if request is None:
        return None
    return Override(kind=request.kind, code=request.code)


# ─── Codes ──────────────────────────────────────────────────────────────────


@router.get("/company/tax/codes", response_model=list[TaxCodeResponse])
def list_tax_codes(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.view")),
):
    """This company's tax codes.

    Inactive ones are included BY DEFAULT, unlike the number-series list.
    The generic pack's standard code ships inactive at 0 % and the whole
    point of the screen is that somebody finds it and sets a rate; hiding it
    would hide the one row that needs attention.
    """
    query = scoped(db.query(TaxCode), membership)
    if not include_inactive:
        query = query.filter(TaxCode.is_active.is_(True))
    return query.order_by(TaxCode.code).all()


@router.patch("/company/tax/codes/{tax_code_id}", response_model=TaxCodeResponse)
def update_tax_code(
    tax_code_id: uuid.UUID,
    body: TaxCodeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Change a tax code's rate, name, notes, reviewed flag or active flag.

    `code` and `category` are not in `TaxCodeUpdate` at all and `extra` is
    forbidden, so sending either is a 422 naming the field rather than a
    silent no-op. A document line snapshots the code it used (rule 3), and
    renaming `AT-20` would restate invoices that are already issued.

    The rate/category rule is checked here with the same function the pack
    loader uses, so the message a user sees and the message a broken pack
    produces are the same sentence. The database's CHECK constraint is the
    floor under both.
    """
    row = _code_or_404(db, membership, tax_code_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        # Nothing to do, and nothing to audit. Returned rather than refused:
        # a save with no edits is a thing a form does, not an error.
        return row

    before = audit.snapshot(row, TAX_CODE_AUDITED)
    if changes.get("rate") is not None:
        # Through the one rounding helper (rule 1), not left to the column.
        # `NUMERIC(18,4)` would round it anyway, and then the audit row would
        # read "20.0000 → 21" — the old value at the column's scale and the
        # new one at whatever the request happened to send.
        changes["rate"] = quantize_qty(changes["rate"])
    for field, value in changes.items():
        setattr(row, field, value)

    try:
        validate_rate_for_category(row.code, row.category, row.rate)
    except PackError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None

    after = audit.snapshot(row, TAX_CODE_AUDITED)
    if audit.diff(before, after, fields=TAX_CODE_AUDITED):
        # In the caller's transaction, before the commit, so a failed save
        # leaves no event (services/audit.record).
        audit.record(
            db,
            audit.actor_from(request, membership=membership),
            row,
            "tax_code.updated",
            before,
            after,
            fields=TAX_CODE_AUDITED,
        )

    db.commit()
    db.refresh(row)
    return row


# ─── Classes and rules ──────────────────────────────────────────────────────


@router.get("/company/tax/classes", response_model=list[TaxClassResponse])
def list_tax_classes(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.view")),
):
    """The classes an item can point at, each with its code and rate resolved.

    Resolved here rather than by the client joining two lists: the web never
    works out a rate (rule 2), and "standard means 20 %" is a fact about this
    company that the server owns.
    """
    rows = (
        scoped(db.query(TaxClass), membership)
        .order_by(TaxClass.position, TaxClass.key)
        .all()
    )
    if not rows:
        return []

    codes = {
        row.id: row
        for row in scoped(db.query(TaxCode), membership).all()
    }
    return [
        _class_response(row, codes[row.code_id])
        for row in rows
        if row.code_id in codes
    ]


@router.get("/company/tax/rules", response_model=list[TaxRuleResponse])
def list_tax_rules(
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.view")),
):
    """The ordered rule list, read-only in P1a.

    Served even though nothing can edit it yet, because "which rule decided
    this" is unreadable without it: the proposal names a rule by position and
    name, and a screen that cannot show the list cannot show what else was
    considered.
    """
    return (
        scoped(db.query(TaxRule), membership)
        .order_by(TaxRule.position, TaxRule.name)
        .all()
    )


# ─── Proposing ──────────────────────────────────────────────────────────────


@router.post("/company/tax/propose", response_model=TaxProposalResponse)
def propose_tax_for_a_case(
    body: TaxProposeRequest,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.view")),
):
    """What tax this sale would carry. Reads; writes nothing.

    `company.view` and not `company.settings.edit`: this is the question an
    accountant asks about an invoice they are checking, and it changes
    nothing. It is also a POST rather than a GET despite being a read — the
    input has nested overrides, and a query string of nested objects is a
    thing every client encodes slightly differently.
    """
    proposal = tax_service.propose(
        db,
        company=membership.company,
        buyer_country=body.buyer_country,
        buyer_type=body.buyer_type,
        buyer_vat_verified=body.buyer_vat_verified,
        supply_kind=body.supply_kind,
        item_class=body.item_class,
        item_override=_override(body.item_override),
        party_override=_override(body.party_override),
        lang=body.lang,
    )

    would = None
    if proposal.would_have_applied is not None:
        source = proposal.would_have_applied
        would = TaxWouldHaveAppliedResponse(
            treatment=source.treatment,
            treatment_label=(
                treatment_label(source.treatment, body.lang)
                if source.treatment
                else None
            ),
            code=source.code,
            rate=source.rate,
            applied_rule=source.applied_rule,
            applied_rule_position=source.applied_rule_position,
            legal_note=source.legal_note,
        )

    return TaxProposalResponse(
        seller_country=membership.company.address_country,
        treatment=proposal.treatment,
        treatment_label=(
            treatment_label(proposal.treatment, body.lang)
            if proposal.treatment
            else None
        ),
        code=proposal.code,
        rate=proposal.rate,
        legal_note=proposal.legal_note,
        applied_rule=proposal.applied_rule,
        applied_rule_position=proposal.applied_rule_position,
        reasons=list(proposal.reasons),
        warnings=[
            TaxWarningResponse(code=warning.code, text=warning.text)
            for warning in proposal.warnings
        ],
        blocking=proposal.blocking,
        would_have_applied=would,
    )
