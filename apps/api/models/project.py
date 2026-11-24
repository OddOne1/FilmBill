"""Projects and their production days (SCOPE §8.2, §11.3).

Slice A2's first half. A project is the thing a budget, and later a quote, an
invoice, a bill and a time entry all hang off; a production day is the unit a
call sheet, a booking and an overtime calculation will hang off (§11.3). Both
are built now because retro-fitting a day once call sheets exist is not cheap
and adding it now is.

**Nothing here is ever deleted.** A project is retired by `status = archived`;
a day is retired by `is_active = False`. There is no DELETE route in
`routers/projects.py` and a test asserts the absence on the OpenAPI document
rather than on a handler — the same shape as the catalog's and parties'.

**A day is a PLAN, which is why it can be removed at all.** A shoot day that
moves is not a historical record being rewritten, it is a plan being changed,
and the Days tab has to be able to take a day out. It is still never deleted:
`is_active = False` keeps the row, the audit event says who took it out, and
the partial unique index below is what lets the same date and type be added
again afterwards.

**`client_party_id` is RESTRICT and nullable.** Nullable because an internal
production (a showreel, a pitch) has no client and refusing to create one
would make the field a lie; RESTRICT because a party that a project points at
is part of that project's explanation — and the archive guard in
`services/project_service.py` refuses to archive such a party in the first
place, which is the version of this rule a user ever sees.
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

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


#: SCOPE §8.1's shape, reduced to what A2 needs. `planning` is where a project
#: starts (a pitch with a budget and no dates confirmed), `active` is in
#: production, `wrapped` is shot and still being billed, `archived` is the
#: retirement state that stands in for a delete.
PROJECT_STATUSES: tuple[str, ...] = ("planning", "active", "wrapped", "archived")

#: SCOPE §11.3's list, exactly. `off` is in it because a day off inside a
#: shooting block is a fact the schedule needs — it is not the absence of a
#: day, and leaving it out would make "5 shoot days with a Sunday in the
#: middle" unrepresentable.
PROJECT_DAY_TYPES: tuple[str, ...] = (
    "prep",
    "shoot",
    "travel",
    "wrap",
    "post",
    "office",
    "off",
)

#: The number series a project number comes from. Not a document type — a
#: project is not a document — and it uses the same machinery for the same
#: reason customer and supplier numbers do (P1b): one promise about numbers,
#: one place it can be broken.
PROJECT_SERIES_TYPE = "project"

#: Two levels of section, matching the engine's group limit (SCOPE §5.4).
#: Stated here as well as enforced in the service because the service's
#: refusal sentence quotes it.
MAX_SECTION_DEPTH = 2

#: What a budget line's `factor` came from. `catalog` means the factor is
#: looked up from the item's rental factor group for the line's own period
#: and is re-resolved on every calculation; `manual` means somebody typed it
#: and nothing may overwrite it.
FACTOR_SOURCES: tuple[str, ...] = ("manual", "catalog")

#: Where a global's value comes from. `manual` is typed; `days:<type>` is
#: COUNTED from the project's active production days of that type and is
#: read-only. A closed vocabulary on purpose (D74): the alternative is an
#: expression language over user input, which is a second programming
#: language inside a budget and the thing D70 refuses.
GLOBAL_SOURCE_MANUAL = "manual"
GLOBAL_SOURCE_DAYS_PREFIX = "days:"


def days_source_for(day_type: str) -> str:
    """`"shoot"` → `"days:shoot"`. The only place the prefix is spelled."""
    return f"{GLOBAL_SOURCE_DAYS_PREFIX}{day_type}"


def day_type_of_source(source: str) -> Optional[str]:
    """`"days:shoot"` → `"shoot"`; anything else → None."""
    if not source or not source.startswith(GLOBAL_SOURCE_DAYS_PREFIX):
        return None
    return source[len(GLOBAL_SOURCE_DAYS_PREFIX) :]


class Project(CompanyScoped, Base):
    """One production (SCOPE §8.2).

    `project_number` is allocated from the `project` series inside the
    creating transaction (CLAUDE.md rule 5) and is NOT NULL: a project with
    no number is a row nobody can refer to in a conversation, and "the
    number arrives later" is how FilmBill v1 ended up with two numbering
    schemes.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_number: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_party_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: One of `PROJECT_STATUSES`. A `String(16)` and not a database enum, for
    #: the reason migration 0014 gives: the authority is the Python
    #: vocabulary and an enum type costs a migration to extend.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="planning"
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Nullable: an open-ended retainer or a project whose wrap is not booked
    #: yet is a normal thing to plan.
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    days = relationship(
        "ProjectDay",
        back_populates="project",
        order_by="ProjectDay.date, ProjectDay.type",
    )
    budgets = relationship("Budget", back_populates="project")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "project_number", name="uq_projects_company_number"
        ),
        Index("ix_projects_company_status", "company_id", "status"),
        Index("ix_projects_company_client", "company_id", "client_party_id"),
    )


class ProjectDay(CompanyScoped, Base):
    """One production day (SCOPE §11.3).

    `(project_id, date, type)` is unique **among active days**, as a partial
    index. A plain unique constraint would make a removed day block its own
    re-creation for ever, which is the opposite of what "a day is a plan"
    means; the partial index keeps "one shoot day per date" true of the plan
    as it stands and lets the row that was taken out stay readable.
    """

    __tablename__ = "project_days"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: One of `PROJECT_DAY_TYPES`.
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: Times rather than timestamps: a call time is a wall-clock time on that
    #: day, and storing it as an instant would mean choosing a timezone for a
    #: field whose whole meaning is "be there when the sheet says".
    call_time: Mapped[Optional[Any]] = mapped_column(Time, nullable=True)
    wrap_time: Mapped[Optional[Any]] = mapped_column(Time, nullable=True)
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

    project = relationship("Project", back_populates="days")

    __table_args__ = (
        Index(
            "uq_project_days_active",
            "project_id",
            "date",
            "type",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("ix_project_days_company_date", "company_id", "date"),
    )


class Budget(CompanyScoped, Base):
    """A cost-and-price sheet for a project. Draft-only, for ever (D72).

    No number, no finalize, no PDF, no ledger posting, no revisions. What it
    has instead is `locked_at`: an approved estimate has to stop moving, and
    until Committed and Actual exist (slice B) a lock is the whole of that.
    Unlocking is an ordinary audited action and not an escape hatch — the
    point is that a locked budget cannot be edited by accident, not that it
    cannot be edited.

    Several budgets per project are normal (scenarios: "with the crane",
    "without"), and `is_primary` marks the one the project screen shows
    first.
    """

    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The company currency, always — A2 writes no other. A column rather
    #: than an assumption for the reason `item_rates.currency` is one: a
    #: budget that did not say which currency it was in would be
    #: unmigratable the day multi-currency lands.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    project = relationship("Project", back_populates="budgets")
    sections = relationship(
        "BudgetSection", back_populates="budget", order_by="BudgetSection.position"
    )
    globals_ = relationship(
        "BudgetGlobal", back_populates="budget", order_by="BudgetGlobal.key"
    )
    adjustments = relationship(
        "BudgetAdjustment",
        back_populates="budget",
        order_by="BudgetAdjustment.position",
    )

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None

    __table_args__ = (Index("ix_budgets_company_project", "company_id", "project_id"),)


class BudgetSection(CompanyScoped, Base):
    """A block of lines with an account code and a subtotal.

    `code` is the user-visible account number (`1100`), free text rather than
    a foreign key for the reason `items.revenue_account` is: the chart of
    accounts arrives in P6, and a string now means an accountant can write
    `1100` today and P6 can validate it against real accounts later.

    **Two levels, no more** (`MAX_SECTION_DEPTH`), because the engine nests
    groups two deep (SCOPE §5.4) and a third level could be stored and not
    calculated. The refusal names the engine's limit.
    """

    __tablename__ = "budget_sections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budget_sections.id", ondelete="RESTRICT"),
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

    budget = relationship("Budget", back_populates="sections")
    lines = relationship(
        "BudgetLine", back_populates="section", order_by="BudgetLine.position"
    )

    __table_args__ = (
        Index("ix_budget_sections_budget_parent", "budget_id", "parent_id"),
    )


class BudgetLine(CompanyScoped, Base):
    """One priced row of a budget.

    **The rates are COPIED from the catalog when the line is inserted** and
    stay editable. `item_id` is kept so the API can say "this differs from
    the catalog now", and nothing ever writes the catalog's current rate back
    over the line: a budget that silently re-priced itself when somebody
    edited the price list would be a budget nobody could trust to still say
    what it said last week.

    `quantity_ref` and `period_ref` name a `budget_globals.key`. They are
    resolved to plain numbers in the service **before** the engine is called
    (D70) — the engine never sees a name and has no hook through which it
    could evaluate one.
    """

    __tablename__ = "budget_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budget_sections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    #: One of `billing/catalog/kinds.ITEM_KINDS`, taken from the item when
    #: there is one. Opaque to the engine, which only matches it as a string
    #: for a kind-filtered adjustment.
    kind: Mapped[str] = mapped_column(String(24), nullable=False, server_default="other")
    quantity: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    quantity_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    period_value: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    period_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    period_unit: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="day"
    )
    factor: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    #: `manual` or `catalog` (`FACTOR_SOURCES`).
    factor_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    unit_price: Mapped[Any] = mapped_column(AmountColumn(), nullable=False)
    unit_cost: Mapped[Any] = mapped_column(AmountColumn(), nullable=False)
    #: `percent`, `amount` or NULL for no discount. Both columns move
    #: together and the service refuses one without the other.
    discount_mode: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    discount_value: Mapped[Optional[Any]] = mapped_column(QtyColumn(), nullable=True)
    #: The tax category this line's net belongs in, for GROUPING ONLY — a
    #: budget computes no VAT (D72). Taken from the item's default tax class
    #: when there is one.
    tax_ref: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    tags: Mapped[Optional[list]] = mapped_column(
        ARRAY(String(64)).with_variant(JSON, "sqlite"),
        nullable=False,
        server_default="{}",
    )
    allow_negative: Mapped[bool] = mapped_column(
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

    section = relationship("BudgetSection", back_populates="lines")

    __table_args__ = (
        Index("ix_budget_lines_company_item", "company_id", "item_id"),
    )


class BudgetGlobal(CompanyScoped, Base):
    """A named number a line can point at — "shoot days", "crew size".

    A **closed vocabulary**, not an expression language (D74). `value` is a
    number; `source` says whether somebody typed it (`manual`) or it is
    counted from the project's days (`days:<type>`). There is no formula
    field and no place one could be added without changing this table, which
    is the point.
    """

    __tablename__ = "budget_globals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: `manual` or `days:<type>`.
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=GLOBAL_SOURCE_MANUAL
    )
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

    budget = relationship("Budget", back_populates="globals_")

    __table_args__ = (
        UniqueConstraint("budget_id", "key", name="uq_budget_globals_budget_key"),
    )


class BudgetAdjustment(CompanyScoped, Base):
    """A discount or surcharge on a section, or on the whole budget.

    The columns are the engine's adjustment contract (`billing/calc/model.py`
    `Adjustment`) and nothing more: `type` carries the sign, `value` is
    always positive, `base` says what a percentage is OF, and `kinds` is the
    filter `base = "kinds"` needs. `section_id` NULL means document level.

    **Adjustments move the price and never the cost** — that is the engine's
    behaviour, not a choice made here (see `_apply_adjustments`). A
    contingency or a fee that has to raise the COST side is modelled as an
    ordinary line of kind `fee` instead (D78).
    """

    __tablename__ = "budget_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budget_sections.id", ondelete="RESTRICT"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `discount` or `surcharge`.
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: `percent` or `amount`.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    #: `subtotal`, `subtotal_incl_previous` or `kinds`.
    base: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="subtotal"
    )
    #: Only meaningful for `base = "kinds"`. JSONB rather than an array for
    #: no better reason than that it is a short list the engine reads as a
    #: tuple of strings; a `text[]` would do as well.
    kinds: Mapped[Optional[list]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"), nullable=False, server_default="[]"
    )
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

    budget = relationship("Budget", back_populates="adjustments")

    __table_args__ = (
        Index("ix_budget_adjustments_budget_section", "budget_id", "section_id"),
    )
