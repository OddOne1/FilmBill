"""Crew: people in the parties list with the `is_crew` flag (B2b part 1).

Everything here writes ordinary `Party` and `PartyContactPoint` rows and
records the ordinary `party.created` event — with `source: crew_quick_add` or
`crew_import` — so a crew member is never a second kind of record that could
drift from the party list. No commit anywhere in this module: the caller owns
the transaction, and the audit row lands or vanishes with the write.
"""

import uuid
from dataclasses import replace
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..billing.resources.crew_csv import (
    CrewImportContext,
    CrewImportPlan,
    CrewValues,
    display_name,
    looks_like_email,
    match_roles,
    normalise_name,
    normalise_roles,
    plan_crew_file,
)
from ..models.catalog import Item
from ..models.party import Party, PartyContactPoint
from . import audit, crew_roles_service, party_service

SOURCE_QUICK_ADD = "crew_quick_add"
SOURCE_IMPORT = "crew_import"

#: What `party.created` records for a crew member: the party's own audited
#: fields, the two contact points, and where it came from.
CREW_CREATED_FIELDS = (
    "type",
    "name",
    "first_name",
    "middle_name",
    "last_name",
    "is_crew",
    "is_customer",
    "is_supplier",
    "party_number",
    "tags",
    "notes",
    "email",
    "phone",
    "source",
)


class CrewError(ValueError):
    """A sentence for the person; surfaces as a 422."""


class DigestMismatch(ValueError):
    """The file no longer plans what the preview showed; surfaces as a 409."""


# ─── Reading ────────────────────────────────────────────────────────────────


def primary_contacts(
    db: Session, company_id: uuid.UUID, party_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, dict[str, str]]:
    """`party id → {"email": …, "phone": …}`, in ONE query for a whole page.

    The primary contact point of each kind if there is one, otherwise the
    oldest — a person entered through the full party form may have an
    address that nobody marked primary.
    """
    ids = list(party_ids)
    out: dict[uuid.UUID, dict[str, str]] = {pid: {} for pid in ids}
    if not ids:
        return out
    rows = (
        db.query(PartyContactPoint)
        .filter(
            PartyContactPoint.company_id == company_id,
            PartyContactPoint.party_id.in_(ids),
        )
        .order_by(
            PartyContactPoint.is_primary.desc(),
            PartyContactPoint.created_at,
            PartyContactPoint.id,
        )
        .all()
    )
    for row in rows:
        out[row.party_id].setdefault(row.kind, row.value)
    return out


def role_suggestions(db: Session, company_id: uuid.UUID) -> tuple[list[str], list[str]]:
    """`(roles in use among crew, most used first; labor item names)`."""
    tag = func.unnest(Party.tags).label("tag")
    used_rows = (
        db.query(tag)
        .filter(Party.company_id == company_id, Party.is_crew.is_(True))
        .subquery()
    )
    used = [
        row[0]
        for row in db.query(used_rows.c.tag, func.count())
        .group_by(used_rows.c.tag)
        .order_by(func.count().desc(), used_rows.c.tag)
        .all()
    ]
    catalog = [
        row[0]
        for row in db.query(Item.name_de)
        .filter(
            Item.company_id == company_id,
            Item.kind == "labor",
            Item.is_active.is_(True),
        )
        .order_by(Item.name_de)
        .all()
    ]
    used_keys = {role.casefold() for role in used}
    return used, [name for name in catalog if name.casefold() not in used_keys]


def import_context(db: Session, company_id: uuid.UUID, *, read_rates: bool = True) -> CrewImportContext:
    """Every party of the company — crew or not, active or archived — by its
    email addresses and by its normalised name; and the labor items a role
    is matched to (R1)."""
    by_email: dict[str, tuple[str, str]] = {}
    by_name: dict[str, tuple[str, str]] = {}
    for party_id, name in (
        db.query(Party.id, Party.name)
        .filter(Party.company_id == company_id)
        .order_by(Party.created_at, Party.id)
        .all()
    ):
        by_name.setdefault(normalise_name(name), (str(party_id), name))
    for value, party_id, name in (
        db.query(PartyContactPoint.value, Party.id, Party.name)
        .join(Party, Party.id == PartyContactPoint.party_id)
        .filter(
            PartyContactPoint.company_id == company_id,
            PartyContactPoint.kind == "email",
        )
        .order_by(Party.created_at, Party.id)
        .all()
    ):
        by_email.setdefault(value.strip().casefold(), (str(party_id), name))
    return CrewImportContext(
        parties_by_email=by_email,
        parties_by_name=by_name,
        labor_items=crew_roles_service.labor_items_by_name(db, company_id),
        read_rates=read_rates,
    )


# ─── Writing ────────────────────────────────────────────────────────────────


def clean_values(
    *,
    name: Optional[str] = None,
    roles: Iterable[Optional[str]] = (),
    email: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
    first_name: Optional[str] = None,
    middle_name: Optional[str] = None,
    last_name: Optional[str] = None,
    day_rate: Optional[Decimal] = None,
) -> CrewValues:
    """The quick-add form's values, held to the import's rules.

    P1b-4: the parts win when there are any, and `name` is derived from
    them; a bare `name` is still accepted (older callers) and stored with
    no parts, exactly like an import line from the old single column.
    """
    first = " ".join((first_name or "").split()) or None
    middle = " ".join((middle_name or "").split()) or None
    last = " ".join((last_name or "").split()) or None
    if middle and not first and not last:
        raise CrewError(
            "This person has a middle name but no first or last name. Enter "
            "at least one of them."
        )
    clean_name = display_name(first, middle, last) or " ".join((name or "").split())
    if not clean_name:
        raise CrewError("This person has no name. Enter a first or last name.")
    try:
        clean_roles = normalise_roles(roles)
    except ValueError as error:
        raise CrewError(str(error)) from None
    clean_email = (email or "").strip() or None
    if clean_email is not None and not looks_like_email(clean_email):
        raise CrewError(
            f"{clean_email!r} is not an email address. Correct it, or leave "
            f"the email field empty."
        )
    try:
        clean_rate = crew_roles_service.clean_rate(day_rate)
    except crew_roles_service.CrewRoleError as error:
        raise CrewError(str(error)) from None
    return CrewValues(
        name=clean_name,
        roles=clean_roles,
        email=clean_email,
        phone=(phone or "").strip() or None,
        notes=(notes or "").strip() or None,
        first_name=first,
        middle_name=middle,
        last_name=last,
        day_rate=clean_rate,
    )


def with_role_matches(db: Session, company_id: uuid.UUID, values: CrewValues) -> CrewValues:
    """The quick add's roles matched to labor items, by the import's rule."""
    matched, unknown = match_roles(
        values.roles, crew_roles_service.labor_items_by_name(db, company_id)
    )
    return replace(values, matched_roles=matched, unknown_roles=unknown)


def create_member(
    db: Session,
    *,
    company_id: uuid.UUID,
    values: CrewValues,
    actor: audit.Actor,
    source: str,
) -> Party:
    """One person, flagged crew, with their email and phone. No commit.

    `allocate_party_number` is called like on every other party write, and
    it gives a crew-only person no number — the rule lives in one function,
    and calling it here is what keeps it the one rule.
    """
    party = Party(
        company_id=company_id,
        type="person",
        parent_id=None,
        name=values.name,
        first_name=values.first_name,
        middle_name=values.middle_name,
        last_name=values.last_name,
        is_crew=True,
        is_customer=False,
        is_supplier=False,
        is_partner=False,
        tags=list(values.tags),
        notes=values.notes,
        einvoice_routing={},
    )
    db.add(party)
    db.flush()
    party_service.allocate_party_number(db, party=party)

    for kind, value in (("email", values.email), ("phone", values.phone)):
        if value:
            db.add(
                PartyContactPoint(
                    company_id=company_id,
                    party_id=party.id,
                    kind=kind,
                    value=value,
                    purpose="general",
                    is_primary=True,
                )
            )
    db.flush()

    after = {
        "type": party.type,
        "name": party.name,
        "first_name": party.first_name,
        "middle_name": party.middle_name,
        "last_name": party.last_name,
        "is_crew": True,
        "is_customer": False,
        "is_supplier": False,
        "party_number": party.party_number,
        "tags": list(values.tags),
        "notes": values.notes,
        "email": values.email,
        "phone": values.phone,
        "source": source,
    }
    audit.record(
        db, actor, party, "party.created", None, after, fields=CREW_CREATED_FIELDS
    )
    # R1: each matched role is a role; the day rate goes on the first one.
    for index, (item_id, _) in enumerate(values.matched_roles):
        item = crew_roles_service.labor_item(db, company_id, uuid.UUID(item_id))
        crew_roles_service.add_role(
            db,
            party=party,
            item=item,
            own_day_rate=values.day_rate if index == 0 else None,
            notes=None,
            actor=actor,
            source=source,
        )
    return party


# ─── The import ─────────────────────────────────────────────────────────────


def preview_import(
    db: Session, company_id: uuid.UUID, payload: bytes, *, read_rates: bool = True
) -> CrewImportPlan:
    return plan_crew_file(payload, import_context(db, company_id, read_rates=read_rates))


def apply_import(
    db: Session,
    *,
    company_id: uuid.UUID,
    payload: bytes,
    actor: audit.Actor,
    expected_digest: Optional[str],
    include_lines: Iterable[int] = (),
    read_rates: bool = True,
) -> tuple[CrewImportPlan, dict[int, Party]]:
    """Recompute the plan from the same file, refuse if it moved, then create
    exactly the planned rows plus the ticked duplicates. No commit.

    Never updates, archives or flags an existing party: the only write in
    this function is `create_member`.
    """
    plan = preview_import(db, company_id, payload, read_rates=read_rates)
    if expected_digest and expected_digest != plan.digest:
        raise DigestMismatch(
            "The file no longer matches the preview you saw (somebody may "
            "have added one of these people in the meantime). Choose the "
            "file again to see a fresh preview."
        )
    created: dict[int, Party] = {}
    for row in plan.to_create(include_lines):
        created[row.line_number] = create_member(
            db,
            company_id=company_id,
            values=row.values,
            actor=actor,
            source=SOURCE_IMPORT,
        )
    return plan, created
