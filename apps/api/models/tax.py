"""Tax codes, tax classes and tax rules — one company's own tax configuration.

SCOPE §6 and D19: **FilmBill proposes, the user decides.** Everything in these
three tables arrives from a region pack and is editable from the moment it is
seeded. Nothing here is a constant in code, because the alternative — a rate
or a legal note compiled into the application — is a country's interpretation
that cannot be corrected without a release (CLAUDE.md rule 7).

**Three tables, three different jobs.**

  * `tax_codes` is what goes on an invoice line: a rate, an EN 16931 category
    and the sentence the law wants printed beside it.
  * `tax_classes` is the handle an ITEM holds. A studio is "standard", a book
    is "reduced" — and what those mean in money is a per-company fact that
    changes when a rate changes, which is exactly why the item does not store
    a rate. One indirection, and a VAT change is one row.
  * `tax_rules` is the ordered list that decides WHICH code a given sale gets.
    Data, validated against a closed vocabulary (`billing/tax/vocabulary.py`),
    and evaluated by a pure function that never touches this file.

**Why `rate` is a percent and not a factor.** 20.0000, not 0.2000. It is the
number on the invoice, in the region pack, in the UVA and in every
conversation anybody has about it; storing the factor would mean every screen
and every template multiplied by 100 and one of them eventually would not.
`NUMERIC(18,4)` via `QtyColumn()` — four decimals because a rate like 2.5 %
exists and because rule 1 says no money or rate passes through a float.

**Why the code and the category are immutable after creation.** A tax code is
referenced by documents that are already issued (rule 3: finalized documents
are never overwritten, and §5.4 snapshots the tax code onto the line). Letting
`AT-20` become `AT-10` would silently restate what an issued invoice says. The
rate IS editable, because a rate legitimately changes on a date — and the
snapshot on the issued document is what keeps history true.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    from ..core.money import QtyColumn
except ImportError:  # pragma: no cover - direct-import path for alembic
    from core.money import QtyColumn

try:
    from ..database import Base
except ImportError:  # pragma: no cover
    from database import Base

try:
    from .company import CompanyScoped
except ImportError:  # pragma: no cover
    from models.company import CompanyScoped


#: The names of the constraints this module creates, so a service can
#: recognise its own IntegrityError by name instead of by matching the text of
#: a Postgres message that differs between versions (the lesson of
#: `company_service.IBAN_UNIQUE_CONSTRAINT`).
TAX_CODE_UNIQUE_CONSTRAINT = "uq_tax_codes_company_code"
TAX_CLASS_UNIQUE_CONSTRAINT = "uq_tax_classes_company_key"

#: Repeated in the CHECK below rather than imported from
#: `billing/tax/vocabulary.py`. The vocabulary module is the one the
#: application validates against and the one a readable error comes from; this
#: is the database's own floor under it, and a floor that imports the thing it
#: is a floor under is not a floor. The test
#: `test_the_check_constraint_and_the_vocabulary_agree` is what keeps the two
#: lists identical.
_CATEGORY_SQL = "'S','Z','E','AE','K','G','O','L','M'"
_ZERO_RATE_CATEGORY_SQL = "'Z','E','AE','K','G','O'"


class TaxCode(CompanyScoped, Base):
    """One rate, one category, one legal note — the thing an invoice line uses."""

    __tablename__ = "tax_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    #: `AT-20`, `EU-AE`. Short, stable and the thing a user recognises in a
    #: dropdown. Unique per company and immutable after creation.
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What the dropdown shows beside the code. Editable — "20 % USt" and
    #: "Normalsteuersatz" are the same code to two different accountants.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Percent, not a factor: 20.0000 means 20 %.
    rate: Mapped[Any] = mapped_column(QtyColumn(), nullable=False)
    #: EN 16931 VAT category. Immutable, and constrained to the nine codes.
    category: Mapped[str] = mapped_column(String(2), nullable=False)

    #: `{lang: text}` — the sentence printed beside the amount. JSONB rather
    #: than a column per language because the set of languages a company
    #: invoices in is a company's business, not a schema change.
    legal_notes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: **False for every text FilmBill ships.** The notes in the region packs
    #: are generic wording written by developers, not advice; the screen says
    #: "not reviewed" until somebody in the company confirms it. A note that
    #: looked authoritative on an invoice because nobody had been asked is the
    #: failure this flag exists to prevent.
    notes_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    #: `{target: field}` — AT UVA field, DE UStVA line (SCOPE §6.1). Empty in
    #: P1a: the targets arrive with the accounting core in P6, and inventing
    #: the shape now would mean migrating it then. The column exists so the
    #: packs have somewhere to put it without a migration.
    reporting_mapping: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    #: The revenue account this code books to. A plain string and nullable:
    #: charts of accounts arrive in P6, and a foreign key to a table that does
    #: not exist yet is a table that gets created early and wrongly.
    revenue_account: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    #: Inactive codes stay, they do not go. A code that appeared on an issued
    #: invoice is part of that invoice's explanation; deleting it would orphan
    #: the snapshot that proves what was charged. The generic pack's standard
    #: code ships INACTIVE at 0 % on purpose — see the pack header.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: Which pack seeded this row (`at`, `de`, `eu_generic`), or NULL for one a
    #: user created. Read by the seeder to answer "is this still mine to
    #: update" — and the answer is no, which is why re-seeding never
    #: overwrites.
    origin_pack: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

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
        UniqueConstraint("company_id", "code", name=TAX_CODE_UNIQUE_CONSTRAINT),
        CheckConstraint(
            f"category IN ({_CATEGORY_SQL})",
            name="ck_tax_codes_category",
        ),
        CheckConstraint("rate >= 0", name="ck_tax_codes_rate_not_negative"),
        # The database's own answer to "exempt at 20 %". The application
        # refuses it first, with a sentence; this is what makes the refusal
        # true for a psql session and a future bug as well.
        CheckConstraint(
            f"category NOT IN ({_ZERO_RATE_CATEGORY_SQL}) OR rate = 0",
            name="ck_tax_codes_zero_rate_categories",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<TaxCode {self.code} {self.rate}% {self.category}>"


class TaxClass(CompanyScoped, Base):
    """What an item points at: "standard", "reduced" — the domestic code.

    SCOPE §6.3's "domestic rate of the item's tax class". The class resolves to
    the company's OWN country's code and nothing else: foreign domestic rates
    and OSS are out of scope for v1, so there is deliberately no
    `(class, country)` pair here. The day OSS arrives this table grows a
    country column and the engine grows a lookup; pretending to support it now
    would ship a half-answer that reads as a whole one.
    """

    __tablename__ = "tax_classes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    #: `standard`, `reduced`, `reduced_2`, `zero` — whichever the pack defines.
    #: Not an enum: Austria has three positive rates and Germany two, and a
    #: country with four is not a migration.
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The domestic code this class means. `RESTRICT`, not `CASCADE`: deleting
    #: a code that a class points at would leave every item in that class with
    #: no rate at all, and the correct way to retire a code is `is_active`.
    code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_codes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Display order, so the picker reads standard → reduced → zero rather
    #: than alphabetically (`reduced`, `reduced_2`, `standard`, `zero`).
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    origin_pack: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

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
        UniqueConstraint("company_id", "key", name=TAX_CLASS_UNIQUE_CONSTRAINT),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaxClass {self.key}>"


class TaxRule(CompanyScoped, Base):
    """One ordered "if this, then that" — the data SCOPE §6.3 ships.

    `conditions` and `outcome` are JSONB over a CLOSED vocabulary
    (`billing/tax/vocabulary.py`), validated before anything is stored. The
    closure is the load-bearing part: a misspelled condition key that was
    merely never true would leave the rule sitting in the list looking
    correct while the engine fell through to the next one, and a reverse
    charge becoming 20 % domestic tax is not a failure anybody notices on the
    screen.

    Read-only through the API in P1a (SCOPE §6.3 calls editing these a later
    step) — but stored per company from the first day, because the alternative
    is a shipped list that a company cannot adjust, which is D19 the wrong way
    round.
    """

    __tablename__ = "tax_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    #: Evaluation order, lowest first. Spaced by ten in the packs so a user
    #: can insert one between two shipped rules without renumbering.
    #:
    #: NOT unique: re-ordering a list through a unique column means either a
    #: temporary duplicate or a deferred constraint, and the tie-break below
    #: (position, then created_at) is deterministic without one.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    conditions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    outcome: Mapped[dict] = mapped_column(JSONB, nullable=False)
    origin_pack: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
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
        Index("ix_tax_rules_company_position", "company_id", "position"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaxRule {self.position} {self.name!r}>"
