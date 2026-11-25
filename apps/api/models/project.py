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
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship
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

#: An extra's lifecycle (B1.1). Only `accepted` counts in any total;
#: a withdrawn extra is `declined` with a note — nothing is deleted.
EXTRA_PROPOSED = "proposed"
EXTRA_ACCEPTED = "accepted"
EXTRA_DECLINED = "declined"
EXTRA_STATUSES: tuple[str, ...] = (EXTRA_PROPOSED, EXTRA_ACCEPTED, EXTRA_DECLINED)

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
    #: B1.1: the production's end. Only a LOCKED budget can be closed, and a
    #: closed one refuses cost entries, still-to-spend and finishing lines
    #: until it is reopened (`budget_service.refuse_if_closed`). What was
    #: approved when it closed is in the `budget.closed` audit event.
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_budgets_closed_by_users"),
        nullable=True,
    )
    close_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

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
    #: B1.1: the budget's one reserved Extras section — created on first
    #: use, never renamed, never removed. Its lines are extras (each with an
    #: `extra_status`) and are managed on the Extras tab, not on the sheet.
    is_extras: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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
        # At most one Extras section per budget (migration 0018).
        Index(
            "uq_budget_sections_one_extras",
            "budget_id",
            unique=True,
            postgresql_where=text("is_extras"),
        ),
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
    #: The user's own estimate of what is still to be spent on this line
    #: (slice B1). `None` means "use the default forecasting rule";
    #: `Decimal("0.00")` means "this line is finished" and is NOT the same
    #: thing — `billing/budget/tracking.py` distinguishes them, and the
    #: column is nullable precisely so it can.
    #:
    #: It lives on the line rather than in a table of its own because there
    #: is exactly one per line and it is overwritten, not accumulated: a
    #: history of ETCs is a thing to want one day and the audit log already
    #: has it (`budget_line.etc_set` carries the before and the after).
    etc: Mapped[Optional[Any]] = mapped_column(AmountColumn(), nullable=True)
    #: B1.1 extras. `proposed` | `accepted` | `declined` on a line in the
    #: Extras section, NULL everywhere else (`EXTRA_STATUSES`; the rule is
    #: the service's). Only `accepted` extras enter the budget's totals.
    extra_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    extra_decided_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    #: What the customer was told.
    extra_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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


#: A commitment's lifecycle, as the DEFAULT this column is created with.
#:
#: The authority is `billing/budget/tracking.py` — `committed_open` is
#: defined in terms of these three words, and nothing here may contradict it.
#: It is spelled again rather than imported for the reason `models/catalog.py`
#: references `billing/catalog/kinds` in a comment and does not import it:
#: `alembic/env.py` does a bare `import models` on a different `sys.path`, and
#: a model that reaches into `billing/` on the way up makes `alembic check`
#: — the job that notices a table with no migration (CLAUDE.md 17d) — depend
#: on an import path that is not the application's.
#:
#: `test_budget_tracking_model.py` asserts that this literal and
#: `tracking.STATUS_OPEN` are the same string, so the duplication is checked
#: rather than trusted.
COMMITMENT_STATUS_DEFAULT = "open"


class BudgetEntry(CompanyScoped):
    """What a commitment and an actual have in common. Not a table.

    A mixin rather than a shared table or a `type` column, because the two
    things genuinely differ — a commitment has a status and closes, an actual
    has dates and may point at a commitment — and the columns below are only
    the ones that are the same question asked twice: which budget, which
    section, which line, who is being paid, what it says on the paperwork,
    how much, and whether somebody has taken it back.

    **`section_id` is required and `line_id` is not.** Every cost belongs
    somewhere in the sheet's account structure, even when it matches no row:
    a taxi nobody budgeted is still Travel. A cost with no section at all
    would be a cost with no place in the top sheet, and the only honest thing
    to do with it would be to print it at the bottom under "other", which is
    where production budgets go to die.

    **`party_id` or `payee_text`, and neither is required.** A supplier that
    is a party gets the foreign key and inherits every tax identifier and
    address P1b gave it; a one-off — a parking receipt, a crew member paid in
    cash — gets free text, because refusing the entry until somebody creates
    a party is how costs end up in a spreadsheet instead. The service refuses
    both at once: two names for one payee is a row nobody can reconcile.

    **Amounts are NET and in the company currency, always, and > 0.** A
    correction is a new entry plus a void, never a negative amount: a
    negative row would make every sum in `tracking.py` depend on which rows
    happened to be included, and "the total is right if you add up the right
    subset" is not a property anybody can check. Foreign currency is
    deliberately not built (SCOPE D88).

    **Nothing is ever deleted.** `voided_at` and `void_reason` move together
    — the service refuses one without the other — and a voided entry stays
    visible in the ledger while leaving every number.
    """

    @declared_attr
    def budget_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("budgets.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def section_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("budget_sections.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )

    @declared_attr
    def line_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("budget_lines.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        )

    @declared_attr
    def party_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        )

    @declared_attr
    def payee_text(cls) -> Mapped[Optional[str]]:
        return mapped_column(String(255), nullable=True)

    @declared_attr
    def description(cls) -> Mapped[Optional[str]]:
        return mapped_column(String(500), nullable=True)

    @declared_attr
    def reference(cls) -> Mapped[Optional[str]]:
        """A PO number on a commitment, a supplier invoice number on an
        actual. Free text, because it is somebody else's numbering scheme."""
        return mapped_column(String(128), nullable=True)

    @declared_attr
    def amount(cls) -> Mapped[Any]:
        return mapped_column(AmountColumn(), nullable=False)

    @declared_attr
    def notes(cls) -> Mapped[Optional[str]]:
        return mapped_column(Text, nullable=True)

    @declared_attr
    def voided_at(cls) -> Mapped[Optional[datetime]]:
        return mapped_column(DateTime(timezone=True), nullable=True)

    @declared_attr
    def void_reason(cls) -> Mapped[Optional[str]]:
        return mapped_column(String(500), nullable=True)

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None


class BudgetCommitment(BudgetEntry, Base):
    """Money promised but not yet invoiced: a PO, a booking, an agreement.

    `status` is the lifecycle `tracking.py` defines — `open` is owed,
    `closed` is settled by the actuals linked to it, `cancelled` was
    withdrawn and was never owed. Closed and cancelled are different states
    rather than one `is_open` flag because they answer `committed_gross`
    differently: a closed order was still ordered, a cancelled one was not.

    **Closing is automatic and reversible.** When the non-voided actuals
    linked to a commitment reach its amount the service closes it; when one
    of those actuals is voided it reopens. Both are audited. It can also be
    closed or cancelled by hand with a reason — a supplier who invoices 940
    against a 1 000 order and then stops is a thing that happens, and the
    only person who knows the other 60 is never coming is the producer.
    """

    __tablename__ = "budget_commitments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: One of `COMMITMENT_STATUSES`. A `String(16)` and not a database enum,
    #: for migration 0015's reason: the authority is the Python vocabulary.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=COMMITMENT_STATUS_DEFAULT
    )
    #: When the money was promised — the date on the order, not the date the
    #: row was typed.
    commitment_date: Mapped[date] = mapped_column(Date, nullable=False)


    __table_args__ = (
        # Stated on the model as well as in migration 0016, because the
        # real-database suite builds its schema from the models: a CHECK that
        # lived only in the migration would be absent from every test
        # database, and `test_the_amount_check_constraint_actually_fires`
        # found exactly that. `alembic check` does not compare CHECKs, so
        # the two copies are kept honest by that test and not by alembic.
        CheckConstraint("amount > 0", name="ck_budget_commitments_amount_positive"),
        Index(
            "ix_budget_commitments_budget_section",
            "budget_id",
            "section_id",
        ),
        Index("ix_budget_commitments_company_party", "company_id", "party_id"),
        Index(
            "ix_budget_commitments_budget_date", "budget_id", "commitment_date"
        ),
    )


class BudgetActual(BudgetEntry, Base):
    """Money actually spent: a supplier invoice, a receipt, a payroll line.

    `commitment_id` links it to the order it settles, where there is one.
    Nullable, because plenty of real costs arrive with no order behind them —
    and the link is what makes `exposure = actual + committed_open` work: an
    invoice with no link does not reduce any open commitment, which is
    correct, because it is not settling one.

    **`incurred_on` is when the cost arose; `paid_on` is when the money
    left.** Two dates, because a budget is read by date of cost ("what has
    this shoot cost so far") and paid by date of payment, and one column
    would answer whichever question somebody asked second. `paid_on` is
    nullable and that is the whole of payment status in B1 — an aging
    report, a payment run and a bank reconciliation are P5 and P6
    (SCOPE D88).
    """

    __tablename__ = "budget_actuals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    commitment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budget_commitments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    incurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_budget_actuals_amount_positive"),
        Index("ix_budget_actuals_budget_section", "budget_id", "section_id"),
        Index("ix_budget_actuals_company_party", "company_id", "party_id"),
        Index("ix_budget_actuals_budget_incurred", "budget_id", "incurred_on"),
    )
