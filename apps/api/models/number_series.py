"""How documents are numbered, and the record of every number ever issued.

CLAUDE.md rule 5: a number is assigned only inside the finalize transaction,
and the sequence is gap-free. Two tables, and the second one is why the first
can make that promise.

**`NumberSeries` holds the counter. `NumberAllocation` holds the history.**
A counter on its own can answer "what is next" and nothing else — not "has
this number been issued", not "what did 2025 reach", and not "is lowering
this counter safe". Every one of those is a question the law asks, so every
allocation is a row.

**Why `period` is a fact about the document date, not about the counter.**
It is the year the document belongs to, derived from `document_date`, and it
is recorded on every allocation regardless of whether the series restarts
annually. For a `yearly` series it is also the counter's bucket, which is
what lets a back-dated document draw from its own year's counter instead of
from the current one (the rule Mathias decided on 2026-10-20). For a `never`
series it is only data. Defining it once, the same way, is what keeps the two
modes from needing two meanings of the same column.

**Austria, concretely.** §11 UStG requires an invoice to carry a
continuously-numbered identifier that is issued once. Both modes here satisfy
that: `yearly` restarts the sequence inside a year token, so
`AR-2026-0001` and `AR-2027-0001` are different identifiers; `never` does not
restart at all. What neither mode may do is issue one identifier twice, which
is what `unique (series_id, number)` is for — the database's answer, not the
application's.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from ..database import Base
except ImportError:
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover - direct-import path for alembic
    from models.company import CompanyScoped


#: How a series treats the turn of the year.
#:
#: Plain strings rather than a Postgres enum, for the same reason as
#: `companies.bookkeeping_mode`: the schema's `Literal` already puts the union
#: into OpenAPI and therefore into the generated TypeScript, while an enum
#: type would cost a migration to add a value and buy nothing.
RESET_YEARLY = "yearly"
RESET_NEVER = "never"
RESET_MODES = (RESET_YEARLY, RESET_NEVER)


class NumberSeries(CompanyScoped, Base):
    """One counter, for one kind of document, in one company.

    `is_active` rather than deletion: a series that issued numbers is the
    only explanation of those numbers' shape, and deleting it would orphan
    the allocations that prove the sequence was gap-free. Superseding a
    series means deactivating it and activating its replacement, and exactly
    one may be active per company and document type — enforced by the partial
    unique index below, because several INACTIVE ones are the normal result
    of having superseded a series twice.
    """

    __tablename__ = "number_series"
    __table_args__ = (
        #: One ACTIVE series per company and document type. Partial, so the
        #: inactive history does not collide with itself — a plain unique
        #: index on the pair would make it impossible to keep two superseded
        #: series, which is the whole reason `is_active` exists.
        Index(
            "uq_number_series_active_per_type",
            "company_id",
            "document_type",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: `invoice`, `quote`, … A string key and not an enum: the document types
    #: are SCOPE §5.1's list today and a region pack may add one (a country
    #: with a document FilmBill has not met), and a new value must not be a
    #: migration.
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What the Number series screen calls it. "Ausgangsrechnungen".
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: `AR-{YYYY}-{SEQ}`. Tokens `{YYYY}`, `{YY}`, `{MM}`, `{SEQ}` plus
    #: literal text — see `services/number_series.py`, which is the only
    #: place that reads or writes this (rule 2: the web app never formats a
    #: number).
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `yearly` or `never`. Mathias's decision (2026-10-20): continuous
    #: numbering is a real option chosen per series, not a fallback.
    reset: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=RESET_YEARLY
    )
    #: The next sequence value for `current_period`. 1-based, because a
    #: document numbered 0 is a document nobody would recognise.
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    #: The period `next_value` belongs to — the four-digit year, as text.
    #:
    #: Text and not an integer so that a future monthly reset is `2026-03`
    #: rather than a second column. NULL on a series that has never
    #: allocated anything, which stays distinguishable from "the counter
    #: belongs to year zero".
    current_period: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    #: How many digits `{SEQ}` is padded to. Padding is a minimum, never a
    #: maximum: value 10000 with padding 4 prints `10000` and is not
    #: truncated or wrapped — see `format_number`.
    padding: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    allocations: Mapped[list["NumberAllocation"]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class NumberAllocation(CompanyScoped, Base):
    """One number, issued once, recorded for ever.

    Rows are never updated and never deleted by the application. They are the
    evidence that the sequence has no gaps and no repeats, and an evidence
    table that the code edits is not evidence.

    `entity_type`/`entity_id` are nullable because the number is allocated
    INSIDE the finalize transaction, before the document it belongs to
    necessarily has an id — and because a number allocated by a transaction
    that then rolled back leaves no row at all, which is the property
    `allocate_number` not committing is there to give.
    """

    __tablename__ = "number_allocations"
    __table_args__ = (
        #: The rule rule 5 is about, in the one place that can hold it. A
        #: pattern change that would re-issue a number hits this and becomes
        #: a 409 naming the number, rather than a second row.
        UniqueConstraint("series_id", "number", name="uq_number_allocations_number"),
        #: One value per period per series. For a `yearly` series this is the
        #: gap-free sequence itself; for `never` the period varies and the
        #: value never repeats anyway, so it costs nothing and still holds.
        UniqueConstraint(
            "series_id", "period", "value", name="uq_number_allocations_value"
        ),
        #: The index the back-dated path reads: "the highest value already
        #: allocated in THIS period of THIS series". It runs under the
        #: series' row lock, so it is on the critical section and wants to be
        #: an index lookup.
        Index("ix_number_allocations_series_period", "series_id", "period"),
        #: And the index behind "the latest document date in this series",
        #: which is what decides whether a date is out of order.
        Index(
            "ix_number_allocations_series_document_date",
            "series_id",
            "document_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("number_series.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The year the document belongs to, as text. See the module docstring.
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    #: The sequence value, before formatting.
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The formatted number, as it appears on the document. Stored rather
    #: than re-derived, because the pattern can change afterwards and the
    #: number on a finalized document cannot (rule 3).
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The document's own date — not when it was allocated. These differ
    #: whenever somebody enters last month's invoice, which is the case the
    #: out-of-order warning is about.
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: What the number was issued for. Nullable — see the class docstring.
    entity_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    series: Mapped["NumberSeries"] = relationship(back_populates="allocations")
