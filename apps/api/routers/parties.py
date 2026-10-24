"""Customers, suppliers, partners, and the people inside them.

Every handler takes its context from `require("parties.view")` or
`require("parties.edit")`, so there is no way to get the company context
without the check, and another company's row is a 404 (rule 6).

**No DELETE route, anywhere.** A party that appears on an issued document is
part of that document's explanation, so retiring is `POST .../archive` and
there is a test asserting the absence on the OpenAPI document rather than on
a handler's behaviour — the same shape as P1a-2's.

**The web never computes tax.** `POST /parties/{id}/tax-preview` builds the
engine's inputs from the resolved recipient and calls the one engine, so the
answer a party screen shows and the answer Settings → Company → Tax shows
come from the same function with the same inputs.

**Duplicates are warnings.** Blocking a create on a guess is worse than a
duplicate: the guess is sometimes wrong, and somebody who cannot save a
customer cannot invoice them.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..billing.parties import VatFormatError, normalise_and_validate, tax_inputs
from ..core.iban import normalise_iban
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.party import (
    Party,
    PartyAddress,
    PartyBankAccount,
    PartyContactPoint,
    PartyTaxId,
    PartyVatCheck,
)
from ..schemas.party import (
    DuplicateWarning,
    PartyAddressResponse,
    PartyAddressWrite,
    PartyBankAccountResponse,
    PartyBankAccountWrite,
    PartyChild,
    PartyContactPointResponse,
    PartyContactPointWrite,
    PartyCreate,
    PartyPage,
    PartyResponse,
    PartyTaxPreviewRequest,
    PartyVatCheckResponse,
    PartyWrite,
    ResolvedFieldResponse,
    ResolvedRecipientResponse,
)
from ..schemas.tax import TaxProposalResponse, TaxWarningResponse, TaxWouldHaveAppliedResponse
from ..services import audit, party_service, tax_service, vies
from ..services.permissions import scoped

router = APIRouter(tags=["parties"])

#: What an audit row records about a party. The fields that end up on a
#: document come first: a change to any of them restates what a future
#: invoice will say.
PARTY_AUDITED = (
    "name",
    "trading_name",
    # `vat_id` is gone from here with the column (D59). A change to an
    # identifier is audited on the identifier itself, under
    # `party_tax_id.*`, which is strictly more informative: it says which
    # KIND changed and keeps the old value as a retired row.
    "tax_country",
    "tax_status_note",
    "recheck_interval_days",
    "business_status",
    "tax_override",
    "is_customer",
    "is_supplier",
    "is_partner",
    "party_number",
    "parent_id",
    "language",
    "default_currency",
    "payment_terms_days",
    "payment_terms_text",
    "po_reference",
    "legal_form",
    "register_number",
    "register_court",
    "title",
    "position",
    "tags",
    "einvoice_routing",
    "notes",
    "is_active",
)

#: A party bank account's audited fields. `iban` is in it and is MASKED by
#: `services/audit.redact` — the same treatment the company's own gets, for
#: the same reason (D43): an audit log must not become a way round the gate
#: on the thing it describes.
BANK_ACCOUNT_AUDITED = ("label", "iban", "bic", "bank_name", "is_default")


def _party_or_404(db: Session, membership: Membership, party_id: uuid.UUID) -> Party:
    row = (
        scoped(db.query(Party), membership).filter(Party.id == party_id).first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Party not found"
        )
    return row


def _refuse(error: Exception, code: int = status.HTTP_409_CONFLICT):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _latest_check(db: Session, party: Party) -> Optional[PartyVatCheck]:
    return (
        db.query(PartyVatCheck)
        .filter(PartyVatCheck.party_id == party.id)
        .order_by(PartyVatCheck.checked_at.desc())
        .first()
    )


def _primary_value(db: Session, party: Party) -> Optional[str]:
    """The party's primary VAT-like identifier's value, or None.

    A thin wrapper so the duplicate check and the response builder ask the
    service the same question rather than each writing their own query —
    "which number stands for this party" has exactly one right answer and it
    lives in `party_service.primary_tax_id`.
    """
    row = party_service.primary_tax_id(db, party)
    return row.value if row else None


def _check_response(row: PartyVatCheck, detail: str = "") -> PartyVatCheckResponse:
    response = dict(row.response or {})
    return PartyVatCheckResponse(
        id=row.id,
        vat_id=row.vat_id,
        country=row.country,
        status=row.status,
        response=response,
        consultation_ref=row.consultation_ref,
        checked_at=row.checked_at,
        detail=detail,
        trader_name=response.get("_trader_name"),
        trader_address=response.get("_trader_address"),
    )


def _party_response(
    db: Session,
    membership: Membership,
    party: Party,
    *,
    duplicates: Optional[list] = None,
) -> PartyResponse:
    """One party with everything a screen needs, in one request.

    The sub-resources come with it because every screen that shows one shows
    all of them, and three round trips to render a detail page is three
    chances for a half-loaded screen.
    """
    resolved = party_service.resolve(db, party)
    inputs = tax_inputs(resolved)
    latest = _latest_check(db, party)

    return PartyResponse(
        **{
            name: getattr(party, name)
            for name in (
                "id", "type", "parent_id", "name", "trading_name", "legal_form",
                "register_number", "register_court", "title", "position",
                "is_customer", "is_supplier", "is_partner", "party_number",
                "language", "default_currency", "payment_terms_days",
                "payment_terms_text", "po_reference", "notes",
                "business_status", "tax_country", "tax_override",
                "tax_status_note", "recheck_interval_days",
                "is_active", "created_at", "updated_at",
            )
        },
        tags=list(party.tags or []),
        einvoice_routing=dict(party.einvoice_routing or {}),
        vat_verified=party_service.vat_is_verified(db, party),
        latest_vat_check=_check_response(latest) if latest else None,
        buyer_type=inputs["buyer_type"],
        buyer_type_reason=inputs["buyer_type_reason"],
        addresses=[
            PartyAddressResponse.model_validate(row)
            for row in db.query(PartyAddress)
            .filter(PartyAddress.party_id == party.id)
            .order_by(PartyAddress.kind, PartyAddress.is_default.desc())
        ],
        contact_points=[
            PartyContactPointResponse.model_validate(row)
            for row in db.query(PartyContactPoint)
            .filter(PartyContactPoint.party_id == party.id)
            .order_by(PartyContactPoint.kind, PartyContactPoint.is_primary.desc())
        ],
        bank_accounts=[
            PartyBankAccountResponse.model_validate(row)
            for row in db.query(PartyBankAccount)
            .filter(PartyBankAccount.party_id == party.id)
            .order_by(PartyBankAccount.is_default.desc())
        ],
        children=[
            PartyChild.model_validate(row)
            for row in db.query(Party)
            .filter(Party.company_id == party.company_id, Party.parent_id == party.id)
            .order_by(Party.type, Party.name)
        ],
        duplicates=[DuplicateWarning(**entry) for entry in (duplicates or [])],
    )


def _normalised_vat(party_or_body, raw: Optional[str], country: Optional[str]):
    """A VAT ID, normalised and format-checked, or a 422 with a sentence.

    Before any network call, which is the point: a typo costs a sentence in a
    tenth of a second rather than an eight-second timeout.
    """
    if raw is None or not str(raw).strip():
        return None
    try:
        return normalise_and_validate(str(raw), default_country=country)
    except VatFormatError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)


def _apply(db: Session, membership: Membership, party: Party, changes: dict) -> None:
    """Write `changes` onto `party`.

    No VAT-ID normalisation here any more: identifiers are their own rows
    with their own endpoints, and each kind normalises differently (D59).
    A party PATCH that could still set a number would be a second write path
    for the thing the whole of P1b-2 exists to give one.
    """
    for field, value in changes.items():
        if field == "parent_id":
            continue
        setattr(party, field, value)


# ─── The list ───────────────────────────────────────────────────────────────


@router.get("/parties", response_model=PartyPage)
def list_parties(
    search: Optional[str] = Query(default=None, max_length=255),
    role: Optional[str] = Query(
        default=None,
        description="customer | supplier | partner | person",
    ),
    type: Optional[str] = Query(default=None),
    include_inactive: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """Search by name, trading name, party number, VAT ID or email.

    One query over five columns rather than five filters, because somebody
    looking for a customer types what they remember and does not know which
    field it was.
    """
    query = scoped(db.query(Party), membership)
    if not include_inactive:
        query = query.filter(Party.is_active.is_(True))
    if type:
        query = query.filter(Party.type == type)
    if role == "customer":
        query = query.filter(Party.is_customer.is_(True))
    elif role == "supplier":
        query = query.filter(Party.is_supplier.is_(True))
    elif role == "partner":
        query = query.filter(Party.is_partner.is_(True))
    elif role == "person":
        query = query.filter(Party.type == "person")

    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        emails = (
            db.query(PartyContactPoint.party_id)
            .filter(
                PartyContactPoint.company_id == membership.company_id,
                PartyContactPoint.kind == "email",
                func.lower(PartyContactPoint.value).like(needle),
            )
            .subquery()
        )
        # Tax identifiers are rows now, so searching them is a subquery like
        # the emails beside it. Every kind, and retired ones too: somebody
        # searching a number they have on a piece of paper wants the party it
        # belonged to, whether or not the number is still current.
        tax_ids = (
            db.query(PartyTaxId.party_id)
            .filter(
                PartyTaxId.company_id == membership.company_id,
                func.lower(PartyTaxId.value).like(needle),
            )
            .subquery()
        )
        query = query.filter(
            or_(
                func.lower(Party.name).like(needle),
                func.lower(Party.trading_name).like(needle),
                func.lower(Party.party_number).like(needle),
                Party.id.in_(db.query(emails.c.party_id)),
                Party.id.in_(db.query(tax_ids.c.party_id)),
            )
        )

    total = query.count()
    rows = (
        query.order_by(Party.type, Party.name).limit(limit).offset(offset).all()
    )
    return PartyPage(
        parties=[_party_response(db, membership, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/parties", response_model=PartyResponse, status_code=status.HTTP_201_CREATED
)
def create_party(
    body: PartyCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Add an organisation, a department or a person.

    The number is allocated in THIS transaction (rule 5's reasoning applied
    to a customer number): a rolled-back create must not consume one and
    leave a gap.
    """
    changes = body.model_dump(exclude_unset=True)
    party_type = changes.pop("type")
    parent_id = changes.get("parent_id")

    try:
        party_service.validate_parent(
            db,
            company_id=membership.company_id,
            party_type=party_type,
            parent_id=parent_id,
        )
    except party_service.ParentNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent not found"
        ) from None
    except party_service.PartyError as error:
        _refuse(error)

    party = Party(
        company_id=membership.company_id,
        type=party_type,
        parent_id=parent_id,
        name=changes.get("name") or "",
        tags=[],
        einvoice_routing={},
    )
    _apply(db, membership, party, changes)
    db.add(party)
    db.flush()

    party_service.allocate_party_number(db, party=party)

    duplicates = party_service.find_duplicates(
        db,
        company_id=membership.company_id,
        name=party.name,
        vat_id=_primary_value(db, party),
        zip_code=None,
        exclude_id=party.id,
    )

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        party,
        "party.created",
        None,
        audit.snapshot(party, PARTY_AUDITED + ("type",)),
        fields=PARTY_AUDITED + ("type",),
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party, duplicates=duplicates)


@router.get("/parties/{party_id}", response_model=PartyResponse)
def read_party(
    party_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    return _party_response(db, membership, _party_or_404(db, membership, party_id))


@router.patch("/parties/{party_id}", response_model=PartyResponse)
def update_party(
    party_id: uuid.UUID,
    body: PartyWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Change a party. `type` cannot change — see `PartyWrite`."""
    party = _party_or_404(db, membership, party_id)
    changes = body.model_dump(exclude_unset=True)

    if "parent_id" in changes and changes["parent_id"] != party.parent_id:
        try:
            party_service.validate_parent(
                db,
                company_id=membership.company_id,
                party_type=party.type,
                parent_id=changes["parent_id"],
                party_id=party.id,
            )
        except party_service.ParentNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Parent not found"
            ) from None
        except party_service.PartyError as error:
            _refuse(error)
        party.parent_id = changes["parent_id"]

    before = audit.snapshot(party, PARTY_AUDITED)
    _apply(db, membership, party, changes)
    party_service.allocate_party_number(db, party=party)

    after = audit.snapshot(party, PARTY_AUDITED)
    if audit.diff(before, after, fields=PARTY_AUDITED):
        audit.record(
            db,
            audit.actor_from(request, membership=membership),
            party,
            "party.updated",
            before,
            after,
            fields=PARTY_AUDITED,
        )

    duplicates = party_service.find_duplicates(
        db,
        company_id=membership.company_id,
        name=party.name,
        vat_id=_primary_value(db, party),
        exclude_id=party.id,
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party, duplicates=duplicates)


@router.post("/parties/{party_id}/archive", response_model=PartyResponse)
def archive_party(
    party_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Retire a party. Never deletes.

    Refused while it still has active entries below it, naming them: an
    archived organisation with a live department would leave that department
    resolving its address from a row the list no longer shows.
    """
    party = _party_or_404(db, membership, party_id)
    try:
        party_service.refuse_archive_with_active_children(db, party)
    except party_service.PartyError as error:
        _refuse(error)

    before = audit.snapshot(party, PARTY_AUDITED)
    party.is_active = False
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        party,
        "party.archived",
        before,
        audit.snapshot(party, PARTY_AUDITED),
        fields=PARTY_AUDITED,
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party)


@router.post("/parties/{party_id}/reactivate", response_model=PartyResponse)
def reactivate_party(
    party_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    party = _party_or_404(db, membership, party_id)
    before = audit.snapshot(party, PARTY_AUDITED)
    party.is_active = True
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        party,
        "party.reactivated",
        before,
        audit.snapshot(party, PARTY_AUDITED),
        fields=PARTY_AUDITED,
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party)


@router.get("/parties/{party_id}/children", response_model=list[PartyChild])
def list_children(
    party_id: uuid.UUID,
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    party = _party_or_404(db, membership, party_id)
    query = scoped(db.query(Party), membership).filter(Party.parent_id == party.id)
    if not include_inactive:
        query = query.filter(Party.is_active.is_(True))
    return [
        PartyChild.model_validate(row)
        for row in query.order_by(Party.type, Party.name)
    ]


# ─── Sub-resources ──────────────────────────────────────────────────────────


@router.post(
    "/parties/{party_id}/addresses",
    response_model=PartyResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_address(
    party_id: uuid.UUID,
    body: PartyAddressWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Add an address. Recorded inside `party.updated`'s diff.

    Inside the party's own event rather than as `party_address.created`,
    because an address is read as part of the party: "what changed about this
    customer" is the question, and three event types for one answer makes the
    log harder to read rather than richer.
    """
    party = _party_or_404(db, membership, party_id)
    before = _address_snapshot(db, party)
    if body.is_default:
        _clear_default_addresses(db, party, body.kind)
    db.add(
        PartyAddress(
            company_id=membership.company_id,
            party_id=party.id,
            **body.model_dump(),
        )
    )
    db.flush()
    _record_subresource_change(db, request, membership, party, before, "addresses")
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party)


@router.post(
    "/parties/{party_id}/contact-points",
    response_model=PartyResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_contact_point(
    party_id: uuid.UUID,
    body: PartyContactPointWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    party = _party_or_404(db, membership, party_id)
    before = _contact_snapshot(db, party)
    if body.is_primary:
        _clear_primary_contacts(db, party, body.kind, body.purpose)
    db.add(
        PartyContactPoint(
            company_id=membership.company_id,
            party_id=party.id,
            **body.model_dump(),
        )
    )
    db.flush()
    _record_subresource_change(
        db, request, membership, party, before, "contact_points"
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party)


@router.post(
    "/parties/{party_id}/bank-accounts",
    response_model=PartyResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_bank_account(
    party_id: uuid.UUID,
    body: PartyBankAccountWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Add a supplier's account. The IBAN is normalised and masked in audit.

    Normalised exactly like the company's own — compact, upper case — because
    one IBAN written two ways is two rows that look like two accounts.
    """
    party = _party_or_404(db, membership, party_id)
    fields = body.model_dump()
    fields["iban"] = normalise_iban(fields["iban"])
    if fields.get("is_default"):
        for row in db.query(PartyBankAccount).filter(
            PartyBankAccount.party_id == party.id,
            PartyBankAccount.is_default.is_(True),
        ):
            row.is_default = False
        db.flush()

    row = PartyBankAccount(
        company_id=membership.company_id, party_id=party.id, **fields
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        "party_bank_account.created",
        None,
        audit.snapshot(row, BANK_ACCOUNT_AUDITED),
        fields=BANK_ACCOUNT_AUDITED,
    )
    db.commit()
    db.refresh(party)
    return _party_response(db, membership, party)


def _address_snapshot(db: Session, party: Party) -> dict:
    return {
        "addresses": sorted(
            f"{row.kind}:{row.street or ''}:{row.zip or ''}:{row.city or ''}:"
            f"{row.country or ''}:{'default' if row.is_default else ''}"
            for row in db.query(PartyAddress).filter(
                PartyAddress.party_id == party.id
            )
        )
    }


def _contact_snapshot(db: Session, party: Party) -> dict:
    return {
        "contact_points": sorted(
            f"{row.kind}:{row.purpose}:{row.value}:"
            f"{'primary' if row.is_primary else ''}"
            for row in db.query(PartyContactPoint).filter(
                PartyContactPoint.party_id == party.id
            )
        )
    }


def _record_subresource_change(
    db: Session,
    request: Request,
    membership: Membership,
    party: Party,
    before: dict,
    kind: str,
) -> None:
    after = (
        _address_snapshot(db, party)
        if kind == "addresses"
        else _contact_snapshot(db, party)
    )
    if audit.diff(before, after, fields=[kind]):
        audit.record(
            db,
            audit.actor_from(request, membership=membership),
            party,
            "party.updated",
            before,
            after,
            fields=[kind],
        )


def _clear_default_addresses(db: Session, party: Party, kind: str) -> None:
    for row in db.query(PartyAddress).filter(
        PartyAddress.party_id == party.id,
        PartyAddress.kind == kind,
        PartyAddress.is_default.is_(True),
    ):
        row.is_default = False
    db.flush()


def _clear_primary_contacts(
    db: Session, party: Party, kind: str, purpose: str
) -> None:
    for row in db.query(PartyContactPoint).filter(
        PartyContactPoint.party_id == party.id,
        PartyContactPoint.kind == kind,
        PartyContactPoint.purpose == purpose,
        PartyContactPoint.is_primary.is_(True),
    ):
        row.is_primary = False
    db.flush()


# ─── Resolution, VAT and tax ────────────────────────────────────────────────


@router.get(
    "/parties/{party_id}/resolved", response_model=ResolvedRecipientResponse
)
def read_resolved(
    party_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """Exactly what a document addressed to this party would use.

    The shape P2 will snapshot, with every field's source, so the screen can
    grey an inherited value and name its origin.
    """
    party = _party_or_404(db, membership, party_id)
    resolved = party_service.resolve(db, party)
    inputs = tax_inputs(resolved)

    def _field(name: str) -> ResolvedFieldResponse:
        entry = resolved.field(name)
        value = entry.value
        if hasattr(value, "__dataclass_fields__"):
            value = {
                key: getattr(value, key) for key in value.__dataclass_fields__
            }
        return ResolvedFieldResponse(value=value, source=entry.source)

    return ResolvedRecipientResponse(
        own_level=resolved.own_level,
        ids={key: str(value) for key, value in resolved.ids.items()},
        names=dict(resolved.names),
        buyer_type=inputs["buyer_type"],
        buyer_type_reason=inputs["buyer_type_reason"],
        **{
            name: _field(name)
            for name in (
                "contact_name", "invoice_name", "billing_address", "vat_id",
                "vat_verified", "tax_id_kind", "tax_status_note",
                "tax_id_evidence", "tax_country", "business_status",
                "tax_override", "language", "default_currency",
                "payment_terms_days", "payment_terms_text", "po_reference",
                "email", "phone", "einvoice_routing",
            )
        },
    )


@router.get(
    "/parties/{party_id}/vat-checks", response_model=list[PartyVatCheckResponse]
)
def list_vat_checks(
    party_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The whole history, newest first. Append-only, so it only grows."""
    party = _party_or_404(db, membership, party_id)
    return [
        _check_response(row)
        for row in db.query(PartyVatCheck)
        .filter(PartyVatCheck.party_id == party.id)
        .order_by(PartyVatCheck.checked_at.desc())
    ]


@router.post(
    "/parties/{party_id}/tax-preview", response_model=TaxProposalResponse
)
def preview_tax(
    party_id: uuid.UUID,
    body: PartyTaxPreviewRequest,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """What tax a sale to this party would carry. Reads; writes nothing.

    The buyer side comes from the resolved recipient and the seller side from
    the company, so neither can be asserted by the caller. Same engine, same
    inputs, same answer as Settings → Company → Tax — the web computes no
    tax anywhere.
    """
    from ..billing.tax import Override, treatment_label

    party = _party_or_404(db, membership, party_id)
    resolved = party_service.resolve(db, party)
    inputs = tax_inputs(resolved)

    def _override(raw) -> Optional[Override]:
        if not raw:
            return None
        return Override(kind=raw.get("kind", ""), code=raw.get("code"))

    proposal = tax_service.propose(
        db,
        company=membership.company,
        buyer_country=inputs["buyer_country"],
        buyer_type=inputs["buyer_type"],
        buyer_vat_verified=inputs["buyer_vat_verified"],
        supply_kind=body.supply_kind,
        item_class=body.item_class,
        item_override=_override(body.item_override),
        party_override=_override(inputs["party_override"]),
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
