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
    AcknowledgeRequest,
    DuplicateWarning,
    ManualConfirmationRequest,
    PartyAddressResponse,
    PartyAddressWrite,
    PartyTaxIdCreate,
    PartyTaxIdResponse,
    PartyTaxIdUpdate,
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
from ..services import tax_id_checks
from ..services.tax_id_policy import load_policy

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
        tax_id_id=row.tax_id_id,
        vat_id=row.vat_id,
        country=row.country,
        kind=row.kind,
        provider=row.provider,
        trigger_kind=row.trigger_kind,
        qualified=bool(row.qualified),
        registered_name=row.registered_name,
        registered_address=row.registered_address,
        name_match=row.name_match,
        address_match=row.address_match,
        registration_active=row.registration_active,
        gst_registered=row.gst_registered,
        manual_note=row.manual_note,
        evidence_reference=row.evidence_reference,
        valid_until=row.valid_until,
        change_detected=row.change_detected,
        status=row.status,
        response=response,
        consultation_ref=row.consultation_ref,
        checked_at=row.checked_at,
        # The server's own sentence for this check. Falls back to the one
        # stored on the row, so a history entry explains itself without the
        # caller having to have been there when it ran.
        detail=detail or response.get("_detail") or "",
        trader_name=response.get("_trader_name") or row.registered_name,
        trader_address=response.get("_trader_address") or row.registered_address,
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
        tax_ids=[
            _tax_id_response(db, row)
            for row in db.query(PartyTaxId)
            .filter(PartyTaxId.party_id == party.id)
            .order_by(
                PartyTaxId.is_active.desc(),
                PartyTaxId.is_primary.desc(),
                PartyTaxId.kind,
                PartyTaxId.created_at,
            )
        ],
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
    needs_attention: bool = Query(
        default=False,
        description=(
            "Only parties with a tax identifier whose latest check differed "
            "from the one before it, and which nobody has acknowledged yet."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """Search by name, trading name, party number, tax identifier or email.

    One query over five columns rather than five filters, because somebody
    looking for a customer types what they remember and does not know which
    field it was. Identifiers are searched across every kind and including
    retired ones: somebody with a number on a piece of paper wants the party
    it belonged to, whether or not it is still current.
    """
    query = scoped(db.query(Party), membership)
    if not include_inactive:
        query = query.filter(Party.is_active.is_(True))
    if needs_attention:
        # A subquery rather than a join, so a party with two flagged
        # identifiers appears once. A join would return it twice and the
        # page count would disagree with the rows.
        flagged = (
            db.query(PartyTaxId.party_id)
            .filter(
                PartyTaxId.company_id == membership.company_id,
                PartyTaxId.needs_attention.is_(True),
            )
            .subquery()
        )
        query = query.filter(Party.id.in_(db.query(flagged.c.party_id)))
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


# ─── Tax identifiers (P1b-2) ───────────────────────────────────────────────
#
# **No DELETE route anywhere**, asserted on the OpenAPI document like P1a-2's
# tax codes. An identifier's checks are the evidence for invoices already
# issued, and deleting the identifier would delete the reason a finalized
# document says what it says. Retire instead; the row stays, the history
# stays, and nothing proposes it any more.


#: What an audit row records about an identifier. `value` first because a
#: change to it is the one that restates what a future invoice will say.
TAX_ID_AUDITED = (
    "kind",
    "value",
    "country",
    "is_primary",
    "is_active",
    "notes",
)


def _tax_id_or_404(
    db: Session, membership: Membership, party: Party, tax_id_id: uuid.UUID
) -> PartyTaxId:
    """Scoped by company AND by party.

    Both, because an identifier id from another party of the same company
    would otherwise be editable through this party's URL — a smaller leak
    than a cross-company one and the same shape of bug.
    """
    row = (
        scoped(db.query(PartyTaxId), membership)
        .filter(PartyTaxId.id == tax_id_id, PartyTaxId.party_id == party.id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such tax identifier."
        )
    return row


def _latest_check_of(db: Session, tax_id: PartyTaxId) -> Optional[PartyVatCheck]:
    """The newest check of ONE identifier.

    Named apart from `_latest_check`, which takes a PARTY and answers "what
    did we last ask about this customer". Two functions one word apart would
    have been a real bug: defined later in the file, this one would have
    shadowed the other and `_party_response` would have passed a Party where
    a PartyTaxId was expected.
    """
    return (
        db.query(PartyVatCheck)
        .filter(PartyVatCheck.tax_id_id == tax_id.id)
        .order_by(PartyVatCheck.checked_at.desc())
        .first()
    )


def _tax_id_response(db: Session, row: PartyTaxId) -> PartyTaxIdResponse:
    from ..billing.parties.tax_ids import is_verifiable

    latest = _latest_check_of(db, row)
    return PartyTaxIdResponse(
        id=row.id,
        kind=row.kind,
        country=row.country,
        value=row.value,
        value_as_entered=row.value_as_entered,
        is_primary=row.is_primary,
        is_active=row.is_active,
        notes=row.notes,
        replaces_id=row.replaces_id,
        verifiable=is_verifiable(row.kind),
        initial_checks_done=row.initial_checks_done or 0,
        next_check_at=row.next_check_at,
        unavailable_streak=row.unavailable_streak or 0,
        needs_attention=bool(row.needs_attention),
        needs_attention_reason=row.needs_attention_reason,
        latest_check=_check_response(latest) if latest else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _normalise_tax_id(kind: str, value: str, country: Optional[str]):
    """Normalise, or refuse with the sentence the normaliser wrote.

    422 and before any network call: a malformed number costs a sentence
    rather than a timeout, and a registry asked about a typo answers
    "invalid" — which we would then store as a finding about the customer
    when it is a finding about the typing.
    """
    from ..billing.parties.tax_ids import TaxIdFormatError, normalise

    try:
        return normalise(kind, value, country=country)
    except TaxIdFormatError as error:
        _refuse(error, status.HTTP_422_UNPROCESSABLE_ENTITY)


def _clear_other_primaries(db: Session, party: Party, kind: str) -> None:
    """Move the primary flag rather than refusing the write.

    The database allows one primary active VAT-like identifier per party.
    A user marking a second one primary means "this one now", so the
    endpoint moves the flag — refusing with "unset the other one first"
    would be making them do the bookkeeping for a rule they cannot see.
    """
    from ..models.party import VAT_LIKE_KINDS

    group = VAT_LIKE_KINDS if kind in VAT_LIKE_KINDS else (kind,)
    for other in (
        db.query(PartyTaxId)
        .filter(
            PartyTaxId.party_id == party.id,
            PartyTaxId.is_primary.is_(True),
            PartyTaxId.is_active.is_(True),
            PartyTaxId.kind.in_(group),
        )
        .all()
    ):
        other.is_primary = False
    db.flush()


@router.get(
    "/parties/{party_id}/tax-ids", response_model=list[PartyTaxIdResponse]
)
def list_tax_ids(
    party_id: uuid.UUID,
    include_inactive: bool = Query(
        True,
        description=(
            "Retired identifiers too. Default true, unlike the party list: "
            "a retired number is part of this party's history and the screen "
            "shows it greyed rather than hiding it."
        ),
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """This party's identifiers, primary first."""
    party = _party_or_404(db, membership, party_id)
    query = db.query(PartyTaxId).filter(PartyTaxId.party_id == party.id)
    if not include_inactive:
        query = query.filter(PartyTaxId.is_active.is_(True))
    rows = query.order_by(
        PartyTaxId.is_active.desc(),
        PartyTaxId.is_primary.desc(),
        PartyTaxId.kind,
        PartyTaxId.created_at,
    ).all()
    return [_tax_id_response(db, row) for row in rows]


@router.post(
    "/parties/{party_id}/tax-ids",
    response_model=PartyTaxIdResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_tax_id(
    party_id: uuid.UUID,
    body: PartyTaxIdCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Add an identifier, and start its initial check series.

    **Re-adding a number somebody retired reactivates that row** rather than
    creating a second one. The checks on it are its history, and two rows
    for one number would make "which of these is the evidence" a question
    with no answer.
    """
    party = _party_or_404(db, membership, party_id)
    normalised = _normalise_tax_id(body.kind, body.value, body.country)
    policy = load_policy(db, company_id=membership.company_id)

    existing = (
        db.query(PartyTaxId)
        .filter(
            PartyTaxId.party_id == party.id,
            PartyTaxId.kind == normalised.kind,
            PartyTaxId.value == normalised.value,
        )
        .first()
    )
    if existing is not None:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{party.name} already has {normalised.value} as a "
                    f"{normalised.kind} identifier."
                ),
            )
        before = audit.snapshot(existing, TAX_ID_AUDITED)
        existing.is_active = True
        if body.is_primary:
            _clear_other_primaries(db, party, existing.kind)
            existing.is_primary = True
        tax_id_checks.schedule_initial(existing, policy=policy)
        db.flush()
        audit.record(
            db,
            audit.actor_from(request, membership=membership),
            existing,
            "party_tax_id.reactivated",
            before,
            audit.snapshot(existing, TAX_ID_AUDITED),
            fields=TAX_ID_AUDITED,
            reason=(
                "Re-added a retired identifier, which keeps its check "
                "history rather than starting a second one."
            ),
        )
        db.commit()
        db.refresh(existing)
        return _tax_id_response(db, existing)

    from ..models.party import VAT_LIKE_KINDS

    # A first VAT-like identifier is primary without being asked: a party
    # with one number and no primary flag would resolve to nothing, and
    # nobody means that.
    wants_primary = body.is_primary
    if wants_primary is None:
        wants_primary = normalised.kind in VAT_LIKE_KINDS and (
            party_service.primary_tax_id(db, party) is None
        )
    if wants_primary:
        _clear_other_primaries(db, party, normalised.kind)

    row = PartyTaxId(
        company_id=membership.company_id,
        party_id=party.id,
        kind=normalised.kind,
        country=normalised.country,
        value=normalised.value,
        value_as_entered=normalised.as_entered,
        is_primary=bool(wants_primary),
        is_active=True,
        # The Swiss VAT-register suffix the user typed, if any. Kept because
        # it is them telling us the company is VAT-registered — worth
        # recording, never worth validating.
        notes=body.notes
        or (
            f"Entered with the {normalised.vat_suffix} suffix."
            if normalised.vat_suffix
            else None
        ),
    )
    db.add(row)
    db.flush()
    tax_id_checks.schedule_initial(row, policy=policy)

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        "party_tax_id.created",
        None,
        audit.snapshot(row, TAX_ID_AUDITED),
        fields=TAX_ID_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _tax_id_response(db, row)


@router.patch(
    "/parties/{party_id}/tax-ids/{tax_id_id}",
    response_model=PartyTaxIdResponse,
)
def update_tax_id(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    body: PartyTaxIdUpdate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Edit an identifier.

    **A changed value or country retires this row and creates a new one**
    linked back to it (D63). The checks on the old row are evidence for
    invoices already issued; letting them follow a different number would
    make an old lookup look like confirmation of a number nobody has
    checked. The new row starts its initial series from scratch, because a
    different number has no history.
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)
    policy = load_policy(db, company_id=membership.company_id)
    actor = audit.actor_from(request, membership=membership)

    changing_value = False
    normalised = None
    if body.value is not None or body.country is not None:
        normalised = _normalise_tax_id(
            row.kind,
            body.value if body.value is not None else row.value,
            body.country if body.country is not None else row.country,
        )
        changing_value = (
            normalised.value != row.value or normalised.country != row.country
        )

    if changing_value:
        before = audit.snapshot(row, TAX_ID_AUDITED)
        was_primary = row.is_primary
        row.is_active = False
        row.is_primary = False
        db.flush()

        replacement = PartyTaxId(
            company_id=membership.company_id,
            party_id=party.id,
            kind=row.kind,
            country=normalised.country,
            value=normalised.value,
            value_as_entered=normalised.as_entered,
            is_primary=(
                body.is_primary if body.is_primary is not None else was_primary
            ),
            is_active=True,
            notes=body.notes if body.notes is not None else row.notes,
            replaces_id=row.id,
        )
        if replacement.is_primary:
            _clear_other_primaries(db, party, replacement.kind)
        db.add(replacement)
        db.flush()
        # The reset, per identifier (D63). Without it the old row's `valid`
        # would be the latest completed check of the party's primary
        # identifier, and the new number would arrive pre-verified.
        tax_id_checks.schedule_initial(replacement, policy=policy)

        audit.record(
            db,
            actor,
            row,
            "party_tax_id.retired",
            before,
            audit.snapshot(row, TAX_ID_AUDITED),
            fields=TAX_ID_AUDITED,
            reason=(
                f"Replaced by {normalised.value}. Retired rather than "
                "edited, so its checks stay attached to the number they "
                "were about."
            ),
        )
        audit.record(
            db,
            actor,
            replacement,
            "party_tax_id.created",
            None,
            audit.snapshot(replacement, TAX_ID_AUDITED),
            fields=TAX_ID_AUDITED,
            reason=f"Replaces {before.get('value')}.",
        )
        db.commit()
        db.refresh(replacement)
        return _tax_id_response(db, replacement)

    before = audit.snapshot(row, TAX_ID_AUDITED)
    if body.is_primary is True:
        _clear_other_primaries(db, party, row.kind)
        row.is_primary = True
    elif body.is_primary is False:
        row.is_primary = False
    if body.notes is not None:
        row.notes = body.notes
    db.flush()

    audit.record(
        db,
        actor,
        row,
        "party_tax_id.updated",
        before,
        audit.snapshot(row, TAX_ID_AUDITED),
        fields=TAX_ID_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _tax_id_response(db, row)


@router.post(
    "/parties/{party_id}/tax-ids/{tax_id_id}/retire",
    response_model=PartyTaxIdResponse,
)
def retire_tax_id(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Stop using an identifier. Never deletes it.

    The checks stay, attached to the number they were about — they are the
    evidence for invoices already issued (D59).
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)

    before = audit.snapshot(row, TAX_ID_AUDITED)
    row.is_active = False
    row.is_primary = False
    # Nothing to schedule for a retired identifier.
    row.next_check_at = None
    db.flush()

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        "party_tax_id.retired",
        before,
        audit.snapshot(row, TAX_ID_AUDITED),
        fields=TAX_ID_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _tax_id_response(db, row)


@router.post(
    "/parties/{party_id}/tax-ids/{tax_id_id}/reactivate",
    response_model=PartyTaxIdResponse,
)
def reactivate_tax_id(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Use it again, and check it again.

    The initial series restarts: the number may have been retired for a
    year, and an old `valid` is a historical fact rather than a current one.
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)

    before = audit.snapshot(row, TAX_ID_AUDITED)
    row.is_active = True
    tax_id_checks.schedule_initial(
        row, policy=load_policy(db, company_id=membership.company_id)
    )
    db.flush()

    audit.record(
        db,
        audit.actor_from(request, membership=membership),
        row,
        "party_tax_id.reactivated",
        before,
        audit.snapshot(row, TAX_ID_AUDITED),
        fields=TAX_ID_AUDITED,
    )
    db.commit()
    db.refresh(row)
    return _tax_id_response(db, row)


@router.post(
    "/parties/{party_id}/tax-ids/{tax_id_id}/check",
    response_model=PartyVatCheckResponse,
)
def check_tax_id(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Ask the registry now, and record the answer.

    Append-only: the row is the evidence for a reverse charge. A failure to
    obtain an answer is `unavailable` and never `invalid`, and the party
    stays usable either way — no check is ever required to save one.

    A kind with no provider returns `not_checkable` with a sentence rather
    than an error, because "there is no register to ask" is an answer about
    the world and not a failure of ours.
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)

    result = tax_id_checks.run_check(db, tax_id=row, party=party)
    check = tax_id_checks.record_check(
        db,
        tax_id=row,
        party=party,
        result=result,
        trigger_kind="manual",
        actor=audit.actor_from(request, membership=membership),
        checked_by=getattr(membership.user, "id", None),
    )
    db.commit()
    db.refresh(check)
    return _check_response(check, detail=result.detail)


@router.get(
    "/parties/{party_id}/tax-ids/{tax_id_id}/checks",
    response_model=list[PartyVatCheckResponse],
)
def list_tax_id_checks(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.view")),
):
    """The whole history of one identifier, newest first.

    Append-only, so it only grows. Newest first because the question is
    almost always "what does it say now", and the history below it is the
    answer to "and has that changed".
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)
    return [
        _check_response(check)
        for check in db.query(PartyVatCheck)
        .filter(PartyVatCheck.tax_id_id == row.id)
        .order_by(PartyVatCheck.checked_at.desc())
    ]


@router.post(
    "/parties/{party_id}/tax-ids/{tax_id_id}/checks/{check_id}/acknowledge",
    response_model=PartyTaxIdResponse,
)
def acknowledge_change(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    check_id: uuid.UUID,
    body: AcknowledgeRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Say a human has looked at a flagged change, and what they concluded.

    The note goes in the audit event rather than onto the check row, because
    that row cannot be updated — it is the evidence, and evidence that can
    be annotated afterwards is not evidence.
    """
    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)
    check = (
        db.query(PartyVatCheck)
        .filter(
            PartyVatCheck.id == check_id, PartyVatCheck.tax_id_id == row.id
        )
        .first()
    )
    if check is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such check on this identifier.",
        )

    tax_id_checks.acknowledge(
        db,
        tax_id=row,
        party=party,
        check=check,
        note=body.note,
        actor=audit.actor_from(request, membership=membership),
    )
    db.commit()
    db.refresh(row)
    return _tax_id_response(db, row)


@router.post(
    "/parties/{party_id}/tax-ids/{tax_id_id}/manual-confirmation",
    response_model=PartyVatCheckResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_manually(
    party_id: uuid.UUID,
    tax_id_id: uuid.UUID,
    body: ManualConfirmationRequest,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("parties.edit")),
):
    """Record that a person confirmed this identifier.

    It appends a check row with provider `manual` and status `valid`, and it
    is shown as "confirmed by <user> on <date>" — never as a registry check.

    **It never verifies an EU VAT ID** (D66). For a `vat` the registry is
    reachable, so a human saying "trust me" in its place would put a reverse
    charge on an invoice with nothing behind it. The row is still written,
    because somebody choosing to record their own confirmation is entitled
    to; `vat_is_verified` simply does not count it, and the response says
    so.
    """
    from ..billing.parties.verification import VALID, VerificationResult
    from ..models.party import PROVIDER_MANUAL, TAX_ID_VAT

    party = _party_or_404(db, membership, party_id)
    row = _tax_id_or_404(db, membership, party, tax_id_id)
    actor = audit.actor_from(request, membership=membership)
    who = getattr(getattr(membership, "user", None), "email", None) or "a user"

    counts = row.kind != TAX_ID_VAT
    detail = (
        f"Confirmed by {who}. This is a person's confirmation, not a "
        "registry check."
    )
    if not counts:
        detail += (
            " It does not make this VAT ID verified for tax purposes: a "
            "reverse charge needs the number confirmed by VIES, and that "
            "lookup is available — run it with Check now."
        )

    result = VerificationResult(
        status=VALID,
        provider=PROVIDER_MANUAL,
        value=row.value,
        kind=row.kind,
        country=row.country,
        detail=detail,
        # Never qualified. Enforced in the value object and by a CHECK
        # constraint as well, because the claim "a tax authority confirmed
        # this" is the one nobody may make by accident.
        qualified=False,
        raw={"_manual": True, "_confirmed_by": who},
    )
    check = tax_id_checks.record_check(
        db,
        tax_id=row,
        party=party,
        result=result,
        trigger_kind="manual",
        actor=None,
        checked_by=getattr(membership.user, "id", None),
        # Passed in, not set afterwards: the table is append-only, so
        # assigning to the row after its insert is an UPDATE the trigger
        # refuses — which is how the first version of this failed.
        manual_note=body.note,
        evidence_reference=body.evidence_reference,
        valid_until=body.valid_until,
    )

    audit.record(
        db,
        actor,
        party,
        "party.tax_id_manual_confirmed",
        None,
        {
            "tax_id": row.value,
            "kind": row.kind,
            "note": body.note,
            "evidence_reference": body.evidence_reference,
            "valid_until": (
                body.valid_until.isoformat() if body.valid_until else None
            ),
            "counts_as_verified": counts,
        },
        fields=[
            "tax_id", "kind", "note", "evidence_reference", "valid_until",
            "counts_as_verified",
        ],
        reason=detail,
    )
    db.commit()
    db.refresh(check)
    return _check_response(check, detail=detail)


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
        ]
        + _party_layer_warnings(db, party, inputs),
        blocking=proposal.blocking,
        would_have_applied=would,
    )


#: The warning code the web keys its testid on. Named so the string exists
#: once rather than in the router, the schema and three tests.
NO_BUSINESS_EVIDENCE = "no_business_evidence"


def _party_layer_warnings(
    db: Session, party: Party, inputs: dict
) -> list[TaxWarningResponse]:
    """Warnings the ENGINE cannot produce, added by the party layer.

    **The engine is untouched** (D67). It knows about countries, codes and
    rules; it does not know whether a human has confirmed a customer's
    business status, and it should not — that is a fact about our records,
    not about tax law, and putting it in the engine would make the engine
    depend on a database.

    So the one warning here is about evidence on file: a business buyer
    outside the EU, with no identifier a register has confirmed and no
    current manual confirmation. **A warning, never a block.** The invoice is
    correct either way; what is missing is the thing a tax advisor would ask
    to see, and refusing to issue it would be FilmBill inventing a rule
    nobody asked for.
    """
    if inputs["buyer_type"] != "business":
        return []

    country = (inputs["buyer_country"] or "").upper()
    if not country:
        # No country at all is the engine's problem, and it says so.
        return []

    from ..billing.tax.regions import EU_MEMBER_STATES

    if country in EU_MEMBER_STATES:
        # Inside the EU the engine's own `buyer_vat_id_not_verified` already
        # covers it, and two warnings about one thing is one too many.
        return []

    if party_service.has_business_evidence(db, party):
        return []

    return [
        TaxWarningResponse(
            code=NO_BUSINESS_EVIDENCE,
            text=(
                f"Nothing on file shows this {country} customer is a "
                "business. The tax is unaffected — this is about what you "
                "could show an auditor. Add an identifier a register can "
                "confirm, or record a confirmation by hand on the Tax tab."
            ),
        )
    ]
