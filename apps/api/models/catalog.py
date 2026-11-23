"""The catalog: units, categories, items, rates and rental factor tables.

SCOPE §4.1–4.3, the LEAN half of it (slice A1). Devices and their physical
units (§4.4), kits, associated items, availability and price lists are
deliberately not here — see SCOPE §16's slice-A decision. What is here is
what a budget and a quote need in order to have something to put on a line.

**`kind` is a fixed system enum, `category` is a user label.** The two are
separate columns on purpose and the distinction is SCOPE §4.1's: a kind
drives behaviour (what a line reports as, whether `rebilled_cost` means
anything, which report it feeds), and a category is whatever a company calls
a drawer of its gear. A user can make a hundred categories and cannot make a
kind. The enum lives in `billing/catalog/kinds.py`, in the pure package,
because the CSV importer has to name the valid kinds in an error message
before anything touches a table.

**Nothing is ever deleted.** No table here has a DELETE route and there is no
cascade that would remove an item: an item that has appeared on an issued
document is part of that document's explanation (D51(d), and the same
reasoning as parties and tax codes). `is_active` retires a row and keeps it
explaining its history. The foreign keys from `items` to `item_categories`,
`units` and `rental_factor_groups` are therefore `RESTRICT`, not `CASCADE` —
retiring a category must not be able to take forty items with it.

**`items.default_tax_class` holds a KEY, not an id.** `tax_classes` is keyed
by `(company_id, key)` and `tax_rules` already name classes by key
(`{class: "standard"}` — see `billing/tax/vocabulary`), so an item naming one
the same way keeps one spelling of "which class" in the system. The cost is
that the reference registry has to look the class up by key to refuse
deactivating one an item uses; that is one query in
`services/tax_references.py` and it is where the guard belongs anyway.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from ..database import Base
except ImportError:  # pragma: no cover - direct-import path for alembic
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped

try:
    from ..core.money import AmountColumn, QtyColumn
except ImportError:  # pragma: no cover
    from core.money import AmountColumn, QtyColumn


#: SCOPE §4.2's two rate kinds. A `bill` rate is what the customer pays and a
#: `cost` rate is what it costs the company — both per item per period, and
#: the pair is what makes margin a number the engine can report (§4.1's
#: "has **cost rate** vs **bill rate**").
RATE_BILL = "bill"
RATE_COST = "cost"
RATE_TYPES: tuple[str, ...] = (RATE_BILL, RATE_COST)

#: Three levels: Equipment › Camera › Cinema. The same depth SCOPE §4.4.1
#: gives device categories, and the same reasoning as the party tree: an
#: unbounded hierarchy makes "which category does this report group by" a
#: question with no bounded answer. Enforced in
#: `services/catalog_service.py`, which is the only thing that can see a
#: whole chain.
MAX_CATEGORY_DEPTH = 3


class Unit(CompanyScoped, Base):
    """What a rate is per: a day, an hour, a kilometre — or "Drehtag".

    **Every unit maps onto one of the engine's period units.** A company may
    invent a unit (SCOPE §4.1 says so) and the invented one still has to say
    which of `billing/calc/vocabulary.PERIOD_UNITS` it behaves as, because
    `period_unit` is what the engine prices with, what a document prints and
    what an e-invoice exports. "Drehtag" mapping to `day` is a label a
    company prefers; a unit mapping to nothing would be a line the engine
    cannot price at all.

    `is_system` marks the nine every company is seeded with. It is not a
    permission — an owner can rename or retire a system unit, because
    "Pauschal" may well not be the word this company uses — it is what the UI
    uses to explain why a row was there before anybody added anything.
    """

    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Stable, lower-case, referenced by the CSV import's `unit` column.
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    label_de: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Nullable, like `items.name_en`: a company inventing "Drehtag" need
    #: not also think of an English word for it, and a NOT NULL here would
    #: make them type one or be refused with nothing useful to say.
    label_en: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: One of `billing/calc/vocabulary.PERIOD_UNITS`. Validated in the
    #: schema and in the service, not by a database enum: the engine's list
    #: is the authority and a Postgres enum would need a migration to follow
    #: it.
    period_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # One `day` per company. Two would make the CSV import's `unit`
        # column ambiguous and the picker show the same word twice.
        UniqueConstraint("company_id", "key", name="uq_units_company_key"),
        Index("ix_units_company_active", "company_id", "is_active"),
    )


class ItemCategory(CompanyScoped, Base):
    """A user label, hung on a kind, up to three levels deep.

    `kind` is on the category as well as on the item, and that is not
    duplication: a category is created FOR a kind ("Drones" is equipment),
    and the item form uses it to offer the right kind by default. The item's
    own `kind` is what counts — an item may sit in a category of a different
    kind, which is unusual rather than forbidden, and forbidding it would
    make re-categorising an item a two-step dance.

    Category names are NOT unique. "Camera" under Equipment and "Camera"
    under Post are both reasonable, and two at the same level are a nuisance
    a person can see and fix rather than a correctness problem — so there is
    no constraint here pretending otherwise.
    """

    __tablename__ = "item_categories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: One of `billing/catalog/kinds.ITEM_KINDS`.
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: `RESTRICT`: a category with children cannot be removed, and nothing
    #: here removes anything anyway.
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("item_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    children = relationship(
        "ItemCategory",
        backref="parent",
        remote_side=[id],
        uselist=True,
        viewonly=True,
    )

    __table_args__ = (
        Index("ix_item_categories_company_parent", "company_id", "parent_id"),
        Index("ix_item_categories_company_kind", "company_id", "kind"),
    )


class RentalFactorGroup(CompanyScoped, Base):
    """A named `{days → factor}` table, shared by many items (SCOPE §4.3).

    A group rather than a table per item because one company has one or two
    of these ("Standard", "Langzeit") and forty cameras on each. The lookup
    rule is in `billing/catalog/factors.py` and is `[Guessing]` — an open
    question for Mathias, recorded in SCOPE §16 and §17.
    """

    __tablename__ = "rental_factor_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    factors = relationship(
        "RentalFactor",
        back_populates="group",
        order_by="RentalFactor.days",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_rental_factor_groups_name"),
    )


class RentalFactor(CompanyScoped, Base):
    """One row of a factor table: N days are billed as `factor` days.

    `CASCADE` from the group, and it is the one cascade in this file. A
    factor row is not a thing in its own right — it has no meaning apart
    from its table and no document ever snapshots one — so a group and its
    rows live and die together. What does not happen is a group being
    deleted: there is no DELETE route for one either.
    """

    __tablename__ = "rental_factors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rental_factor_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `NUMERIC(18,4)`: 2.8 days, and a factor carried to four places is the
    #: scale CLAUDE.md rule 1 gives quantities and rates.
    factor: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)

    group = relationship("RentalFactorGroup", back_populates="factors")

    __table_args__ = (
        UniqueConstraint("group_id", "days", name="uq_rental_factors_group_days"),
    )


class Item(CompanyScoped, Base):
    """Anything that can appear on a budget or a quote line (SCOPE §4.2).

    Crew, gear, a studio, a taxi, a production fee. **A line does not need
    one** — free-text lines are normal in a budget, and the engine takes a
    price rather than an item id — so every reference to an item from a
    document is a convenience and a snapshot source, never a requirement.

    `revenue_account` and `expense_account` are plain strings and not foreign
    keys, because the chart of accounts arrives in P6. A string now means an
    accountant can write `4000` into the field today and P6 can validate it
    against real accounts later; an FK now would mean the field cannot be
    used until P6 ships.
    """

    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Optional, and unique per company WHEN SET — a partial index, because
    #: most items in a production company's catalog never get an article
    #: number and a NOT NULL unique would force forty empty strings to be
    #: forty different empty strings.
    sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name_de: Mapped[str] = mapped_column(String(255), nullable=False)
    name_en: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: One of `billing/catalog/kinds.ITEM_KINDS`. Drives behaviour; not
    #: user-extensible.
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("item_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )
    #: Two descriptions, because they are read by different people: the
    #: internal one explains the item to whoever is building the budget, and
    #: the customer-facing one is what goes on the document.
    description_internal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_customer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="RESTRICT"),
        nullable=True,
    )
    #: A `tax_classes.key`, not an id. See the module docstring.
    default_tax_class: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    rental_factor_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rental_factor_groups.id", ondelete="RESTRICT"),
        nullable=True,
    )
    revenue_account: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    expense_account: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: SCOPE §4.1's travel flag: is this a cost passed on, or a service sold?
    #: It changes the tax treatment (§12 S12) and only means anything on a
    #: `travel` item — the API refuses to set it on anything else rather than
    #: storing a flag nothing reads.
    rebilled_cost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    rates = relationship(
        "ItemRate",
        back_populates="item",
        order_by="ItemRate.rate_type, ItemRate.period_unit",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Unique per company WHEN SET. A partial index rather than a plain
        # UNIQUE: most items never get an article number, and under a plain
        # unique constraint Postgres would treat every NULL as distinct —
        # which happens to be the behaviour we want, but relies on a detail
        # of NULL semantics rather than saying so. `WHERE sku IS NOT NULL`
        # states the rule, and keeps the index off the rows that have no
        # SKU to index.
        Index(
            "uq_items_company_sku",
            "company_id",
            "sku",
            unique=True,
            postgresql_where=text("sku IS NOT NULL"),
        ),
        Index("ix_items_company_active", "company_id", "is_active"),
        Index("ix_items_company_kind", "company_id", "kind"),
        Index("ix_items_company_category", "company_id", "category_id"),
    )


class ItemRate(CompanyScoped, Base):
    """What one item costs, or is billed at, per one period unit.

    `(item, rate_type, period_unit)` is unique: an item has at most one bill
    rate per day and at most one cost rate per day. A second one would make
    "the day rate" a question with two answers and whichever the query
    happened to return would be the price on the quote.

    **Price lists and customer-specific rates are not here** (SCOPE §4.2
    mentions them; slice A1 does not build them). When they arrive they add a
    `price_list_id` to this table and the unique constraint grows a column —
    which is why `currency` is already a column rather than assumed: the
    company currency is the only one slice A1 writes, and a rate that did not
    say which currency it was in would be unmigratable later.
    """

    __tablename__ = "item_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rate_type: Mapped[str] = mapped_column(String(8), nullable=False)
    period_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `NUMERIC(18,2)` — money (CLAUDE.md rule 1).
    amount: Mapped[Any] = mapped_column(AmountColumn(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    item = relationship("Item", back_populates="rates")

    __table_args__ = (
        UniqueConstraint(
            "item_id", "rate_type", "period_unit", name="uq_item_rates_item_type_period"
        ),
    )
