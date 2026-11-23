"""Writing the catalog: seeding, the hierarchy rules, and the CSV import.

Everything in here needs a `Session`; everything that does not is in
`apps/api/billing/catalog/` (the kind enum, the factor lookup, the whole
import rule set). The split is why `test_catalog_import.py` can assert every
error sentence the importer produces without a database.

**The import applies the plan it was shown.** `preview_import` and
`apply_import` both call `billing.catalog.plan_from_file` with the same
context, so there is one parser and one set of rules. `apply_import` also
takes the digest the preview returned: if the freshly computed plan does not
match it, nothing is written and the refusal says so. Between looking at a
preview and pressing Apply, somebody in another tab can rename a category or
create an item with one of the file's SKUs — and the honest answer then is
"this is no longer what you looked at", not a silent different outcome.

**Seeding never overwrites.** `seed_catalog` is safe to call on a company
that already has a catalog: it adds what is missing by key and touches
nothing else. Called from `create_company`, and again when a company's
country is first set, for the same reason `seed_tax_configuration` is.
"""

import uuid
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from ..billing.catalog import (
    ACTION_CREATE,
    ACTION_UPDATE,
    ITEM_KINDS,
    SYSTEM_UNITS,
    FactorError,
    ImportContext,
    ImportPlan,
    ImportRow,
    kind_label,
    normalise_table,
    plan_from_file,
)
from ..core.money import quantize_amount
from ..models.catalog import (
    MAX_CATEGORY_DEPTH,
    RATE_BILL,
    RATE_COST,
    Item,
    ItemCategory,
    ItemRate,
    RentalFactor,
    RentalFactorGroup,
    Unit,
)
from ..models.company import Company
from ..models.tax import TaxClass
from . import audit
from .permissions import scoped
from .tax_references import Referrer, register_class_referrers


class CatalogError(ValueError):
    """The catalog cannot be changed that way, and the message says why.

    Surfaces as a 409 (a rule about existing rows: a cycle, a depth, a
    referenced class) or a 422 (a value that cannot be what it claims). The
    router decides which; this layer's job is the sentence.
    """


# ─── Audit actions ──────────────────────────────────────────────────────────
#
# Named constants rather than string literals at the call sites, so the set
# of actions this module can write is one screen and a typo is a NameError
# rather than an audit row nobody will ever search for.

ITEM_CREATED = "item.created"
ITEM_UPDATED = "item.updated"
ITEM_DEACTIVATED = "item.deactivated"
ITEM_REACTIVATED = "item.reactivated"
ITEM_RATES_CHANGED = "item.rates_changed"
CATEGORY_CREATED = "item_category.created"
CATEGORY_UPDATED = "item_category.updated"
CATEGORY_DEACTIVATED = "item_category.deactivated"
CATEGORY_REACTIVATED = "item_category.reactivated"
UNIT_CREATED = "unit.created"
UNIT_UPDATED = "unit.updated"
FACTOR_GROUP_CREATED = "rental_factor_group.created"
FACTOR_GROUP_UPDATED = "rental_factor_group.updated"
FACTOR_GROUP_FACTORS_CHANGED = "rental_factor_group.factors_changed"
IMPORT_APPLIED = "catalog.import_applied"

CATALOG_AUDIT_ACTIONS: tuple[str, ...] = (
    ITEM_CREATED,
    ITEM_UPDATED,
    ITEM_DEACTIVATED,
    ITEM_REACTIVATED,
    ITEM_RATES_CHANGED,
    CATEGORY_CREATED,
    CATEGORY_UPDATED,
    CATEGORY_DEACTIVATED,
    CATEGORY_REACTIVATED,
    UNIT_CREATED,
    UNIT_UPDATED,
    FACTOR_GROUP_CREATED,
    FACTOR_GROUP_UPDATED,
    FACTOR_GROUP_FACTORS_CHANGED,
    IMPORT_APPLIED,
)

#: What an audit row records about an item. The fields that would end up on a
#: document or change a price come first.
ITEM_AUDITED: tuple[str, ...] = (
    "sku",
    "name_de",
    "name_en",
    "kind",
    "category_id",
    "unit_id",
    "default_tax_class",
    "rental_factor_group_id",
    "revenue_account",
    "expense_account",
    "rebilled_cost",
    "description_internal",
    "description_customer",
    "notes",
    "is_active",
)

CATEGORY_AUDITED: tuple[str, ...] = ("name", "kind", "parent_id", "position", "is_active")
UNIT_AUDITED: tuple[str, ...] = (
    "key",
    "label_de",
    "label_en",
    "period_unit",
    "position",
    "is_active",
)
FACTOR_GROUP_AUDITED: tuple[str, ...] = ("name", "notes", "is_active")


# ─── Seeding ────────────────────────────────────────────────────────────────


def seed_catalog(db: Session, *, company: Company) -> dict[str, int]:
    """The system units and one default category per kind. No commit.

    Returns `{"units": n, "categories": n}` — what was ADDED, so a caller can
    tell a first seed from a no-op.

    **Data, not code** (the A1 prompt's wording, and SCOPE's principle): the
    units come from `billing/catalog/kinds.SYSTEM_UNITS` and the categories
    from `KIND_LABELS`, both tables in the pure package. Nothing here decides
    what a unit is called.

    **Idempotent, by key.** A unit is added when the company has no unit with
    that key; a default category is added when the company has no category
    at all for that kind. The second test is deliberately looser than the
    first: somebody who renamed "Equipment-Miete" to "Technik" must not get a
    second default category for `equipment_rental` on the next seed, and
    matching on the NAME would give them one.
    """
    added = {"units": 0, "categories": 0}

    existing_units = {
        key
        for (key,) in db.query(Unit.key).filter(Unit.company_id == company.id).all()
    }
    for position, unit in enumerate(SYSTEM_UNITS):
        if unit["key"] in existing_units:
            continue
        db.add(
            Unit(
                company_id=company.id,
                key=unit["key"],
                label_de=unit["de"],
                label_en=unit["en"],
                period_unit=unit["period_unit"],
                is_system=True,
                position=position,
            )
        )
        added["units"] += 1

    kinds_with_a_category = {
        kind
        for (kind,) in db.query(ItemCategory.kind)
        .filter(ItemCategory.company_id == company.id)
        .distinct()
        .all()
    }
    for position, kind in enumerate(ITEM_KINDS):
        if kind in kinds_with_a_category:
            continue
        db.add(
            ItemCategory(
                company_id=company.id,
                name=kind_label(kind, "de"),
                kind=kind,
                parent_id=None,
                position=position,
            )
        )
        added["categories"] += 1

    db.flush()
    return added


# ─── The category hierarchy ─────────────────────────────────────────────────


def ancestors(db: Session, category: ItemCategory) -> list[ItemCategory]:
    """From `category`'s parent up to the root, nearest first.

    Bounded by `MAX_CATEGORY_DEPTH + 1` iterations rather than by trusting
    the tree to be acyclic. A cycle that somehow got in — a direct UPDATE, a
    future bulk import — would otherwise make this loop forever, and a
    request that never returns is a worse failure than a wrong answer.
    """
    chain: list[ItemCategory] = []
    current = category
    for _ in range(MAX_CATEGORY_DEPTH + 1):
        if current.parent_id is None:
            return chain
        parent = (
            db.query(ItemCategory)
            .filter(
                ItemCategory.id == current.parent_id,
                ItemCategory.company_id == category.company_id,
            )
            .first()
        )
        if parent is None:
            return chain
        chain.append(parent)
        current = parent
    raise CatalogError(
        f"the category {category.name!r} is in a loop of parents. That is a "
        f"state the API refuses to create; it has to be fixed before the "
        f"category can be changed."
    )


def subtree_height(db: Session, category: ItemCategory) -> int:
    """How many levels `category` is at the top of. A leaf is 1.

    Needed because moving a category moves everything under it: a two-level
    branch dropped under a level-2 category would put its leaves at level 4,
    and checking only the category's own new depth would allow it.
    """
    children = (
        db.query(ItemCategory)
        .filter(
            ItemCategory.company_id == category.company_id,
            ItemCategory.parent_id == category.id,
        )
        .all()
    )
    if not children:
        return 1
    return 1 + max(subtree_height(db, child) for child in children)


def check_parent(
    db: Session,
    *,
    category: Optional[ItemCategory],
    parent: Optional[ItemCategory],
) -> None:
    """Refuse a parent that would make a cycle or break the depth limit.

    `category` is None when creating — there is nothing to cycle yet and
    nothing underneath, so only the parent's own depth matters.

    Both rules produce a sentence naming the categories involved, because the
    person hitting them is dragging rows around in a tree and "invalid
    parent" tells them nothing about which rule they met.
    """
    if parent is None:
        return

    if category is not None:
        if parent.id == category.id:
            raise CatalogError(
                f"a category cannot be its own parent ({category.name!r})."
            )
        chain = ancestors(db, parent)
        if any(step.id == category.id for step in chain):
            route = " › ".join(step.name for step in reversed(chain))
            raise CatalogError(
                f"{parent.name!r} is already below {category.name!r} "
                f"({route}), so making it the parent would create a loop."
            )

    depth_of_parent = len(ancestors(db, parent)) + 1
    height = subtree_height(db, category) if category is not None else 1
    deepest = depth_of_parent + height

    if deepest > MAX_CATEGORY_DEPTH:
        raise CatalogError(
            f"categories go {MAX_CATEGORY_DEPTH} levels deep and this would "
            f"make {deepest}. {parent.name!r} is already at level "
            f"{depth_of_parent}"
            + (
                f", and {category.name!r} has {height} level(s) under it"
                if category is not None and height > 1
                else ""
            )
            + ". Put it higher up, or move its sub-categories out first."
        )


def category_path(db: Session, category: ItemCategory) -> str:
    """`Equipment › Camera › Cinema`. For a list screen and for an error."""
    chain = [step.name for step in reversed(ancestors(db, category))]
    return " › ".join(chain + [category.name])


# ─── Rates ──────────────────────────────────────────────────────────────────


def replace_rates(
    db: Session,
    *,
    item: Item,
    rates: Sequence[dict],
    currency: str,
) -> tuple[dict, dict]:
    """Make the item's rates exactly `rates`. Returns `(before, after)`.

    A full replacement rather than per-rate CRUD: the item detail screen
    shows the whole set and submits the whole set, and a diff computed here
    is one the audit row can state as "bill/day 850.00 → 900.00" instead of
    three separate events that have to be read together.

    The before/after pair is returned rather than audited in here, because
    CLAUDE.md says the audit row goes in the CALLER's transaction with the
    caller's actor — this function has no request and no actor.
    """
    before = {
        f"{row.rate_type}/{row.period_unit}": str(row.amount) for row in item.rates
    }

    wanted: dict[tuple[str, str], Decimal] = {}
    for entry in rates:
        rate_type = entry["rate_type"]
        period_unit = entry["period_unit"]
        if rate_type not in (RATE_BILL, RATE_COST):
            raise CatalogError(
                f"{rate_type!r} is not a rate type; a rate is either "
                f"{RATE_BILL!r} (what the customer pays) or {RATE_COST!r} "
                f"(what it costs us)."
            )
        key = (rate_type, period_unit)
        if key in wanted:
            raise CatalogError(
                f"there are two {rate_type} rates for {period_unit!r}. An "
                f"item has one rate per type per period — two would make "
                f"\"the day rate\" a question with two answers."
            )
        amount = quantize_amount(entry["amount"])
        if amount < 0:
            raise CatalogError(
                f"the {rate_type} rate for {period_unit!r} is {amount}. A "
                f"negative rate is not a discount — put the discount on the "
                f"line or the group."
            )
        wanted[key] = amount

    existing = {(row.rate_type, row.period_unit): row for row in item.rates}
    for key, amount in wanted.items():
        row = existing.get(key)
        if row is None:
            db.add(
                ItemRate(
                    company_id=item.company_id,
                    item_id=item.id,
                    rate_type=key[0],
                    period_unit=key[1],
                    amount=amount,
                    currency=currency,
                )
            )
        else:
            row.amount = amount
            row.currency = currency
    for key, row in existing.items():
        if key not in wanted:
            # The one deletion in the catalog, and it is not a catalog row:
            # a rate is a price that is no longer offered, not a historical
            # fact — a document that was priced from it carries its own
            # snapshot (rule 4). Removing a rate the screen no longer shows
            # is the only way "the item has no cost rate" can be expressed.
            db.delete(row)

    db.flush()
    after = {
        f"{rate_type}/{period_unit}": str(amount)
        for (rate_type, period_unit), amount in sorted(wanted.items())
    }
    return before, after


def rate_of(
    item: Item, *, rate_type: str, period_unit: str
) -> Optional[Decimal]:
    """One of an item's rates, or None. Reads the loaded collection."""
    for row in item.rates:
        if row.rate_type == rate_type and row.period_unit == period_unit:
            return row.amount
    return None


# ─── Factor tables ──────────────────────────────────────────────────────────


def replace_factors(
    db: Session, *, group: RentalFactorGroup, factors: Sequence[dict]
) -> tuple[dict, dict]:
    """Make the group's table exactly `factors`. Returns `(before, after)`.

    Validated through the pure `normalise_table`, so the rules the lookup
    relies on (whole days, days ≥ 1, factor > 0, no two factors for one day)
    are the lookup's own rules and not a second copy of them here.
    """
    before = {str(row.days): str(row.factor) for row in group.factors}
    try:
        table = normalise_table(
            [(entry["days"], entry["factor"]) for entry in factors]
        )
    except FactorError as error:
        raise CatalogError(str(error)) from None

    existing = {row.days: row for row in group.factors}
    for days, factor in table.items():
        row = existing.get(days)
        if row is None:
            db.add(
                RentalFactor(
                    company_id=group.company_id,
                    group_id=group.id,
                    days=days,
                    factor=factor,
                )
            )
        else:
            row.factor = factor
    for days, row in existing.items():
        if days not in table:
            db.delete(row)

    db.flush()
    after = {str(days): str(factor) for days, factor in sorted(table.items())}
    return before, after


# ─── The CSV import ─────────────────────────────────────────────────────────


def import_context(db: Session, membership) -> ImportContext:
    """What already exists in this company, as plain data for the planner.

    One query per lookup table and no joins: the biggest catalog this will
    ever see is a few thousand rows, and four small `SELECT`s are cheaper to
    read than one clever statement.

    Units are indexed by key AND by both labels, lower-cased, because the
    column in the spreadsheet says "Tag" rather than "day" about half the
    time. Categories likewise by name — there is no other handle on one in a
    CSV.
    """
    items = {
        sku: str(item_id)
        for item_id, sku in scoped(db.query(Item.id, Item.sku), membership)
        .filter(Item.sku.isnot(None))
        .all()
    }
    categories: dict[str, str] = {}
    for category_id, name in scoped(
        db.query(ItemCategory.id, ItemCategory.name), membership
    ).filter(ItemCategory.is_active.is_(True)):
        # First one wins, so a company with two "Camera" categories imports
        # into the first consistently rather than into whichever the planner
        # happened to see last.
        categories.setdefault(name.strip().lower(), str(category_id))

    units: dict[str, str] = {}
    for unit_id, key, label_de, label_en in scoped(
        db.query(Unit.id, Unit.key, Unit.label_de, Unit.label_en), membership
    ).filter(Unit.is_active.is_(True)):
        for handle in (key, label_de, label_en):
            if handle:
                units.setdefault(handle.strip().lower(), str(unit_id))

    tax_classes = frozenset(
        key.lower()
        for (key,) in scoped(db.query(TaxClass.key), membership)
        .filter(TaxClass.is_active.is_(True))
        .all()
    )

    return ImportContext(
        items_by_sku=items,
        categories_by_name=categories,
        units_by_key=units,
        tax_class_keys=tax_classes,
    )


def preview_import(db: Session, membership, payload: bytes | str) -> ImportPlan:
    """What the file would do. Writes nothing."""
    return plan_from_file(payload, import_context(db, membership))


def apply_import(
    db: Session,
    membership,
    payload: bytes | str,
    *,
    actor: audit.Actor,
    expected_digest: Optional[str] = None,
    currency: str,
) -> ImportPlan:
    """Apply the plan this file produces. No commit — the caller owns it.

    `expected_digest` is the digest the preview returned. When given and the
    freshly planned import does not match it, NOTHING is written and the
    refusal names both digests. See the module docstring.

    Error rows are skipped, not refused: the A1 prompt's acceptance has a
    five-row file with one bad row, and the good four have to land. The
    counts in the result and in the audit row say how many of each.
    """
    plan = preview_import(db, membership, payload)

    if expected_digest is not None and plan.digest != expected_digest:
        raise CatalogError(
            f"this file no longer does what the preview showed "
            f"(the preview was {expected_digest}, the file now plans "
            f"{plan.digest}). Something in the catalog changed in between — "
            f"a category renamed, or an item created with one of these SKUs. "
            f"Run the preview again and check it before applying."
        )

    for row in plan.rows:
        if row.action == ACTION_CREATE:
            _create_from_row(db, membership, row, currency=currency, actor=actor)
        elif row.action == ACTION_UPDATE:
            _update_from_row(db, membership, row, currency=currency, actor=actor)

    audit.record(
        db,
        actor,
        None,
        IMPORT_APPLIED,
        after={
            "created": len(plan.creates),
            "updated": len(plan.updates),
            "skipped_with_errors": len(plan.errors),
            "digest": plan.digest,
            "delimiter": plan.delimiter,
        },
        entity_type="items",
        company_id=membership.company_id,
    )
    db.flush()
    return plan


def _rate_entries(row: ImportRow) -> list[dict]:
    """The one or two day rates a row carries, as `replace_rates` wants them."""
    entries: list[dict] = []
    values = row.values
    if values is None:  # pragma: no cover - error rows never reach here
        return entries
    if values.bill_rate_day is not None:
        entries.append(
            {"rate_type": RATE_BILL, "period_unit": "day", "amount": values.bill_rate_day}
        )
    if values.cost_rate_day is not None:
        entries.append(
            {"rate_type": RATE_COST, "period_unit": "day", "amount": values.cost_rate_day}
        )
    return entries


def _create_from_row(
    db: Session, membership, row: ImportRow, *, currency: str, actor: audit.Actor
) -> Item:
    values = row.values
    item = Item(
        company_id=membership.company_id,
        sku=values.sku,
        name_de=values.name_de,
        name_en=values.name_en,
        kind=values.kind,
        category_id=uuid.UUID(row.category_id) if row.category_id else None,
        unit_id=uuid.UUID(row.unit_id) if row.unit_id else None,
        default_tax_class=values.tax_class,
        is_active=True if values.active is None else values.active,
    )
    db.add(item)
    db.flush()
    replace_rates(db, item=item, rates=_rate_entries(row), currency=currency)
    audit.record(
        db,
        actor,
        item,
        ITEM_CREATED,
        after=audit.snapshot(item, ITEM_AUDITED),
        reason=f"CSV import, line {row.line_number}",
    )
    return item


def _update_from_row(
    db: Session, membership, row: ImportRow, *, currency: str, actor: audit.Actor
) -> Optional[Item]:
    item = (
        scoped(db.query(Item), membership)
        .filter(Item.id == uuid.UUID(row.item_id))
        .first()
    )
    if item is None:  # pragma: no cover - the plan resolved it a moment ago
        return None

    values = row.values
    before = audit.snapshot(item, ITEM_AUDITED)

    # A blank column LEAVES THE FIELD ALONE rather than clearing it. See
    # `billing/catalog/csv_import.ItemValues` for why that is the safer of
    # the two readings for a bridge from a spreadsheet.
    if values.name_de:
        item.name_de = values.name_de
    if values.name_en:
        item.name_en = values.name_en
    if values.kind:
        item.kind = values.kind
    if row.category_id:
        item.category_id = uuid.UUID(row.category_id)
    if row.unit_id:
        item.unit_id = uuid.UUID(row.unit_id)
    if values.tax_class:
        item.default_tax_class = values.tax_class
    if values.active is not None:
        item.is_active = values.active

    rate_before, rate_after = (None, None)
    entries = _rate_entries(row)
    if entries:
        # Only the rates the file names. A file with no `cost_rate_day`
        # column must not wipe the cost rates of every item in it, which is
        # what a full `replace_rates` with an empty list would do.
        merged = {
            (existing.rate_type, existing.period_unit): existing.amount
            for existing in item.rates
        }
        for entry in entries:
            merged[(entry["rate_type"], entry["period_unit"])] = entry["amount"]
        rate_before, rate_after = replace_rates(
            db,
            item=item,
            rates=[
                {"rate_type": key[0], "period_unit": key[1], "amount": amount}
                for key, amount in merged.items()
            ],
            currency=currency,
        )

    db.flush()
    audit.record(
        db,
        actor,
        item,
        ITEM_UPDATED,
        before=before,
        after=audit.snapshot(item, ITEM_AUDITED),
        reason=f"CSV import, line {row.line_number}",
    )
    if rate_after is not None and rate_before != rate_after:
        audit.record(
            db,
            actor,
            item,
            ITEM_RATES_CHANGED,
            before=rate_before,
            after=rate_after,
            reason=f"CSV import, line {row.line_number}",
        )
    return item


# ─── The reference registry (services/tax_references.py) ────────────────────


@register_class_referrers
def _items_using_this_class(db: Session, tax_class: TaxClass) -> list[Referrer]:
    """Active items whose `default_tax_class` is this class's key.

    Registered HERE, at the bottom of the module that owns the referring
    table, which is what `services/tax_references.py`'s docstring asks for:
    a guard whose knowledge lives in the endpoint is a guard the next
    endpoint forgets.

    **Active items only.** A retired item resolves nothing and is not a
    reason to keep a rate switched on. Reactivating it later with a dead
    class is a state the tax engine already reports (`code_inactive`,
    blocking, naming the code).

    Matched by KEY and `company_id`, because that is what the column holds —
    see `models/catalog.py` for why it is a key and not an id.
    """
    rows = (
        db.query(Item)
        .filter(
            Item.company_id == tax_class.company_id,
            Item.default_tax_class == tax_class.key,
            Item.is_active.is_(True),
        )
        .order_by(Item.name_de)
        .all()
    )
    return [Referrer(kind="item", name=item.name_de) for item in rows]
