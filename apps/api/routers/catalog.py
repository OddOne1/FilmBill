"""The price list: items, categories, units, rental factor tables, import.

**No DELETE route, anywhere**, and a test asserts the absence on the OpenAPI
document rather than on a handler (same shape as P1a-2's and parties'). An
item that has ever been on a quote is part of that quote's explanation;
retiring is `POST .../deactivate`. The two exceptions are not catalog rows:
an item's RATES and a factor table's ROWS are replaced wholesale by their
sub-resource, because "this item no longer has a cost rate" cannot be said
any other way, and a finalized document carries its own snapshot of the
price it used (rule 4).

**Every handler takes its context from `require("catalog.view")` or
`require("catalog.edit")`**, so the company context cannot be obtained
without the check, and another company's row is a 404 (rule 6).

**The import is two calls and one plan.** `POST /catalog/items/import/preview`
returns the table and a digest; `POST /catalog/items/import` takes the file
again plus that digest and refuses if the plan it computes differs. Both go
through `services/catalog_service` → `billing/catalog/plan_from_file`, which
is the only thing that decides what a row does. See that service's docstring
for why the digest is there.

**What this router validates and what it does not.** A category, unit, tax
class or factor group named by an item must exist, belong to this company and
be active — the refusals name the value. It does NOT require an item's kind
to match its category's: a category is the company's own filing (SCOPE §4.2)
and nothing in the engine reads it, so "Kamera" may hold a camera and the
assistant who operates it. That is a decision, not an omission, and it is the
same in the CSV import — one rule, not two.
"""

import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..billing.catalog import (
    FactorError,
    ImportFormatError,
    ImportPlan,
    REBILLED_COST_KIND,
    billable_days,
    explain_factor,
)
from ..database import get_db
from ..middleware.company import Membership, require
from ..models.catalog import (
    Item,
    ItemCategory,
    RentalFactorGroup,
    Unit,
)
from ..models.tax import TaxClass
from ..schemas.catalog import (
    FactorLookupResponse,
    ImportApplyResponse,
    ImportPreviewResponse,
    ImportRowResponse,
    ItemCategoryCreate,
    ItemCategoryResponse,
    ItemCategoryWrite,
    ItemCreate,
    ItemPage,
    ItemRateResponse,
    ItemRateWrite,
    ItemResponse,
    ItemWrite,
    RentalFactorGroupResponse,
    RentalFactorGroupWrite,
    RentalFactorResponse,
    RentalFactorWrite,
    UnitCreate,
    UnitResponse,
    UnitWrite,
)
from ..services import audit, catalog_service
from ..services.catalog_service import (
    CATEGORY_AUDITED,
    CATEGORY_CREATED,
    CATEGORY_DEACTIVATED,
    CATEGORY_REACTIVATED,
    CATEGORY_UPDATED,
    FACTOR_GROUP_AUDITED,
    FACTOR_GROUP_CREATED,
    FACTOR_GROUP_FACTORS_CHANGED,
    FACTOR_GROUP_UPDATED,
    ITEM_AUDITED,
    ITEM_CREATED,
    ITEM_DEACTIVATED,
    ITEM_RATES_CHANGED,
    ITEM_REACTIVATED,
    ITEM_UPDATED,
    UNIT_AUDITED,
    UNIT_CREATED,
    UNIT_UPDATED,
    CatalogError,
)
from ..services.permissions import scoped

router = APIRouter(tags=["catalog"])

#: The largest import file this endpoint reads, in bytes. A catalog is a few
#: thousand rows; 4 MB is roughly a hundred thousand of them. The limit is
#: here rather than left to the proxy so the refusal is a sentence about a
#: catalogue and not a 413 from nginx.
MAX_IMPORT_BYTES = 4 * 1024 * 1024


def _refuse(error: Exception, code: int = status.HTTP_409_CONFLICT):
    raise HTTPException(status_code=code, detail=str(error)) from None


def _unprocessable(message: str):
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message
    ) from None


def _actor(request: Request, membership: Membership) -> audit.Actor:
    return audit.actor_from(request, membership=membership)


# ─── Lookups. Another company's row is a 404, never a 403 (rule 6). ─────────


def _item_or_404(db: Session, membership: Membership, item_id: uuid.UUID) -> Item:
    row = scoped(db.query(Item), membership).filter(Item.id == item_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    return row


def _category_or_404(
    db: Session, membership: Membership, category_id: uuid.UUID
) -> ItemCategory:
    row = (
        scoped(db.query(ItemCategory), membership)
        .filter(ItemCategory.id == category_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found"
        )
    return row


def _unit_or_404(db: Session, membership: Membership, unit_id: uuid.UUID) -> Unit:
    row = scoped(db.query(Unit), membership).filter(Unit.id == unit_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found"
        )
    return row


def _factor_group_or_404(
    db: Session, membership: Membership, group_id: uuid.UUID
) -> RentalFactorGroup:
    row = (
        scoped(db.query(RentalFactorGroup), membership)
        .filter(RentalFactorGroup.id == group_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Factor group not found"
        )
    return row


# ─── Responses ──────────────────────────────────────────────────────────────


def _category_response(
    db: Session,
    membership: Membership,
    category: ItemCategory,
    *,
    counts: Optional[dict] = None,
) -> ItemCategoryResponse:
    response = ItemCategoryResponse.model_validate(category)
    response.path = catalog_service.category_path(db, category)
    response.depth = len(catalog_service.ancestors(db, category)) + 1
    if counts is None:
        response.item_count = (
            scoped(db.query(func.count(Item.id)), membership)
            .filter(Item.category_id == category.id)
            .scalar()
            or 0
        )
    else:
        response.item_count = counts.get(category.id, 0)
    return response


def _item_response(
    db: Session, membership: Membership, item: Item
) -> ItemResponse:
    response = ItemResponse.model_validate(item)
    if item.category_id is not None:
        category = (
            scoped(db.query(ItemCategory), membership)
            .filter(ItemCategory.id == item.category_id)
            .first()
        )
        if category is not None:
            response.category_path = catalog_service.category_path(db, category)
    if item.unit_id is not None:
        unit = (
            scoped(db.query(Unit), membership).filter(Unit.id == item.unit_id).first()
        )
        if unit is not None:
            response.unit_key = unit.key
    if item.rental_factor_group_id is not None:
        group = (
            scoped(db.query(RentalFactorGroup), membership)
            .filter(RentalFactorGroup.id == item.rental_factor_group_id)
            .first()
        )
        if group is not None:
            response.rental_factor_group_name = group.name
    response.rates = [
        ItemRateResponse.model_validate(rate)
        for rate in sorted(item.rates, key=lambda row: (row.rate_type, row.period_unit))
    ]
    return response


def _factor_group_response(group: RentalFactorGroup) -> RentalFactorGroupResponse:
    response = RentalFactorGroupResponse.model_validate(group)
    response.factors = [
        RentalFactorResponse.model_validate(row)
        for row in sorted(group.factors, key=lambda entry: entry.days)
    ]
    return response


def _plan_rows(plan: ImportPlan) -> list[ImportRowResponse]:
    rows: list[ImportRowResponse] = []
    for row in plan.rows:
        values = row.values
        rows.append(
            ImportRowResponse(
                line_number=row.line_number,
                action=row.action,
                message=row.message,
                # From the ROW, not from `values`: an error row has no
                # `values` and still has to be identifiable in the preview
                # table. See `ImportRow.sku`.
                sku=row.sku,
                name_de=row.name,
                kind=values.kind if values else None,
                category=values.category if values else None,
                unit=values.unit if values else None,
                bill_rate_day=values.bill_rate_day if values else None,
                cost_rate_day=values.cost_rate_day if values else None,
                tax_class=values.tax_class if values else None,
                active=values.active if values else None,
                item_id=uuid.UUID(row.item_id) if row.item_id else None,
            )
        )
    return rows


# ─── Units ──────────────────────────────────────────────────────────────────


@router.get("/catalog/units", response_model=list[UnitResponse])
def list_units(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    query = scoped(db.query(Unit), membership)
    if not include_inactive:
        query = query.filter(Unit.is_active.is_(True))
    rows = query.order_by(Unit.position, Unit.key).all()
    return [UnitResponse.model_validate(row) for row in rows]


@router.post(
    "/catalog/units", response_model=UnitResponse, status_code=status.HTTP_201_CREATED
)
def create_unit(
    body: UnitCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """A unit of the company's own. The nine seeded ones are already there."""
    key = body.key.strip().lower()
    clash = scoped(db.query(Unit), membership).filter(Unit.key == key).first()
    if clash is not None:
        _refuse(
            ValueError(
                f"there is already a unit with the key {key!r} "
                f"({clash.label_de}). Edit that one, or pick another key."
            )
        )
    unit = Unit(
        company_id=membership.company_id,
        key=key,
        label_de=body.label_de,
        label_en=body.label_en,
        period_unit=body.period_unit,
        is_system=False,
        position=body.position if body.position is not None else 100,
        is_active=True if body.is_active is None else body.is_active,
    )
    db.add(unit)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        unit,
        UNIT_CREATED,
        after=audit.snapshot(unit, UNIT_AUDITED),
    )
    db.commit()
    db.refresh(unit)
    return UnitResponse.model_validate(unit)


@router.patch("/catalog/units/{unit_id}", response_model=UnitResponse)
def update_unit(
    unit_id: uuid.UUID,
    body: UnitWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Relabel a unit, reorder it, or switch it off.

    A system unit's `key` and `period_unit` are fixed — the engine's period
    vocabulary is built on them — but its LABELS are not: a company that
    says "Drehtag" rather than "Tag" may say so.
    """
    unit = _unit_or_404(db, membership, unit_id)
    before = audit.snapshot(unit, UNIT_AUDITED)
    unit.label_de = body.label_de
    unit.label_en = body.label_en
    if body.position is not None:
        unit.position = body.position
    if body.is_active is not None:
        if not body.is_active:
            in_use = (
                scoped(db.query(func.count(Item.id)), membership)
                .filter(Item.unit_id == unit.id, Item.is_active.is_(True))
                .scalar()
                or 0
            )
            if in_use:
                _refuse(
                    ValueError(
                        f"{unit.label_de} is the unit of {in_use} active "
                        f"item(s). Move them to another unit first — an item "
                        f"whose unit is switched off has no period to price."
                    )
                )
        unit.is_active = body.is_active
    audit.record(
        db,
        _actor(request, membership),
        unit,
        UNIT_UPDATED,
        before,
        audit.snapshot(unit, UNIT_AUDITED),
        fields=UNIT_AUDITED,
    )
    db.commit()
    db.refresh(unit)
    return UnitResponse.model_validate(unit)


# ─── Categories ─────────────────────────────────────────────────────────────


@router.get("/catalog/categories", response_model=list[ItemCategoryResponse])
def list_categories(
    include_inactive: bool = False,
    kind: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    """The whole tree, flat, each row carrying its path and its depth.

    Flat and not nested: the screen renders a tree from `parent_id` either
    way, and a flat list is one response shape instead of a recursive one
    that the generated types have to be taught about.
    """
    query = scoped(db.query(ItemCategory), membership)
    if not include_inactive:
        query = query.filter(ItemCategory.is_active.is_(True))
    if kind:
        query = query.filter(ItemCategory.kind == kind)
    rows = query.order_by(ItemCategory.kind, ItemCategory.position, ItemCategory.name).all()

    # One grouped count rather than one per row: a tree of forty categories
    # would otherwise be forty queries behind a screen nobody thinks of as
    # expensive.
    counts = dict(
        scoped(db.query(Item.category_id, func.count(Item.id)), membership)
        .group_by(Item.category_id)
        .all()
    )
    return [
        _category_response(db, membership, row, counts=counts) for row in rows
    ]


@router.post(
    "/catalog/categories",
    response_model=ItemCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    body: ItemCategoryCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    parent = None
    if body.parent_id is not None:
        parent = _category_or_404(db, membership, body.parent_id)
    try:
        catalog_service.check_parent(db, category=None, parent=parent)
    except CatalogError as error:
        _refuse(error)

    category = ItemCategory(
        company_id=membership.company_id,
        name=body.name.strip(),
        kind=body.kind,
        parent_id=parent.id if parent else None,
        position=body.position if body.position is not None else 100,
        is_active=True if body.is_active is None else body.is_active,
    )
    db.add(category)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        category,
        CATEGORY_CREATED,
        after=audit.snapshot(category, CATEGORY_AUDITED),
    )
    db.commit()
    db.refresh(category)
    return _category_response(db, membership, category)


@router.patch(
    "/catalog/categories/{category_id}", response_model=ItemCategoryResponse
)
def update_category(
    category_id: uuid.UUID,
    body: ItemCategoryWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Rename a category or move it. `kind` is fixed at create.

    Moving it is where the two tree rules are enforced: no cycle, and
    `MAX_CATEGORY_DEPTH` levels counting whatever is already underneath.
    """
    category = _category_or_404(db, membership, category_id)
    before = audit.snapshot(category, CATEGORY_AUDITED)

    parent = None
    if body.parent_id is not None:
        parent = _category_or_404(db, membership, body.parent_id)
    try:
        catalog_service.check_parent(db, category=category, parent=parent)
    except CatalogError as error:
        _refuse(error)

    category.name = body.name.strip()
    category.parent_id = parent.id if parent else None
    if body.position is not None:
        category.position = body.position
    if body.is_active is not None:
        category.is_active = body.is_active
    audit.record(
        db,
        _actor(request, membership),
        category,
        CATEGORY_UPDATED,
        before,
        audit.snapshot(category, CATEGORY_AUDITED),
        fields=CATEGORY_AUDITED,
    )
    db.commit()
    db.refresh(category)
    return _category_response(db, membership, category)


@router.post(
    "/catalog/categories/{category_id}/deactivate",
    response_model=ItemCategoryResponse,
)
def deactivate_category(
    category_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Retire a category. Never deletes; refused while it has live children."""
    category = _category_or_404(db, membership, category_id)
    live_children = (
        scoped(db.query(func.count(ItemCategory.id)), membership)
        .filter(
            ItemCategory.parent_id == category.id,
            ItemCategory.is_active.is_(True),
        )
        .scalar()
        or 0
    )
    if live_children:
        _refuse(
            ValueError(
                f"{category.name!r} still has {live_children} active "
                f"sub-categor{'y' if live_children == 1 else 'ies'}. Retire "
                f"those first — a sub-category under a retired parent would "
                f"disappear from the tree while its items kept pointing at "
                f"it."
            )
        )
    before = audit.snapshot(category, CATEGORY_AUDITED)
    category.is_active = False
    audit.record(
        db,
        _actor(request, membership),
        category,
        CATEGORY_DEACTIVATED,
        before,
        audit.snapshot(category, CATEGORY_AUDITED),
        fields=CATEGORY_AUDITED,
    )
    db.commit()
    db.refresh(category)
    return _category_response(db, membership, category)


@router.post(
    "/catalog/categories/{category_id}/reactivate",
    response_model=ItemCategoryResponse,
)
def reactivate_category(
    category_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    category = _category_or_404(db, membership, category_id)
    before = audit.snapshot(category, CATEGORY_AUDITED)
    category.is_active = True
    audit.record(
        db,
        _actor(request, membership),
        category,
        CATEGORY_REACTIVATED,
        before,
        audit.snapshot(category, CATEGORY_AUDITED),
        fields=CATEGORY_AUDITED,
    )
    db.commit()
    db.refresh(category)
    return _category_response(db, membership, category)


# ─── Rental factor tables ───────────────────────────────────────────────────


@router.get(
    "/catalog/rental-factor-groups", response_model=list[RentalFactorGroupResponse]
)
def list_factor_groups(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    query = scoped(db.query(RentalFactorGroup), membership)
    if not include_inactive:
        query = query.filter(RentalFactorGroup.is_active.is_(True))
    return [
        _factor_group_response(row)
        for row in query.order_by(RentalFactorGroup.name).all()
    ]


@router.post(
    "/catalog/rental-factor-groups",
    response_model=RentalFactorGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_factor_group(
    body: RentalFactorGroupWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    group = RentalFactorGroup(
        company_id=membership.company_id,
        name=body.name.strip(),
        notes=body.notes,
        is_active=True if body.is_active is None else body.is_active,
    )
    db.add(group)
    db.flush()
    audit.record(
        db,
        _actor(request, membership),
        group,
        FACTOR_GROUP_CREATED,
        after=audit.snapshot(group, FACTOR_GROUP_AUDITED),
    )
    db.commit()
    db.refresh(group)
    return _factor_group_response(group)


@router.patch(
    "/catalog/rental-factor-groups/{group_id}",
    response_model=RentalFactorGroupResponse,
)
def update_factor_group(
    group_id: uuid.UUID,
    body: RentalFactorGroupWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    group = _factor_group_or_404(db, membership, group_id)
    before = audit.snapshot(group, FACTOR_GROUP_AUDITED)
    group.name = body.name.strip()
    group.notes = body.notes
    if body.is_active is not None:
        group.is_active = body.is_active
    audit.record(
        db,
        _actor(request, membership),
        group,
        FACTOR_GROUP_UPDATED,
        before,
        audit.snapshot(group, FACTOR_GROUP_AUDITED),
        fields=FACTOR_GROUP_AUDITED,
    )
    db.commit()
    db.refresh(group)
    return _factor_group_response(group)


@router.put(
    "/catalog/rental-factor-groups/{group_id}/factors",
    response_model=RentalFactorGroupResponse,
)
def replace_factors(
    group_id: uuid.UUID,
    body: list[RentalFactorWrite],
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Replace the whole table. A `PUT`, because that is what it is.

    Per-row CRUD on a factor table would let somebody save a table that is
    momentarily wrong — 5 days at 2.8 while 7 days still says 2.0 — and the
    thing being edited is the table, not the rows.
    """
    group = _factor_group_or_404(db, membership, group_id)
    try:
        before, after = catalog_service.replace_factors(
            db, group=group, factors=[entry.model_dump() for entry in body]
        )
    except CatalogError as error:
        _unprocessable(str(error))
    if before != after:
        audit.record(
            db,
            _actor(request, membership),
            group,
            FACTOR_GROUP_FACTORS_CHANGED,
            before,
            after,
        )
    db.commit()
    db.refresh(group)
    return _factor_group_response(group)


@router.get(
    "/catalog/rental-factor-groups/{group_id}/factor",
    response_model=FactorLookupResponse,
)
def look_up_factor(
    group_id: uuid.UUID,
    days: int = Query(ge=1, le=3650, description="Rental days to price."),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    """What this table charges for `days` days, and which row it used.

    The row and the rule are in the response because the lookup rule is
    `[Guessing]` (SCOPE D71): "6 days → 2.8, from the 5-day entry" is a rule
    somebody can disagree with, and "2.8" is not.
    """
    group = _factor_group_or_404(db, membership, group_id)
    table = [(row.days, row.factor) for row in group.factors]
    try:
        factor, matched, rule = explain_factor(table, days)
    except FactorError as error:
        _unprocessable(str(error))
    return FactorLookupResponse(
        days=days,
        factor=factor,
        matched_days=matched,
        rule=rule,
        billable_days=billable_days(table, days),
    )


# ─── The CSV import ─────────────────────────────────────────────────────────
#
# Registered BEFORE `/catalog/items/{item_id}`: FastAPI matches routes in
# registration order, and `/catalog/items/import` would otherwise be read as
# an item id and refused as a malformed UUID.


async def _read_upload(file: UploadFile) -> bytes:
    payload = await file.read()
    if len(payload) > MAX_IMPORT_BYTES:
        _unprocessable(
            f"that file is {len(payload) // 1024} KB and the limit is "
            f"{MAX_IMPORT_BYTES // 1024} KB. A catalog import is a few "
            f"thousand rows; a file this size is usually the wrong file."
        )
    if not payload.strip():
        _unprocessable("that file is empty.")
    return payload


@router.post(
    "/catalog/items/import/preview", response_model=ImportPreviewResponse
)
async def preview_item_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """What the file would do. Writes nothing.

    `catalog.edit` and not `catalog.view`: a preview writes nothing, but it
    reports which SKUs exist and what they currently cost, and the person
    about to press Apply is the person who needs it.
    """
    payload = await _read_upload(file)
    try:
        plan = catalog_service.preview_import(db, membership, payload)
    except ImportFormatError as error:
        _unprocessable(str(error))
    return ImportPreviewResponse(
        rows=_plan_rows(plan),
        created=len(plan.creates),
        updated=len(plan.updates),
        errors=len(plan.errors),
        digest=plan.digest,
        delimiter=plan.delimiter,
        ignored_columns=list(plan.ignored_columns),
    )


@router.post("/catalog/items/import", response_model=ImportApplyResponse)
async def apply_item_import(
    request: Request,
    file: UploadFile = File(...),
    digest: Optional[str] = Form(
        default=None,
        description=(
            "The digest from the preview. When given and the file no longer "
            "plans the same thing, nothing is written."
        ),
    ),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Apply the file. Creates and updates; never deletes, never deactivates
    an item the file does not mention.

    Error rows are skipped rather than failing the import: a five-row file
    with one bad row lands the four good ones, and the response says which
    was which. The alternative — all or nothing — means one typo in row 40
    of a 200-row price list wastes the other 199.
    """
    payload = await _read_upload(file)
    try:
        plan = catalog_service.apply_import(
            db,
            membership,
            payload,
            actor=_actor(request, membership),
            expected_digest=digest,
            currency=_currency_of(db, membership),
        )
    except ImportFormatError as error:
        db.rollback()
        _unprocessable(str(error))
    except CatalogError as error:
        db.rollback()
        _refuse(error)
    db.commit()
    return ImportApplyResponse(
        rows=_plan_rows(plan),
        created=len(plan.creates),
        updated=len(plan.updates),
        errors=len(plan.errors),
        applied=len(plan.creates) + len(plan.updates),
        digest=plan.digest,
        delimiter=plan.delimiter,
        ignored_columns=list(plan.ignored_columns),
    )


# ─── Items ──────────────────────────────────────────────────────────────────


def _currency_of(db: Session, membership: Membership) -> str:
    """The company's default currency, for a rate that does not name one."""
    return getattr(membership.company, "default_currency", None) or "EUR"


def _resolve_item_references(
    db: Session,
    membership: Membership,
    body: ItemWrite,
) -> tuple[Optional[ItemCategory], Optional[Unit], Optional[RentalFactorGroup]]:
    """Check everything the item points at. Refusals name the value.

    Active rows only: pointing a new item at a retired category or a switched
    -off unit produces an item that cannot be priced and a screen that does
    not say why.
    """
    category = None
    if body.category_id is not None:
        category = _category_or_404(db, membership, body.category_id)
        if not category.is_active:
            _unprocessable(
                f"the category {category.name!r} is retired. Reactivate it, "
                f"or file this item somewhere live."
            )

    unit = None
    if body.unit_id is not None:
        unit = _unit_or_404(db, membership, body.unit_id)
        if not unit.is_active:
            _unprocessable(
                f"the unit {unit.label_de!r} is switched off. An item whose "
                f"unit is off has no period to be priced per."
            )

    group = None
    if body.rental_factor_group_id is not None:
        group = _factor_group_or_404(db, membership, body.rental_factor_group_id)
        if not group.is_active:
            _unprocessable(
                f"the factor table {group.name!r} is retired. Pick another, "
                f"or reactivate it."
            )

    if body.default_tax_class:
        known = (
            scoped(db.query(TaxClass), membership)
            .filter(
                TaxClass.key == body.default_tax_class,
                TaxClass.is_active.is_(True),
            )
            .first()
        )
        if known is None:
            _unprocessable(
                f"{body.default_tax_class!r} is not one of this company's "
                f"active tax classes. Settings → Tax lists them; an item "
                f"whose class does not resolve cannot be priced on a "
                f"document."
            )

    if body.rebilled_cost and body.kind != REBILLED_COST_KIND:
        _unprocessable(
            f"`rebilled_cost` only means something on a "
            f"{REBILLED_COST_KIND!r} item (SCOPE §4.1) — it marks a cost "
            f"passed through rather than a service sold. This item is "
            f"{body.kind!r}."
        )

    return category, unit, group


@router.get("/catalog/items", response_model=ItemPage)
def list_items(
    search: Optional[str] = Query(default=None, max_length=255),
    kind: Optional[str] = Query(default=None),
    category_id: Optional[uuid.UUID] = Query(default=None),
    rental_factor_group_id: Optional[uuid.UUID] = Query(default=None),
    include_inactive: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    """Search by SKU or either name; filter by kind, category or factor table.

    One `search` over three columns rather than three filters: somebody
    looking for a camera types "Alexa" and does not know whether it was
    filed under the German or the English name.
    """
    query = scoped(db.query(Item), membership)
    if not include_inactive:
        query = query.filter(Item.is_active.is_(True))
    if kind:
        query = query.filter(Item.kind == kind)
    if category_id is not None:
        query = query.filter(Item.category_id == category_id)
    if rental_factor_group_id is not None:
        query = query.filter(Item.rental_factor_group_id == rental_factor_group_id)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Item.sku).like(needle),
                func.lower(Item.name_de).like(needle),
                func.lower(Item.name_en).like(needle),
            )
        )

    total = query.count()
    rows = query.order_by(Item.kind, Item.name_de).limit(limit).offset(offset).all()
    return ItemPage(
        items=[_item_response(db, membership, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/catalog/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED
)
def create_item(
    body: ItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    category, unit, group = _resolve_item_references(db, membership, body)
    if body.sku:
        clash = scoped(db.query(Item), membership).filter(Item.sku == body.sku).first()
        if clash is not None:
            _refuse(
                ValueError(
                    f"the SKU {body.sku!r} already belongs to "
                    f"{clash.name_de!r}. An SKU identifies one item."
                )
            )

    item = Item(
        company_id=membership.company_id,
        sku=body.sku,
        name_de=body.name_de.strip(),
        name_en=(body.name_en or "").strip() or None,
        kind=body.kind,
        category_id=category.id if category else None,
        description_internal=body.description_internal,
        description_customer=body.description_customer,
        unit_id=unit.id if unit else None,
        default_tax_class=body.default_tax_class,
        rental_factor_group_id=group.id if group else None,
        revenue_account=body.revenue_account,
        expense_account=body.expense_account,
        rebilled_cost=body.rebilled_cost,
        notes=body.notes,
        is_active=True if body.is_active is None else body.is_active,
    )
    db.add(item)
    db.flush()
    if body.rates is not None:
        try:
            catalog_service.replace_rates(
                db,
                item=item,
                rates=[rate.model_dump() for rate in body.rates],
                currency=_currency_of(db, membership),
            )
        except CatalogError as error:
            db.rollback()
            _unprocessable(str(error))
    audit.record(
        db,
        _actor(request, membership),
        item,
        ITEM_CREATED,
        after=audit.snapshot(item, ITEM_AUDITED),
    )
    db.commit()
    db.refresh(item)
    return _item_response(db, membership, item)


@router.get("/catalog/items/{item_id}", response_model=ItemResponse)
def read_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.view")),
):
    return _item_response(db, membership, _item_or_404(db, membership, item_id))


@router.patch("/catalog/items/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: uuid.UUID,
    body: ItemWrite,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    item = _item_or_404(db, membership, item_id)
    category, unit, group = _resolve_item_references(db, membership, body)
    if body.sku:
        clash = (
            scoped(db.query(Item), membership)
            .filter(Item.sku == body.sku, Item.id != item.id)
            .first()
        )
        if clash is not None:
            _refuse(
                ValueError(
                    f"the SKU {body.sku!r} already belongs to "
                    f"{clash.name_de!r}. An SKU identifies one item."
                )
            )

    before = audit.snapshot(item, ITEM_AUDITED)
    item.sku = body.sku
    item.name_de = body.name_de.strip()
    item.name_en = (body.name_en or "").strip() or None
    item.kind = body.kind
    item.category_id = category.id if category else None
    item.description_internal = body.description_internal
    item.description_customer = body.description_customer
    item.unit_id = unit.id if unit else None
    item.default_tax_class = body.default_tax_class
    item.rental_factor_group_id = group.id if group else None
    item.revenue_account = body.revenue_account
    item.expense_account = body.expense_account
    item.rebilled_cost = body.rebilled_cost
    item.notes = body.notes
    if body.is_active is not None:
        item.is_active = body.is_active

    rate_before = rate_after = None
    if body.rates is not None:
        try:
            rate_before, rate_after = catalog_service.replace_rates(
                db,
                item=item,
                rates=[rate.model_dump() for rate in body.rates],
                currency=_currency_of(db, membership),
            )
        except CatalogError as error:
            db.rollback()
            _unprocessable(str(error))

    audit.record(
        db,
        _actor(request, membership),
        item,
        ITEM_UPDATED,
        before,
        audit.snapshot(item, ITEM_AUDITED),
        fields=ITEM_AUDITED,
    )
    if rate_after is not None and rate_before != rate_after:
        audit.record(
            db,
            _actor(request, membership),
            item,
            ITEM_RATES_CHANGED,
            rate_before,
            rate_after,
        )
    db.commit()
    db.refresh(item)
    return _item_response(db, membership, item)


@router.put(
    "/catalog/items/{item_id}/rates", response_model=list[ItemRateResponse]
)
def replace_item_rates(
    item_id: uuid.UUID,
    body: list[ItemRateWrite],
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Replace an item's whole rate set. The one sub-resource items have.

    A `PUT` with the full set for the same reason the factor table is one:
    the thing being edited is "what this item costs", and a half-saved
    version of that is a price nobody agreed to.
    """
    item = _item_or_404(db, membership, item_id)
    try:
        before, after = catalog_service.replace_rates(
            db,
            item=item,
            rates=[rate.model_dump() for rate in body],
            currency=_currency_of(db, membership),
        )
    except CatalogError as error:
        db.rollback()
        _unprocessable(str(error))
    if before != after:
        audit.record(
            db,
            _actor(request, membership),
            item,
            ITEM_RATES_CHANGED,
            before,
            after,
        )
    db.commit()
    db.refresh(item)
    return [
        ItemRateResponse.model_validate(rate)
        for rate in sorted(item.rates, key=lambda row: (row.rate_type, row.period_unit))
    ]


@router.post("/catalog/items/{item_id}/deactivate", response_model=ItemResponse)
def deactivate_item(
    item_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    """Retire an item. Never deletes — it may be on a document already."""
    item = _item_or_404(db, membership, item_id)
    before = audit.snapshot(item, ITEM_AUDITED)
    item.is_active = False
    audit.record(
        db,
        _actor(request, membership),
        item,
        ITEM_DEACTIVATED,
        before,
        audit.snapshot(item, ITEM_AUDITED),
        fields=ITEM_AUDITED,
    )
    db.commit()
    db.refresh(item)
    return _item_response(db, membership, item)


@router.post("/catalog/items/{item_id}/reactivate", response_model=ItemResponse)
def reactivate_item(
    item_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    membership: Membership = Depends(require("catalog.edit")),
):
    item = _item_or_404(db, membership, item_id)
    before = audit.snapshot(item, ITEM_AUDITED)
    item.is_active = True
    audit.record(
        db,
        _actor(request, membership),
        item,
        ITEM_REACTIVATED,
        before,
        audit.snapshot(item, ITEM_AUDITED),
        fields=ITEM_AUDITED,
    )
    db.commit()
    db.refresh(item)
    return _item_response(db, membership, item)
