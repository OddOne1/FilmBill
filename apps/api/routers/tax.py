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
    NoTaxCategoryOption,
    TaxClassResponse,
    TaxClassUpdate,
    TaxNoTaxCaseCreate,
    TaxRateCreate,
    TaxRateResponse,
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

#: What an audit row records about a class. `key` is in it even though it
#: cannot change, because a `tax_class.created` row with no key does not say
#: which class was created — and the key is what every item will store.
TAX_CLASS_AUDITED = ("key", "label", "code_id", "position", "is_active")

#: The sentence a rate change earns. Carried in the response rather than
#: written into the screen: it is a statement about what the system does
#: (rule 3 — an issued line keeps the code it snapshotted), and every API
#: consumer deserves to read it, not only this web app.
RATE_CHANGED_NOTICE = (
    "This rate applies to documents created from now on. Documents that "
    "have already been issued keep the rate they were issued with."
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


def _code_response(row: TaxCode, notices: Optional[list[str]] = None) -> TaxCodeResponse:
    """One code, plus anything the server wants said about the write.

    `notices` is empty on every read. It carries one sentence today — that a
    rate change applies from now on — and the screen renders whatever is in
    it rather than owning the wording.
    """
    response = TaxCodeResponse.model_validate(row)
    if notices:
        response.notices = list(notices)
    return response


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
        is_active=bool(row.is_active),
        position=row.position,
        origin_pack=row.origin_pack,
        is_custom=row.origin_pack is None,
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
    return [_code_response(row) for row in query.order_by(TaxCode.code).all()]


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
        return _code_response(row)

    # A rate and its class are a 1:1 pair, and the pair's `is_active` moves
    # together. Flipping only the code would leave a live class resolving to
    # a dead code — the item would still be labelled "Reduced" and the
    # proposal would refuse — which is a state nobody can explain from the
    # screen. The class endpoint does both.
    if "is_active" in changes:
        owning_class = tax_service.class_for_code(
            db, company_id=membership.company_id, code_id=row.id
        )
        if owning_class is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{row.code} is the code of the rate "
                    f"“{owning_class.label}”, so activate or deactivate the "
                    f"rate itself — PATCH /company/tax/classes/"
                    f"{owning_class.id}. A rate is a class and a code "
                    "together, and only one of the two switched off is a "
                    "state nothing can explain."
                ),
            )

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
    return _code_response(
        row, notices=[RATE_CHANGED_NOTICE] if "rate" in changes else []
    )


# ─── Rates: a class and its code, created together ──────────────────────────


@router.post(
    "/company/tax/rates",
    response_model=TaxRateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tax_rate(
    body: TaxRateCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Add a domestic rate — one `S` code and the class that points at it.

    Mathias, 2026-10-23: a company anywhere in Europe may need one reduced
    rate or three, and the number is the company's business. The pack is a
    starting point; this is how a company goes on from there.

    One transaction for both halves. A class with no code is a class every
    item in it resolves nothing from, and a code with no class is a no-tax
    case — so half of this succeeding would produce one of two things nobody
    asked for.
    """
    try:
        code_row, class_row = tax_service.create_rate(
            db,
            company=membership.company,
            label=body.label,
            rate=body.rate,
            code=body.code,
        )
    except tax_service.TaxConfigurationError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None

    actor = audit.actor_from(request, membership=membership)
    audit.record(
        db,
        actor,
        code_row,
        "tax_code.created",
        None,
        audit.snapshot(code_row, TAX_CODE_AUDITED + ("code", "category")),
        fields=TAX_CODE_AUDITED + ("code", "category"),
    )
    audit.record(
        db,
        actor,
        class_row,
        "tax_class.created",
        None,
        audit.snapshot(class_row, TAX_CLASS_AUDITED),
        fields=TAX_CLASS_AUDITED,
    )

    db.commit()
    db.refresh(code_row)
    db.refresh(class_row)
    return TaxRateResponse(
        tax_code=_code_response(code_row),
        tax_class=_class_response(class_row, code_row),
    )


@router.post(
    "/company/tax/no-tax-cases",
    response_model=TaxCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_no_tax_case(
    body: TaxNoTaxCaseCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Add a rate-0 code in one of the six no-tax categories. No class.

    Reverse charge inside the EU is the one everybody needs and the packs
    ship it; the rest is a company's own list — an exemption its advisor
    named, a supply that is not taxable where it sits.

    **No shipped rule can ever choose one of these**, and that is structural
    rather than a promise: a pack rule may only name a code the pack itself
    defines (the loader refuses anything else), and a company's own code is
    in no pack. It is selected explicitly, as an item or party override — and
    from P2, per document or per line.
    """
    try:
        row = tax_service.create_no_tax_case(
            db,
            company=membership.company,
            label=body.label,
            category=body.category,
            code=body.code,
            legal_notes=dict(body.legal_notes or {}),
        )
    except tax_service.TaxConfigurationError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        "tax_code.created",
        None,
        audit.snapshot(row, TAX_CODE_AUDITED + ("code", "category")),
        fields=TAX_CODE_AUDITED + ("code", "category"),
    )

    db.commit()
    db.refresh(row)
    return _code_response(row)


@router.get(
    "/company/tax/no-tax-categories", response_model=list[NoTaxCategoryOption]
)
def list_no_tax_categories(
    membership: Membership = Depends(require("company.view")),
):
    """The six categories a no-tax case may choose, with their one-liners.

    Served rather than written into the web app so the sentence a user
    chooses from lives beside the definition it explains, in both languages.
    It is the same list the create endpoint validates against, which is the
    property that matters: a dialog cannot offer a seventh.
    """
    return [
        NoTaxCategoryOption(category=category, labels=dict(labels))
        for category, labels in tax_service.NO_TAX_CATEGORIES.items()
    ]


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


def _class_or_404(
    db: Session, membership: Membership, tax_class_id: uuid.UUID
) -> TaxClass:
    row = (
        scoped(db.query(TaxClass), membership)
        .filter(TaxClass.id == tax_class_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tax class not found"
        )
    return row


@router.patch(
    "/company/tax/classes/{tax_class_id}", response_model=TaxClassResponse
)
def update_tax_class(
    tax_class_id: uuid.UUID,
    body: TaxClassUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("company.settings.edit")),
):
    """Rename a rate, or retire it.

    `key` is not in `TaxClassUpdate` and `extra` is forbidden: the key is
    what an ITEM stores, so renaming it would silently re-tax every item in
    that class. The label is the editable part.

    `is_active` sets the class AND its code, in this transaction. They are a
    1:1 pair (Mathias, 2026-10-23), and only one of the two switched off is a
    state nothing on the screen can explain.

    **Nothing is ever deleted.** A class any item has been labelled with is
    the only explanation of that item's tax, so retiring is a flag and there
    is no delete endpoint.
    """
    row = _class_or_404(db, membership, tax_class_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        code = _code_of_class(db, membership, row)
        return _class_response(row, code)

    code = _code_of_class(db, membership, row)
    before_class = audit.snapshot(row, TAX_CLASS_AUDITED)
    before_code = audit.snapshot(code, TAX_CODE_AUDITED)

    if "label" in changes and changes["label"] is not None:
        row.label = changes["label"]
    if "is_active" in changes and changes["is_active"] is not None:
        row.is_active = changes["is_active"]
        code.is_active = changes["is_active"]

    actor = audit.actor_from(request, membership=membership)
    after_class = audit.snapshot(row, TAX_CLASS_AUDITED)
    if audit.diff(before_class, after_class, fields=TAX_CLASS_AUDITED):
        audit.record(
            db,
            actor,
            row,
            "tax_class.updated",
            before_class,
            after_class,
            fields=TAX_CLASS_AUDITED,
        )
    after_code = audit.snapshot(code, TAX_CODE_AUDITED)
    if audit.diff(before_code, after_code, fields=TAX_CODE_AUDITED):
        # The code's own row, so "why is AT-20 inactive" is answerable from
        # the code's history and not only from the class's.
        audit.record(
            db,
            actor,
            code,
            "tax_code.updated",
            before_code,
            after_code,
            fields=TAX_CODE_AUDITED,
        )

    db.commit()
    db.refresh(row)
    db.refresh(code)
    return _class_response(row, code)


def _code_of_class(
    db: Session, membership: Membership, row: TaxClass
) -> TaxCode:
    """The class's code, scoped. A class without one cannot exist.

    Raised rather than returned as None: `tax_classes.code_id` is NOT NULL
    with a RESTRICT foreign key, so a missing one means the two tables
    disagree and a 500 is the honest answer.
    """
    code = (
        scoped(db.query(TaxCode), membership)
        .filter(TaxCode.id == row.code_id)
        .first()
    )
    if code is None:  # pragma: no cover - the FK makes this unreachable
        raise RuntimeError(
            f"tax class {row.key!r} points at code {row.code_id} which this "
            "company does not have; the foreign key should have prevented it."
        )
    return code


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
