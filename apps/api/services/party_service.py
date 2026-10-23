"""Parties: the tree's rules, the chain, and the customer/supplier number.

The translation layer between `models/party.py` and
`apps/api/billing/parties/`, which knows nothing about rows. Three jobs:

  * **validate the tree.** An organisation has no parent, a department's
    parent is an organisation, a person's is an organisation or a department
    or nothing. The database says the first of those (no join needed); the
    other two need the parent's type, so they are here, with a sentence, and
    with the cycle and cross-company checks that no CHECK could express.
  * **build the chain** a resolver call takes, innermost first, from rows.
  * **allocate the number**, from the same number-series machinery documents
    use (P0b-3). One mechanism for "a number issued once, from a counter a
    user can re-point", because a second one would be a second place for
    that promise to break.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from ..billing.parties import (
    LEVEL_DEPARTMENT,
    LEVEL_ORGANISATION,
    LEVEL_PERSON,
    PartyAddressValue,
    PartyLevel,
    ResolvedRecipient,
    resolve_recipient,
)
from ..models.party import (
    ALLOWED_PARENTS,
    PARTY_DEPARTMENT,
    PARTY_ORGANISATION,
    PARTY_PERSON,
    PARTY_TYPES,
    VAT_VALID,
    Party,
    PartyAddress,
    PartyContactPoint,
    PartyVatCheck,
)

#: The tree is three deep and no deeper. Not a configuration: the resolver
#: walks a known number of levels and a document's recipient has to be
#: answerable without an unbounded walk (see `billing/parties/__init__`).
MAX_DEPTH = 3


class PartyError(ValueError):
    """A party that cannot be stored as asked. A 409 or 422 at the router."""


class ParentNotFound(PartyError):
    """The parent is not this company's. A 404 at the router (rule 6)."""


def validate_parent(
    db: Session,
    *,
    company_id: uuid.UUID,
    party_type: str,
    parent_id: Optional[uuid.UUID],
    party_id: Optional[uuid.UUID] = None,
) -> Optional[Party]:
    """Check the parent is allowed, exists here, and closes no cycle.

    Returns the parent row (or None), so the caller does not look it up
    twice. Raises `ParentNotFound` for another company's id — the same 404 a
    stranger gets, because answering differently would confirm it exists.
    """
    if party_type not in PARTY_TYPES:
        raise PartyError(
            f"{party_type!r} is not a party type. Use one of "
            f"{', '.join(PARTY_TYPES)}."
        )

    allowed = ALLOWED_PARENTS[party_type]
    if parent_id is None:
        if party_type == PARTY_DEPARTMENT:
            raise PartyError(
                "A department belongs to an organisation, so it needs a "
                "parent. A standalone department is not a thing that can be "
                "invoiced."
            )
        return None

    if not allowed:
        raise PartyError(
            "An organisation is the top of the tree and cannot have a "
            "parent. If this is a part of a larger body, make it a "
            "department of that organisation instead."
        )

    parent = (
        db.query(Party)
        .filter(Party.company_id == company_id, Party.id == parent_id)
        .first()
    )
    if parent is None:
        raise ParentNotFound("Parent not found")

    if parent.type not in allowed:
        raise PartyError(
            f"A {party_type} cannot belong to a {parent.type}. "
            f"Allowed: {', '.join(allowed)}. "
            f"({parent.name!r} is a {parent.type}.)"
        )

    if party_id is not None:
        if parent.id == party_id:
            raise PartyError("A party cannot be its own parent.")
        # Walk up from the proposed parent: meeting ourselves means this
        # edit would close a loop, and a loop is a resolver that never
        # terminates.
        seen = {party_id}
        walker: Optional[Party] = parent
        while walker is not None:
            if walker.id in seen:
                raise PartyError(
                    f"That would put {parent.name!r} inside its own "
                    "hierarchy. A party cannot end up below itself."
                )
            seen.add(walker.id)
            walker = (
                db.query(Party)
                .filter(
                    Party.company_id == company_id,
                    Party.id == walker.parent_id,
                )
                .first()
                if walker.parent_id
                else None
            )

    depth = _depth_of(db, company_id, parent) + 1
    if depth >= MAX_DEPTH + 1:
        raise PartyError(
            f"The hierarchy is {MAX_DEPTH} levels deep at most — an "
            "organisation, its departments and the people in them. This "
            "would make a fourth."
        )
    return parent


def _depth_of(db: Session, company_id: uuid.UUID, party: Party) -> int:
    """How many levels above `party` there are, counting itself as 1."""
    depth = 1
    walker = party
    seen = {party.id}
    while walker.parent_id is not None and walker.parent_id not in seen:
        seen.add(walker.parent_id)
        walker = (
            db.query(Party)
            .filter(Party.company_id == company_id, Party.id == walker.parent_id)
            .first()
        )
        if walker is None:
            break
        depth += 1
    return depth


# ─── Building the chain the resolver takes ──────────────────────────────────


def _address_value(row: Optional[PartyAddress]) -> Optional[PartyAddressValue]:
    if row is None:
        return None
    return PartyAddressValue(
        street=row.street,
        street2=row.street2,
        zip=row.zip,
        city=row.city,
        country=row.country,
    )


def _default_address(db: Session, party_id: uuid.UUID, kind: str):
    """The default of that kind, or the first one there is.

    "Or the first" rather than "or nothing": a party with one address and
    nobody having ticked the default box has an address, and refusing to see
    it would make the invoice say nothing at all.
    """
    rows = (
        db.query(PartyAddress)
        .filter(PartyAddress.party_id == party_id, PartyAddress.kind == kind)
        .order_by(PartyAddress.is_default.desc(), PartyAddress.created_at)
        .all()
    )
    return rows[0] if rows else None


def _primary_contact(db: Session, party_id: uuid.UUID, kind: str) -> Optional[str]:
    rows = (
        db.query(PartyContactPoint)
        .filter(
            PartyContactPoint.party_id == party_id,
            PartyContactPoint.kind == kind,
        )
        .order_by(
            PartyContactPoint.is_primary.desc(),
            PartyContactPoint.purpose,
            PartyContactPoint.created_at,
        )
        .all()
    )
    return rows[0].value if rows else None


def vat_is_verified(db: Session, party: Party) -> bool:
    """Whether the latest check for the party's CURRENT VAT ID said `valid`.

    The definition of `buyer_vat_verified` (D56), and the two halves matter
    equally:

      * **latest**: an older `valid` followed by an `invalid` is not verified;
      * **current**: a check of a different number is not evidence about this
        one, so changing the VAT ID resets the flag without anything having
        to remember to clear it. The check row stores the number it checked
        for exactly this reason.
    """
    if not party.vat_id:
        return False
    latest = (
        db.query(PartyVatCheck)
        .filter(
            PartyVatCheck.party_id == party.id,
            PartyVatCheck.vat_id == party.vat_id,
        )
        .order_by(PartyVatCheck.checked_at.desc())
        .first()
    )
    return latest is not None and latest.status == VAT_VALID


def _level_of(db: Session, party: Party) -> PartyLevel:
    billing = _default_address(db, party.id, "billing")
    postal = _default_address(db, party.id, "postal")
    return PartyLevel(
        level=party.type,
        id=party.id,
        name=party.name,
        trading_name=party.trading_name,
        billing_address=_address_value(billing),
        postal_address=_address_value(postal),
        vat_id=party.vat_id,
        tax_country=party.tax_country,
        business_status=party.business_status,
        tax_override=dict(party.tax_override) if party.tax_override else None,
        language=party.language,
        default_currency=party.default_currency,
        payment_terms_days=party.payment_terms_days,
        payment_terms_text=party.payment_terms_text,
        po_reference=party.po_reference,
        email=_primary_contact(db, party.id, "email"),
        phone=_primary_contact(db, party.id, "phone"),
        einvoice_routing=dict(party.einvoice_routing or {}) or None,
        vat_verified=vat_is_verified(db, party),
    )


def build_chain(db: Session, party: Party) -> list[PartyLevel]:
    """The party and its ancestors, innermost first.

    Bounded by `MAX_DEPTH + 1` regardless of what the rows say: a cycle that
    got into the database some other way must not hang a request. The
    validation refuses cycles on write; this is the floor under it.
    """
    chain: list[PartyLevel] = []
    walker: Optional[Party] = party
    seen: set = set()
    while walker is not None and len(chain) <= MAX_DEPTH:
        if walker.id in seen:
            break
        seen.add(walker.id)
        chain.append(_level_of(db, walker))
        if walker.parent_id is None:
            break
        walker = (
            db.query(Party)
            .filter(
                Party.company_id == party.company_id,
                Party.id == walker.parent_id,
            )
            .first()
        )
    return chain


def resolve(db: Session, party: Party) -> ResolvedRecipient:
    """What a document addressed to `party` would use."""
    return resolve_recipient(build_chain(db, party))


# ─── The customer / supplier number ─────────────────────────────────────────


def allocate_party_number(
    db: Session, *, party: Party
) -> Optional[str]:
    """Give `party` a number from the right series, if it needs one. No commit.

    No commit because this runs inside the party write: the number and the
    party land together or not at all, which is the only reading under which
    a rolled-back create cannot consume a number and leave a gap (rule 5's
    reasoning, applied to a customer number).

    A party that is both customer and supplier gets the CUSTOMER number: one
    party, one number, and the customer side is the one that appears on a
    document. A party already carrying a number keeps it — a company
    migrating from another system has its own.
    """
    from datetime import date

    from .number_series import NumberSeriesMissing, allocate_number

    if party.party_number:
        return party.party_number
    document_type = (
        "customer"
        if party.is_customer
        else "supplier"
        if party.is_supplier
        else None
    )
    if document_type is None:
        return None

    try:
        plan = allocate_number(
            db,
            company_id=party.company_id,
            document_type=document_type,
            document_date=date.today(),
            entity_type="parties",
            entity_id=party.id,
        )
    except NumberSeriesMissing:
        # A company whose customer series was deleted. Left without a number
        # rather than refused: a party with no number is usable and a party
        # that cannot be saved is not, and the Number series screen says what
        # is missing.
        return None
    party.party_number = plan.number
    return party.party_number
