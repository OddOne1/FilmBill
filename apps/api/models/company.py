"""Companies, their bank accounts, and who may act inside them.

The layer everything after P0b sits on. Three tables and one mixin, and the
mixin is the important one: `CompanyScoped` is what makes "which company does
this row belong to" a property of the model rather than a `filter()` each
router remembers to write (CLAUDE.md rule 6).

**Install-level and company-level authority are different things and do not
imply each other.** `User.role` (superadmin / superuser / user) runs the
*installation*: users, mail, site settings. It confers no access to any
company's data — a superadmin who is not a member of a company gets the same
404 a stranger does. What a person may do inside a company lives only in
`CompanyMembership`, and the two ladders never collapse into one. The one
bridge is deliberate and narrow: a superadmin may CREATE a company, and doing
so makes them its Owner, because an installation with no way to make the
first company is an installation nobody can use.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

try:
    from ..database import Base
except ImportError:
    from database import Base


class CompanyRole(str, PyEnum):
    """What a person may do inside ONE company.

    Six values, fixed here rather than in a table, because they are the
    vocabulary the permission map is keyed by — a role someone can invent at
    runtime is a role no permission has an entry for, which fails open or
    fails silently depending on how the lookup is written. Adding a role is
    a migration plus a line in `services/permissions.py`, on purpose.

    `tax_advisor` is the one that shapes the rest (SCOPE §9.3b): an external
    person, usually at another firm, who is handed a company's archive and
    nothing else. Every rule in this file that looks over-careful — the
    expiry column, the 404-not-403 answer, the per-role 2FA requirement — is
    there because this role hands data to someone outside the company.
    """

    owner = "owner"
    admin = "admin"
    accountant = "accountant"
    producer = "producer"
    staff = "staff"
    tax_advisor = "tax_advisor"


class Company(Base):
    """One legal entity that issues documents.

    Not a "tenant" and not an "organisation": a company here is the thing
    whose VAT ID appears on an invoice, which is why the register and tax
    identifiers are first-class columns rather than a settings blob. What
    goes on a finalized document must be a column that a snapshot can copy
    (CLAUDE.md rule 4).
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Identity ─────────────────────────────────────────────────────
    #: The name on the register. This is what goes on an invoice.
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: What the company calls itself day to day, when that differs. NULL
    #: rather than a copy of `legal_name`, so "they are the same" stays
    #: distinguishable from "nobody has said".
    trading_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: "OG", "GmbH", "e.U." … A free string, not an enum: legal forms are a
    #: per-country list and belong in a region pack (CLAUDE.md rule 7), not
    #: in a Python enum that would need a migration per country.
    legal_form: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    register_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    register_court: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: Stored as typed, not normalised. Validation is a region-pack question
    #: and lands with the tax rules; silently rewriting what someone entered
    #: on their own registration certificate is worse than storing it.
    vat_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tax_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Address and contact ──────────────────────────────────────────
    address_street: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_zip: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    address_city: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: ISO 3166-1 alpha-2. The key the region pack is looked up by, so it is
    #: two characters and upper case by convention rather than a country name
    #: someone would have to parse.
    address_country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Defaults for the documents this company issues ───────────────
    #: ISO 4217. NOT NULL with a default, because every money column in the
    #: system is meaningless without one and "unknown currency" is not a
    #: state any calculation can survive.
    default_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="EUR"
    )
    #: ISO 639-1. What a document is written in when nothing overrides it.
    default_language: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="de"
    )
    #: 1–12. Austria's is January; it is a column because the UK's is April
    #: and a hardcoded January would be wrong for a whole country.
    fiscal_year_start_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    #: IANA zone name, deciding what a document's date means. Separate from
    #: `site_settings.timezone`, which is about when the maintenance jobs
    #: run: one installation can host companies in two countries.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    #: S3 key, not a URL. Browser-facing URLs are minted by the presign
    #: client at read time (CLAUDE.md rule 16); storing one here would freeze
    #: an endpoint into the database and break the moment it changes.
    logo_s3_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # ── Security policy, per company ─────────────────────────────────
    #: Company roles whose holders must have two-factor authentication, as a
    #: list of `CompanyRole` values.
    #:
    #: A list on the COMPANY rather than a flag on the user, because that is
    #: what the rule actually is: "whoever is our accountant must have 2FA",
    #: not "this person must". A flag on `users` would have to be rewritten
    #: every time a membership changed and would be wrong in between — see
    #: `services/company_service.two_factor_roles_for`, which derives the
    #: answer instead so it cannot drift.
    #:
    #: `tax_advisor` is NOT stored here and does not need to be: that role is
    #: required to have 2FA unconditionally, on every install, because it is
    #: the one role routinely held by someone outside the company.
    require_2fa_roles: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    #: Whether this company's tax advisor may also see reports and download
    #: exports, beyond the archive they always get.
    #:
    #: Off by default. The archive is the documents this company already
    #: issued — the advisor needs those to do the books. Reports are the
    #: company's own analysis of itself, which is a separate decision and is
    #: the company's to make. See TAX_ADVISOR_OPTIONAL in
    #: `services/permissions.py`.
    tax_advisor_reports: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Archived, never deleted. A company's documents are records with
    #: statutory retention periods behind them; the row that says whose
    #: records they are cannot go away while they exist.
    archived_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    memberships: Mapped[list["CompanyMembership"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    bank_accounts: Mapped[list["CompanyBankAccount"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        """What to put in a switcher. The trading name if there is one."""
        return self.trading_name or self.legal_name


class CompanyScoped:
    """Mixin: this row belongs to exactly one company.

    A mixin rather than a convention, so `company_id` cannot be forgotten,
    cannot be nullable, and cannot be un-indexed — three things that were
    each a per-table decision before and would each eventually be decided
    wrongly. `services/permissions.scoped()` recognises models by this class
    and refuses to filter anything else, which is what makes "every business
    query goes through `scoped`" checkable rather than aspirational.

    `declared_attr` because a plain `mapped_column` on a mixin is one column
    object shared by every subclass; SQLAlchemy needs a fresh one per table.
    """

    @declared_attr
    def company_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class CompanyBankAccount(CompanyScoped, Base):
    """An account this company is paid into.

    Several per company (a production account and a payroll account is the
    normal case), one of them flagged as the one that goes on a document
    unless something says otherwise.

    No endpoint serves this yet — the company-settings UI is P0b-2. It is
    modelled here because it is part of what a company IS, and because a
    table that arrives with the migration that creates its parent is one
    migration instead of two.
    """

    __tablename__ = "company_bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: "Production account", "Payroll". What a human picks it by.
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: Stored exactly as entered, spaces and all. Formatting for display and
    #: for SEPA export are two different renderings of this one value, and
    #: neither belongs in the column.
    iban: Mapped[str] = mapped_column(String(64), nullable=False)
    bic: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="bank_accounts")


class CompanyMembership(CompanyScoped, Base):
    """One person's authority inside one company, for a period of time.

    Rows are never deleted. Revoking sets `revoked_at`, so "this person used
    to have access until March" stays answerable — which is the question an
    audit actually asks, and which a DELETE destroys.

    Both `expires_at` and `revoked_at` exist because they mean different
    things. An expiry is a decision taken in advance ("the advisor has it for
    the duration of the annual accounts"); a revocation is a decision taken
    now. Collapsing them into one column would make an expiry indistinguish-
    able from someone having been thrown out.
    """

    __tablename__ = "company_memberships"
    __table_args__ = (
        #: The index the login path leans on. `two_factor_required_for` runs
        #: on every single login and asks exactly this: "the active
        #: memberships of THIS user". `revoked_at` is in the key because the
        #: overwhelming majority of rows are NULL there and the query always
        #: constrains it.
        Index("ix_company_memberships_user_revoked", "user_id", "revoked_at"),
        #: Scoped listings — "who is in this company" — read the other way
        #: round, so they get their own.
        Index("ix_company_memberships_company_revoked", "company_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[CompanyRole] = mapped_column(Enum(CompanyRole), nullable=False)
    #: Who granted this. Nullable because the very first membership on an
    #: install is created by the setup flow, where there is no grantor yet —
    #: the superadmin is making themselves the Owner of the company they are
    #: creating in the same transaction.
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: When this access lapses on its own. NULL means it does not.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When it was taken away. NULL means it has not been.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    company: Mapped["Company"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(foreign_keys=[user_id])  # noqa: F821

    def is_active(self, *, now: Optional[datetime] = None) -> bool:
        """Whether this row grants anything at this moment.

        The single definition of "active", used by the dependency, by the
        2FA policy and by the listings, so those three cannot disagree about
        an expiry the way three separate inline conditions eventually would.
        """
        from datetime import timezone as _tz

        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        moment = now or datetime.now(_tz.utc)
        expires = self.expires_at
        # A naive value can reach here from a fixture or a hand-edited row;
        # comparing it with an aware `now` raises TypeError, which would turn
        # an authorisation question into a 500. Read as UTC, which is what
        # the column stores anyway (same reasoning as
        # site_settings_service.passwordless_window_closed).
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=_tz.utc)
        return expires > moment
