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
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
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
    PartyTaxId,
    PartyVatCheck,
    PROVIDER_MANUAL,
    TAX_ID_VAT,
    VAT_LIKE_KINDS,
    VAT_NOT_CONFIGURED,
    VAT_UNAVAILABLE,
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


def primary_tax_id(db: Session, party: Party):
    """The party's primary active VAT-like identifier, or None.

    One place answers "which of this party's numbers is THE number", because
    the engine takes one and a second answer would be a wrong invoice nobody
    could explain. The database enforces that at most one exists
    (`uq_party_tax_ids_primary_vat_like`); this is the read side of it.

    When nothing is marked primary but exactly one VAT-like ID is active,
    that one is it. Being tidy about a flag is not something a user should
    have to do before their invoice is right — and the index means "exactly
    one active" is unambiguous.
    """
    rows = (
        db.query(PartyTaxId)
        .filter(
            PartyTaxId.party_id == party.id,
            PartyTaxId.is_active.is_(True),
            PartyTaxId.kind.in_(VAT_LIKE_KINDS),
        )
        .order_by(PartyTaxId.is_primary.desc(), PartyTaxId.created_at)
        .all()
    )
    if not rows:
        return None
    for row in rows:
        if row.is_primary:
            return row
    return rows[0] if len(rows) == 1 else rows[0]


def latest_completed_check(db: Session, tax_id) -> Optional[PartyVatCheck]:
    """The newest check of this identifier that actually reached an answer.

    **`unavailable` is not an answer** (D62). A row saying "we could not
    reach the registry" must not displace the `valid` from last week, or a
    government service having a bad afternoon would un-verify every customer
    at once. Same for `not_configured`: the operator has not supplied a
    credential, which says nothing about the number.
    """
    return (
        db.query(PartyVatCheck)
        .filter(
            PartyVatCheck.tax_id_id == tax_id.id,
            PartyVatCheck.status.notin_(
                (VAT_UNAVAILABLE, VAT_NOT_CONFIGURED)
            ),
        )
        .order_by(PartyVatCheck.checked_at.desc())
        .first()
    )


def vat_is_verified(db: Session, party: Party) -> bool:
    """Whether the party's primary VAT-like ID is currently verified.

    The definition of `buyer_vat_verified` (D56, extended by D62). Four
    halves now, each of which has cost somebody a wrong invoice somewhere:

      * **latest**: an older `valid` followed by an `invalid` is not verified;
      * **current**: a check of a different number is not evidence about this
        one. Now structural rather than remembered — the check points at the
        identifier row, and changing a value retires that row and creates a
        new one, so an old check cannot follow a new number;
      * **completed**: `unavailable` and `not_configured` do not count as
        answers and do not displace an earlier verdict;
      * **fresh**: a `valid` older than the company's `max_check_age_days` is
        no longer relied on (D62). A verification from 2019 is a historical
        fact, not a current one.

    A **manual confirmation never verifies an EU VAT ID** (D66). It is how a
    human records evidence for a number no register will answer about; for a
    `vat` the registry is reachable, so a human saying "trust me" in its
    place would put a reverse charge on an invoice with nothing behind it.
    """
    tax_id = primary_tax_id(db, party)
    if tax_id is None:
        return False
    latest = latest_completed_check(db, tax_id)
    if latest is None or latest.status != VAT_VALID:
        return False
    if latest.provider == PROVIDER_MANUAL and tax_id.kind == TAX_ID_VAT:
        return False
    return not _check_is_stale(db, party, latest)


def _check_is_stale(db: Session, party: Party, check: PartyVatCheck) -> bool:
    """Whether a check is older than the company's policy allows."""
    from .tax_id_policy import load_policy

    policy = load_policy(db, company_id=party.company_id)
    max_age = policy.max_check_age_days
    if not max_age:
        return False
    checked_at = check.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at < datetime.now(timezone.utc) - timedelta(days=max_age)


def tax_id_evidence(db: Session, party: Party) -> Optional[dict]:
    """The check `vat_is_verified` relied on, as plain data, or None.

    For P2 to snapshot onto a finalized document (D64): an invoice claiming
    a reverse charge should be able to say which lookup, on which day, with
    which consultation reference, it rested on. Returns None whenever
    `vat_is_verified` is False, so the evidence and the flag can never
    disagree — they are computed from the same two reads.
    """
    tax_id = primary_tax_id(db, party)
    if tax_id is None:
        return None
    latest = latest_completed_check(db, tax_id)
    if latest is None or latest.status != VAT_VALID:
        return None
    if latest.provider == PROVIDER_MANUAL and tax_id.kind == TAX_ID_VAT:
        return None
    if _check_is_stale(db, party, latest):
        return None
    return {
        "tax_id_id": str(tax_id.id),
        "kind": tax_id.kind,
        "value": tax_id.value,
        "check_id": str(latest.id),
        "status": latest.status,
        "qualified": bool(latest.qualified),
        "checked_at": latest.checked_at.isoformat(),
        "consultation_ref": latest.consultation_ref,
        "provider": latest.provider,
    }


def has_business_evidence(db: Session, party: Party) -> bool:
    """Whether anything on file shows this party is a business.

    Used only for the non-EU warning (D67), never to block. A valid registry
    answer counts; so does a current manual confirmation, which for a US or
    Swiss party is the only kind of evidence obtainable at all. A company
    register number on its own does not — it says the entity exists, which
    nobody doubted.
    """
    rows = (
        db.query(PartyTaxId)
        .filter(PartyTaxId.party_id == party.id, PartyTaxId.is_active.is_(True))
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        latest = latest_completed_check(db, row)
        if latest is None or latest.status != VAT_VALID:
            continue
        if latest.valid_until is not None:
            until = latest.valid_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until < now:
                continue
        return True
    return False


def _level_of(db: Session, party: Party) -> PartyLevel:
    billing = _default_address(db, party.id, "billing")
    postal = _default_address(db, party.id, "postal")
    primary = primary_tax_id(db, party)
    return PartyLevel(
        level=party.type,
        id=party.id,
        name=party.name,
        trading_name=party.trading_name,
        billing_address=_address_value(billing),
        postal_address=_address_value(postal),
        # The primary VAT-like identifier, not a column. `vat_id` keeps its
        # name in the resolver because its CONTRACT is unchanged — "the one
        # number that stands for this level" — and renaming it would have
        # churned every golden fixture for no gain.
        vat_id=primary.value if primary else None,
        tax_id_kind=primary.kind if primary else None,
        tax_status_note=party.tax_status_note,
        tax_id_evidence=tax_id_evidence(db, party),
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


# ─── Duplicates: a warning, never a refusal ─────────────────────────────────


def find_duplicates(
    db: Session,
    *,
    company_id: uuid.UUID,
    name: Optional[str] = None,
    vat_id: Optional[str] = None,
    email: Optional[str] = None,
    zip_code: Optional[str] = None,
    exclude_id: Optional[uuid.UUID] = None,
) -> list[dict]:
    """"This might be one you already have." Three signals, never blocking.

    Blocking a create on a guess is worse than a duplicate: the guess is
    sometimes wrong — a holding company and its subsidiary legitimately
    share a name and a postcode — and somebody who cannot save a customer
    cannot invoice them. So it is a list of warnings, and the merge tool is
    later.

    The three signals, in descending confidence: the same normalised VAT ID
    (near-certain), the same primary email (near-certain), the same name and
    postcode (a hint).
    """
    found: list[dict] = []
    seen: set = set()

    def _add(row, reason: str, detail: str) -> None:
        if row.id in seen or row.id == exclude_id:
            return
        seen.add(row.id)
        found.append(
            {
                "party_id": row.id,
                "name": row.name,
                "reason": reason,
                "detail": detail,
            }
        )

    if vat_id:
        # Across every kind and including retired identifiers: if somebody
        # retired a number and is now adding it to a different party, that is
        # exactly the mix-up this warning exists to catch.
        for row, tax_id in (
            db.query(Party, PartyTaxId)
            .join(PartyTaxId, PartyTaxId.party_id == Party.id)
            .filter(Party.company_id == company_id, PartyTaxId.value == vat_id)
            .all()
        ):
            _add(
                row,
                "vat_id",
                f"{row.name} already has the {tax_id.kind} identifier "
                f"{vat_id}."
                + ("" if tax_id.is_active else " (retired, but still on file.)"),
            )

    if email:
        rows = (
            db.query(Party)
            .join(PartyContactPoint, PartyContactPoint.party_id == Party.id)
            .filter(
                Party.company_id == company_id,
                PartyContactPoint.kind == "email",
                func.lower(PartyContactPoint.value) == email.strip().lower(),
            )
            .all()
        )
        for row in rows:
            _add(row, "email", f"{row.name} already uses the address {email}.")

    if name and zip_code:
        rows = (
            db.query(Party)
            .join(PartyAddress, PartyAddress.party_id == Party.id)
            .filter(
                Party.company_id == company_id,
                func.lower(Party.name) == name.strip().lower(),
                PartyAddress.zip == zip_code,
            )
            .all()
        )
        for row in rows:
            _add(
                row,
                "name_and_zip",
                f"{row.name} has the same name and postcode ({zip_code}).",
            )

    return found


# ─── Archiving ──────────────────────────────────────────────────────────────


def active_children(db: Session, party: Party) -> list[Party]:
    return (
        db.query(Party)
        .filter(
            Party.company_id == party.company_id,
            Party.parent_id == party.id,
            Party.is_active.is_(True),
        )
        .order_by(Party.name)
        .all()
    )


def refuse_archive_with_active_children(db: Session, party: Party) -> None:
    """An organisation with live departments or people cannot be archived.

    Named, because "cannot archive: it has children" leaves somebody opening
    rows one at a time to find out which. Archiving the parent while a
    department is still live would leave that department resolving its
    address from an archived organisation — usable, but explaining itself
    with a row the list no longer shows.
    """
    children = active_children(db, party)
    if not children:
        return
    listed = ", ".join(f"{child.name} ({child.type})" for child in children)
    raise PartyError(
        f"{party.name} still has active entries below it: {listed}. Archive "
        "those first — archiving never deletes, so they stay readable "
        "afterwards."
    )


# ─── Parties as referrers of tax codes and classes (P1a-2 part 2) ───────────
#
# Registered HERE, at the bottof the module that owns the referring table,
# which is what `tax_references`' registration point is for: a guard whose
# knowledge lives in the tax router is a guard the next endpoint forgets.
#
# A party's `tax_override` points at a tax code by its CODE STRING or names
# the `domestic_for_foreign_business` kind, which points at no code at all.
# So only the first shape is a reference — and deactivating a code a customer
# is pinned to would make every sale to that customer unpriceable, with a
# `blocking` answer and no indication which switch caused it.


def _parties_overriding_to(db: Session, code) -> list:
    """Active parties whose `tax_override` names this code."""
    from .tax_references import Referrer

    rows = (
        db.query(Party)
        .filter(
            Party.company_id == code.company_id,
            Party.is_active.is_(True),
            Party.tax_override.isnot(None),
        )
        .order_by(Party.name)
        .all()
    )
    found = []
    for row in rows:
        override = row.tax_override or {}
        if override.get("kind") == "code" and override.get("code") == code.code:
            found.append(Referrer(kind="customer", name=row.name))
    return found


def register_referrers() -> None:
    """Tell the tax reference registry about parties.

    A function rather than a decorator at import time, called from
    `main.py`, because the registry lives in `tax_references` and importing
    this module from there would close a cycle. Idempotent — the registry
    refuses a duplicate source — so importing twice is harmless.
    """
    from .tax_references import register_code_referrers

    register_code_referrers(_parties_overriding_to)


# Registered on import as well, so a test that imports this module without
# going through `main` still gets the guard. The registry is idempotent.
register_referrers()
