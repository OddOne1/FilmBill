"""Crew roles and rates (R1 part 1).

**The roles list IS the catalog's labor items** (decided, 2026-11-26): one
list, edited in the catalog, where each item's cost rate per day is the
TYPICAL day rate for that line of work. A person's role is a
`PartyCrewRole` row pointing at one of those items, optionally with the
person's OWN day rate.

**The effective rate** = the person's own rate for the role if set, else
the item's typical cost rate per day, else none. It is defined twice, once
in Python (`effective`, for responses) and once in SQL
(`effective_rate_rows`, for the list's filter and sort), and
`test_crew_roles.py` proves the two agree on the same rows.

**Rates are cost data** [Guessing, SCOPE D130]: only a member holding
`budgets.view` (`RATE_PERMISSION`) is given any number. Everybody else gets
the roles without one — `rates_visible: false` says so.

No commit anywhere in this module: the caller owns the transaction, and the
audit row lands or vanishes with the write.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..billing.catalog.kinds import KIND_LABOR
from ..core.money import quantize_amount, to_decimal
from ..models.catalog import Item, ItemRate, RATE_COST
from ..models.crew import PartyCrewRole
from ..models.party import Party
from . import audit

#: Who may see what a person costs. The existing key, not a new one: "may
#: this person see what things cost us" is a decision the matrix has made.
RATE_PERMISSION = "budgets.view"

RATE_OWN = "own"
RATE_TYPICAL = "typical"
RATE_NONE = "none"

ROLE_FIELDS = ("party_id", "item_id", "role", "own_day_rate", "notes", "is_active", "from_tag", "source")


class CrewRoleError(ValueError):
    """A sentence for the person; surfaces as a 422."""


class NotCrew(CrewRoleError):
    """The party is not flagged crew; surfaces as a 422 the screen can offer
    "Mark as crew" for."""


class RoleExists(CrewRoleError):
    """The person already holds this role; surfaces as a 409."""


# ─── The rate rule ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffectiveRate:
    own: Optional[Decimal]
    typical: Optional[Decimal]
    effective: Optional[Decimal]
    source: str
    #: own − typical, when both exist. Negative when the person is cheaper.
    difference: Optional[Decimal]


def effective(own: Optional[Decimal], typical: Optional[Decimal]) -> EffectiveRate:
    """Own if set, else typical, else none."""
    if own is not None:
        diff = own - typical if typical is not None else None
        return EffectiveRate(own, typical, own, RATE_OWN, diff)
    if typical is not None:
        return EffectiveRate(None, typical, typical, RATE_TYPICAL, None)
    return EffectiveRate(None, None, None, RATE_NONE, None)


def clean_rate(value) -> Optional[Decimal]:
    """A day rate as money, or None. Raises `CrewRoleError` for a negative."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    amount = quantize_amount(to_decimal(value, what="The day rate"))
    if amount < 0:
        raise CrewRoleError(
            f"The day rate is {amount}. A day rate cannot be negative; enter "
            "what this person costs per day, or leave it empty to use the typical rate."
        )
    return amount


def typical_rates(
    db: Session, company_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Optional[Decimal]]:
    """`item id → cost rate per day` (None when the item has none). One query."""
    ids = list(set(item_ids))
    out: dict[uuid.UUID, Optional[Decimal]] = {item_id: None for item_id in ids}
    if not ids:
        return out
    for item_id, amount in (
        db.query(ItemRate.item_id, ItemRate.amount)
        .filter(
            ItemRate.company_id == company_id,
            ItemRate.item_id.in_(ids),
            ItemRate.rate_type == RATE_COST,
            ItemRate.period_unit == "day",
        )
        .all()
    ):
        out[item_id] = amount
    return out


def effective_rate_rows(company_id: uuid.UUID):
    """The same rule in SQL: one row per active role, `(party_id, item_id,
    rate)` with `rate = COALESCE(own, typical)`. For the list's filter and
    sort, so a range finds people on pages that were never loaded."""
    typical = (
        select(ItemRate.item_id, ItemRate.amount)
        .where(
            ItemRate.company_id == company_id,
            ItemRate.rate_type == RATE_COST,
            ItemRate.period_unit == "day",
        )
        .subquery("typical")
    )
    return (
        select(
            PartyCrewRole.party_id.label("party_id"),
            PartyCrewRole.item_id.label("item_id"),
            func.coalesce(PartyCrewRole.own_day_rate, typical.c.amount).label("rate"),
        )
        .select_from(PartyCrewRole)
        .outerjoin(typical, typical.c.item_id == PartyCrewRole.item_id)
        .where(PartyCrewRole.company_id == company_id, PartyCrewRole.is_active.is_(True))
        .subquery("effective_rates")
    )


# ─── Reading ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleView:
    row: PartyCrewRole
    name: str
    rate: EffectiveRate


def roles_of(
    db: Session, company_id: uuid.UUID, party_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[RoleView]]:
    """`party id → active roles, oldest first`, with their rates. Two queries
    for a whole page."""
    ids = list(party_ids)
    out: dict[uuid.UUID, list[RoleView]] = {pid: [] for pid in ids}
    if not ids:
        return out
    rows = (
        db.query(PartyCrewRole, Item.name_de)
        .join(Item, Item.id == PartyCrewRole.item_id)
        .filter(
            PartyCrewRole.company_id == company_id,
            PartyCrewRole.party_id.in_(ids),
            PartyCrewRole.is_active.is_(True),
        )
        .order_by(PartyCrewRole.created_at, PartyCrewRole.id)
        .all()
    )
    typical = typical_rates(db, company_id, [row.item_id for row, _ in rows])
    for row, name in rows:
        out[row.party_id].append(
            RoleView(row=row, name=name, rate=effective(row.own_day_rate, typical[row.item_id]))
        )
    return out


def labor_item(db: Session, company_id: uuid.UUID, item_id: uuid.UUID) -> Optional[Item]:
    """This company's item, or None. The KIND is the caller's to check, so
    it can say what is wrong."""
    return (
        db.query(Item)
        .filter(Item.company_id == company_id, Item.id == item_id)
        .first()
    )


def labor_items_by_name(db: Session, company_id: uuid.UUID) -> dict[str, tuple[str, str]]:
    """`casefold(name) → (item id, name)` over the ACTIVE labor items, by
    the German name and then the English one. Two items of the same name:
    the older one wins (the catalog does not make names unique)."""
    out: dict[str, tuple[str, str]] = {}
    rows = (
        db.query(Item.id, Item.name_de, Item.name_en)
        .filter(Item.company_id == company_id, Item.kind == KIND_LABOR, Item.is_active.is_(True))
        .order_by(Item.created_at, Item.id)
        .all()
    )
    for item_id, name_de, _ in rows:
        out.setdefault(" ".join(name_de.split()).casefold(), (str(item_id), name_de))
    for item_id, name_de, name_en in rows:
        if name_en:
            out.setdefault(" ".join(name_en.split()).casefold(), (str(item_id), name_de))
    return out


def tag_suggestions(db: Session, party: Party) -> list[tuple[str, str, str]]:
    """`(tag, item id, item name)` for each of the person's tags that matches
    a labor item they do not hold as a role yet. Offered, never applied."""
    by_name = labor_items_by_name(db, party.company_id)
    held = {
        str(item_id)
        for (item_id,) in db.query(PartyCrewRole.item_id).filter(
            PartyCrewRole.party_id == party.id, PartyCrewRole.is_active.is_(True)
        )
    }
    out = []
    for tag in party.tags or []:
        match = by_name.get(" ".join(tag.split()).casefold())
        if match and match[0] not in held:
            out.append((tag, match[0], match[1]))
    return out


# ─── Writing ────────────────────────────────────────────────────────────────


def _snapshot(row: PartyCrewRole, name: str, **extra) -> dict:
    return {
        "party_id": row.party_id,
        "item_id": row.item_id,
        "role": name,
        "own_day_rate": row.own_day_rate,
        "notes": row.notes,
        "is_active": row.is_active,
        **extra,
    }


def check_crew(party: Party) -> None:
    if not party.is_crew:
        raise NotCrew(
            f"{party.name} is not marked as crew. Mark them as crew first, "
            "then add the role."
        )


def check_labor_item(item: Optional[Item]) -> Item:
    if item is None:
        raise LookupError("item")
    if item.kind != KIND_LABOR:
        raise CrewRoleError(
            f"“{item.name_de}” is not a labor item in the catalog. A crew role "
            "is one of the catalog's labor items; choose one of those."
        )
    if not item.is_active:
        raise CrewRoleError(
            f"“{item.name_de}” is retired in the catalog. Reactivate it in the "
            "catalog, or choose another role."
        )
    return item


def add_role(
    db: Session,
    *,
    party: Party,
    item: Item,
    own_day_rate: Optional[Decimal],
    notes: Optional[str],
    actor: audit.Actor,
    from_tag: Optional[str] = None,
    source: Optional[str] = None,
) -> PartyCrewRole:
    """One role for a crew person. No commit."""
    check_crew(party)
    check_labor_item(item)
    existing = (
        db.query(PartyCrewRole)
        .filter(
            PartyCrewRole.party_id == party.id,
            PartyCrewRole.item_id == item.id,
            PartyCrewRole.is_active.is_(True),
        )
        .first()
    )
    if existing is not None:
        raise RoleExists(
            f"{party.name} already has the role “{item.name_de}”. Change its "
            "rate in the list instead."
        )
    row = PartyCrewRole(
        company_id=party.company_id,
        party_id=party.id,
        item_id=item.id,
        own_day_rate=own_day_rate,
        notes=(notes or "").strip() or None,
        is_active=True,
        # The wall clock, not the transaction's start: a quick add creates
        # several roles in one transaction and their order is the order typed.
        created_at=func.clock_timestamp(),
    )
    db.add(row)
    db.flush()
    extra = {}
    if from_tag is not None:
        extra["from_tag"] = from_tag
    if source is not None:
        extra["source"] = source
    audit.record(
        db, actor, row, "party_crew_role.added", None, _snapshot(row, item.name_de, **extra),
        fields=ROLE_FIELDS,
    )
    return row


def update_role(
    db: Session,
    *,
    row: PartyCrewRole,
    name: str,
    changes: dict,
    actor: audit.Actor,
) -> PartyCrewRole:
    """Own rate and notes. A rate change is recorded with before and after."""
    before = _snapshot(row, name)
    if "own_day_rate" in changes:
        row.own_day_rate = changes["own_day_rate"]
    if "notes" in changes:
        row.notes = (changes["notes"] or "").strip() or None
    db.flush()
    after = _snapshot(row, name)
    if before != after:
        audit.record(
            db, actor, row, "party_crew_role.updated", before, after, fields=ROLE_FIELDS
        )
    return row


def remove_role(db: Session, *, row: PartyCrewRole, name: str, actor: audit.Actor) -> None:
    """Retire the role; the row is kept."""
    if not row.is_active:
        return
    before = _snapshot(row, name)
    row.is_active = False
    db.flush()
    audit.record(
        db, actor, row, "party_crew_role.removed", before, _snapshot(row, name), fields=ROLE_FIELDS
    )


def role_from_tag(db: Session, *, party: Party, tag: str, actor: audit.Actor) -> PartyCrewRole:
    """"Use 'Gaffer' as a role": the tag becomes the matching labor item's
    role and leaves the tags, in one step and only when asked."""
    wanted = " ".join((tag or "").split()).casefold()
    match = next(
        ((t, item_id) for t, item_id, _ in tag_suggestions(db, party) if " ".join(t.split()).casefold() == wanted),
        None,
    )
    if match is None:
        raise CrewRoleError(
            f"{party.name} has no tag “{tag}” that matches a labor item in the "
            "catalog. Add the role from the list instead."
        )
    original, item_id = match
    item = labor_item(db, party.company_id, uuid.UUID(item_id))
    row = add_role(
        db, party=party, item=item, own_day_rate=None, notes=None, actor=actor, from_tag=original
    )
    before_tags = list(party.tags or [])
    party.tags = [t for t in before_tags if t != original]
    db.flush()
    audit.record(
        db, actor, party, "party.updated", {"tags": before_tags}, {"tags": list(party.tags)},
        fields=("tags",),
    )
    return row


def active_role(
    db: Session, company_id: uuid.UUID, party_id: uuid.UUID, role_id: uuid.UUID
) -> Optional[tuple[PartyCrewRole, str]]:
    found = (
        db.query(PartyCrewRole, Item.name_de)
        .join(Item, Item.id == PartyCrewRole.item_id)
        .filter(
            PartyCrewRole.company_id == company_id,
            PartyCrewRole.party_id == party_id,
            PartyCrewRole.id == role_id,
            PartyCrewRole.is_active.is_(True),
        )
        .first()
    )
    return (found[0], found[1]) if found else None


def lowest_rate_expr(company_id: uuid.UUID, party_id_column, item_id: Optional[uuid.UUID] = None):
    """A scalar subquery: the person's lowest effective rate (of one role
    when `item_id` is given). Null when they have none — sorted last."""
    rates = effective_rate_rows(company_id)
    condition = [rates.c.party_id == party_id_column]
    if item_id is not None:
        condition.append(rates.c.item_id == item_id)
    return select(func.min(rates.c.rate)).where(and_(*condition)).scalar_subquery()


def in_rate_range(
    company_id: uuid.UUID,
    party_id_column,
    *,
    item_id: Optional[uuid.UUID],
    low: Optional[Decimal],
    high: Optional[Decimal],
):
    """EXISTS a role (that role, when given) whose effective rate is in range."""
    rates = effective_rate_rows(company_id)
    condition = [rates.c.party_id == party_id_column, rates.c.rate.isnot(None)]
    if item_id is not None:
        condition.append(rates.c.item_id == item_id)
    if low is not None:
        condition.append(rates.c.rate >= low)
    if high is not None:
        condition.append(rates.c.rate <= high)
    return select(rates.c.party_id).where(and_(*condition)).exists()


def has_role(company_id: uuid.UUID, party_id_column, item_id: uuid.UUID):
    return (
        select(PartyCrewRole.id)
        .where(
            PartyCrewRole.company_id == company_id,
            PartyCrewRole.party_id == party_id_column,
            PartyCrewRole.item_id == item_id,
            PartyCrewRole.is_active.is_(True),
        )
        .exists()
    )
